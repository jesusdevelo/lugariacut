"""
Conexión con la base de datos.

FirebaseBackend
  - Escucha en tiempo real `students`, `vehicles`, el documento de su
    pluma en `gates` y las órdenes de apertura manual en `commands`.
  - Guarda una copia del directorio en disco (cache.json) para arrancar y
    seguir operando aunque no haya internet.
  - Escribe la bitácora (`accessLogs`) con una cola que reintenta si la red
    falla; además deja una copia local en bitacora-local.jsonl.

LocalBackend
  - Lee estudiantes y vehículos de un CSV (misma plantilla que el panel).
    Sirve para probar en el escritorio sin configurar Firebase.
"""
from __future__ import annotations

import csv
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from directory import Directory
from plates import normalize

log = logging.getLogger("db")

VERSION = "1.0.0"


class _LocalLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, entry: dict) -> None:
        e = {k: v for k, v in entry.items() if k != "thumb"}
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")


class BaseBackend:
    def __init__(self, cfg: dict, directory: Directory, data_dir: Path):
        self.cfg = cfg
        self.dir = directory
        self.data_dir = data_dir
        self.local_log = _LocalLog(data_dir / "bitacora-local.jsonl")
        g = cfg["pluma"]
        self.gate_id = g["id"]
        self.gate = {"name": g.get("nombre", self.gate_id),
                     "direction": g.get("sentido", "entrada"),
                     "mode": g.get("modo", "auto")}
        self.status = {"cameraOk": False, "lastError": ""}

    def start(self, on_command: Callable[[str, dict], None]) -> None: ...
    def log_access(self, entry: dict) -> None: self.local_log.write(entry)
    def command_done(self, cmd_id: str, status: str = "done", note: str = "") -> None: ...
    def command_result(self, cmd_id: str, status: str, result: dict | None = None,
                       image: str | None = None, note: str = "") -> None: ...
    def bump_occupancy(self, delta: int) -> None: ...
    def stop(self) -> None: ...


class LocalBackend(BaseBackend):
    def __init__(self, cfg, directory, data_dir, csv_path: str):
        super().__init__(cfg, directory, data_dir)
        self.csv_path = csv_path

    def start(self, on_command):
        students, vehicles = load_csv(self.csv_path)
        self.dir.set_students(students)
        self.dir.set_vehicles(vehicles)
        s, v = self.dir.size
        log.info("Modo sin Firebase: %d estudiantes, %d vehículos desde %s", s, v, self.csv_path)

    def log_access(self, entry):
        super().log_access(entry)


def load_csv(path: str) -> tuple[dict, dict]:
    """matricula,nombre,perfil,carrera,correo,telefono,vigencia,placa,tipo,marca,modelo,color"""
    students, vehicles = {}, {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            mat = row.get("matricula", "").upper()
            if not mat:
                continue
            students.setdefault(mat, {
                "id": mat, "matricula": mat, "name": row.get("nombre", ""),
                "kind": row.get("perfil", "").lower() or "alumno",
                "career": row.get("carrera", ""), "validUntil": row.get("vigencia", ""),
                "active": row.get("activo", "si").lower() not in ("no", "0", "false"),
            })
            key = normalize(row.get("placa"))
            if key:
                vehicles[key] = {
                    "plate": row.get("placa"), "plateKey": key, "studentId": mat,
                    "studentName": row.get("nombre", ""), "type": row.get("tipo", "auto"),
                    "brand": row.get("marca", ""),
                    "model": row.get("modelo", ""), "color": row.get("color", ""), "active": True,
                }
    return students, vehicles


class FirebaseBackend(BaseBackend):
    def __init__(self, cfg, directory, data_dir):
        super().__init__(cfg, directory, data_dir)
        import firebase_admin
        from firebase_admin import credentials, firestore
        self.fs = firestore
        cred = credentials.Certificate(cfg["firebase"]["credenciales"])
        firebase_admin.initialize_app(cred)
        self.db = firestore.client()
        self.cache_path = data_dir / "cache.json"
        self._q: queue.Queue = queue.Queue(maxsize=5000)
        self._stop = threading.Event()
        self._watches = []
        self._students: dict = {}
        self._vehicles: dict = {}
        self._on_command = None
        self._seen_cmds: set[str] = set()

    # ── arranque ───────────────────────────────────────────
    def start(self, on_command):
        self._on_command = on_command
        self._load_cache()
        self._ensure_gate_doc()

        from google.cloud.firestore_v1.base_query import FieldFilter
        self._watches = [
            self.db.collection("students").on_snapshot(self._on_students),
            self.db.collection("vehicles").on_snapshot(self._on_vehicles),
            self.db.collection("gates").document(self.gate_id).on_snapshot(self._on_gate),
            self.db.collection("commands")
                .where(filter=FieldFilter("gateId", "==", self.gate_id))
                .where(filter=FieldFilter("status", "==", "pending"))
                .on_snapshot(self._on_commands),
        ]
        threading.Thread(target=self._writer, daemon=True, name="escritor").start()
        threading.Thread(target=self._heartbeat, daemon=True, name="latido").start()

    def _ensure_gate_doc(self):
        ref = self.db.collection("gates").document(self.gate_id)
        try:
            if not ref.get(timeout=10).exists:
                ref.set({**self.gate, "createdAt": self.fs.SERVER_TIMESTAMP})
                log.info("Pluma '%s' registrada en Firestore", self.gate_id)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo verificar la pluma en Firestore (¿sin internet?): %s", e)

    # ── caché en disco ─────────────────────────────────────
    def _load_cache(self):
        if not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._students, self._vehicles = data.get("students", {}), data.get("vehicles", {})
            self.dir.set_students(self._students)
            self.dir.set_vehicles(self._vehicles)
            if data.get("gate"):
                self.gate.update(data["gate"])
            s, v = self.dir.size
            log.info("Caché local cargada: %d estudiantes, %d vehículos", s, v)
        except Exception as e:  # noqa: BLE001
            log.warning("Caché ilegible, se ignora: %s", e)

    def _save_cache(self):
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"students": self._students, "vehicles": self._vehicles,
                                   "gate": self.gate, "savedAt": _now().isoformat()},
                                  ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.cache_path)

    # ── listeners ──────────────────────────────────────────
    def _on_students(self, docs, _changes, _t):
        self._students = {d.id: {**_plain(d.to_dict()), "id": d.id} for d in docs}
        self.dir.set_students(self._students)
        self._save_cache()
        log.info("Estudiantes sincronizados: %d", len(self._students))

    def _on_vehicles(self, docs, _changes, _t):
        self._vehicles = {d.id: _plain(d.to_dict()) for d in docs}
        self.dir.set_vehicles(self._vehicles)
        self._save_cache()
        log.info("Vehículos sincronizados: %d", len(self._vehicles))

    def _on_gate(self, docs, _changes, _t):
        for d in docs:
            if d.exists:
                data = d.to_dict()
                for k in ("name", "direction", "mode"):
                    if data.get(k):
                        self.gate[k] = data[k]
                log.info("Configuración de pluma: %s", self.gate)

    def _on_commands(self, _docs, changes, _t):
        for ch in changes:
            if ch.type.name != "ADDED":
                continue
            doc = ch.document
            if doc.id in self._seen_cmds:
                continue
            self._seen_cmds.add(doc.id)
            data = doc.to_dict()
            created = data.get("createdAt")
            max_age = float(self.cfg["pluma"].get("orden_manual_expira_s", 60))
            if created and (_now() - created).total_seconds() > max_age:
                self.command_done(doc.id, "expired", "Orden demasiado antigua")
                continue
            threading.Thread(target=self._on_command, args=(doc.id, data), daemon=True).start()

    # ── escrituras (con reintento) ─────────────────────────
    def _enqueue(self, desc: str, fn: Callable[[], None]):
        try:
            self._q.put_nowait((desc, fn))
        except queue.Full:
            log.error("Cola de escritura llena, se descarta: %s", desc)

    def _writer(self):
        backoff = 1.0
        while not self._stop.is_set():
            desc, fn = self._q.get()
            while not self._stop.is_set():
                try:
                    fn()
                    backoff = 1.0
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("Falló escritura (%s): %s. Reintento en %.0fs", desc, e, backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)

    def log_access(self, entry):
        super().log_access(entry)
        doc = {**entry, "ts": entry.get("ts") or _now()}

        def write():
            try:
                self.db.collection("accessLogs").add(doc)
            except Exception as e:  # noqa: BLE001
                # Si no se aplicó la exención de índice del campo "thumb"
                # (firestore.indexes.json), Firestore rechaza el documento.
                if doc.get("thumb") and "index" in str(e).lower():
                    log.warning("Bitácora rechazada por tamaño de índice; se guarda sin miniatura. "
                                "Aplica firestore.indexes.json para conservarlas.")
                    doc.pop("thumb", None)
                    self.db.collection("accessLogs").add(doc)
                else:
                    raise
        self._enqueue("bitácora", write)

    def command_done(self, cmd_id, status="done", note=""):
        upd = {"status": status, "doneAt": self.fs.SERVER_TIMESTAMP}
        if note:
            upd["result"] = note
        self._enqueue("orden", lambda: self.db.collection("commands").document(cmd_id).update(upd))

    def command_result(self, cmd_id, status, result=None, image=None, note=""):
        """Respuesta de una orden de lectura (read/snapshot). Borra la imagen recibida."""
        upd = {"status": status, "doneAt": self.fs.SERVER_TIMESTAMP, "image": self.fs.DELETE_FIELD}
        if result is not None:
            upd["result"] = result
        if image:
            upd["resultImage"] = image
        if note:
            upd["note"] = note
        self._enqueue("lectura", lambda: self.db.collection("commands").document(cmd_id).update(upd))

    def bump_occupancy(self, delta):
        ref = self.db.collection("stats").document("occupancy")
        self._enqueue("ocupación", lambda: ref.set(
            {"inside": self.fs.Increment(delta), "updatedAt": self.fs.SERVER_TIMESTAMP}, merge=True))

    def _heartbeat(self):
        ref = self.db.collection("gates").document(self.gate_id)
        every = float(self.cfg["pluma"].get("latido_s", 30))
        while not self._stop.is_set():
            try:
                ref.set({"lastSeen": self.fs.SERVER_TIMESTAMP, "version": VERSION,
                         "cameraOk": self.status["cameraOk"],
                         "lastError": self.status["lastError"],
                         "cachedVehicles": self.dir.size[1]}, merge=True, timeout=10)
            except Exception as e:  # noqa: BLE001
                log.debug("Latido falló: %s", e)
            self._stop.wait(every)

    def stop(self):
        self._stop.set()
        for w in self._watches:
            try:
                w.unsubscribe()
            except Exception:  # noqa: BLE001
                pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plain(d: dict) -> dict:
    """Convierte Timestamps de Firestore a texto para poder guardarlos en JSON."""
    out = {}
    for k, v in (d or {}).items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out
