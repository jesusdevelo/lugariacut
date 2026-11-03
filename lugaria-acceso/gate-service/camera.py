"""
Captura de video.

Con cámaras IP (RTSP) OpenCV acumula cuadros en un búfer y la imagen se
retrasa varios segundos. Para evitarlo, un hilo lee sin parar y sólo
guardamos el cuadro más reciente.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import cv2

log = logging.getLogger("camara")


class Camera:
    def __init__(self, source):
        # "0" -> webcam USB 0; otra cosa -> URL RTSP/HTTP o archivo de video
        self.source = int(source) if str(source).isdigit() else source
        self.is_file = isinstance(self.source, str) and os.path.isfile(self.source)
        self._frame = None
        self._t = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ok = False
        self.finished = False
        self._th = threading.Thread(target=self._run, daemon=True, name="camara")

    def start(self) -> "Camera":
        self._th.start()
        return self

    def _open(self):
        if isinstance(self.source, str) and self.source.startswith("rtsp"):
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        cap = cv2.VideoCapture(self.source)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self.ok = False
                log.warning("No se pudo abrir la cámara (%s). Reintento en %.0fs", self.source, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            log.info("Cámara conectada")
            backoff = 1.0
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    if self.is_file:
                        self.finished = True
                        self._stop.set()
                    break
                with self._lock:
                    self._frame, self._t = frame, time.monotonic()
                self.ok = True
                if self.is_file:            # simular tiempo real con archivos
                    time.sleep(1 / fps)
            cap.release()
            self.ok = False
            if not self._stop.is_set():
                log.warning("Se perdió la señal de la cámara, reconectando…")
                time.sleep(1)

    def latest(self, max_age: float = 2.0):
        """Devuelve el último cuadro si tiene menos de `max_age` segundos."""
        with self._lock:
            if self._frame is None or time.monotonic() - self._t > max_age:
                return None
            return self._frame.copy()

    def stop(self) -> None:
        self._stop.set()
