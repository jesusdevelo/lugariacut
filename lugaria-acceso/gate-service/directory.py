"""
Directorio en memoria de estudiantes y vehículos.

El servicio mantiene una copia local completa (sincronizada en tiempo real
desde Firestore y respaldada en disco), así la decisión de abrir la pluma
toma milisegundos y sigue funcionando si se cae el internet.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from plates import distance, fold, normalize


@dataclass
class Match:
    vehicle: dict | None = None
    student: dict | None = None
    method: str = ""                 # exacta | aproximada | distancia1
    candidates: list[dict] = field(default_factory=list)  # si fue ambigua


@dataclass
class Decision:
    allowed: bool
    reason: str                      # ver REASONS
    match: Match


REASONS = {
    "ok":                  "Acceso autorizado",
    "no_registrado":       "Placa no registrada",
    "ambigua":             "Lectura ambigua (coincide con varias placas)",
    "vehiculo_inactivo":   "Vehículo dado de baja",
    "vehiculo_pendiente":  "Placa pendiente de aprobación",
    "vehiculo_rechazado":  "Placa rechazada por administración",
    "registro_pendiente":  "Registro del alumno pendiente de aprobación",
    "estudiante_inactivo": "Estudiante inactivo",
    "estudiante_no_existe":"Vehículo sin estudiante válido",
    "vigencia_vencida":    "Vigencia vencida",
}


class Directory:
    def __init__(self, allow_one_char_error: bool = False):
        self.allow_one_char_error = allow_one_char_error
        self._lock = threading.RLock()
        self.students: dict[str, dict] = {}   # id (matrícula) -> datos
        self.vehicles: dict[str, dict] = {}   # plateKey -> datos
        self._by_fold: dict[str, list[str]] = defaultdict(list)

    # ── carga ──────────────────────────────────────────────
    def set_students(self, students: dict[str, dict]) -> None:
        with self._lock:
            self.students = dict(students)

    def set_vehicles(self, vehicles: dict[str, dict]) -> None:
        with self._lock:
            self.vehicles = {}
            self._by_fold = defaultdict(list)
            for vid, v in vehicles.items():
                key = normalize(v.get("plateKey") or v.get("plate") or vid)
                v = {**v, "id": vid, "plateKey": key}
                self.vehicles[key] = v
                self._by_fold[fold(key)].append(key)

    def snapshot(self) -> dict:
        with self._lock:
            return {"students": dict(self.students),
                    "vehicles": {v["id"]: v for v in self.vehicles.values()}}

    @property
    def size(self) -> tuple[int, int]:
        return len(self.students), len(self.vehicles)

    # ── búsqueda ───────────────────────────────────────────
    def lookup(self, read_text: str) -> Match:
        key = normalize(read_text)
        with self._lock:
            # 1) coincidencia exacta
            v = self.vehicles.get(key)
            if v:
                return Match(v, self.students.get(v.get("studentId", "")), "exacta")

            # 2) misma placa salvo confusiones típicas del OCR (O/0, I/1, B/8…)
            keys = self._by_fold.get(fold(key), [])
            if len(keys) == 1:
                v = self.vehicles[keys[0]]
                return Match(v, self.students.get(v.get("studentId", "")), "aproximada")
            if len(keys) > 1:
                return Match(candidates=[self.vehicles[k] for k in keys], method="ambigua")

            # 3) opcional: un carácter distinto (desactivado por defecto)
            if self.allow_one_char_error:
                near = [k for k in self.vehicles if distance(fold(k), fold(key)) == 1]
                if len(near) == 1:
                    v = self.vehicles[near[0]]
                    return Match(v, self.students.get(v.get("studentId", "")), "distancia1")
                if len(near) > 1:
                    return Match(candidates=[self.vehicles[k] for k in near], method="ambigua")
        return Match()

    # ── reglas de acceso ───────────────────────────────────
    def decide(self, read_text: str, today: date) -> Decision:
        m = self.lookup(read_text)
        if m.method == "ambigua":
            return Decision(False, "ambigua", m)
        if not m.vehicle:
            return Decision(False, "no_registrado", m)
        if m.vehicle.get("active") is False:
            return Decision(False, "vehiculo_inactivo", m)
        # Placas registradas por el propio alumno requieren aprobación.
        # Sin campo "status" (altas del admin o CSV) se consideran aprobadas.
        vst = m.vehicle.get("status", "aprobado")
        if vst == "pendiente":
            return Decision(False, "vehiculo_pendiente", m)
        if vst == "rechazado":
            return Decision(False, "vehiculo_rechazado", m)
        if not m.student:
            return Decision(False, "estudiante_no_existe", m)
        if m.student.get("status") == "pendiente":
            return Decision(False, "registro_pendiente", m)
        if m.student.get("active") is False:
            return Decision(False, "estudiante_inactivo", m)
        vu = (m.student.get("validUntil") or "").strip()
        if vu:
            try:
                if date.fromisoformat(vu[:10]) < today:
                    return Decision(False, "vigencia_vencida", m)
            except ValueError:
                pass  # fecha mal capturada: no bloquear por eso
        return Decision(True, "ok", m)
