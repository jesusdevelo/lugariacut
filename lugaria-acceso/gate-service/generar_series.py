"""
Genera series-mx.json (gate-service/ y web/) a partir de la NOM-001-SCT-2-2016.
Si la SICT publica nuevas series, edita las tablas de abajo y ejecuta:
    python generar_series.py
"""
import json
STATES = {
 "AGS":"Aguascalientes","BC":"Baja California","BCS":"Baja California Sur","CAMP":"Campeche","CHIS":"Chiapas",
 "CHIH":"Chihuahua","COAH":"Coahuila","COL":"Colima","CDMX":"Ciudad de México","DGO":"Durango","GTO":"Guanajuato",
 "GRO":"Guerrero","HGO":"Hidalgo","JAL":"Jalisco","MEX":"Estado de México","MICH":"Michoacán","MOR":"Morelos",
 "NAY":"Nayarit","NL":"Nuevo León","OAX":"Oaxaca","PUE":"Puebla","QRO":"Querétaro","QROO":"Quintana Roo",
 "SLP":"San Luis Potosí","SIN":"Sinaloa","SON":"Sonora","TAB":"Tabasco","TAMPS":"Tamaulipas","TLAX":"Tlaxcala",
 "VER":"Veracruz","YUC":"Yucatán","ZAC":"Zacatecas"}
# Transporte privado, automóviles: AAA-000-A
AUTOS = """AGS AAA AFZ|BC AGA CYZ|BCS CZA DEZ|CAMP DFA DKZ|CHIS DLA DSZ|CHIH DTA ETZ|COAH EUA FPZ|COL FRA FWZ|DGO FXA GFZ|
MEX LGA PEZ|GTO GGA GYZ|GRO GZA HFZ|HGO HGA HRZ|JAL HSA LFZ|MICH PFA PUZ|MOR PVA RDZ|NAY REA RJZ|NL RKA TGZ|
OAX THA TMZ|PUE TNA UJZ|QRO UKA UPZ|QROO URA UVZ|SLP UWA VEZ|SIN VFA VSZ|SON VTA WKZ|TAB WLA WWZ|TAMPS WXA XSZ|
TLAX XTA XXZ|VER XYA YVZ|YUC YWA ZCZ|ZAC ZDA ZHZ"""
# Transporte privado, camiones: AA-0000-A  (Morelos aparece como "UN" en el DOF; se corrige a NU)
CAMIONES = """AGS AA AF|BC AG CD|BCS CE CL|CAMP CM CU|CHIS CV DC|CHIH DD EG|COAH EH FB|COL FC FJ|DGO FK FX|MEX KL MS|
GTO FY GW|GRO GX HG|HGO HH HT|JAL HU KK|MICH MT NT|MOR NU NZ|NAY PA PG|NL PH RP|OAX RR RY|PUE RZ SR|QRO SS SY|
QROO SZ TB|SLP TC TP|SIN TR UL|SON UM VK|TAB VL VT|TAMPS VU WX|TLAX WY XE|VER XF YM|YUC YN YU|ZAC YV ZJ"""
def rows(s): return [r.split() for r in s.replace("\n","").split("|") if r.strip()]

series = []
for st, a, b in rows(AUTOS):
    series.append({"state":st,"kind":"automovil","format":"LLL-DDD-L","from":a+"001A","to":b+"999Z","era":"actual"})
    # Formato anterior (ABC-12-34) con las mismas series de tres letras asignadas desde 1992
    series.append({"state":st,"kind":"automovil","format":"LLL-DD-DD","from":a+"0101","to":b+"9999","era":"anterior"})
for st, a, b in rows(CAMIONES):
    series.append({"state":st,"kind":"camion","format":"LL-DDDD-L","from":a+"0001A","to":b+"9999Z","era":"actual"})
series += [
  {"state":"CDMX","kind":"automovil","format":"LDD-LLL","from":"A01AAA","to":"Z99ZZZ","era":"actual"},
  {"state":"CDMX","kind":"automovil","format":"DDD-LLL","from":"001AAA","to":"999ZZZ","era":"anterior"},
  {"state":"CDMX","kind":"camion","format":"L-DDD-LL","from":"A001AA","to":"Z999ZZ","era":"actual"},
  # Jalisco: series específicas
  {"state":"JAL","kind":"motocicleta","format":"LDDLL","from":"M01AA","to":"M99ZZ","era":"actual"},
  {"state":"JAL","kind":"motocicleta","format":"LDLLD","from":"L1CK1","to":"L9VW9","era":"actual"},
  {"state":"JAL","kind":"capacidades_diferentes","format":"DD-LLL","from":"01HLW","to":"99JBA","era":"actual"},
  {"state":"JAL","kind":"ecologico","format":"DDL-DDD","from":"02N001","to":"08R999","era":"actual"},
]
data = {
  "_fuente": "NOM-001-SCT-2-2016, Apéndice C normativo (DOF 24/06/2016). Transporte privado.",
  "_reglas": "Letras sin I, Ñ, O, Q. Orden de la serie: primero las letras (en su orden), después los dígitos. Ningún grupo de dígitos puede ser todo ceros.",
  "alphabet": "ABCDEFGHJKLMNPRSTUVWXYZ",
  "home": "JAL",
  "states": STATES,
  "kinds": {"automovil":"automóvil","camion":"camión / carga","motocicleta":"motocicleta",
            "capacidades_diferentes":"capacidades diferentes","ecologico":"vehículo ecológico"},
  "typeKinds": {"auto":["automovil","capacidades_diferentes","ecologico"],
                "camioneta":["automovil","camion","capacidades_diferentes","ecologico"],
                "pickup":["camion","capacidades_diferentes","ecologico"],
                "motocicleta":["motocicleta"]},
  "series": series,
}
out = json.dumps(data, ensure_ascii=False, indent=1)
from pathlib import Path
here = Path(__file__).resolve().parent
for p in [here / "series-mx.json", here.parent / "web" / "series-mx.json"]:
    open(p, "w", encoding="utf-8").write(out + "\n")
print(len(series), "series")
