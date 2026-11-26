// Pruebas de firestore.rules contra el emulador de Firestore.
//   cd pruebas-reglas && npm install && npm test
// Requiere Java 11 o superior (el emulador corre en Java).

import { test, before, after, beforeEach } from 'node:test';
import { readFileSync } from 'node:fs';
import { initializeTestEnvironment, assertSucceeds, assertFails } from '@firebase/rules-unit-testing';
import { doc, getDoc, setDoc, deleteDoc, writeBatch, serverTimestamp, addDoc, collection } from 'firebase/firestore';

let env;
before(async () => {
  env = await initializeTestEnvironment({
    projectId: 'lugaria-pruebas',
    firestore: { rules: readFileSync(new URL('../firestore.rules', import.meta.url), 'utf8') },
  });
});
after(() => env.cleanup());
beforeEach(() => env.clearFirestore());

const EMAIL = 'ana@escuela.edu.mx';
const alumno = (uid = 'u1', email = EMAIL, verified = true) =>
  env.authenticatedContext(uid, { email, email_verified: verified }).firestore();
const admin = () => env.authenticatedContext('adm', { email: 'adm@escuela.edu.mx', email_verified: true }).firestore();

const seed = data => env.withSecurityRulesDisabled(async ctx => {
  const db = ctx.firestore();
  for (const [path, value] of Object.entries(data)) await setDoc(doc(db, path), value);
});

// Lo mismo que hace alumno.html al completar el registro de una matrícula nueva.
function registrar(db, { uid = 'u1', mat = 'A1', email = EMAIL, extra = {} } = {}) {
  const b = writeBatch(db), now = serverTimestamp();
  b.set(doc(db, 'accounts', uid), { matricula: mat, createdAt: now });
  b.set(doc(db, 'students', mat), {
    name: 'Ana López', career: 'Sistemas', phone: '', email, uid,
    status: 'aprobado', active: true, source: 'autoregistro', createdAt: now, updatedAt: now, ...extra,
  });
  return b.commit();
}
// Lo mismo que hace alumno.html al vincular una matrícula importada por la escuela.
function vincular(db, { uid = 'u1', mat = 'A2' } = {}) {
  const b = writeBatch(db), now = serverTimestamp();
  b.set(doc(db, 'accounts', uid), { matricula: mat, createdAt: now });
  b.update(doc(db, 'students', mat), { uid, updatedAt: now });
  return b.commit();
}
const vehiculo = (db, placa, tipo, status, studentId = 'A1') => setDoc(doc(db, 'vehicles', placa), {
  plate: placa, plateKey: placa, type: tipo, brand: '', model: '', color: '',
  studentId, studentName: 'Ana López', status, active: true, ownerUid: 'u1', source: 'alumno', createdAt: serverTimestamp(),
});
const conCuenta = () => seed({
  'accounts/u1': { matricula: 'A1' },
  'students/A1': { name: 'Ana López', email: EMAIL, uid: 'u1', status: 'aprobado', active: true },
});

// ── Registro de alumnos ─────────────────────────────────────
test('registro abierto sin configuración: el alumno crea su cuenta', async () => {
  await assertSucceeds(getDoc(doc(alumno(), 'students', 'A1')));   // consultar matrícula libre
  await assertSucceeds(registrar(alumno()));
});

test('sin correo confirmado no puede registrarse', async () => {
  await assertFails(registrar(alumno('u1', EMAIL, false)));
});

test('registro cerrado por el admin', async () => {
  await seed({ 'settings/registration': { open: false, domain: '' } });
  await assertFails(registrar(alumno()));
});

test('varios dominios institucionales (alumnos, profesores, administrativos)', async () => {
  await seed({ 'settings/registration': { open: true, domains: ['escuela.edu.mx', 'academicos.edu.mx'] } });
  await assertFails(registrar(alumno('u2', 'ana@gmail.com'), { uid: 'u2', email: 'ana@gmail.com' }));
  await assertFails(registrar(alumno('u3', 'x@sub.escuela.edu.mx'), { uid: 'u3', mat: 'A3', email: 'x@sub.escuela.edu.mx' }));
  await assertSucceeds(registrar(alumno()));
  await assertSucceeds(registrar(alumno('u4', 'prof@academicos.edu.mx'),
    { uid: 'u4', mat: 'P1', email: 'prof@academicos.edu.mx', extra: { kind: 'profesor' } }));
});

test('formato anterior de un solo dominio sigue funcionando', async () => {
  await seed({ 'settings/registration': { open: true, domain: 'escuela.edu.mx' } });
  await assertFails(registrar(alumno('u2', 'ana@gmail.com'), { uid: 'u2', email: 'ana@gmail.com' }));
  await assertSucceeds(registrar(alumno()));
});

test('perfil válido: alumno, profesor o administrativo', async () => {
  await assertFails(registrar(alumno(), { extra: { kind: 'director' } }));
  await assertSucceeds(registrar(alumno(), { extra: { kind: 'administrativo' } }));
});

test('el alumno no puede darse vigencia ni crear la ficha a nombre de otro', async () => {
  await assertFails(registrar(alumno(), { extra: { validUntil: '2099-12-31' } }));
  await assertFails(registrar(alumno(), { email: 'otro@escuela.edu.mx' }));
});

test('vincula una matrícula importada con el mismo correo', async () => {
  await seed({ 'students/A2': { name: 'Ana López', email: EMAIL, active: true, validUntil: '2027-07-31' } });
  await assertSucceeds(getDoc(doc(alumno(), 'students', 'A2')));
  await assertSucceeds(vincular(alumno()));
});

test('no puede tomar una matrícula importada con otro correo', async () => {
  await seed({ 'students/A3': { name: 'Otra persona', email: 'otra@escuela.edu.mx', active: true } });
  await assertFails(getDoc(doc(alumno(), 'students', 'A3')));
  await assertFails(vincular(alumno(), { mat: 'A3' }));
});

test('no puede tomar una matrícula ya vinculada a otra cuenta', async () => {
  await seed({ 'students/A4': { name: 'Ana López', email: EMAIL, uid: 'otra-cuenta', active: true } });
  await assertFails(vincular(alumno(), { mat: 'A4' }));
});

test('no puede leer la ficha de otro alumno', async () => {
  await conCuenta();
  await seed({ 'students/B9': { name: 'Otro', email: 'otro@escuela.edu.mx', uid: 'u9' } });
  await assertSucceeds(getDoc(doc(alumno(), 'students', 'A1')));
  await assertFails(getDoc(doc(alumno(), 'students', 'B9')));
});

// ── Placas registradas por el alumno ───────────────────────
test('placa de Jalisco del tipo correcto se aprueba sola', async () => {
  await conCuenta();
  await assertSucceeds(vehiculo(alumno(), 'JKL123A', 'auto', 'aprobado'));
  await assertSucceeds(vehiculo(alumno(), 'HU0001A', 'pickup', 'aprobado'));
  await assertSucceeds(vehiculo(alumno(), 'M27KP', 'motocicleta', 'aprobado'));
});

test('placa de otro estado o de otro tipo no puede marcarse aprobada', async () => {
  await conCuenta();
  await assertFails(vehiculo(alumno(), 'PGA123B', 'auto', 'aprobado'));     // Michoacán
  await assertFails(vehiculo(alumno(), 'HU0002A', 'auto', 'aprobado'));     // camión registrado como auto
  await assertSucceeds(vehiculo(alumno(), 'PGA123B', 'auto', 'pendiente'));
});

test('no puede registrar una placa que ya existe ni a nombre de otro', async () => {
  await conCuenta();
  await seed({ 'vehicles/JKL999A': { plate: 'JKL999A', studentId: 'B9', status: 'aprobado', active: true } });
  await assertFails(vehiculo(alumno(), 'JKL999A', 'auto', 'aprobado'));
  await assertFails(vehiculo(alumno(), 'JKL555A', 'auto', 'aprobado', 'B9'));
});

// ── Órdenes de cámara y apertura ───────────────────────────
const staffYPluma = () => seed({ 'staff/adm': { role: 'admin', name: 'Admin' }, 'gates/p1': { name: 'Entrada' } });
const orden = (db, data) => addDoc(collection(db, 'commands'), {
  gateId: 'p1', status: 'pending', requestedBy: 'adm', requestedByName: 'Admin', createdAt: serverTimestamp(), ...data,
});

test('el personal pide lectura de imagen y foto de la cámara', async () => {
  await staffYPluma();
  await assertSucceeds(orden(admin(), { action: 'read', image: 'data:image/jpeg;base64,AAAA' }));
  await assertSucceeds(orden(admin(), { action: 'snapshot' }));
  await assertFails(orden(admin(), { action: 'read', image: 'x'.repeat(900001) }));
  await assertFails(orden(admin(), { action: 'snapshot', note: 'campo no permitido' }));
});

test('apertura por placa reconocida con la cámara del panel', async () => {
  await staffYPluma();
  await assertSucceeds(orden(admin(), { action: 'open', via: 'camara-panel', plate: 'JKL123A', note: 'Placa reconocida', studentId: 'A1', studentName: 'Ana' }));
  await assertFails(orden(admin(), { action: 'open', via: 'otra-cosa', plate: 'JKL123A' }));
});

test('un alumno no puede mandar órdenes a la pluma', async () => {
  await staffYPluma();
  await conCuenta();
  await assertFails(orden(alumno(), { action: 'open', requestedBy: 'u1', note: '' }));
  await assertFails(orden(alumno(), { action: 'snapshot', requestedBy: 'u1' }));
});

test('las lecturas se pueden borrar, las aperturas no', async () => {
  await staffYPluma();
  await seed({
    'commands/r1': { action: 'read', requestedBy: 'adm', gateId: 'p1', status: 'done' },
    'commands/o1': { action: 'open', requestedBy: 'adm', gateId: 'p1', status: 'done' },
  });
  await assertSucceeds(deleteDoc(doc(admin(), 'commands', 'r1')));
  await assertFails(deleteDoc(doc(admin(), 'commands', 'o1')));
});
