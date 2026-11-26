"""
Pruebas sin cámara ni Firebase:  python -m pytest -q   (o  python test_acceso.py)
"""
from datetime import date
from pathlib import Path

import numpy as np

import main as svc_mod
from directory import Directory
from plates import Cooldown, PlateVoter, fold, looks_like_plate, normalize
from recognizer import Reading

HOY = date(2026, 10, 28)


def _dir(**kw):
    d = Directory(**kw)
    d.set_students({
        "A1": {"id": "A1", "name": "Mariana", "active": True, "validUntil": "2027-07-31"},
        "A2": {"id": "A2", "name": "Carlos", "active": True, "validUntil": "2025-12-31"},
        "A3": {"id": "A3", "name": "Sofía", "active": False},
    })
    d.set_vehicles({
        "JKL123A": {"plate": "JKL-123-A", "studentId": "A1"},
        "JPS908B": {"plate": "JPS-908-B", "studentId": "A2"},
        "KAB7712": {"plate": "KAB-77-12", "studentId": "A3"},
        "XYZ999":  {"plate": "XYZ-999", "studentId": "A1", "active": False},
    })
    return d


def test_normalize_y_fold():
    assert normalize(" jkl-123 a ") == "JKL123A"
    assert fold("JKL-1O3") == fold("JKL103")
    assert looks_like_plate("JKL123A") and not looks_like_plate("ESCUELA") and not looks_like_plate("AB1")


def test_decisiones():
    d = _dir()
    assert d.decide("JKL-123-A", HOY).reason == "ok"
    assert d.decide("JKI-I23-A", HOY).reason == "ok"          # OCR confundió L/1 con I
    assert d.decide("JKI-I23-A", HOY).match.method == "aproximada"
    assert d.decide("JPS908B", HOY).reason == "vigencia_vencida"
    assert d.decide("KAB7712", HOY).reason == "estudiante_inactivo"
    assert d.decide("XYZ999", HOY).reason == "vehiculo_inactivo"
    assert d.decide("AAA0000", HOY).reason == "no_registrado"


def test_autoregistro_requiere_aprobacion():
    d = _dir()
    d.set_students({**d.students, "A9": {"id": "A9", "name": "Nuevo", "active": False, "status": "pendiente"}})
    d.set_vehicles({
        "JKL123A": {"studentId": "A1", "status": "pendiente"},
        "JKL456A": {"studentId": "A1", "status": "rechazado"},
        "JKL789A": {"studentId": "A1", "status": "aprobado"},
        "NEW1234": {"studentId": "A9", "status": "aprobado"},
    })
    assert d.decide("JKL123A", HOY).reason == "vehiculo_pendiente"
    assert d.decide("JKL456A", HOY).reason == "vehiculo_rechazado"
    assert d.decide("JKL789A", HOY).reason == "ok"
    assert d.decide("NEW1234", HOY).reason == "registro_pendiente"


def test_ambigua_no_abre():
    d = _dir()
    d.set_vehicles({"ABC120": {"studentId": "A1"}, "A8C12O": {"studentId": "A1"}})
    assert d.decide("ABC120", HOY).reason == "ok"            # exacta gana
    assert d.decide("A8C1Z0", HOY).reason == "ambigua"       # pliega a ambas


def test_un_error_opcional():
    assert _dir().decide("JKL124A", HOY).reason == "no_registrado"
    assert _dir(allow_one_char_error=True).decide("JKL124A", HOY).reason == "ok"


def test_votacion_y_enfriamiento():
    v = PlateVoter(votes_required=3, window_s=3)
    assert v.add("JKL123A", .9, now=0) is None
    assert v.add("JKL1Z3A", .5, now=1) is None               # variante mal leída
    assert v.add("JKL123A", .9, now=2) == "JKL123A"          # gana la de más confianza
    assert v.add("JKL123A", .9, now=10) is None              # se reinició
    c = Cooldown(30)
    c.mark("JKL123A", now=0)
    assert c.active("JKL-I23-A", now=10) and not c.active("JKL123A", now=31)


def test_servicio_completo(tmp_path=None, monkeypatch=None):
    """Simula 3 cuadros con la misma placa y verifica que la pluma abre una vez."""
    tmp = Path(tmp_path or "/tmp/lugaria-test")
    lecturas = iter([[Reading("JKL123A", .95, (10, 10, 60, 30))]] * 5)

    class FakeRec:
        def read(self, _frame):
            return next(lecturas)

    orig = svc_mod.make_recognizer
    svc_mod.make_recognizer = lambda _cfg: FakeRec()
    try:
        cfg = {"carpeta_datos": str(tmp), "guardar_capturas": False,
               "reconocimiento": {"votos_requeridos": 3},
               "pluma": {"id": "p1", "driver": "simulado", "pulse_ms": 1, "sentido": "entrada"}}
        csv = Path(__file__).with_name("estudiantes-prueba.csv")
        s = svc_mod.GateService(cfg, backend_csv=str(csv))
        s.backend.start(lambda *_: None)
        abiertas = []
        s.gate.open = lambda why="": abiertas.append(why) or True
        frame = np.zeros((100, 200, 3), np.uint8)
        for _ in range(5):
            s.process_frame(frame)
        assert len(abiertas) == 1, abiertas
        log = (tmp / "bitacora-local.jsonl").read_text(encoding="utf-8")
        assert '"decision": "permitido"' in log and "Mariana" in log
    finally:
        svc_mod.make_recognizer = orig



def test_ordenes_de_lectura_no_abren_la_pluma(tmp_path=None):
    """read (imagen del panel) y snapshot (cámara de la pluma) devuelven placas sin abrir."""
    tmp = Path(tmp_path or "/tmp/lugaria-test-lectura")

    class FakeRec:
        def read(self, frame):
            return [Reading("jkl-123-a", .93, (100, 50, 220, 90))] if frame.mean() > 0 else []

    class FakeCam:
        def __init__(self, frame):
            self.frame = frame

        def latest(self, max_age=2.0):
            return self.frame

    orig = svc_mod.make_recognizer
    svc_mod.make_recognizer = lambda _cfg: FakeRec()
    try:
        cfg = {"carpeta_datos": str(tmp), "guardar_capturas": False, "reconocimiento": {},
               "pluma": {"id": "p1", "driver": "simulado", "pulse_ms": 1}}
        s = svc_mod.GateService(cfg, backend_csv=str(Path(__file__).with_name("estudiantes-prueba.csv")))
        s.backend.start(lambda *_: None)
        abiertas, respuestas = [], []
        s.gate.open = lambda why="": abiertas.append(why) or True
        s.backend.command_result = lambda cid, st, result=None, image=None, note="": \
            respuestas.append((cid, st, result, image, note))

        foto = np.full((480, 1280, 3), 120, np.uint8)
        s.handle_command("c1", {"action": "read", "image": svc_mod.encode_image(foto, 1280)})
        cid, st, result, image, _ = respuestas[-1]
        assert (cid, st) == ("c1", "done") and image is None
        assert result["plates"][0]["text"] == "JKL123A" and result["width"] == 1280

        s.handle_command("c2", {"action": "read", "image": "data:image/jpeg;base64,basura"})
        assert respuestas[-1][1] == "error"

        s.handle_command("c3", {"action": "snapshot"})               # sin cámara (modo prueba)
        assert respuestas[-1][1] == "error" and "cámara" in respuestas[-1][4]

        s.cam = FakeCam(foto)
        s.handle_command("c4", {"action": "snapshot"})
        _, st, result, image, _ = respuestas[-1]
        assert st == "done" and image.startswith("data:image/jpeg;base64,")
        assert len(image) < 300_000 and result["plates"][0]["box"] == [100, 50, 220, 90]
        assert svc_mod.decode_image(image).shape[1] == 960           # reducida para Firestore

        assert abiertas == [], "una orden de lectura nunca debe abrir la pluma"
    finally:
        svc_mod.make_recognizer = orig



def test_apertura_desde_camara_del_panel(tmp_path=None):
    """via=camara-panel: abre la pluma y registra 'permitido' sólo si la pluma también la autoriza."""
    import json
    tmp = Path(tmp_path or "/tmp/lugaria-test-camara")
    cfg = {"carpeta_datos": str(tmp), "guardar_capturas": False, "reconocimiento": {},
           "pluma": {"id": "p1", "driver": "simulado", "pulse_ms": 1, "sentido": "entrada"}}
    orig = svc_mod.make_recognizer
    svc_mod.make_recognizer = lambda _cfg: None
    try:
        s = svc_mod.GateService(cfg, backend_csv=str(Path(__file__).with_name("estudiantes-prueba.csv")))
        s.backend.start(lambda *_: None)
        abiertas, hechas = [], []
        s.gate.open = lambda why="": abiertas.append(why) or True
        s.backend.command_done = lambda cid, st="done", note="": hechas.append((cid, st))
        base = {"action": "open", "via": "camara-panel", "requestedByName": "Admin", "note": "Placa reconocida"}
        s.handle_command("o1", {**base, "plate": "JKL-123-A"})          # Mariana, vigente
        s.handle_command("o2", {**base, "plate": "JPS908B"})            # Carlos, vigencia vencida
        s.handle_command("o3", {"action": "open", "requestedByName": "Guardia", "note": "Visitante"})
        assert len(abiertas) == 3 and hechas == [("o1", "done"), ("o2", "done"), ("o3", "done")]
        log = [json.loads(l) for l in (tmp / "bitacora-local.jsonl").read_text(encoding="utf-8").splitlines()][-3:]
        assert log[0]["decision"] == "permitido" and log[0]["studentName"] == "Mariana López Ruiz"
        assert log[0]["reasonText"] == "Acceso autorizado · cámara del panel" and log[0]["plateOrigin"] == "jalisco"
        assert log[1]["decision"] == "manual" and "Vigencia vencida" in log[1]["reasonText"]
        assert log[2]["decision"] == "manual" and log[2]["reasonText"] == "Visitante"
        assert s.cooldown.active("JKL123A")       # la cámara de la pluma no la vuelve a procesar
    finally:
        svc_mod.make_recognizer = orig


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✔", name)
