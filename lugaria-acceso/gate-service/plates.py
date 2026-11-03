"""
Utilidades de placas: normalización, "plegado" de caracteres que el OCR
confunde, votación entre cuadros y enfriamiento por placa.

IMPORTANTE: normalize() y fold() deben ser idénticas a las de
web/common.js, porque el panel guarda los vehículos usando estas mismas
llaves y el servicio las busca igual.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

# Caracteres que el OCR suele confundir. Ambos lados (placa registrada y
# placa leída) se pliegan igual, así "JKL-1O3" y "JKL-103" coinciden.
_FOLD = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
    "G": "6",
})


def normalize(text: str | None) -> str:
    """'jkl-123 a' -> 'JKL123A'"""
    t = (text or "").upper().replace("Ñ", "N")
    return re.sub(r"[^A-Z0-9]", "", t)


def fold(text: str | None) -> str:
    """Llave tolerante a confusiones del OCR: 'JKL-1O3' -> 'JK1103'"""
    return normalize(text).translate(_FOLD)


def looks_like_plate(key: str, min_len: int = 5, max_len: int = 8) -> bool:
    """Filtro barato para descartar lecturas basura (letreros, calcomanías)."""
    if not (min_len <= len(key) <= max_len):
        return False
    return any(c.isdigit() for c in key)


def distance(a: str, b: str) -> int:
    """Distancia de Levenshtein (sólo se usa si se habilita en config)."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 1:
        return 2
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class _Vote:
    t: float
    key: str
    conf: float


@dataclass
class PlateVoter:
    """
    Confirma una placa sólo cuando aparece `votes_required` veces dentro de
    `window_s` segundos. Evita abrir la pluma por una lectura aislada mala.
    """
    votes_required: int = 3
    window_s: float = 3.0
    _votes: list[_Vote] = field(default_factory=list)

    def add(self, key: str, conf: float, now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else now
        self._votes = [v for v in self._votes if now - v.t <= self.window_s]
        self._votes.append(_Vote(now, key, conf))

        target = fold(key)
        group = [v for v in self._votes if fold(v.key) == target]
        if len(group) < self.votes_required:
            return None

        # De las variantes leídas, gana la de mayor confianza acumulada.
        score: dict[str, float] = defaultdict(float)
        for v in group:
            score[v.key] += v.conf
        best = max(score, key=score.get)
        self._votes = [v for v in self._votes if fold(v.key) != target]
        return best

    def reset(self) -> None:
        self._votes.clear()


class Cooldown:
    """No vuelve a procesar la misma placa durante `seconds` segundos."""

    def __init__(self, seconds: float = 30.0):
        self.seconds = seconds
        self._last: dict[str, float] = {}

    def active(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        t = self._last.get(fold(key))
        return t is not None and now - t < self.seconds

    def mark(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._last[fold(key)] = now
        # limpieza para que el dict no crezca sin fin
        if len(self._last) > 500:
            self._last = {k: v for k, v in self._last.items() if now - v < self.seconds}
