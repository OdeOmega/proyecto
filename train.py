import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import unicodedata
import pandas as pd 
from datetime import datetime

def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.strip().lower()

URL = "https://www.ferrovias.com.ar/varios/sitemap.php"
response = requests.get(URL)

soup = BeautifulSoup(response.content, "html.parser")

link = soup.find("a", string="Horarios")

if link:
    href_rel = link["href"]
    href_abs = urljoin(URL, href_rel)
    print("Voy a entrar a:", href_abs)

    response2 = requests.get(href_abs)
    soup2 = BeautifulSoup(response2.text, "html.parser")
    print("Título de la nueva página:", soup2.title.string if soup2.title else None)
else:
    print("No encontré el link 'Horarios'")

form = soup2.find("form")
action = urljoin(response2.url, form.get("action", "")) if form else href_abs

payload = {
    "estacion_o": "1",       # Retiro
    "estacion_d": "22",      # Cecilia Grierson
    "tipo_dia": "1",         # Lunes a Viernes
    "hora_d": "00:00",
    "hora_h": "23:00",
    "Consultar.x": "10",     # simular click
    "Consultar.y": "10",
}

headers = {"Referer": response2.url}
response3 = requests.post(action, data=payload, headers=headers)
soup3 = BeautifulSoup(response3.text, "html.parser")

# === Buscar tabla de horarios ===
def es_tabla_horarios(tabla):
    primera_fila = tabla.find("tr")
    if not primera_fila:
        return False
    celdas = [td.get_text(strip=True).lower() for td in primera_fila.find_all("td")]
    return len(celdas) >= 2 and "salida" in celdas[0] and "llegada" in celdas[1]

tablas = soup3.find_all("table")
tabla_horarios = None
for t in tablas:
    if es_tabla_horarios(t):
        tabla_horarios = t
        break

if tabla_horarios:
    filas = tabla_horarios.find_all("tr")
    horarios = []
    for tr in filas[1:]:  # salto encabezado
        celdas = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(celdas) >= 2:
            horarios.append({"salida": celdas[0], "llegada": celdas[1]})
    # === Guardar en DataFrame ===
    df_horarios = pd.DataFrame(horarios, columns=["salida", "llegada"])
    print(df_horarios.head())
else:
    print("No se encontró la tabla de horarios")
    df_horarios = pd.DataFrame(columns=["salida", "llegada"])


ahora = datetime.now().time()
df_horarios["salida_time"] = pd.to_datetime(df_horarios["salida"].str.replace(" hs", ""), format="%H:%M").dt.time
proximos = df_horarios[df_horarios["salida_time"] >= ahora].head(3)

print(proximos[["salida", "llegada"]])