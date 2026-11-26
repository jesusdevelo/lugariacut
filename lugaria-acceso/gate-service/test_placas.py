"""
Pruebas del verificador de placas.  python test_placas.py   (o pytest)

1. Casos conocidos (bordes de cada serie de Jalisco, otros estados, errores de captura).
2. Las expresiones de firestore.rules (jaliscoValida) aprueban exactamente las mismas
   placas que el verificador de Python, sobre ~200 000 placas generadas.
3. Si Node.js está instalado: web/placas-mx.js da el mismo resultado que Python.
"""
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

from placas_mx import PlateVerifier, verify

ROOT = Path(__file__).resolve().parent.parent
V = PlateVerifier()
L = V.alpha


def test_casos_conocidos():
    casos = [
        ("JKL-123-A", "auto", "jalisco_valida"),
        ("HSA-001-A", "auto", "jalisco_valida"),       # primera de la serie
        ("LFZ-999-Z", "auto", "jalisco_valida"),       # última de la serie
        ("HRZ-999-Z", "auto", "foranea"),          # justo antes: Hidalgo
        ("LGA-001-A", "auto", "foranea"),          # justo después: Edo. de México
        ("JKL-000-A", "auto", "foranea"),        # 000 no se usa
        ("JKL-123-A", "pickup", "tipo_distinto"),
        ("JKL-123-A", "camioneta", "jalisco_valida"),
        ("HU-0001-A", "pickup", "jalisco_valida"),
        ("KK-9999-Z", "pickup", "jalisco_valida"),
        ("KL-0001-A", "pickup", "foranea"),
        ("JS-1234-A", "auto", "tipo_distinto"),
        ("JKL-12-34", "auto", "formato_anterior"),
        ("M01AA", "motocicleta", "jalisco_valida"),
        ("L1CK1", "motocicleta", "jalisco_valida"),
        ("L9VW9", "motocicleta", "jalisco_valida"),
        ("L1CJ1", "motocicleta", "foranea"),
        ("L9VX1", "motocicleta", "foranea"),
        ("01HLW", "auto", "jalisco_valida"),           # capacidades diferentes
        ("02N001", "camioneta", "jalisco_valida"),     # ecológico
        ("01N999", "auto", "foranea"),           # ecológico de otro estado: fuera de la tabla
        ("A12-BCD", "auto", "foranea"),            # Ciudad de México
        ("JIL-123-A", "auto", "foranea"),        # I no se usa
        ("ABC", "auto", "invalida"),
        ("ESCUELA", "auto", "invalida"),
    ]
    for plate, t, want in casos:
        got = verify(plate, t)
        assert got.result == want, (plate, t, got.result, want)
    assert verify("JKL-1O3-A").suggestion == "JKL-103-A"
    assert verify("JS-1234-A", "auto").suggestion == ""       # no "corrige" placas reales
    assert verify("PGA-123-B").state_name == "Michoacán" and verify("PGA-123-B").foreign
    assert verify("PGA-123-B").message == "Placa foránea de Michoacán (automóvil)."
    assert verify("XX-999").foreign and verify("XX-999").state == ""          # placa de otro país o formato raro


def _rules_regexes():
    rules = (ROOT / "firestore.rules").read_text(encoding="utf-8")
    block = rules.split("BEGIN SERIES-JALISCO")[1].split("END SERIES-JALISCO")[0]
    out = {}
    for name, body in re.findall(r"function (jal\w+)\(p\)\s*\{(.*?)\}\s*(?=function|//)", block, re.S):
        out[name] = [re.compile(r) for r in re.findall(r"p\.matches\('([^']+)'\)", body)]
    return out


def _rules_ok(rx, p, t):
    m = lambda n: any(r.fullmatch(p) for r in rx[n])
    return {"auto": m("jalAuto") or m("jalCapDif") or m("jalEco"),
            "camioneta": m("jalAuto") or m("jalCamion") or m("jalCapDif") or m("jalEco"),
            "pickup": m("jalCamion") or m("jalCapDif") or m("jalEco"),
            "motocicleta": m("jalMoto")}[t]


def _muestras(n=40_000, seed=7):
    rnd = random.Random(seed)
    alfa = L + "IOQ"
    fmts = {s["tpl"] for s in V.series}
    out = []
    for _ in range(n):
        tpl = rnd.choice(sorted(fmts))
        p = "".join(rnd.choice(alfa) if c == "L" else rnd.choice("0123456789") for c in tpl)
        if rnd.random() < 0.5:       # sesgar hacia el rango de Jalisco
            s = rnd.choice([s for s in V.series if s["state"] == "JAL"])
            base = s["from"] if rnd.random() < .5 else s["to"]
            p = "".join(ch if rnd.random() < .7 else (rnd.choice(alfa) if ch.isalpha() else rnd.choice("0123456789"))
                        for ch in base)
        out.append(p)
    # bordes exactos de cada serie de Jalisco
    for s in V.series:
        if s["state"] == "JAL":
            out += [s["from"], s["to"]]
    return out


def test_reglas_firestore_igual_que_python():
    rx = _rules_regexes()
    assert set(rx) == {"jalAuto", "jalCamion", "jalMoto", "jalCapDif", "jalEco"}, rx.keys()
    malos = []
    for p in _muestras():
        for t in ("auto", "camioneta", "pickup", "motocicleta"):
            py = V.verify(p, t).auto_approve
            if py != _rules_ok(rx, p, t):
                malos.append((p, t, py))
    assert not malos, malos[:20]


def test_javascript_igual_que_python():
    node = shutil.which("node")
    if not node:
        print("   (Node.js no está instalado: se omite la comparación con placas-mx.js)")
        return
    muestras = _muestras(n=8_000, seed=11)
    tipos = ("auto", "camioneta", "pickup", "motocicleta")
    js = f"""
      const fs = require('fs');
      import('{(ROOT / 'web' / 'placas-mx.js').as_uri()}').then(m => {{
        const v = m.createVerifier(JSON.parse(fs.readFileSync('{ROOT / 'web' / 'series-mx.json'}', 'utf8')));
        const plates = JSON.parse(fs.readFileSync(0, 'utf8'));
        const out = [];
        for (const p of plates) for (const t of {json.dumps(tipos)}) {{
          const r = v.verify(p, t); out.push([r.result, r.display, r.suggestion, r.message]);
        }}
        process.stdout.write(JSON.stringify(out));
      }});"""
    res = subprocess.run([node, "-e", js], input=json.dumps(muestras), capture_output=True, text=True, check=True)
    got = iter(json.loads(res.stdout))
    malos = []
    for p in muestras:
        for t in tipos:
            r = V.verify(p, t)
            j = next(got)
            if [r.result, r.display, r.suggestion, r.message] != j:
                malos.append((p, t, r.result, j))
    assert not malos, malos[:10]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✔", name)
