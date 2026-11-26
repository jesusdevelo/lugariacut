/* Verificador de placas contra las series oficiales (NOM-001-SCT-2-2016).
   Traducción 1:1 de gate-service/placas_mx.py; ambos leen series-mx.json.
   Si cambias la lógica aquí, cámbiala también allá (test_placas.py lo comprueba). */

const TO_DIGIT  = { O:'0', Q:'0', D:'0', I:'1', L:'1', Z:'2', S:'5', B:'8', G:'6', T:'7' };
const TO_LETTER = { '0':'D', '1':'L', '2':'Z', '5':'S', '8':'B', '6':'G', '7':'T', O:'D', Q:'D', I:'L' };
export const APPROVE = 'jalisco_valida';
export const RESULT_UI = {
  jalisco_valida:   { c:'ok',   t:'Serie de Jalisco' },
  tipo_distinto:    { c:'wait', t:'Tipo distinto' },
  formato_anterior: { c:'wait', t:'Formato anterior' },
  foranea:          { c:'wait', t:'Foránea' },
  invalida:         { c:'no',   t:'Inválida' },
};
const MESSAGES = {
  jalisco_valida:   'Serie oficial de Jalisco para {kind}.',
  tipo_distinto:    'Es una placa de Jalisco para {kind}, pero registraste {declared}.',
  formato_anterior: 'Placa de Jalisco con numeración anterior a 2016. Debe revisarla administración.',
  foranea_estado:   'Placa foránea de {state} ({kind}).',
  foranea:          'Placa foránea: no corresponde a ninguna serie de Jalisco.',
  invalida:         'No tiene forma de placa: debe tener entre 5 y 8 letras y números.',
};
const TYPE_LABEL = { auto:'auto', camioneta:'camioneta / SUV', pickup:'pickup', motocicleta:'motocicleta' };

const normalize = t => (t || '').toUpperCase().replace(/Ñ/g, 'N').replace(/[^A-Z0-9]/g, '');
const cmp = (a, b) => { for (let i = 0; i < Math.max(a.length, b.length); i++) { const d = (a[i] ?? -1) - (b[i] ?? -1); if (d) return d; } return 0; };

export function createVerifier(data) {
  const rank = Object.fromEntries([...data.alphabet].map((c, i) => [c, i]));
  const key = (p, tpl) => [
    ...[...p].filter((_, i) => tpl[i] === 'L').map(c => rank[c]),
    ...[...p].filter((_, i) => tpl[i] === 'D').map(Number),
  ];
  const fits = (p, tpl) => {
    if (p.length !== tpl.length) return false;
    for (let i = 0; i < p.length; i++) {
      if (tpl[i] === 'L' && !(p[i] in rank)) return false;
      if (tpl[i] === 'D' && !/[0-9]/.test(p[i])) return false;
    }
    for (const m of tpl.matchAll(/D+/g)) if (/^0+$/.test(p.slice(m.index, m.index + m[0].length))) return false;
    return true;
  };
  const display = (p, fmt) => { let i = 0; return [...fmt].map(ch => ch === '-' ? '-' : p[i++]).join(''); };
  const series = data.series.map(s => { const tpl = s.format.replace(/-/g, ''); return { ...s, tpl, lo:key(s.from, tpl), hi:key(s.to, tpl) }; });
  const inRange = (p, s) => fits(p, s.tpl) && cmp(s.lo, key(p, s.tpl)) <= 0 && cmp(key(p, s.tpl), s.hi) <= 0;

  function find(plate) {
    const p = normalize(plate);
    return series.filter(s => inRange(p, s))
      .map(s => ({ state:s.state, stateName:data.states[s.state], kind:s.kind, era:s.era, display:display(p, s.format) }));
  }

  function suggest(plate, type) {
    const p = normalize(plate), ok = data.typeKinds[type] || [];
    for (const s of series) {
      if (s.state !== data.home || s.era !== 'actual' || !ok.includes(s.kind) || p.length !== s.tpl.length) continue;
      const fixed = [...p].map((c, i) => {
        const t = s.tpl[i];
        if ((t === 'D' && /[0-9]/.test(c)) || (t === 'L' && c in rank)) return c;
        return (t === 'D' ? TO_DIGIT[c] : TO_LETTER[c]) ?? c;
      }).join('');
      if (fixed !== p && inRange(fixed, s)) return display(fixed, s.format);
    }
    return '';
  }

  function verify(plate, type = 'auto') {
    const p = normalize(plate), ok = data.typeKinds[type] || [];
    if (p.length < 5 || p.length > 8 || !/[0-9]/.test(p) || !/[A-Z]/.test(p))
      return { plate:p, result:'invalida', message:MESSAGES.invalida, display:'', state:'', stateName:'', kind:'', suggestion:'', matches:[], autoApprove:false };
    const ms = find(p), home = ms.filter(m => m.state === data.home);
    const best = home.find(m => m.era === 'actual' && ok.includes(m.kind)) || home.find(m => m.era === 'actual') || home[0] || ms[0];
    let result;
    if (!best) result = 'foranea';
    else if (best.state !== data.home) result = 'foranea';
    else if (best.era === 'anterior') result = 'formato_anterior';
    else if (!ok.includes(best.kind)) result = 'tipo_distinto';
    else result = APPROVE;
    let message = MESSAGES[best && result === 'foranea' ? 'foranea_estado' : result]
      .replace('{kind}', data.kinds[best?.kind] || best?.kind || '')
      .replace('{state}', best?.stateName || '')
      .replace('{declared}', TYPE_LABEL[type] || type);
    if (!best && /[IOQ]/.test(p)) message += ' Si es de Jalisco, revísala: las placas no usan las letras I, O ni Q.';
    return {
      plate:p, result, message, display:best?.display || '', state:best?.state || '', stateName:best?.stateName || '',
      kind:best?.kind || '', suggestion: !ms.length && result === 'foranea' ? suggest(p, type) : '', matches:ms, autoApprove: result === APPROVE, foreign: result === 'foranea',
    };
  }

  return { verify, find, suggest, kinds:data.kinds, home:data.home };
}

/* En el navegador: carga series-mx.json junto a este archivo. */
let _v = null;
export async function loadVerifier(url = new URL('./series-mx.json', import.meta.url)) {
  if (!_v) _v = createVerifier(await (await fetch(url)).json());
  return _v;
}
