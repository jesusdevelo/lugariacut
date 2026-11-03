"""
Reconocimiento de placas (ALPR).

  motor: local          -> fast-alpr (ONNX, gratis, corre sin internet en
                           una PC o Raspberry Pi 5)
  motor: platerecognizer -> API en la nube de Plate Recognizer (de pago,
                           tiene plan de prueba; muy buena con placas de MX)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("alpr")


@dataclass
class Reading:
    text: str
    conf: float                     # 0..1 (confianza del OCR)
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 en el cuadro original


def _mean_conf(c) -> float:
    if c is None:
        return 0.0
    if isinstance(c, (list, tuple, np.ndarray)):
        return float(np.mean(c)) if len(c) else 0.0
    return float(c)


class LocalRecognizer:
    def __init__(self, cfg: dict):
        from fast_alpr import ALPR
        self.alpr = ALPR(
            detector_model=cfg.get("detector_model", "yolo-v9-t-384-license-plate-end2end"),
            ocr_model=cfg.get("ocr_model", "cct-xs-v2-global-model"),
        )
        self.min_det = float(cfg.get("min_detection_conf", 0.5))

    def read(self, frame: np.ndarray) -> list[Reading]:
        out = []
        for r in self.alpr.predict(frame):
            det, ocr = r.detection, r.ocr
            if ocr is None or not ocr.text:
                continue
            if det is not None and _mean_conf(getattr(det, "confidence", 1)) < self.min_det:
                continue
            bb = det.bounding_box
            out.append(Reading(ocr.text, _mean_conf(ocr.confidence),
                               (int(bb.x1), int(bb.y1), int(bb.x2), int(bb.y2))))
        return out


class PlateRecognizerCloud:
    URL = "https://api.platerecognizer.com/v1/plate-reader/"

    def __init__(self, cfg: dict):
        import requests
        self.requests = requests
        self.token = cfg["platerecognizer_token"]
        self.regions = cfg.get("regions", ["mx"])

    def read(self, frame: np.ndarray) -> list[Reading]:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return []
        r = self.requests.post(
            self.URL,
            headers={"Authorization": f"Token {self.token}"},
            data=[("regions", rg) for rg in self.regions],
            files={"upload": ("frame.jpg", buf.tobytes(), "image/jpeg")},
            timeout=8,
        )
        r.raise_for_status()
        out = []
        for res in r.json().get("results", []):
            b = res.get("box", {})
            out.append(Reading(res.get("plate", ""), float(res.get("score", 0)),
                               (b.get("xmin", 0), b.get("ymin", 0), b.get("xmax", 0), b.get("ymax", 0))))
        return out


def make_recognizer(cfg: dict):
    engine = cfg.get("motor", "local")
    log.info("Cargando motor de reconocimiento: %s", engine)
    if engine == "local":
        return LocalRecognizer(cfg)
    if engine == "platerecognizer":
        return PlateRecognizerCloud(cfg)
    raise ValueError(f"reconocimiento.motor desconocido: {engine}")


def crop_roi(frame: np.ndarray, roi: list[float] | None) -> tuple[np.ndarray, int, int]:
    """
    Recorta la zona de interés (fracciones 0..1: [x1, y1, x2, y2]).
    Procesar sólo el carril donde se detiene el auto acelera y evita leer
    placas de autos estacionados al fondo.
    """
    if not roi:
        return frame, 0, 0
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
    return frame[y1:y2, x1:x2], x1, y1


def plate_thumb(frame: np.ndarray, box, height: int = 70) -> str | None:
    """Miniatura JPEG de la placa como data URL (~3-6 KB) para la bitácora."""
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return None
    h, w = frame.shape[:2]
    pw, ph = x2 - x1, y2 - y1
    x1, x2 = max(0, x1 - pw // 4), min(w, x2 + pw // 4)
    y1, y2 = max(0, y1 - ph // 2), min(h, y2 + ph // 2)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    scale = height / crop.shape[0]
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), height))
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if not ok:
        return None
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
