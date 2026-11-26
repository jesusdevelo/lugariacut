"""
Lugaria Acceso — servicio de pluma con lectura de placas.

Uso:
  python main.py                                   # producción (config.yaml)
  python main.py --mostrar                         # abre ventana con el video
  python main.py --sin-firebase estudiantes.csv    # prueba sin Firebase
  python main.py --imagen foto.jpg --sin-firebase estudiantes.csv
  python main.py --fuente video_prueba.mp4 --mostrar
"""
from __future__ import annotations

import argparse
import base64
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import yaml

from backend import FirebaseBackend, LocalBackend
from camera import Camera
from directory import REASONS, Directory
from gate import make_gate
from placas_mx import verify as verify_plate
from plates import Cooldown, PlateVoter, looks_like_plate, normalize
from recognizer import crop_roi, make_recognizer, plate_thumb

log = logging.getLogger("lugaria")


class GateService:
    def __init__(self, cfg: dict, backend_csv: str | None = None):
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.get("zona_horaria", "America/Mexico_City"))
        rc = cfg["reconocimiento"]
        self.roi = rc.get("zona_interes")
        self.min_conf = float(rc.get("confianza_minima", 0.80))
        self.min_len = int(rc.get("largo_minimo", 5))
        self.max_len = int(rc.get("largo_maximo", 8))
        self.interval = 1.0 / float(rc.get("cuadros_por_segundo", 4))

        self.directory = Directory(allow_one_char_error=bool(rc.get("tolerar_un_error", False)))
        data_dir = Path(cfg.get("carpeta_datos", "datos"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.snap_dir = data_dir / "capturas"
        self.snap_dir.mkdir(exist_ok=True)
        self.keep_days = int(cfg.get("dias_capturas", 15))

        self.backend = (LocalBackend(cfg, self.directory, data_dir, backend_csv)
                        if backend_csv else FirebaseBackend(cfg, self.directory, data_dir))
        self.gate = make_gate(cfg["pluma"])
        self.voter = PlateVoter(int(rc.get("votos_requeridos", 3)), float(rc.get("ventana_votos_s", 3)))
        self.cooldown = Cooldown(float(cfg["pluma"].get("enfriamiento_s", 30)))
        self.recognizer = make_recognizer(rc)
        self._rec_lock = threading.Lock()   # el bucle y las órdenes de lectura comparten el modelo
        self.cam: Camera | None = None

    # ── decisión ───────────────────────────────────────────
    def handle_plate(self, plate_text: str, conf: float, frame=None, box=None) -> dict:
        key = normalize(plate_text)
        today = datetime.now(self.tz).date()
        d = self.directory.decide(key, today)
        m = d.match
        g = self.backend.gate
        mode = g.get("mode", "auto")

        if mode == "libre":
            open_it, decision = True, "permitido"
        elif mode == "supervisado":
            open_it, decision = False, ("en_espera" if d.allowed else "denegado")
        else:  # auto
            open_it, decision = d.allowed, ("permitido" if d.allowed else "denegado")

        if open_it:
            self.gate.open(f"{key} {d.reason}")
            self.backend.bump_occupancy(1 if g.get("direction") == "entrada" else -1)

        v, s = m.vehicle or {}, m.student or {}
        entry = {
            "ts": datetime.now(timezone.utc),
            "gateId": self.backend.gate_id, "gateName": g.get("name"),
            "direction": g.get("direction"), "mode": mode,
            "plate": key, "conf": round(conf, 3),
            "decision": decision, "reason": d.reason, "reasonText": REASONS.get(d.reason, d.reason),
            "method": m.method,
            "vehicleId": v.get("id"), "vehiclePlate": v.get("plate"),
            "vehicleType": v.get("type"),
            "vehicleDesc": " ".join(x for x in (v.get("brand"), v.get("model"), v.get("color")) if x),
            "studentId": s.get("id") or v.get("studentId"),
            "studentName": s.get("name") or v.get("studentName"),
            "career": s.get("career"),
            **plate_origin(key, v.get("type")),
        }
        if frame is not None and box is not None and self.cfg.get("guardar_miniatura", True):
            entry["thumb"] = plate_thumb(frame, box)
        if frame is not None:
            self._save_snapshot(frame, key, decision)

        icon = {"permitido": "✅", "denegado": "⛔", "en_espera": "🕒"}[decision]
        log.info("%s %s  %s  %s  (conf %.2f, %s)", icon, key, entry["studentName"] or "—",
                 entry["reasonText"], conf, m.method or "sin coincidencia")
        self.backend.log_access(entry)
        return entry

    def handle_command(self, cmd_id: str, cmd: dict) -> None:
        """Órdenes desde la web: open (apertura manual), read y snapshot (sólo lectura)."""
        action = cmd.get("action", "open")
        if action == "open":
            return self._cmd_open(cmd_id, cmd)
        if action in ("read", "snapshot"):
            return self._cmd_read(cmd_id, cmd, action)
        self.backend.command_done(cmd_id, "error", f"Acción desconocida: {action}")

    def _cmd_read(self, cmd_id: str, cmd: dict, action: str) -> None:
        """Lee placas de una imagen enviada (read) o del cuadro actual de la cámara (snapshot).
        Nunca abre la pluma ni escribe en la bitácora."""
        try:
            if action == "read":
                frame = decode_image(cmd.get("image"))
                if frame is None:
                    return self.backend.command_result(cmd_id, "error", note="No se pudo leer la imagen enviada")
            else:
                frame = self.cam.latest(max_age=5) if self.cam else None
                if frame is None:
                    return self.backend.command_result(
                        cmd_id, "error", note="La cámara de la pluma no está enviando imagen")
            with self._rec_lock:
                readings = self.recognizer.read(frame)
            h, w = frame.shape[:2]
            result = {
                "width": w, "height": h, "roi": self.roi if action == "snapshot" else None,
                "plates": [{"text": normalize(r.text), "raw": r.text, "conf": round(float(r.conf), 3),
                            "box": [int(v) for v in r.box]} for r in readings],
            }
            image = encode_image(frame, 960) if action == "snapshot" else None
            self.backend.command_result(cmd_id, "done", result=result, image=image)
            log.info("🔍 Lectura %s: %s", action, ", ".join(p["text"] for p in result["plates"]) or "sin placas")
        except Exception as e:  # noqa: BLE001
            log.exception("Error en orden de lectura")
            self.backend.command_result(cmd_id, "error", note=str(e)[:200])

    def _cmd_open(self, cmd_id: str, cmd: dict) -> None:
        """Apertura desde la caseta o el panel. Con via="camara-panel" la placa la reconoció la cámara
        del panel: se vuelve a validar aquí y, si está autorizada, se registra como acceso autorizado."""
        who = cmd.get("requestedByName") or cmd.get("requestedBy") or "desconocido"
        plate = normalize(cmd.get("plate"))
        from_camera = cmd.get("via") == "camara-panel" and bool(plate)
        d = self.directory.decide(plate, datetime.now(self.tz).date()) if from_camera else None
        authorized = bool(d and d.allowed)

        ok = self.gate.open(f"{'placa ' + plate + ' (cámara del panel)' if from_camera else 'manual'} por {who}")
        g = self.backend.gate
        if ok:
            self.backend.bump_occupancy(1 if g.get("direction") == "entrada" else -1)
        if plate:
            self.cooldown.mark(plate)
        entry = {
            "ts": datetime.now(timezone.utc),
            "gateId": self.backend.gate_id, "gateName": g.get("name"),
            "direction": g.get("direction"), "mode": g.get("mode"),
            "plate": plate or None,
            "openedBy": who, "openedByUid": cmd.get("requestedBy"), "commandId": cmd_id,
            "studentName": cmd.get("studentName"), "studentId": cmd.get("studentId"),
            **(plate_origin(plate) if plate else {}),
        }
        if authorized:
            v, s = d.match.vehicle or {}, d.match.student or {}
            entry.update({
                "decision": "permitido" if ok else "error", "reason": "ok", "method": d.match.method,
                "reasonText": "Acceso autorizado · cámara del panel",
                "vehicleId": v.get("id"), "vehiclePlate": v.get("plate"), "vehicleType": v.get("type"),
                "vehicleDesc": " ".join(x for x in (v.get("brand"), v.get("model"), v.get("color")) if x),
                "studentId": s.get("id") or v.get("studentId"), "studentName": s.get("name") or v.get("studentName"),
                "career": s.get("career"), **plate_origin(plate, v.get("type")),
            })
        else:
            # Apertura manual, o la placa ya no está autorizada según el padrón de la pluma.
            note = cmd.get("note") or "Apertura manual"
            if from_camera:
                note += f" (la pluma no la reconoce como autorizada: {REASONS.get(d.reason, d.reason)})"
            entry.update({"decision": "manual" if ok else "error", "reason": "apertura_manual", "reasonText": note})
        self.backend.log_access(entry)
        self.backend.command_done(cmd_id, "done" if ok else "error")
        log.info("%s %s por %s", "✅ Acceso autorizado (cámara del panel)" if authorized else "🖐  Apertura manual",
                 plate or "", who)

    # ── bucle principal ────────────────────────────────────
    def process_frame(self, frame):
        sub, ox, oy = crop_roi(frame, self.roi)
        with self._rec_lock:
            readings = self.recognizer.read(sub)
        out = []
        for r in readings:
            key = normalize(r.text)
            box = (r.box[0] + ox, r.box[1] + oy, r.box[2] + ox, r.box[3] + oy)
            out.append((key, r.conf, box))
            if r.conf < self.min_conf or not looks_like_plate(key, self.min_len, self.max_len):
                continue
            confirmed = self.voter.add(key, r.conf)
            if confirmed and not self.cooldown.active(confirmed):
                self.cooldown.mark(confirmed)
                self.handle_plate(confirmed, r.conf, frame, box)
        return out

    def run(self, source, show: bool = False):
        self.backend.start(self.handle_command)
        cam = self.cam = Camera(source).start()
        stop = {"v": False}
        signal.signal(signal.SIGINT, lambda *_: stop.update(v=True))
        signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
        log.info("Pluma '%s' lista. Fuente: %s", self.backend.gate_id, source)
        last_clean = 0.0
        while not stop["v"]:
            t0 = time.monotonic()
            self.backend.status["cameraOk"] = cam.ok
            frame = cam.latest()
            if frame is None:
                if cam.finished:
                    break
                time.sleep(0.2)
                continue
            try:
                found = self.process_frame(frame)
                self.backend.status["lastError"] = ""
            except Exception as e:  # noqa: BLE001
                log.exception("Error procesando cuadro")
                self.backend.status["lastError"] = str(e)[:200]
                found = []
            if show:
                self._draw(frame, found)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if time.monotonic() - last_clean > 3600:
                self._clean_snapshots()
                last_clean = time.monotonic()
            time.sleep(max(0.0, self.interval - (time.monotonic() - t0)))
        cam.stop()
        self.backend.stop()
        self.gate.close()
        log.info("Servicio detenido")

    def test_image(self, path: str):
        self.backend.start(lambda *_: None)
        frame = cv2.imread(path)
        if frame is None:
            sys.exit(f"No se pudo leer la imagen: {path}")
        sub, ox, oy = crop_roi(frame, self.roi)
        readings = self.recognizer.read(sub)
        if not readings:
            print("No se detectó ninguna placa.")
        for r in readings:
            print(f"Lectura: {r.text!r}  confianza {r.conf:.2f}")
            if r.conf >= self.min_conf:
                b = (r.box[0] + ox, r.box[1] + oy, r.box[2] + ox, r.box[3] + oy)
                self.handle_plate(r.text, r.conf, frame, b)
            else:
                print(f"  (por debajo de confianza_minima={self.min_conf}, no se evalúa)")
        time.sleep(1.5)  # dar tiempo a que se escriba la bitácora

    # ── auxiliares ─────────────────────────────────────────
    def _draw(self, frame, found):
        if self.roi:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = (int(self.roi[0] * w), int(self.roi[1] * h),
                              int(self.roi[2] * w), int(self.roi[3] * h))
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 0), 1)
        for key, conf, (x1, y1, x2, y2) in found:
            color = (0, 220, 120) if conf >= self.min_conf else (0, 160, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{key} {conf:.2f}", (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.imshow("Lugaria - pluma (q para salir)", frame)

    def _save_snapshot(self, frame, key, decision):
        if not self.cfg.get("guardar_capturas", True):
            return
        now = datetime.now(self.tz)
        day = self.snap_dir / now.strftime("%Y-%m-%d")
        day.mkdir(exist_ok=True)
        small = cv2.resize(frame, (960, int(frame.shape[0] * 960 / frame.shape[1])))
        cv2.imwrite(str(day / f"{now:%H%M%S}_{key}_{decision}.jpg"), small,
                    [cv2.IMWRITE_JPEG_QUALITY, 75])

    def _clean_snapshots(self):
        limit = time.time() - self.keep_days * 86400
        for d in self.snap_dir.iterdir():
            if d.is_dir() and d.stat().st_mtime < limit:
                for f in d.iterdir():
                    f.unlink(missing_ok=True)
                d.rmdir()


def plate_origin(plate: str, vehicle_type: str | None = None) -> dict:
    """'jalisco' o 'foranea' según las series oficiales (NOM-001-SCT-2-2016), y el estado si se conoce."""
    try:
        v = verify_plate(plate, vehicle_type or "camioneta")
    except Exception:  # noqa: BLE001  (sin series-mx.json no se bloquea la pluma)
        return {}
    if v.result == "invalida":
        return {}
    return {"plateOrigin": "foranea" if v.foreign else "jalisco", "plateState": v.state_name or None}


def decode_image(data_url: str | None):
    """'data:image/jpeg;base64,...' -> imagen BGR de OpenCV (o None)."""
    if not data_url or "," not in data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except ValueError:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def encode_image(frame, max_width: int = 960, quality: int = 72) -> str:
    """Imagen BGR -> data URL JPEG reducido (~60-120 KB, cabe en un documento de Firestore)."""
    h, w = frame.shape[:2]
    if w > max_width:
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""


def main():
    ap = argparse.ArgumentParser(description="Lugaria — servicio de pluma con lectura de placas")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--fuente", help="Reemplaza camara.fuente (0, rtsp://…, video.mp4)")
    ap.add_argument("--mostrar", action="store_true", help="Muestra el video con las detecciones")
    ap.add_argument("--sin-firebase", metavar="CSV", help="Usa un CSV local en lugar de Firebase")
    ap.add_argument("--imagen", help="Prueba con una sola foto y termina")
    ap.add_argument("--controlador", help="Reemplaza pluma.driver (simulado, gpio, http, serial)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if args.controlador:
        cfg["pluma"]["driver"] = args.controlador

    logging.basicConfig(level=cfg.get("nivel_log", "INFO"),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    svc = GateService(cfg, backend_csv=args.sin_firebase)
    if args.imagen:
        svc.test_image(args.imagen)
    else:
        svc.run(args.fuente or cfg["camara"]["fuente"], show=args.mostrar)


if __name__ == "__main__":
    main()
