/* Lugaria Acceso — cámara y lectura de placas en el panel (sección Plumas).
 *
 * Fuentes de imagen
 *   · Cámara de este equipo: webcam o cámara USB del navegador (getUserMedia).
 *   · Cámara de la pluma: foto del cuadro actual, pedida al servicio (orden "snapshot").
 *
 * Lectores
 *   · Servicio de una pluma (fast-alpr, el mismo modelo que abre la pluma): la imagen se manda
 *     como orden "read" por Firestore y la pluma responde con las placas. Más preciso.
 *   · Navegador (Tesseract.js): no necesita servidor; lee el texto dentro del recuadro guía.
 *
 * Si la placa leída coincide con una registrada y con acceso, se abre la pluma elegida en
 * "Al reconocer una placa autorizada" (orden "open" con via "camara-panel"; el servicio vuelve a
 * validar la placa y lo registra en la bitácora como acceso autorizado). Las lecturas por sí solas
 * (órdenes "read" y "snapshot") nunca abren la pluma.
 */
import { collection, addDoc, doc, onSnapshot, deleteDoc, serverTimestamp }
  from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import { db, $, esc, normalize, VEHICLE_TYPES, VSTATUS, vStatus, gateOnline, decideAccess } from "./common.js";
import { RESULT_UI } from "./placas-mx.js";

const TESSERACT_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js';
const GUIDE = { x:.12, y:.30, w:.76, h:.40 };          // recuadro guía, en fracciones del cuadro
const TYPES = ['auto', 'camioneta', 'pickup', 'motocicleta'];
const AUTO_MAX_MS = 90_000;                               // la lectura automática se apaga sola
const OPEN_COOLDOWN_MS = 20_000;                          // no reabrir por la misma placa en este tiempo

/**
 * app = { state: () => S (user, staff, gates, vehicles, students), verifier: () => VER,
 *         openStudent(id), showVehicle(plate) }
 */
export function initPlateCamera(app) {
  const C = { stream:null, mode:'off', gateId:null, busy:false, auto:false, autoUntil:0, timer:null,
              worker:null, workerLoading:null, srcKey:'', cands:[], engineLabel:'', actKey:'', opened:new Map() };
  const video = $('cam-video'), view = $('cam-canvas');

  const status = (msg, bad = false) => { const e = $('cam-status'); e.textContent = msg || ''; e.style.color = bad ? 'var(--red)' : ''; };
  const onlineGates = () => app.state().gates.filter(gateOnline);
  const gateName = id => { const g = app.state().gates.find(x => x.id === id); return g?.name || id; };

  /* ── Listas de cámaras y lectores (se rehacen sólo si cambian) ── */
  function refresh() {
    const gates = app.state().gates;
    const key = JSON.stringify(gates.map(g => [g.id, g.name, gateOnline(g)]));
    if (key === C.srcKey) return;
    C.srcKey = key;
    const src = $('cam-src'), cur = src.value;
    src.innerHTML = '<option value="local">Cámara de este equipo</option>' + gates.map(g =>
      `<option value="gate:${esc(g.id)}" ${gateOnline(g) ? '' : 'disabled'}>Cámara de la pluma · ${esc(g.name || g.id)}${gateOnline(g) ? '' : ' (sin conexión)'}</option>`).join('');
    src.value = [...src.options].some(o => o.value === cur && !o.disabled) ? cur : 'local';
    const eng = $('cam-eng'), curE = eng.value;
    eng.innerHTML = onlineGates().map(g => `<option value="svc:${esc(g.id)}">Leer con el servicio de ${esc(g.name || g.id)} (más preciso)</option>`).join('')
      + '<option value="browser">Leer en este navegador (sin servidor)</option>';
    // Por defecto, el servicio de una pluma conectada (más preciso); se respeta lo que el usuario eligió.
    eng.value = C.engChosen && [...eng.options].some(o => o.value === curE) ? curE : eng.options[0].value;
    syncControls();
  }

  function syncControls() {
    const gateSrc = $('cam-src').value.startsWith('gate:');
    $('cam-eng').hidden = gateSrc;
    $('cam-on').textContent = gateSrc ? 'Ver cámara' : 'Encender cámara';
    $('cam-on').hidden = C.mode !== 'off';
    $('cam-off').hidden = C.mode === 'off';
    $('cam-dev').hidden = gateSrc || $('cam-dev').options.length < 2;
    $('cam-read').disabled = C.mode === 'off';
    $('cam-read').textContent = C.mode === 'gate' ? 'Tomar foto y leer' : 'Leer placa';
    syncAct();
  }

  /* Pluma que se abre al reconocer una placa autorizada. Por defecto, la de la cámara o el lector elegidos. */
  function syncAct() {
    const sel = $('cam-act'), cur = sel.value, on = onlineGates();
    $('cam-act-row').hidden = !on.length;        // sin plumas conectadas el panel sólo muestra el resultado
    const key = JSON.stringify(on.map(g => [g.id, g.name]));
    if (key !== C.actKey) {
      C.actKey = key;
      sel.innerHTML = on.map(g => `<option value="${esc(g.id)}">Abrir ${esc(g.name || g.id)}</option>`).join('')
        + `<option value="none">${on.length ? 'Sólo consultar (no abrir)' : 'Sin plumas conectadas: sólo consultar'}</option>`;
    }
    const src = $('cam-src').value, eng = $('cam-eng').value;
    const def = src.startsWith('gate:') && on.some(g => g.id === src.slice(5)) ? src.slice(5)
      : eng.startsWith('svc:') && on.some(g => g.id === eng.slice(4)) ? eng.slice(4)
      : on[0]?.id || 'none';
    sel.value = C.actChosen && [...sel.options].some(o => o.value === cur) ? cur : def;
  }

  /* ── Encender / apagar ── */
  async function start() {
    stop();
    const src = $('cam-src').value;
    $('cam-offmsg').hidden = true;
    $('cam-view').classList.add('on');
    if (src.startsWith('gate:')) {
      C.mode = 'gate'; C.gateId = src.slice(5);
      view.hidden = false;
      syncControls();
      return readOnce();
    }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      showOff('El navegador sólo permite usar la cámara en páginas https o en localhost. Abre el panel desde su dirección publicada (https://…) o con http://localhost.');
      return;
    }
    const devId = $('cam-dev').value;
    try {
      status('Pidiendo permiso para usar la cámara…');
      C.stream = await navigator.mediaDevices.getUserMedia({ audio:false, video: devId
        ? { deviceId:{ exact:devId }, width:{ ideal:1920 }, height:{ ideal:1080 } }
        : { facingMode:{ ideal:'environment' }, width:{ ideal:1920 }, height:{ ideal:1080 } } });
    } catch (e) {
      const msg = { NotAllowedError:'Permiso de cámara denegado. Actívalo en el candado de la barra de direcciones y vuelve a intentar.',
                    NotFoundError:'No se encontró ninguna cámara en este equipo.',
                    NotReadableError:'La cámara está ocupada por otra aplicación (Zoom, Teams…). Ciérrala e intenta de nuevo.',
                    OverconstrainedError:'La cámara elegida no está disponible. Elige otra.' }[e.name] || e.message;
      return showOff(msg);
    }
    C.mode = 'local';
    video.srcObject = C.stream;
    video.hidden = false; view.hidden = true; $('cam-guide').hidden = false;
    await video.play().catch(() => {});
    await listDevices();
    syncControls();
    status('Cámara encendida. Coloca la placa dentro del recuadro y presiona Leer placa.');
  }

  function stop() {
    setAuto(false);
    C.stream?.getTracks().forEach(t => t.stop());
    C.stream = null; C.mode = 'off'; C.gateId = null;
    video.srcObject = null; video.hidden = true; view.hidden = true;
    $('cam-guide').hidden = true;
    $('cam-offmsg').hidden = false; $('cam-offmsg').textContent = 'Cámara apagada';
    $('cam-view').classList.remove('on');
    syncControls();
    status('');
  }

  function showOff(msg) {
    stop();
    $('cam-offmsg').textContent = msg;
    status(msg, true);
  }

  async function listDevices() {
    try {
      const cams = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
      const sel = $('cam-dev'), active = C.stream?.getVideoTracks()[0]?.getSettings().deviceId;
      sel.innerHTML = cams.map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || 'Cámara ' + (i + 1))}</option>`).join('');
      if (active) sel.value = active;
    } catch { /* sin lista: se usa la cámara por defecto */ }
  }

  /* ── Leer ── */
  async function readOnce() {
    if (C.busy || C.mode === 'off') return null;
    C.busy = true; $('cam-read').disabled = true;
    try {
      const eng = $('cam-eng').value;
      const found = C.mode === 'gate' ? await readGateSnapshot()
        : eng.startsWith('svc:') ? await readWithService(eng.slice(4)) : await readInBrowser();
      if (found?.acc.allowed) openFor(found);          // sin esperar: la lectura automática sigue
      return found;
    } catch (e) {
      status(e.message, true);
      return null;
    } finally {
      C.busy = false; $('cam-read').disabled = C.mode === 'off';
    }
  }

  async function readGateSnapshot() {
    status(`Pidiendo foto a ${gateName(C.gateId)}…`);
    const t0 = performance.now();
    const d = await sendCommand('snapshot', C.gateId);
    const img = await loadImage(d.resultImage);
    const r = d.result || {};
    drawFrame(view, img, r.width || img.width, (r.plates || []).map(p => p.box), r.roi);
    $('cam-shot').replaceChildren();               // la foto ya se ve en grande; no dejar una captura anterior
    C.engineLabel = `servicio de ${gateName(C.gateId)}`;
    showCandidates((r.plates || []).map(p => scored(p.text, p.conf)), null);
    status(`Foto de ${gateName(C.gateId)} · ${new Date().toLocaleTimeString('es-MX')} · ${Math.round(performance.now() - t0)} ms`);
    return best();
  }

  async function readWithService(gateId) {
    const frame = grabFrame(1280);
    status(`Enviando la imagen a ${gateName(gateId)}…`);
    const t0 = performance.now();
    const d = await sendCommand('read', gateId, { image:toJpeg(frame) });
    const r = d.result || {};
    drawFrame(shotCanvas(), frame, r.width || frame.width, (r.plates || []).map(p => p.box));
    C.engineLabel = `servicio de ${gateName(gateId)}`;
    showCandidates((r.plates || []).map(p => scored(p.text, p.conf)), null);
    status(`Leída por ${gateName(gateId)} en ${Math.round(performance.now() - t0)} ms` +
      ((r.plates || []).length ? '' : ' · no encontró placas: acerca la placa o mejora la luz'));
    return best();
  }

  async function readInBrowser() {
    const w = await ocrWorker();
    const crop = cropGuide();
    const sc = shotCanvas(); sc.width = crop.width; sc.height = crop.height;
    sc.getContext('2d').drawImage(crop, 0, 0);
    status('Leyendo en el navegador…');
    const t0 = performance.now();
    let text = '', conf = 0;
    for (const psm of ['7', '6']) {                         // una línea; si no, bloque de texto
      await w.setParameters({ tessedit_pageseg_mode:psm });
      const { data } = await w.recognize(crop);
      text += '\n' + data.text; conf = Math.max(conf, (data.confidence || 0) / 100);
      if (candidatesFromText(text, conf).some(c => c.score >= 35)) break;
    }
    C.engineLabel = 'navegador (Tesseract)';
    const cands = candidatesFromText(text, conf);
    showCandidates(cands, text.trim());
    status(`Leída en el navegador en ${Math.round(performance.now() - t0)} ms` +
      (cands.length ? '' : ' · no se reconoció una placa: acerca la placa al recuadro'));
    return best();
  }

  /* ── Lectura automática ── */
  function setAuto(on) {
    C.auto = on; $('cam-auto').checked = on;
    clearTimeout(C.timer);
    if (on) { C.autoUntil = Date.now() + AUTO_MAX_MS; loop(); }
  }
  async function loop() {
    if (!C.auto || C.mode === 'off') return;
    if (Date.now() > C.autoUntil) { setAuto(false); return status('Lectura automática detenida tras 90 segundos. Actívala de nuevo si la necesitas.'); }
    const found = await readOnce();
    // Con la cámara del equipo se detiene al encontrar una placa clara; con la de la pluma sigue mirando.
    if (C.mode === 'local' && found && found.score >= 60) { setAuto(false); status($('cam-status').textContent + ' · lectura automática detenida: placa encontrada'); return; }
    if (C.auto) C.timer = setTimeout(loop, C.mode === 'gate' ? 2500 : 1200);
  }

  /* ── Órdenes a la pluma (Firestore) ── */
  async function sendCommand(action, gateId, extra = {}, { keep = false } = {}) {
    const S = app.state();
    const ref = await addDoc(collection(db, 'commands'), {
      gateId, action, status:'pending', requestedBy:S.user.uid,
      requestedByName:S.staff?.name || S.user.email, createdAt:serverTimestamp(), ...extra,
    });
    try {
      return await new Promise((resolve, reject) => {
        let un = () => {};
        const t = setTimeout(() => { un(); reject(new Error(`${gateName(gateId)} no respondió en 20 segundos. Revisa que su servicio esté encendido y actualizado.`)); }, 20000);
        un = onSnapshot(ref, s => {
          const d = s.data();
          if (!d || d.status === 'pending') return;
          clearTimeout(t); un();
          if (d.status === 'done') resolve(d);
          else reject(new Error(d.note || (typeof d.result === 'string' ? d.result : '') || `La pluma respondió: ${d.status}`));
        }, e => { clearTimeout(t); reject(e); });
      });
    } finally {
      if (!keep) deleteDoc(ref).catch(() => {});     // no acumular imágenes en Firestore
    }
  }

  /* ── Apertura al reconocer una placa autorizada ── */
  function openMsg(text, kind) {
    const e = $('cam-open-msg');
    if (e) { e.textContent = text; e.dataset.k = kind; }
  }
  async function openFor(c, force = false) {
    const gateId = $('cam-act').value;
    if (!c?.acc.allowed || !gateId || gateId === 'none') return;
    const k = `${gateId}:${c.key}`, last = C.opened.get(k) || 0;
    if (!force && Date.now() - last < OPEN_COOLDOWN_MS) {
      return openMsg(`${gateName(gateId)} ya se abrió para esta placa hace ${Math.round((Date.now() - last) / 1000)} s.`, 'ok');
    }
    C.opened.set(k, Date.now());
    openMsg(`Abriendo ${gateName(gateId)}…`, 'wait');
    try {
      await sendCommand('open', gateId, {
        note:`Placa ${c.ver?.display || c.key} reconocida con la cámara del panel`, plate:c.key, via:'camara-panel',
        studentId:c.acc.vehicle?.studentId || null, studentName:c.acc.student?.name || c.acc.vehicle?.studentName || null,
      }, { keep:true });                                 // las aperturas se conservan en Firestore
      openMsg(`${gateName(gateId)} abierta · ${new Date().toLocaleTimeString('es-MX')}`, 'ok');
    } catch (e) {
      C.opened.delete(k);
      openMsg(`No se pudo abrir ${gateName(gateId)}: ${e.message}`, 'no');
    }
  }

  /* ── Imagen ── */
  function grabFrame(maxW) {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw) throw new Error('La cámara todavía no envía imagen. Espera un segundo.');
    const k = Math.min(1, maxW / vw), c = document.createElement('canvas');
    c.width = Math.round(vw * k); c.height = Math.round(vh * k);
    c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
    return c;
  }
  function toJpeg(canvas) {
    let q = .85, url = canvas.toDataURL('image/jpeg', q);
    while (url.length > 850_000 && q > .4) { q -= .15; url = canvas.toDataURL('image/jpeg', q); }
    return url;
  }
  /* Recorte del recuadro guía, en gris y con contraste estirado: así lo lee mejor Tesseract. */
  function cropGuide() {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw) throw new Error('La cámara todavía no envía imagen. Espera un segundo.');
    const sx = GUIDE.x * vw, sy = GUIDE.y * vh, sw = GUIDE.w * vw, sh = GUIDE.h * vh;
    // Recorte entre 240 y 480 px de alto: suficiente para el lector y rápido aun con cámaras 4K.
    const k = sh < 240 ? Math.min(3, 240 / sh) : Math.min(1, 480 / sh);
    const c = document.createElement('canvas'); c.width = Math.round(sw * k); c.height = Math.round(sh * k);
    const g = c.getContext('2d', { willReadFrequently:true });
    g.drawImage(video, sx, sy, sw, sh, 0, 0, c.width, c.height);
    const img = g.getImageData(0, 0, c.width, c.height), d = img.data, hist = new Uint32Array(256);
    for (let i = 0; i < d.length; i += 4) { const y = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000 | 0; d[i] = y; hist[y]++; }
    const n = d.length / 4; let lo = 0, hi = 255, acc = 0;            // percentiles 2 % y 98 %
    for (let v = 0; v < 256; v++) { acc += hist[v]; if (acc > n * .02) { lo = v; break; } }
    acc = 0; for (let v = 255; v >= 0; v--) { acc += hist[v]; if (acc > n * .02) { hi = v; break; } }
    const k2 = 255 / Math.max(1, hi - lo);
    for (let i = 0; i < d.length; i += 4) { const v = Math.max(0, Math.min(255, (d[i] - lo) * k2)); d[i] = d[i + 1] = d[i + 2] = v; }
    g.putImageData(img, 0, 0);
    return c;
  }
  function loadImage(src) {
    return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('La pluma devolvió una imagen ilegible.')); i.src = src; });
  }
  function shotCanvas() {
    let c = $('cam-shot').querySelector('canvas');
    if (!c) { c = document.createElement('canvas'); $('cam-shot').replaceChildren(c); }
    return c;
  }
  /* Dibuja la imagen, el recuadro de cada placa y la zona de interés de la pluma. */
  function drawFrame(canvas, img, srcW, boxes, roi) {
    const W = Math.min(960, img.width), k = W / img.width, kb = W / srcW;
    canvas.width = W; canvas.height = Math.round(img.height * k);
    const g = canvas.getContext('2d');
    g.drawImage(img, 0, 0, canvas.width, canvas.height);
    if (roi) {
      g.setLineDash([6, 6]); g.strokeStyle = '#f5c842'; g.lineWidth = 2;
      g.strokeRect(roi[0] * W, roi[1] * canvas.height, (roi[2] - roi[0]) * W, (roi[3] - roi[1]) * canvas.height);
      g.setLineDash([]);
    }
    g.strokeStyle = '#00e5a0'; g.lineWidth = 3;
    for (const b of boxes) g.strokeRect(b[0] * kb, b[1] * kb, (b[2] - b[0]) * kb, (b[3] - b[1]) * kb);
  }

  /* ── Tesseract (se descarga sólo la primera vez que se usa) ── */
  function loadScript(src) {
    return new Promise((res, rej) => {
      if (window.Tesseract) return res();
      const s = document.createElement('script'); s.src = src; s.onload = res;
      s.onerror = () => rej(new Error('No se pudo descargar el lector del navegador. Revisa la conexión a internet.'));
      document.head.appendChild(s);
    });
  }
  async function ocrWorker() {
    if (C.worker) return C.worker;
    C.workerLoading ??= (async () => {
      status('Descargando el lector del navegador (sólo la primera vez, unos 5 MB)…');
      await loadScript(TESSERACT_URL);
      const w = await window.Tesseract.createWorker('eng', 1, {
        logger: m => { if (m.status && m.status !== 'recognizing text') status(`Preparando lector: ${m.status} ${Math.round((m.progress || 0) * 100)} %`); },
      });
      // Las placas no usan I, O, Q ni Ñ: así el lector elige 1 y 0 en su lugar.
      await w.setParameters({ tessedit_char_whitelist:'ABCDEFGHJKLMNPRSTUVWXYZ0123456789-' });
      return w;
    })();
    try { C.worker = await C.workerLoading; }
    catch (e) { C.workerLoading = null; throw e; }
    return C.worker;
  }

  /* ── Candidatos y puntuación ── */
  function verifyPlate(key, type) {
    const VER = app.verifier();
    if (!VER) return null;
    if (type) return VER.verify(key, type);
    const all = TYPES.map(t => VER.verify(key, t));
    return all.find(r => r.autoApprove) || all.find(r => r.matches?.length) || all[0];
  }
  function scored(text, conf) {
    const S = app.state(), key = normalize(text);
    const acc = decideAccess(key, S.vehicles, S.students);
    const ver = verifyPlate(key, acc.vehicle?.type);
    let score = 0;
    if (acc.vehicle) score = acc.method === 'exacta' ? 100 : 90;
    else if (ver?.autoApprove) score = 60;
    else if (ver?.matches?.length) score = 35;          // serie oficial de otro estado o formato anterior
    score += 3 - Math.abs(key.length - 7);
    return { key, conf, score, acc, ver };
  }
  /* El texto de Tesseract trae ruido ("JALISCO", bordes). Se prueban todos los tramos de 5 a 8
     caracteres y se quedan los que existen en el padrón o encajan en una serie oficial. */
  function candidatesFromText(text, conf) {
    const seen = new Map();
    const add = (k, bonus = 0) => {
      if (seen.has(k)) return;
      const c = scored(k, conf); c.score += bonus; seen.set(k, c);
      if (c.ver?.suggestion && c.score < 35) add(normalize(c.ver.suggestion), -5);
    };
    for (const line of text.split('\n')) {
      const clean = normalize(line);
      for (let len = 8; len >= 5; len--) for (let i = 0; i + len <= clean.length; i++) {
        const sub = clean.slice(i, i + len);
        if (/[0-9]/.test(sub) && /[A-Z]/.test(sub)) add(sub);
      }
    }
    const good = [...seen.values()].filter(c => c.score >= 35).sort((a, b) => b.score - a.score);
    if (good.length) return good.slice(0, 4);
    const raw = [...seen.values()].filter(c => c.key.length >= 6).sort((a, b) => b.key.length - a.key.length);
    return raw.slice(0, 1);
  }

  /* ── Resultado ── */
  const best = () => C.cands[0] || null;
  function showCandidates(cands, rawText) {
    C.cands = cands.sort((a, b) => b.score - a.score || b.conf - a.conf);
    $('cam-cands').innerHTML = C.cands.length > 1
      ? '<div class="sub" style="margin-bottom:6px">Otras lecturas posibles:</div>' + C.cands.map((c, i) =>
          `<button class="cand${i ? '' : ' on'}" data-i="${i}"><span class="plate">${esc(c.ver?.display || c.key)}</span></button>`).join('')
      : '';
    $('cam-cands').querySelectorAll('[data-i]').forEach(b => b.onclick = () => {
      $('cam-cands').querySelectorAll('.cand').forEach(x => x.classList.toggle('on', x === b));
      report(C.cands[+b.dataset.i]);
    });
    if (C.cands.length) report(C.cands[0]);
    else $('cam-info').innerHTML = `<div class="sub">No se reconoció ninguna placa.${rawText ? ` Texto leído: «${esc(rawText.replace(/\s+/g, ' ').slice(0, 60))}».` : ''} Puedes escribirla abajo para consultarla.</div>`;
  }

  function report(c) {
    const { acc, ver } = c, v = acc.vehicle, s = acc.student;
    $('cam-manual').value = ver?.display || c.key;
    const act = $('cam-act').value;
    const verdict = acc.allowed ? { c:'ok', t:'Acceso autorizado' } : { c:'no', t:`Acceso denegado: ${acc.text}` };
    const ui = ver ? RESULT_UI[ver.result] : null;
    const row = (k, val) => val ? `<div class="kv"><span>${k}</span><b>${val}</b></div>` : '';
    $('cam-info').innerHTML = `
      <div class="cam-plate"><span class="plate">${esc(ver?.display || c.key)}</span></div>
      <div class="sub" style="text-align:center;margin-bottom:12px">${C.engineLabel === 'consulta manual' ? 'Consulta manual' : `Leída con ${esc(C.engineLabel)}`}${c.conf ? ` · confianza ${Math.round(c.conf * 100)} %` : ''}${acc.method === 'aproximada' ? ' · coincidencia aproximada' : ''}</div>
      <div class="verdict ${verdict.c} big" role="status">${esc(verdict.t)}
        ${acc.allowed ? `<div class="open-msg" id="cam-open-msg" data-k="">${act === 'none' && onlineGates().length ? 'Sólo consulta: la pluma no se abrió.' : ''}</div>` : ''}</div>
      ${acc.allowed && act !== 'none' ? `<button class="btn-open" id="cam-open-now" style="margin:-4px 0 12px">Abrir ${esc(gateName(act))} ahora</button>` : ''}
      ${ui ? `<div class="kv"><span>Serie</span><b><span class="bdg ${ui.c}">${ui.t}</span></b></div><div class="sub" style="margin:-2px 0 8px">${esc(ver.message)}${ver.suggestion ? ` ¿${esc(ver.suggestion)}?` : ''}</div>` : ''}
      ${v ? `
        ${row('Estudiante', esc(s?.name || v.studentName || '—'))}
        ${row('Matrícula', esc(v.studentId))}
        ${row('Carrera', esc(s?.career || ''))}
        ${row('Vigencia', esc(s?.validUntil || (s ? 'Sin límite' : '')))}
        ${row('Vehículo', esc([VEHICLE_TYPES[v.type], v.brand, v.model, v.color].filter(Boolean).join(' · ')))}
        ${row('Estado de la placa', `<span class="bdg ${v.active === false ? 'off' : VSTATUS[vStatus(v)].c}">${v.active === false ? 'Dado de baja' : VSTATUS[vStatus(v)].t}</span>`)}
        <div style="display:flex;gap:6px;margin-top:12px;flex-wrap:wrap">
          ${s ? `<button class="btn-ghost" id="cam-open-st">Ver ficha del estudiante</button>` : ''}
          <button class="btn-ghost" id="cam-open-ve">Ver en Vehículos</button>
        </div>`
      : acc.reason === 'ambigua'
        ? `<div class="sub">Coincide con: ${acc.candidates.map(x => esc(x.plate || x.id)).join(', ')}. Por seguridad la pluma no abre.</div>`
        : `<div class="sub">Esta placa no está registrada${ver?.foreign ? ' y es foránea' : ''}.</div>`}`;
    $('cam-open-now')?.addEventListener('click', () => openFor(c, true));
    $('cam-open-st')?.addEventListener('click', () => app.openStudent(s.id || v.studentId));
    $('cam-open-ve')?.addEventListener('click', () => app.showVehicle(v.plate || v.id));
  }

  function checkManual() {
    const k = normalize($('cam-manual').value);
    if (k.length < 5) return status('Escribe al menos 5 caracteres de la placa.', true);
    C.engineLabel = 'consulta manual';
    const c = scored(k, 0);
    C.cands = [c]; $('cam-cands').innerHTML = '';
    report(c);
  }

  /* ── Eventos ── */
  $('cam-eng').onchange = () => { C.engChosen = true; syncAct(); };
  $('cam-act').onchange = () => { C.actChosen = true; };
  $('cam-src').onchange = () => { if (C.mode !== 'off') start(); else syncControls(); };
  $('cam-dev').onchange = () => { if (C.mode === 'local') start(); };
  $('cam-on').onclick = start;
  $('cam-off').onclick = stop;
  $('cam-read').onclick = () => readOnce();
  $('cam-auto').onchange = e => {
    if (e.target.checked && C.mode === 'off') { e.target.checked = false; return status('Primero enciende una cámara.', true); }
    setAuto(e.target.checked);
  };
  $('cam-check').onclick = checkManual;
  $('cam-manual').onkeydown = e => { if (e.key === 'Enter') checkManual(); };
  window.addEventListener('pagehide', stop);

  return { refresh, stop };
}
