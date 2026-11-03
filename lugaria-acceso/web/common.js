/* Lugaria Acceso — utilidades compartidas por la caseta y el panel */
import { initializeApp }   from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth, onAuthStateChanged, signInWithEmailAndPassword, signOut }
                           from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { getFirestore, doc, getDoc }
                           from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import { firebaseConfig }  from "./firebase-config.js";

export const app  = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db   = getFirestore(app);

/* ── Placas: DEBEN ser idénticas a gate-service/plates.py ── */
const FOLD = { O:'0', Q:'0', D:'0', I:'1', L:'1', Z:'2', S:'5', B:'8', G:'6' };
export const normalize = t => (t || '').toUpperCase().replace(/Ñ/g, 'N').replace(/[^A-Z0-9]/g, '');
export const fold      = t => normalize(t).replace(/[OQDILZSBG]/g, c => FOLD[c]);

/* ── Textos ── */
export const DECISION = {
  permitido: { t: 'Permitido',  c: 'ok'   },
  denegado:  { t: 'Denegado',   c: 'no'   },
  en_espera: { t: 'En espera',  c: 'wait' },
  manual:    { t: 'Manual',     c: 'man'  },
  error:     { t: 'Error',      c: 'no'   },
};
export const MODES = {
  auto:        'Automático — abre a vehículos autorizados',
  supervisado: 'Supervisado — avisa a la caseta, el guardia decide',
  libre:       'Libre — abre a cualquier placa y la registra',
};

/* ── DOM ── */
export const $   = id => document.getElementById(id);
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
export const toDate = ts => !ts ? null : ts.toDate ? ts.toDate() : new Date(ts);
export const fmtHora = ts => { const d = toDate(ts); return d ? d.toLocaleTimeString('es-MX', { hour:'2-digit', minute:'2-digit', second:'2-digit' }) : '—'; };
export const fmtFecha = ts => { const d = toDate(ts); return d ? d.toLocaleDateString('es-MX', { day:'2-digit', month:'short' }) + ' ' + d.toLocaleTimeString('es-MX', { hour:'2-digit', minute:'2-digit' }) : '—'; };
export const startOfDay = (d = new Date()) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const gateOnline = g => { const t = toDate(g?.lastSeen); return !!t && Date.now() - t.getTime() < 90_000; };

export function snack(msg, err = false) {
  const e = $('snack');
  e.textContent = msg;
  e.className = 'on' + (err ? ' err' : '');
  clearTimeout(snack._t);
  snack._t = setTimeout(() => e.className = '', 3000);
}

export function busy(btn, on) {
  if (!btn) return;
  if (on) { btn.dataset.t = btn.textContent; btn.innerHTML = '<span class="ld"></span>'; btn.disabled = true; }
  else    { btn.textContent = btn.dataset.t || 'OK'; btn.disabled = false; }
}

/* ── Sesión con rol (colección staff/{uid}: { name, email, role }) ── */
export function guard({ roles, onIn, onOut }) {
  onAuthStateChanged(auth, async user => {
    if (!user) return onOut();
    try {
      const snap = await getDoc(doc(db, 'staff', user.uid));
      const staff = snap.exists() ? snap.data() : null;
      if (!staff || !roles.includes(staff.role)) {
        await signOut(auth);
        return onOut(staff ? 'Tu cuenta no tiene permiso para esta pantalla.'
                           : 'Esta cuenta no está dada de alta como personal. UID: ' + user.uid);
      }
      onIn(user, staff);
    } catch (e) {
      onOut('No se pudo verificar tu cuenta: ' + e.message);
    }
  });
}

export async function login(email, pass) {
  try {
    await signInWithEmailAndPassword(auth, email, pass);
    return null;
  } catch (e) {
    return ({
      'auth/invalid-credential': 'Correo o contraseña incorrectos',
      'auth/user-not-found':     'Correo no registrado',
      'auth/wrong-password':     'Contraseña incorrecta',
      'auth/too-many-requests':  'Demasiados intentos. Espera un momento.',
    })[e.code] || e.message;
  }
}
export const logout = () => signOut(auth);
