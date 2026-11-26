"""
Audita las placas de un CSV del padrón (misma plantilla que el panel) antes de importarlo.

  python verificar_placas.py estudiantes.csv
  python verificar_placas.py estudiantes.csv --salida reporte.csv

Muestra cuántas placas son de Jalisco y lista las que conviene revisar.
"""
import argparse
import csv
from collections import Counter

from placas_mx import verify

TIPOS = {"auto", "camioneta", "pickup", "motocicleta"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--salida", help="Escribe el resultado de cada placa en este CSV")
    a = ap.parse_args()

    filas = []
    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("placa"):
                continue
            tipo = row.get("tipo", "").lower()
            tipo = tipo if tipo in TIPOS else "auto"
            v = verify(row["placa"], tipo)
            filas.append({"matricula": row.get("matricula", ""), "nombre": row.get("nombre", ""),
                          "placa": row["placa"], "tipo": tipo, "resultado": v.result,
                          "estado": v.state_name, "detalle": v.message, "sugerencia": v.suggestion})

    cuenta = Counter(f["resultado"] for f in filas)
    print(f"{len(filas)} placas revisadas")
    for k, n in cuenta.most_common():
        print(f"  {k:18} {n}")
    revisar = [f for f in filas if f["resultado"] != "jalisco_valida"]
    if revisar:
        print("\nPara revisar:")
        for f in revisar:
            sug = f"  ¿{f['sugerencia']}?" if f["sugerencia"] else ""
            print(f"  {f['placa']:11} {f['matricula']:10} {f['resultado']:17} {f['detalle']}{sug}")
    if a.salida:
        with open(a.salida, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0]) if filas else ["placa"])
            w.writeheader()
            w.writerows(filas)
        print(f"\nReporte guardado en {a.salida}")


if __name__ == "__main__":
    main()
