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
| — | Portal del alumno (`alumno.html`): crea su cuenta y registra placa y tipo de vehículo |
| — | Verificador de placas contra las series oficiales de Jalisco (NOM-001-SCT-2-2016) |
| `ADMIN_UIDS` en el código | Colección `staff` con roles *admin* y *guardia* |
| `index.html` = app pública | `index.html` = pantalla de la caseta |
| — | `gate-service/`: servicio que lee placas y abre la pluma |
| — | Bitácora de accesos, aperturas manuales, contador de ocupación |

`seed.html` ya no se usa (cargaba estacionamientos de ejemplo).

## Archivos

```
lugaria-acceso/
├── firebase.json              publicación con la CLI (reglas, índices, hosting) y emulador
├── firestore.rules            reglas de seguridad
├── firestore.indexes.json     exenciones de índice para imágenes (obligatorias)
├── pruebas-reglas/            pruebas de las reglas con el emulador (npm test)
├── web/
│   ├── firebase-config.js     ← pega aquí tu configuración (un solo lugar)
│   ├── common.js, styles.css  compartidos
│   ├── index.html             caseta (guardias y admins)
│   ├── admin.html             administración (sólo admins)
│   ├── alumno.html            cuenta del alumno: registra sus placas (provisional)
│   ├── placas-mx.js           verificador de placas (navegador)
│   ├── camara.js              cámara y lectura de placas en admin → Plumas
│   ├── series-mx.json         series oficiales por estado y tipo (generado)
│   └── plantilla-estudiantes.csv
└── gate-service/              corre en la computadora junto a la pluma
    ├── main.py                servicio principal
    ├── config.example.yaml    copia como config.yaml
    ├── plates.py, directory.py, recognizer.py, camera.py, gate.py, backend.py
    ├── placas_mx.py           verificador de placas (Python, mismo resultado que el de la web)
    ├── series-mx.json         copia de las series oficiales
    ├── generar_series.py      regenera ambos series-mx.json
    ├── verificar_placas.py    audita las placas de un CSV antes de importarlo
    ├── test_acceso.py         pruebas sin cámara ni Firebase
    ├── test_placas.py         pruebas del verificador (Python = web = reglas de Firestore)
    ├── estudiantes-prueba.csv
    └── lugaria-pluma.service  arranque automático (Linux)
```

---

## 1. Firebase

Puedes reutilizar tu proyecto actual o crear uno nuevo.

1. **Authentication** → Sign-in method → habilita *Correo/Contraseña*.
2. **Reglas e índices** (con la CLI de Firebase, desde la carpeta del proyecto):
   ```bash
   npm install -g firebase-tools && firebase login
   firebase use --add                      # elige tu proyecto
   firebase deploy --only firestore        # publica firestore.rules y firestore.indexes.json
   ```
   Sin la CLI: Firestore → *Reglas* → pega `firestore.rules` → Publicar; y en *Índices* →
   *Exenciones de campo único* agrega `accessLogs.thumb`, `commands.image` y
   `commands.resultImage` con todos los índices desactivados.
   **Cada vez que actualices el proyecto vuelve a publicar las reglas**: la web y las reglas
   cambian juntas, y unas reglas viejas rechazan el registro de alumnos.
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
# o publícalo:  firebase deploy --only hosting     (firebase.json ya apunta a web/)
```

La cámara del panel sólo funciona en `https://` (Firebase Hosting) o en `http://localhost`.

En **admin.html**:
- *Estudiantes*: alumnos, profesores y administrativos (campo *Perfil*); alta, edición,
  vigencia y vehículos vinculados en la misma ficha.
  Suspender a un estudiante corta el acceso de todas sus placas al instante.
- *Importar CSV*: carga masiva desde el sistema escolar. Usa `plantilla-estudiantes.csv`
  (una fila por vehículo, con columnas `perfil` —alumno, profesor o administrativo— y `tipo`).
  Incluye el correo de cada persona: es lo que le permite vincular su cuenta desde `alumno.html`. Opción para desactivar a quienes ya no aparecen (cambio de ciclo).
- *Bitácora*: por fecha, resultado, pluma o placa; exporta a CSV.
- *Plumas*: nombre, sentido y modo de cada pluma; capacidad y corrección del contador; y
  **Cámara y lectura de placas** (ver abajo).
- *Personal*: crea cuentas de guardia o administrador.

En **index.html** (caseta): el último auto en grande con su placa, nombre, matrícula y
resultado; botón para abrir manualmente con motivo (queda registrado quién abrió);
consulta rápida de placas; alerta sonora opcional cuando se niega un acceso.

### Cámara y lectura de placas (admin → Plumas)

Lee una placa con una cámara y, **si coincide con una registrada y con acceso, abre la pluma y
muestra "Acceso autorizado"**. Sirve también para consultar placas sin abrir.

| Cámara | Cómo funciona |
|---|---|
| Cámara de este equipo | La webcam o cámara USB de la computadora (o la trasera del celular). El navegador pide permiso la primera vez. |
| Cámara de la pluma | Pide al servicio una foto del cuadro actual (orden `snapshot`) y la muestra con las placas marcadas y la zona de interés. |

| Lector | Cuándo usarlo |
|---|---|
| Servicio de una pluma conectada | Por defecto. La imagen viaja por Firestore (orden `read`) y la lee el mismo modelo que abre la pluma. Más preciso. |
| Este navegador (Tesseract.js) | Sin servidor. Lee el texto del recuadro guía; menos preciso, útil para probar antes de instalar la pluma. La primera vez descarga unos 5 MB. |

El resultado muestra la placa, si es de Jalisco o **foránea**, **Acceso autorizado** o
**Acceso denegado** con el motivo (la misma regla que usa el servicio), y los datos de la persona
y del vehículo. *Lectura automática* repite la lectura cada 1–3 segundos durante 90 segundos
como máximo.

**Al reconocer una placa autorizada** elige qué pluma se abre (por defecto, la de la cámara o
el lector elegidos) o *Sólo consultar*. La apertura:
- se manda como orden `open` con `via: "camara-panel"`; el servicio **vuelve a validar la
  placa** contra su padrón y la registra en la bitácora como *Permitido · cámara del panel*
  (si ya no estuviera autorizada, la registra como apertura manual con el motivo);
- no se repite para la misma placa durante 20 segundos;
- sólo ocurre con lecturas de cámara: al escribir una placa a mano aparece el botón
  *Abrir … ahora*, que hay que presionar.

**Sin pluma conectada** (por ejemplo, sin el equipo del servicio): sólo está el lector del
navegador, no aparece la elección de pluma y el resultado muestra únicamente *Acceso autorizado*
o *Acceso denegado*. No se abre nada ni se escribe en la bitácora (sólo el servicio la escribe).

Las imágenes de las órdenes `read` y `snapshot` se borran de Firestore en cuanto llega la
respuesta; las aperturas se conservan. Requiere el servicio de la pluma de esta versión.

## 3. Portal del alumno (provisional)

`alumno.html` permite que cada estudiante cree su cuenta y registre sus vehículos
mientras no exista una conexión directa con el sistema escolar.

**Flujo del alumno**
1. *Crear cuenta*: nombre, correo y contraseña. Recibe un correo de confirmación.
2. Confirma su correo, elige su perfil (alumno, profesor o administrativo) y escribe su
   matrícula o código, carrera o dependencia y teléfono. **La cuenta queda activa de
   inmediato**, sin aprobación de administración.
   - Si la escuela ya había importado esa matrícula con el mismo correo, la cuenta se vincula
     a ese registro (conserva su vigencia).
   - Si la matrícula no existe, se crea un registro nuevo, activo y sin fecha de vigencia.
   - Si la matrícula existe con otro correo, se le pide acudir a administración. Así nadie
     puede apropiarse de la matrícula de otra persona.
3. En su perfil agrega hasta 3 vehículos: placa, tipo (auto, camioneta, pickup, motocicleta),
   marca, modelo y color. Mientras escribe, el verificador le dice si la placa corresponde a
   una serie de Jalisco y le sugiere correcciones (por ejemplo O por 0).
   - Serie oficial de Jalisco compatible con el tipo → **queda activa al instante**.
   - Placa que no corresponde a las series de Jalisco → se marca como **placa foránea** y queda
     **en revisión** hasta que administración la apruebe. Si parece un error de captura de una
     placa de Jalisco, se le sugiere la corrección.
   - Placa de Jalisco de otro tipo de vehículo o con numeración anterior a 2016 → en revisión.
4. Ve el estado de cada placa (activa, en revisión o rechazada con motivo) y puede quitarla.

**Lo que hace administración** (`admin.html` → *Revisión de placas*)
- Cerrar o abrir el registro y, opcionalmente, limitarlo a **varios dominios de correo**
  separados por coma, por ejemplo `alumnos.udg.mx, academicos.udg.mx`. Cada dominio debe
  coincidir exacto (`udg.mx` no acepta `alumnos.udg.mx`). Vacío = cualquier correo. **El registro
  está abierto desde el inicio**; sólo se cierra si lo desactivas.
- Aprobar o rechazar las placas en revisión, viendo el resultado del verificador de cada una.
- *Verificar todas las placas*: audita todo el padrón (incluidas las importadas por CSV).
- Para limitar el acceso de un alumno: editar su ficha (vigencia o *Suspendido*).

**Lo que garantiza el sistema**
- La pluma no abre con placas en revisión o rechazadas.
- La aprobación automática la valida también Firestore (`jaliscoValida` en las reglas): un
  alumno no puede marcar como aprobada una placa que no sea de Jalisco aunque manipule la página.
- Una placa sólo puede estar registrada una vez.
- El alumno sólo ve su propia ficha y sus vehículos.

Para dejar de usarlo basta con cerrar el registro; las cuentas existentes siguen funcionando.

### Si un alumno no puede registrarse

El portal ahora dice la causa en lugar de un mensaje genérico:

| Mensaje | Causa | Solución |
|---|---|---|
| "El registro con correo no está habilitado" | Falta activar el proveedor | Authentication → Sign-in method → Correo/Contraseña |
| "Esta página no está autorizada en Firebase" | El dominio de la página no está autorizado | Authentication → Settings → Authorized domains |
| "El registro de alumnos está cerrado" | Se desactivó en *Revisión de placas* | Volver a activarlo |
| "Sólo se aceptan correos @…" | Su dominio no está en la lista | Agregar el dominio (por ejemplo el de profesores) en *Revisión de placas* |
| "Firestore rechazó el registro… publicar firestore.rules" | Reglas publicadas de una versión anterior | `firebase deploy --only firestore` |
| "Esa matrícula ya está registrada con otro correo" | La escuela la importó con otro correo, o ya tiene cuenta | Administración corrige el correo en la ficha |

Se corrigió además un error: un alumno que confirmaba su correo y volvía a abrir el portal
quedaba con la sesión marcada como "no verificada" hasta por una hora, y veía "Esa matrícula ya
está registrada con otro correo" aunque fuera nueva. Ahora la sesión se renueva sola.

**Probar las reglas** (requiere Node.js y Java 11+):

```bash
cd pruebas-reglas
npm install
npm test        # levanta el emulador de Firestore y corre 18 casos de registro, placas y cámara
```

### Verificador de placas: qué hace y qué no

Compara cada placa contra la asignación oficial de series por estado y tipo de vehículo de
la **NOM-001-SCT-2-2016, Apéndice C** (DOF 24/06/2016). Para Jalisco, transporte privado:

| Tipo | Formato | Series de Jalisco |
|---|---|---|
| Automóvil | AAA-000-A | HSA-001-A a LFZ-999-Z |
| Camión / pickup | AA-0000-A | HU-0001-A a KK-9999-Z |
| Motocicleta | A00AA y A0AA0 | M01AA a M99ZZ; L1CK1 a L9VW9 |
| Capacidades diferentes | 00-AAA | 01-HLW a 99-JBA |
| Vehículo ecológico | 00A-000 | 02N-001 a 08R-999 |

Las placas no usan las letras I, Ñ, O ni Q.

**No consulta el padrón vehicular de Jalisco** (a nombre de quién está una placa o si está
vigente). Ese padrón es confidencial y no tiene acceso público: la consulta de la Secretaría
de la Hacienda Pública pide placa y número de serie de cada vehículo, y las copias del
padrón que circulan en internet son ilegales. El verificador distingue placas de Jalisco de
**placas foráneas** (de otro estado, que nombra cuando la serie es oficial, o de ninguna serie
conocida, como placas de otro país) y detecta errores de captura y de tipo de vehículo, pero no
confirma que el auto sea de la persona. La caseta y la bitácora muestran también si cada placa
leída es foránea. Si la institución obtiene un convenio con la Secretaría, el manual técnico explica
dónde conectar esa consulta.

Si la SICT publica nuevas series, se editan las tablas de `gate-service/generar_series.py`,
se ejecuta y se corren las pruebas (`python test_placas.py`). Si cambian las series de
Jalisco hay que actualizar también las expresiones del bloque `SERIES-JALISCO` de
`firestore.rules`; la prueba avisa si no coinciden.

Antes de importar un CSV puedes auditarlo: `python verificar_placas.py estudiantes.csv`.

En Authentication → *Templates* puedes traducir al español el correo de verificación y el de
cambio de contraseña.

## 4. Servicio de la pluma

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
5. Revisa que el vehículo esté activo y aprobado (no en revisión), que el estudiante esté activo,
   y que la vigencia no haya vencido.
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
students/{matricula}     name, kind alumno|profesor|administrativo, career, email, phone, validUntil "AAAA-MM-DD", active,
                         status aprobado, uid (cuenta del alumno), source
vehicles/{PLACA}         plate, plateKey, type auto|camioneta|pickup|motocicleta, studentId, studentName,
                         brand, model, color, active, status pendiente|aprobado|rechazado,
                         rejectReason, ownerUid, source admin|alumno
accounts/{uid}           matricula        (vincula la cuenta del alumno con su ficha)
settings/registration    open, domains[]  (autorregistro abierto/cerrado y dominios de correo permitidos)
gates/{id}               name, direction entrada|salida, mode auto|supervisado|libre,
                         lastSeen, cameraOk, lastError, cachedVehicles   (lo mantiene el servicio)
accessLogs/{auto}        ts, gateId, gateName, plate, conf, decision permitido|denegado|en_espera|manual,
                         reason, reasonText, method, studentId, studentName, vehicleDesc,
                         thumb, openedBy, plateOrigin jalisco|foranea, plateState
commands/{auto}          gateId, action open|read|snapshot, status pending|done|expired|error,
                         note, requestedBy, requestedByName, createdAt, via (open: camara-panel),
                         plate, studentId, studentName, image (read), result {width, height, roi, plates[]}, resultImage (snapshot)
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
- Historial de accesos visible para cada alumno en su perfil.
- Validar la placa contra una foto de la tarjeta de circulación al registrarla.
- Reporte de ocupación por hora.
