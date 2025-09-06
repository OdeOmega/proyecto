import re
import unicodedata
from urllib.parse import urljoin
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import pandas as pd

# === Nuevo: argumentos CLI mínimos ===
import argparse
parser = argparse.ArgumentParser(description="Consulta próximos trenes Ferrovías.")
parser.add_argument("--origen", required=True, help="Estación de origen (p.ej. 'Retiro')")
parser.add_argument("--llegada", required=True, help="Estación de destino (p.ej. 'Cecilia Grierson')")
parser.add_argument("--tipo-dia", type=int, default=1, help="1=Lun a Vie, 2=Sábados, 3=Dom/Feriados")
args = parser.parse_args()

# =========================
# Utilidades
# =========================
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip().lower()

def find_link_by_text(soup: BeautifulSoup, text: str):
    target = norm(text)
    for a in soup.find_all("a"):
        if a.string and norm(a.string) == target:
            return a
    return None

def first_text(el):
    return el.get_text(strip=True) if el else ""

def extract_time(txt: str):
    """
    Devuelve 'HH:MM' si aparece en txt (p.ej. '06:35 hs'), si no None.
    """
    m = re.search(r"\b(\d{1,2}:\d{2})\b", txt)
    return m.group(1) if m else None

# =========================
# Config
# =========================
BASE = "https://www.ferrovias.com.ar/varios/sitemap.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Parámetros "humanos"
ESTACION_ORIGEN = "Retiro"
ESTACION_DESTINO = "Cecilia Grierson"
# 1 = Lun a Vie, 2 = Sábados, 3 = Dom/Feriados (ajusta si difiere)
TIPO_DIA = 1

# === Nuevo: sobrescribir con CLI (mínimo cambio) ===
ESTACION_ORIGEN = args.origen
ESTACION_DESTINO = args.llegada
TIPO_DIA = args.tipo_dia

# =========================
# Sesión y fetch sitemap
# =========================
session = requests.Session()
session.headers.update({"User-Agent": UA})
resp = session.get(BASE, timeout=20)
resp.raise_for_status()
# A veces sitios antiguos declaran latin-1; fuerza lo detectado si es necesario
resp.encoding = resp.apparent_encoding or resp.encoding

soup = BeautifulSoup(resp.text, "html.parser")
lnk = find_link_by_text(soup, "Horarios")
if not lnk:
    raise RuntimeError("No encontré el link 'Horarios' en el sitemap.")

href_abs = urljoin(BASE, lnk.get("href", ""))
resp2 = session.get(href_abs, timeout=20)
resp2.raise_for_status()
resp2.encoding = resp2.apparent_encoding or resp2.encoding
soup2 = BeautifulSoup(resp2.text, "html.parser")

# =========================
# Descubrir el form y mapear estaciones -> valores
# =========================
form = soup2.find("form")
if not form:
    raise RuntimeError("No encontré el formulario de consulta de horarios.")

action = urljoin(resp2.url, form.get("action", ""))

# Buscar selects por nombre probable (ajusta si el sitio usa otros names)
select_origen = form.find(["select"], attrs={"name": re.compile(r"estacion_o", re.I)})
select_destino = form.find(["select"], attrs={"name": re.compile(r"estacion_d", re.I)})
select_tipo = form.find(["select"], attrs={"name": re.compile(r"tipo_dia", re.I)})

if not (select_origen and select_destino and select_tipo):
    raise RuntimeError("No pude identificar los selects de origen/destino/tipo_dia.")

def option_value_for(select, visible_text):
    tgt = norm(visible_text)
    for opt in select.find_all("option"):
        label = first_text(opt)
        if norm(label) == tgt:
            return opt.get("value", "").strip()
    # Si no match exacto, intenta contains
    for opt in select.find_all("option"):
        if tgt in norm(first_text(opt)):
            return opt.get("value", "").strip()
    raise RuntimeError(f"No encontré la opción '{visible_text}' en el select.")

val_origen = option_value_for(select_origen, ESTACION_ORIGEN)
val_destino = option_value_for(select_destino, ESTACION_DESTINO)

# Selecciona tipo de día por value si existe, si no por índice visible
def tipo_dia_value(select, tipo_int: int):
    # primero intenta value exacto "1"/"2"/"3"
    candidate = str(tipo_int)
    for opt in select.find_all("option"):
        if (opt.get("value") or "").strip() == candidate:
            return candidate
    # fallback heurístico por texto
    mapping = {
        1: ("lunes", "viernes"),
        2: ("sabado",),
        3: ("domingo", "feriado"),
    }
    keys = mapping.get(tipo_int, ())
    for opt in select.find_all("option"):
        txt = norm(first_text(opt))
        if all(k in txt for k in keys):
            return opt.get("value", "").strip()
    # último recurso: primera opción
    return (select.find("option").get("value") or "").strip()

val_tipo = tipo_dia_value(select_tipo, TIPO_DIA)

# Identificar names de hora-desde / hora-hasta por patrón
input_hora_d = form.find(["input","select"], attrs={"name": re.compile(r"hora_d", re.I)})
input_hora_h = form.find(["input","select"], attrs={"name": re.compile(r"hora_h", re.I)})

hora_desde = "00:00"
hora_hasta = "23:59"

payload = {
    select_origen.get("name"): val_origen,
    select_destino.get("name"): val_destino,
    select_tipo.get("name"): val_tipo,
    (input_hora_d.get("name") if input_hora_d else "hora_d"): hora_desde,
    (input_hora_h.get("name") if input_hora_h else "hora_h"): hora_hasta,
}

# Algunos sitios requieren coords de un "image submit"; agrega claves si existen
# Busca el botón submit por name
submit = form.find("input", attrs={"type": "image"}) or form.find("input", attrs={"type": "submit"})
if submit and submit.get("type") == "image":
    name = submit.get("name", "Consultar")
    payload[name + ".x"] = "10"
    payload[name + ".y"] = "10"

headers = {"Referer": resp2.url, "Origin": requests.utils.urlparse(resp2.url).scheme + "://" + requests.utils.urlparse(resp2.url).netloc}

resp3 = session.post(action, data=payload, headers=headers, timeout=30)
resp3.raise_for_status()
resp3.encoding = resp3.apparent_encoding or resp3.encoding
soup3 = BeautifulSoup(resp3.text, "html.parser")

# =========================
# Detectar tabla de horarios de forma robusta
# =========================
def is_schedule_table(tbl):
    # toma primera fila con headers (th) o data (td)
    head = tbl.find("tr")
    if not head:
        return False
    cells = [first_text(td).lower() for td in head.find_all(["th","td"])]
    cells_norm = [norm(c) for c in cells]
    return any("salida" in c for c in cells_norm) and any("llegada" in c for c in cells_norm)

tables = soup3.find_all("table")
tabla = next((t for t in tables if is_schedule_table(t)), None)

# Fallback con pandas si no detecta por BS4
if not tabla:
    dfs = pd.read_html(resp3.text, flavor="bs4")
    # busca df que tenga columnas similares
    def looks_like(df):
        cols = [norm(str(c)) for c in df.columns]
        return any("salida" in c for c in cols) and any("llegada" in c for c in cols)
    df = next((d for d in dfs if looks_like(d)), None)
else:
    rows = tabla.find_all("tr")
    header_cells = [first_text(c) for c in rows[0].find_all(["th","td"])]
    body = []
    for tr in rows[1:]:
        cells = [first_text(td) for td in tr.find_all("td")]
        if not cells:
            continue
        body.append(cells)
    df = pd.DataFrame(body, columns=[norm(h) for h in header_cells])

if df is None or df.empty:
    raise RuntimeError("No se encontró la tabla de horarios en la respuesta.")

# Filtra columnas relevantes y normaliza tiempos
# Intenta mapear distintas variantes de encabezados
col_salida = next((c for c in df.columns if "salida" in norm(str(c))), None)
col_llegada = next((c for c in df.columns if "llegada" in norm(str(c))), None)
if not (col_salida and col_llegada):
    raise RuntimeError(f"No identifiqué columnas de salida/llegada. Encabezados: {list(df.columns)}")

df_clean = df[[col_salida, col_llegada]].copy()
df_clean.columns = ["salida", "llegada"]

df_clean["salida_hhmm"] = df_clean["salida"].map(lambda x: extract_time(str(x)))
df_clean["llegada_hhmm"] = df_clean["llegada"].map(lambda x: extract_time(str(x)))
df_clean = df_clean.dropna(subset=["salida_hhmm"])

# Convierte a datetimes del día de hoy en Buenos Aires
tz = ZoneInfo("America/Argentina/Buenos_Aires")
today = datetime.now(tz).date()

def to_dt_today(hhmm: str) -> datetime:
    hh, mm = map(int, hhmm.split(":"))
    return datetime(today.year, today.month, today.day, hh, mm, tzinfo=tz)

df_clean["salida_dt"] = df_clean["salida_hhmm"].map(to_dt_today)

# Maneja servicios que pasan medianoche (si llegada < salida, asume +1 día)
def llegada_dt(row):
    if pd.isna(row["llegada_hhmm"]):
        return pd.NaT
    out = to_dt_today(row["salida_hhmm"])
    inn = to_dt_today(row["llegada_hhmm"])
    if inn < out:
        inn = inn + timedelta(days=1)
    return inn

df_clean["llegada_dt"] = df_clean.apply(llegada_dt, axis=1)

# Próximas 3 salidas
now = datetime.now(tz)
proximos = (df_clean[df_clean["salida_dt"] >= now]
            .sort_values("salida_dt")
            .head(3)
            .loc[:, ["salida_hhmm", "llegada_hhmm"]]
            .rename(columns={"salida_hhmm": "salida", "llegada_hhmm": "llegada"}))

print("Primeras filas del DataFrame limpio:")
print(df_clean[["salida_hhmm","llegada_hhmm"]].head())

print("\nPróximos 3 trenes desde", ESTACION_ORIGEN, "hacia", ESTACION_DESTINO, "hoy", today.isoformat(), "(hora local):")
if proximos.empty:
    print("No hay más salidas para el rango seleccionado.")
else:
    print(proximos.to_string(index=False))
