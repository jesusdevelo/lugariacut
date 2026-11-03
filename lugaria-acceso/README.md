# Lugaria Acceso — pluma con lectura de placas

Control de acceso vehicular para un estacionamiento privado (escuela/universidad).
Una cámara lee la placa, el sistema la busca en el padrón de estudiantes y, si el
estudiante tiene acceso vigente, levanta la pluma. Todo queda en una bitácora.

Esta versión reemplaza el flujo de mapa, reservas y cobro de Lugaria. Se conserva
Firebase (Auth + Firestore) y la identidad visual.

```
 ┌───────────┐  RTSP/USB  ┌────────────────────────────┐   relevador   ┌────────┐
 │  Cámara   │ ─────────▶ │  gate-service (Python)     │ ────────────▶ │ Pluma  │
 └───────────┘            │  · lee placas (fast-alpr)  │  contacto seco└────────┘
                          │  · padrón en memoria/disco │
                          │  · decide y registra       │
                          └─────────────┬──────────────┘
                                        │ Firestore (tiempo real)
                     ┌──────────────────┴──────────────────┐
              ┌──────┴──────┐                        ┌──────┴──────┐
              │ index.html  │  caseta del guardia    │ admin.html  │  administración
              │ en vivo +   │                        │ padrón, CSV,│
              │ abrir manual│                        │ bitácora…   │
              └─────────────┘                        └─────────────┘
```

## Qué cambió respecto a la versión anterior

| Antes (Lugaria) | Ahora (Lugaria Acceso) |
|---|---|
| Mapa de estacionamientos, reservas, precios | Eliminados |
| Usuarios se registraban solos | El administrador da de alta estudiantes (uno a uno o por CSV) |
| `users/{uid}/vehicles` | `vehicles/{placa}`: la placa es el ID, no se puede repetir |
| `ADMIN_UIDS` en el código | Colección `staff` con roles *admin* y *guardia* |
| `index.html` = app pública | `index.html` = pantalla de la caseta |
| — | `gate-service/`: servicio que lee placas y abre la pluma |
| — | Bitácora de accesos, aperturas manuales, contador de ocupación |

`seed.html` ya no se usa (cargaba estacionamientos de ejemplo).

## Archivos

```
lugaria-acceso/
├── firestore.rules            reglas de seguridad
├── firestore.indexes.json     exención de índice para las miniaturas (obligatoria)
├── web/
│   ├── firebase-config.js     ← pega aquí tu configuración (un solo lugar)
│   ├── common.js, styles.css  compartidos
│   ├── index.html             caseta (guardias y admins)
│   ├── admin.html             administración (sólo admins)
│   └── plantilla-estudiantes.csv
└── gate-service/              corre en la computadora junto a la pluma
    ├── main.py                servicio principal
    ├── config.example.yaml    copia como config.yaml
    ├── plates.py, directory.py, recognizer.py, camera.py, gate.py, backend.py
    ├── test_acceso.py         pruebas sin cámara ni Firebase
    ├── estudiantes-prueba.csv
    └── lugaria-pluma.service  arranque automático (Linux)
```

---

## 1. Firebase

Puedes reutilizar tu proyecto actual o crear uno nuevo.

1. **Authentication** → Sign-in method → habilita *Correo/Contraseña*.
2. **Firestore** → pestaña *Reglas* → pega `firestore.rules` → Publicar.
3. **Exención de índice (obligatoria)**: Firestore → *Índices* → *Exenciones de campo único*
   → Agregar exención: colección `accessLogs`, campo `thumb`, desactiva todos los índices.
   (O con la CLI: `firebase deploy --only firestore:indexes`.)
   Sin esto, la bitácora se guarda pero sin la miniatura de la placa.
4. Copia tu `firebaseConfig` a `web/firebase-config.js`.
5. **Cuenta de servicio para la pluma**: ⚙ Configuración del proyecto → *Cuentas de servicio*
   → *Generar nueva clave privada*. Guarda el archivo como
   `gate-service/credenciales-firebase.json`. Trátalo como una contraseña: quien lo
   tenga controla toda la base de datos. No lo subas a GitHub (ya está en `.gitignore`).

### Primer administrador

Las reglas no permiten que nadie se haga admin solo, así que el primero se crea a mano:

1. Authentication → Users → *Agregar usuario* (tu correo y contraseña). Copia el **UID**.
2. Firestore → *Iniciar colección* `staff` → ID del documento = ese UID → campos:
   `name` (string), `email` (string), `role` (string) = `admin`.
3. Abre `admin.html` e inicia sesión. Los demás guardias y admins se crean desde *Personal*.

## 2. Panel web

Igual que antes, necesita servirse por HTTP (no abrir el archivo con doble clic):

```bash
cd web
python3 -m http.server 3000      # http://localhost:3000/admin.html
# o publícalo:  firebase init hosting  (public = web)  →  firebase deploy
```

En **admin.html**:
- *Estudiantes*: alta, edición, vigencia y vehículos vinculados en la misma ficha.
  Suspender a un estudiante corta el acceso de todas sus placas al instante.
- *Importar CSV*: carga masiva desde el sistema escolar. Usa `plantilla-estudiantes.csv`
  (una fila por vehículo). Opción para desactivar a quienes ya no aparecen (cambio de ciclo).
- *Bitácora*: por fecha, resultado, pluma o placa; exporta a CSV.
- *Plumas*: nombre, sentido y modo de cada pluma; capacidad y corrección del contador.
- *Personal*: crea cuentas de guardia o administrador.

En **index.html** (caseta): el último auto en grande con su placa, nombre, matrícula y
resultado; botón para abrir manualmente con motivo (queda registrado quién abrió);
consulta rápida de placas; alerta sonora opcional cuando se niega un acceso.

## 3. Servicio de la pluma

### Equipo recomendado

- **Computadora**: mini PC (Intel N100 o similar, ~4–8 lecturas/s con el modelo local) o
  Raspberry Pi 5 de 8 GB. Una por pluma, o una para entrada y salida si están juntas.
- **Cámara**: IP con RTSP y lente varifocal (2.8–12 mm), a 3–6 m del punto donde se
  detiene el auto, con ángulo menor a 30° respecto a la placa e infrarrojo para la noche.
  Si puedes, una cámara LPR dedicada (obturador rápido, contraluz): es lo que más mejora
  la precisión. Usa el sub-stream de 720p o 1080p; 4K sólo añade carga.
- **Relevador**: módulo de 1 canal (GPIO de la Raspberry), relevador WiFi tipo Shelly, o
  Arduino por USB. Se conecta a la entrada de *apertura por contacto seco* de la tarjeta de
  la pluma (la misma que usa el botón o el control remoto). **Pide al instalador de la
  pluma que haga esa conexión**: no se toca la línea de 127 V.

### Instalación

```bash
cd gate-service
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml                       # y edítalo
```

La primera vez, fast-alpr descarga sus modelos (unos MB); después funciona sin internet.

### Probar en tu computadora antes de instalar (sin Firebase, sin pluma)

```bash
python test_acceso.py                                          # lógica de placas y decisiones
python main.py --sin-firebase estudiantes-prueba.csv --imagen foto_de_un_auto.jpg
python main.py --sin-firebase estudiantes-prueba.csv --fuente 0 --mostrar   # webcam
```

Con `driver: simulado` la "apertura" sólo se imprime en la consola. Para ver la ventana
de video instala `opencv-python` en lugar de `opencv-python-headless`.

### Producción

1. En `config.yaml`: `camara.fuente` con la URL RTSP, `pluma.id` único, `pluma.sentido`
   y el `driver` real (`gpio`, `http` o `serial`).
2. `python main.py --mostrar` y ajusta la `zona_interes` para que el recuadro cubra sólo el
   carril.
3. Arranque automático en Linux: edita y copia `lugaria-pluma.service` (instrucciones
   dentro del archivo). En Windows puedes usar el Programador de tareas o NSSM.

La pluma aparece sola en *admin → Plumas* y en la caseta en cuanto el servicio arranca.

### Cómo decide

1. Lee placas en la zona de interés unas 4 veces por segundo.
2. Descarta lecturas con poca confianza o que no parecen placa.
3. **Confirma** la placa cuando la misma lectura aparece 3 veces en 3 segundos.
4. Busca la placa: primero exacta y luego tolerando confusiones típicas del OCR
   (O/0, I/1, B/8, S/5, Z/2, G/6). Si la lectura coincide con dos placas registradas,
   no abre y marca *lectura ambigua*.
5. Revisa que el vehículo esté activo, que el estudiante esté activo y que la vigencia no
   haya vencido.
6. Según el **modo** de la pluma:
   - *Automático*: abre si todo está en regla.
   - *Supervisado*: nunca abre sola; la caseta ve el resultado y el guardia decide.
   - *Libre*: abre a cualquier placa y la registra (útil en eventos o para salidas).
7. Registra el evento con miniatura de la placa y no vuelve a procesar esa placa durante
   30 s.

### Si se cae el internet

El servicio tiene una copia completa del padrón en `datos/cache.json`, así que **sigue
abriendo a los estudiantes registrados**. La bitácora se acumula y se sube cuando vuelve
la conexión (además queda una copia en `datos/bitacora-local.jsonl`). Lo que no funciona
sin internet son las aperturas manuales desde la caseta remota: deja siempre el botón
físico o el control de la pluma a la mano del guardia.

### Ajustes

| Síntoma | Qué mover en `config.yaml` |
|---|---|
| Tarda en abrir | `votos_requeridos: 2`, `cuadros_por_segundo: 6` |
| Lee placas de autos estacionados al fondo | `zona_interes` |
| Rechaza placas bien registradas | revisa la bitácora: si la placa leída sale casi igual, mejora el ángulo/iluminación o baja `confianza_minima` a 0.7 |
| Muchos "no registrado" de noche | iluminación IR, obturador más rápido en la cámara |
| Quieres más precisión sin cambiar cámara | `motor: platerecognizer` (servicio en la nube de pago con región `mx`) |

`tolerar_un_error: true` acepta placas con un carácter distinto. Abre más fácil, pero
también podría abrirle a una placa parecida que no está registrada: úsalo sólo si la
cámara no puede mejorar.

## Datos en Firestore

```
students/{matricula}     name, career, email, phone, validUntil "AAAA-MM-DD", active
vehicles/{PLACA}         plate, plateKey, studentId, studentName, brand, model, color, active
gates/{id}               name, direction entrada|salida, mode auto|supervisado|libre,
                         lastSeen, cameraOk, lastError, cachedVehicles   (lo mantiene el servicio)
accessLogs/{auto}        ts, gateId, gateName, plate, conf, decision permitido|denegado|en_espera|manual,
                         reason, reasonText, method, studentId, studentName, vehicleDesc,
                         thumb, openedBy
commands/{auto}          gateId, action "open", status pending|done|expired|error, note,
                         requestedBy, requestedByName, createdAt
stats/occupancy          inside, capacity
staff/{uid}              name, email, role admin|guardia
```

La matrícula y la placa son los ID de documento, así que importar el mismo CSV dos veces
actualiza en lugar de duplicar.

**¿El padrón vive en otra base de datos (SQL del sistema escolar)?** Lo más sencillo es
exportarlo como CSV cada inicio de ciclo e importarlo. Si quieres conexión directa,
la única pieza que cambia es `backend.py`: carga `students` y `vehicles` desde tu base en
lugar de Firestore y llama a `self.dir.set_students()` / `set_vehicles()`.

## Privacidad

Vas a tratar datos personales (nombre, matrícula, placa, horarios de entrada) y fotos.
En México esto requiere un **aviso de privacidad** para los estudiantes que explique qué se
captura, para qué y cuánto tiempo se guarda. El servicio borra las fotos locales a los 15
días (`dias_capturas`); define también cuánto conservas la bitácora. Revísalo con el área
jurídica de la institución antes de arrancar.

## Costos

Firebase plan gratuito: 50 000 lecturas y 20 000 escrituras al día. Cada paso de auto es
una escritura de bitácora y un incremento de ocupación; un estacionamiento con 1 000
movimientos diarios usa ~10 % del límite. Cada reinicio del servicio lee el padrón completo
una vez (p. ej. 3 000 estudiantes + 3 000 autos = 6 000 lecturas).

## Ideas para después

- Horarios por estudiante o por pluma (p. ej. sin acceso en fin de semana).
- Antipassback: no permitir dos entradas seguidas sin una salida.
- Pases temporales de visitante con placa y fecha de expiración.
- Reporte de ocupación por hora.
