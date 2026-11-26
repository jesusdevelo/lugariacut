"""
Verificador de placas mexicanas contra las series oficiales asignadas por la
NOM-001-SCT-2-2016 (Apéndice C). Los datos están en series-mx.json, el mismo
archivo que usa la web (web/placas-mx.js), así ambos dan el mismo resultado.

Qué comprueba
  · Que la placa tenga un formato oficial (letras sin I, Ñ, O, Q).
  · Que caiga dentro de una serie asignada, y a qué estado y tipo pertenece.
  · Que el tipo de serie (automóvil, camión, moto…) sea compatible con el
    tipo de vehículo que declaró el alumno.

Qué NO comprueba
  · Que la placa exista en el padrón vehicular ni a nombre de quién está.
    Ese padrón es confidencial; sólo la Secretaría de la Hacienda Pública
    de Jalisco puede confirmarlo (ver manual técnico, "Consulta oficial").

Resultados (campo `result`)
  jalisco_valida    serie vigente de Jalisco y compatible con el tipo → se aprueba sola
  tipo_distinto     serie de Jalisco, pero de otro tipo de vehículo  → revisión
  formato_anterior  serie de Jalisco con numeración anterior a 2016   → revisión
  foranea           no es serie de Jalisco: de otro estado (se nombra) o
                    de ninguna serie conocida                         → revisión
  invalida          ni siquiera tiene forma de placa                  → no se acepta
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DATA_PATH = Path(__file__).with_name("series-mx.json")

# Correcciones típicas de captura, según lo que debería ir en esa posición.
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"}
_TO_LETTER = {"0": "D", "1": "L", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "O": "D", "Q": "D", "I": "L"}

APPROVE = "jalisco_valida"
MESSAGES = {
    "jalisco_valida":   "Serie oficial de Jalisco para {kind}.",
    "tipo_distinto":    "Es una placa de Jalisco para {kind}, pero registraste {declared}.",
    "formato_anterior": "Placa de Jalisco con numeración anterior a 2016. Debe revisarla administración.",
    "foranea_estado":   "Placa foránea de {state} ({kind}).",
    "foranea":          "Placa foránea: no corresponde a ninguna serie de Jalisco.",
    "invalida":         "No tiene forma de placa: debe tener entre 5 y 8 letras y números.",
}


def normalize(text: str | None) -> str:
    t = (text or "").upper().replace("Ñ", "N")
    return re.sub(r"[^A-Z0-9]", "", t)


@dataclass
class Match:
    state: str
    state_name: str
    kind: str
    era: str
    display: str          # placa con guiones según su formato


@dataclass
class Verification:
    plate: str                      # normalizada
    result: str
    message: str
    display: str = ""
    state: str = ""
    state_name: str = ""
    kind: str = ""
    suggestion: str = ""            # placa corregida que sí sería de Jalisco
    matches: list[Match] = field(default_factory=list)

    @property
    def auto_approve(self) -> bool:
        return self.result == APPROVE

    @property
    def foreign(self) -> bool:
        return self.result == "foranea"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["auto_approve"] = self.auto_approve
        d["foreign"] = self.foreign
        return d


class PlateVerifier:
    def __init__(self, data: dict | None = None):
        if data is None:
            data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
        self.data = data
        self.home = data["home"]
        self.alpha = data["alphabet"]
        self.rank = {c: i for i, c in enumerate(self.alpha)}
        self.series = []
        for s in data["series"]:
            tpl = s["format"].replace("-", "")
            self.series.append({**s, "tpl": tpl,
                                "lo": self._key(s["from"], tpl), "hi": self._key(s["to"], tpl)})

    # ── núcleo ─────────────────────────────────────────────
    def _fits(self, p: str, tpl: str) -> bool:
        if len(p) != len(tpl):
            return False
        for c, t in zip(p, tpl):
            if t == "L" and c not in self.rank:
                return False
            if t == "D" and not c.isdigit():
                return False
        # ningún grupo de dígitos puede ser todo ceros
        return all(set(p[m.start():m.end()]) != {"0"} for m in re.finditer(r"D+", tpl))

    def _key(self, p: str, tpl: str) -> tuple:
        # Orden oficial de la serie: primero las letras, después los dígitos.
        letters = tuple(self.rank[c] for c, t in zip(p, tpl) if t == "L")
        digits = tuple(int(c) for c, t in zip(p, tpl) if t == "D")
        return letters + digits

    @staticmethod
    def _display(p: str, fmt: str) -> str:
        out, i = [], 0
        for ch in fmt:
            if ch == "-":
                out.append("-")
            else:
                out.append(p[i])
                i += 1
        return "".join(out)

    def find(self, plate: str) -> list[Match]:
        p = normalize(plate)
        found = []
        for s in self.series:
            if self._fits(p, s["tpl"]) and s["lo"] <= self._key(p, s["tpl"]) <= s["hi"]:
                found.append(Match(s["state"], self.data["states"][s["state"]], s["kind"], s["era"],
                                   self._display(p, s["format"])))
        return found

    # ── veredicto ──────────────────────────────────────────
    def verify(self, plate: str, vehicle_type: str = "auto") -> Verification:
        p = normalize(plate)
        kinds_ok = self.data["typeKinds"].get(vehicle_type, [])
        if not (5 <= len(p) <= 8) or not any(c.isdigit() for c in p) or not any(c.isalpha() for c in p):
            return Verification(p, "invalida", MESSAGES["invalida"])

        ms = self.find(p)
        home = [m for m in ms if m.state == self.home]
        best = (next((m for m in home if m.era == "actual" and m.kind in kinds_ok), None)
                or next((m for m in home if m.era == "actual"), None)
                or next((m for m in home), None)
                or next((m for m in ms), None))

        if best is None:
            v = Verification(p, "foranea", MESSAGES["foranea"])
            if any(c in "IOQ" for c in p):
                v.message += " Si es de Jalisco, revísala: las placas no usan las letras I, O ni Q."
        elif best.state != self.home:
            v = Verification(p, "foranea", "", best.display, best.state, best.state_name, best.kind)
            v.message = MESSAGES["foranea_estado"].format(kind=self.data["kinds"].get(v.kind, v.kind), state=v.state_name)
        elif best.era == "anterior":
            v = Verification(p, "formato_anterior", "", best.display, best.state, best.state_name, best.kind)
        elif best.kind not in kinds_ok:
            v = Verification(p, "tipo_distinto", "", best.display, best.state, best.state_name, best.kind)
        else:
            v = Verification(p, APPROVE, "", best.display, best.state, best.state_name, best.kind)
        v.matches = ms
        if not v.message:
            v.message = MESSAGES[v.result].format(
                kind=self.data["kinds"].get(v.kind, v.kind), state=v.state_name,
                declared=_TYPE_LABEL.get(vehicle_type, vehicle_type))
        if v.result == "foranea" and not ms:   # no coincide con ninguna serie: quizá error de captura
            v.suggestion = self.suggest(p, vehicle_type)
        return v

    def suggest(self, plate: str, vehicle_type: str) -> str:
        """Si un error de captura (O por 0, I por 1…) explica la falla, propone la placa corregida."""
        p = normalize(plate)
        kinds_ok = self.data["typeKinds"].get(vehicle_type, [])
        for s in self.series:
            if s["state"] != self.home or s["era"] != "actual" or s["kind"] not in kinds_ok:
                continue
            tpl = s["tpl"]
            if len(p) != len(tpl):
                continue
            fixed = "".join((_TO_DIGIT.get(c, c) if t == "D" else _TO_LETTER.get(c, c)) if not
                            ((t == "D" and c.isdigit()) or (t == "L" and c in self.rank)) else c
                            for c, t in zip(p, tpl))
            if fixed != p and self._fits(fixed, tpl) and s["lo"] <= self._key(fixed, tpl) <= s["hi"]:
                return self._display(fixed, s["format"])
        return ""


_TYPE_LABEL = {"auto": "auto", "camioneta": "camioneta / SUV", "pickup": "pickup", "motocicleta": "motocicleta"}

_default: PlateVerifier | None = None


def verify(plate: str, vehicle_type: str = "auto") -> Verification:
    global _default
    if _default is None:
        _default = PlateVerifier()
    return _default.verify(plate, vehicle_type)
