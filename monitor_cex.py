import os
import re
import json
import time
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API_BASE = "https://wss2.cex.es.webuy.io/v3"
WEB_BASE = "https://es.webuy.com"
SITEMAP_INDEX = f"{WEB_BASE}/sitemap.xml"

# Feeds públicos (productos calientes / novedades)
FEEDS = ("hotproducts", "topsellers", "mostwanted")

# Filtros sobre la ruta de imagen del sitemap (categorías interesantes)
FILTROS_INCLUIR = (
    "iphone",
    "android",
    "moviles -",
    "móviles -",
    "telefonos moviles",
    "teléfonos moviles",
    "ipad",
    "portatil",
    "portátil",
    "portatiles",
    "portátiles",
    "macbook",
    "ps5",
    "switch",
    "xbox series",
)
FILTROS_EXCLUIR = (
    "accesor",
    "accessor",
    "cable",
    "basics",
    "mandos",
    "dvd portatil",
)

SITEMAPS_PRODUCTOS = [
    f"{WEB_BASE}/sitemaps/cex/es/sitemap-es-products-{i}.xml"
    for i in range(1, 6)
]

ARCHIVO_HISTORIAL = "vistos.json"
ARCHIVO_ESTADO = "estado.json"

WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE", "34613484447")
WHATSAPP_APIKEY = os.getenv("WHATSAPP_APIKEY", "4010754")

# Rechequeo de precios vía /detail (este endpoint SÍ funciona).
# Ritmo contenido: la API corta con 403 si hay demasiados hilos.
# 4000 SKUs × 12 corridas/día (cada 2 h) ≈ catálogo completo en ~1 día.
RECHECK_POR_CORRIDA = 4000
WORKERS_DETAIL = 4
# Máximo de peticiones /detail en vuelo + pausa entre arranques
MAX_IN_FLIGHT = 3
PAUSA_ENTRE_DETAIL = 0.08

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Origin": WEB_BASE,
    "Referer": f"{WEB_BASE}/",
})

_THREAD_LOCAL = threading.local()
_HISTORIAL_LOCK = threading.Lock()
_DETAIL_GATE = threading.Semaphore(MAX_IN_FLIGHT)
_DETAIL_PACING = threading.Lock()
_LAST_DETAIL_AT = 0.0


def _session():
    """Session por hilo (requests.Session no es thread-safe)."""
    s = getattr(_THREAD_LOCAL, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(SESSION.headers)
        _THREAD_LOCAL.session = s
    return s


def _pace_detail():
    """Evita ráfagas que provocan 403/429 en la API CeX."""
    global _LAST_DETAIL_AT
    with _DETAIL_PACING:
        now = time.time()
        wait = PAUSA_ENTRE_DETAIL - (now - _LAST_DETAIL_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_DETAIL_AT = time.time()

RE_GRADO_FINAL = re.compile(
    r"(?:,\s*)?(Perfecto|Bueno|Razonable|Aceptable|A\+|A|B|C)\s*$",
    re.IGNORECASE,
)
RE_SITEMAP_ITEM = re.compile(
    r"product-detail/\?id=([^<]+)</loc>\s*"
    r"<image:image>\s*<image:loc>([^<]+)</image:loc>",
    re.IGNORECASE,
)


def enviar_whatsapp(mensaje):
    """Envía un mensaje formateado por WhatsApp mediante CallMeBot."""
    mensaje_encoded = urllib.parse.quote(mensaje)
    url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={WHATSAPP_PHONE}&text={mensaje_encoded}&apikey={WHATSAPP_APIKEY}"
    )
    try:
        r = SESSION.get(url, timeout=15)
        # CallMeBot: 200 = enviado; 210 = en cola por rate-limit (también OK)
        if r.status_code in (200, 210):
            print("📲 Alerta enviada/encolada por WhatsApp correctamente.")
        else:
            print(f"⚠️ Error enviando WhatsApp: {r.status_code} | {r.text[:160]}")
    except Exception as e:
        print(f"❌ Error al enviar mensaje: {e}")


def api_get(path, params=None, timeout=25, raw_query=None):
    """GET a la API WeBuy ES. Devuelve data o None si falla."""
    status, data = api_get_result(path, params=params, timeout=timeout, raw_query=raw_query)
    return data if status == "ok" else None


def api_get_result(path, params=None, timeout=25, raw_query=None):
    """GET con estado: ok | not_found | error (no marcar N/A si es error temporal)."""
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    if raw_query:
        url = f"{url}?{raw_query}"
        params = None
    try:
        r = _session().get(url, params=params, timeout=timeout)
        if r.status_code in (403, 429) or r.status_code >= 500:
            return "error", None
        if r.status_code == 404:
            return "not_found", None
        if r.status_code != 200:
            return "error", None
        if "application/json" not in (r.headers.get("content-type") or ""):
            return "error", None
        payload = r.json()
        resp = payload.get("response") or {}
        if resp.get("ack") != "Success":
            return "not_found", None
        return "ok", resp.get("data")
    except Exception as e:
        print(f"⚠️ API error ({path}): {e}")
        return "error", None


def formatear_precio(valor):
    if valor is None:
        return "N/A"
    try:
        num = float(valor)
        if num.is_integer():
            return f"{int(num)} €"
        return f"{num:.2f} €".replace(".", ",")
    except (TypeError, ValueError):
        return str(valor)


def extraer_grado(box):
    """Grado/condición desde el nombre o atributos del producto."""
    nombre = (box.get("boxName") or "").strip()
    m = RE_GRADO_FINAL.search(nombre)
    if m:
        g = m.group(1)
        return g.upper() if len(g) <= 2 else g.capitalize()

    for attr in box.get("attributeInfo") or []:
        friendly = (attr.get("attributeFriendlyName") or "").lower()
        name = (attr.get("attributeName") or "").lower()
        if "condici" in friendly or "condition" in name or "grade" in name:
            vals = attr.get("attributeValue") or []
            if vals:
                return str(vals[0])
    return "N/A"


def link_producto(box_id, category_name=None):
    params = {"id": box_id}
    if category_name:
        params["categoryName"] = category_name
    return f"{WEB_BASE}/product-detail?{urllib.parse.urlencode(params)}"


def normalizar_box(box):
    """Unifica feeds y detalle en una ficha común."""
    box_id = box.get("boxId")
    if not box_id:
        return None
    precio_raw = box.get("sellPrice")
    return {
        "sku": str(box_id),
        "nombre": (box.get("boxName") or "").strip(),
        "precio": formatear_precio(precio_raw),
        "precio_num": precio_raw,
        "grado": extraer_grado(box),
        "categoria": box.get("categoryFriendlyName") or box.get("categoryName") or "",
        "super_cat": box.get("superCatFriendlyName") or box.get("superCatName") or "",
        "link": link_producto(box_id, box.get("categoryName")),
        "out_of_stock": bool(box.get("outOfStock") or box.get("outOfEcomStock")),
    }


def cargar_json(ruta, default):
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error cargando {ruta} ({e})")
    return default


def guardar_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def categoria_interesante(ruta_imagen):
    ruta = urllib.parse.unquote(ruta_imagen or "").lower()
    # Corregir mojibake habitual del sitemap (MÃ³viles -> móviles)
    try:
        ruta = ruta.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if any(f in ruta for f in FILTROS_EXCLUIR):
        return False
    return any(f in ruta for f in FILTROS_INCLUIR)


def descubrir_desde_sitemap(sitemap_url):
    """Extrae SKUs interesantes de un sitemap de productos CeX."""
    try:
        r = SESSION.get(sitemap_url, timeout=120)
        if r.status_code != 200:
            print(f"⚠️ Sitemap no disponible: {sitemap_url} ({r.status_code})")
            return []
        encontrados = []
        for sku, img in RE_SITEMAP_ITEM.findall(r.text):
            if categoria_interesante(img):
                # categoría aproximada desde la ruta de imagen
                partes = urllib.parse.unquote(img).split("/product_images/")
                categoria = ""
                if len(partes) > 1:
                    trozos = partes[1].split("/")
                    if len(trozos) >= 2:
                        categoria = f"{trozos[0]} / {trozos[1]}"
                encontrados.append({
                    "sku": sku,
                    "categoria": categoria,
                    "link": link_producto(sku),
                })
        print(f"🗺️ Sitemap {sitemap_url.split('/')[-1]}: {len(encontrados)} SKUs interesantes")
        return encontrados
    except Exception as e:
        print(f"⚠️ Error leyendo sitemap: {e}")
        return []


def fetch_feed(nombre):
    data = api_get(f"/boxlists/{nombre}")
    if not data:
        print(f"⚠️ Feed {nombre} no disponible")
        return []
    boxes = data.get("boxlistsBoxes") or []
    print(f"📦 Feed {nombre}: {len(boxes)} productos")
    return boxes


def fetch_detalle(sku):
    data = api_get(f"/boxes/{sku}/detail")
    if not data:
        return None
    details = data.get("boxDetails") or []
    return details[0] if details else None


def fetch_detalle_result(sku):
    """Devuelve (status, box|None) con status ok|not_found|error."""
    _pace_detail()
    with _DETAIL_GATE:
        status, data = api_get_result(f"/boxes/{sku}/detail")
    if status != "ok":
        return status, None
    details = (data or {}).get("boxDetails") or []
    if not details:
        return "not_found", None
    return "ok", details[0]


def precio_valido(precio):
    return bool(precio) and precio not in ("N/A", "PENDIENTE", "")


def procesar_ficha(ficha, vistos, avisos, avisar_nuevo=False):
    """Actualiza historial y avisa de novedad (opcional) o cambio de precio."""
    sku = ficha["sku"]
    precio = ficha["precio"]
    entrada = {
        "precio": precio,
        "precio_num": ficha.get("precio_num"),
        "nombre": ficha.get("nombre") or "",
        "grado": ficha.get("grado") or "N/A",
        "categoria": ficha.get("categoria") or "",
        "link": ficha.get("link") or link_producto(sku),
        "origen": ficha.get("origen") or "api",
    }

    with _HISTORIAL_LOCK:
        if sku not in vistos:
            if avisar_nuevo and precio_valido(precio):
                print(f"🆕 Nuevo: {entrada['nombre']} ({precio}) [SKU {sku}]")
                msg = (
                    f"📦 [CeX] Producto nuevo: {entrada['nombre']}\n"
                    f"🔢 SKU: {sku}\n"
                    f"🏷️ Grado: {entrada['grado']}\n"
                    f"📁 Categoría: {entrada['categoria'] or 'N/A'}\n"
                    f"💰 Precio: {precio}\n\n"
                    f"🔗 Link: {entrada['link']}"
                )
                enviar_whatsapp(msg)
                avisos["nuevos"] += 1
                time.sleep(1)
            vistos[sku] = entrada
            return

        prev = vistos[sku] if isinstance(vistos[sku], dict) else {}
        precio_prev = prev.get("precio")

        # Primera vez que obtenemos precio real de un SKU descubierto por sitemap
        if not precio_valido(precio_prev) and precio_valido(precio):
            vistos[sku] = {**prev, **entrada}
            return

        if (
            precio_valido(precio_prev)
            and precio_valido(precio)
            and precio != precio_prev
        ):
            nombre = entrada["nombre"] or prev.get("nombre") or sku
            print(f"📉 Cambio: {nombre} ({precio_prev} ➡️ {precio}) [SKU {sku}]")
            msg = (
                f"📉 [CeX] ¡CAMBIO DE PRECIO! 📉\n\n"
                f"📦 Producto: {nombre}\n"
                f"🔢 SKU: {sku}\n"
                f"🏷️ Grado: {entrada['grado']}\n"
                f"💵 Precio anterior: {precio_prev}\n"
                f"💰 Nuevo precio: {precio}\n\n"
                f"🔗 Link: {entrada['link']}"
            )
            enviar_whatsapp(msg)
            avisos["cambios"] += 1
            time.sleep(1)

        # Conservar nombre previo si el detalle viniera vacío
        if not entrada["nombre"] and prev.get("nombre"):
            entrada["nombre"] = prev["nombre"]
        vistos[sku] = {**prev, **entrada}


def _recheck_sku(sku, vistos, avisos):
    """Obtiene detalle de un SKU y actualiza historial (seguro entre hilos)."""
    status, detalle = fetch_detalle_result(sku)
    if status == "error":
        # Fallo temporal (403/429/red): no tocar historial, se reintenta luego
        return "error"
    if status == "not_found" or not detalle:
        with _HISTORIAL_LOCK:
            prev = vistos.get(sku) if isinstance(vistos.get(sku), dict) else {}
            if not precio_valido(prev.get("precio")) or prev.get("precio") == "PENDIENTE":
                prev = {**prev, "precio": "N/A", "precio_num": None}
                vistos[sku] = prev
        return "fail"
    ficha = normalizar_box(detalle)
    if ficha:
        ficha["origen"] = "detail"
        procesar_ficha(ficha, vistos, avisos, avisar_nuevo=False)
        return "ok"
    return "fail"


def comprobar_tienda():
    vistos = cargar_json(ARCHIVO_HISTORIAL, {})
    estado = cargar_json(ARCHIVO_ESTADO, {
        "sitemap_index": 0,
        "recheck_offset": 0,
    })

    avisos = {"nuevos": 0, "cambios": 0, "sitemap_nuevos": 0}
    habia_historial = len(vistos) > 0

    # ----------------------------------------------------
    # FASE 1: Descubrir catálogo interesante vía sitemap
    # ----------------------------------------------------
    print("🔄 Fase 1: descubrimiento por sitemap")
    sm_idx = int(estado.get("sitemap_index", 0)) % len(SITEMAPS_PRODUCTOS)
    sitemap_url = SITEMAPS_PRODUCTOS[sm_idx]
    descubiertos = descubrir_desde_sitemap(sitemap_url)
    for item in descubiertos:
        sku = item["sku"]
        if sku in vistos:
            continue
        vistos[sku] = {
            "precio": "PENDIENTE",
            "precio_num": None,
            "nombre": "",
            "grado": "N/A",
            "categoria": item.get("categoria", ""),
            "link": item["link"],
            "origen": "sitemap",
        }
        avisos["sitemap_nuevos"] += 1
    estado["sitemap_index"] = (sm_idx + 1) % len(SITEMAPS_PRODUCTOS)
    print(
        f"🆕 SKUs nuevos desde sitemap: {avisos['sitemap_nuevos']} | "
        f"Historial total: {len(vistos)}"
    )

    # ----------------------------------------------------
    # FASE 2: Feeds públicos (aquí sí avisamos productos nuevos)
    # ----------------------------------------------------
    print("🔄 Fase 2: feeds CeX")
    for feed in FEEDS:
        for box in fetch_feed(feed):
            ficha = normalizar_box(box)
            if not ficha:
                continue
            ficha["origen"] = f"feed:{feed}"
            # Solo avisar "nuevo" si ya había historial previo
            procesar_ficha(
                ficha,
                vistos,
                avisos,
                avisar_nuevo=habia_historial,
            )
        time.sleep(0.3)

    # ----------------------------------------------------
    # FASE 3: Rechequeo masivo de precios por /detail (en paralelo)
    # Prioriza SKUs sin precio y luego rota por todo el historial
    # ----------------------------------------------------
    print("🔄 Fase 3: rechequeo de precios (catálogo completo, paralelo)")
    skus = list(vistos.keys())
    if skus:
        pendientes = [
            s for s in skus
            if not precio_valido((vistos[s] or {}).get("precio") if isinstance(vistos[s], dict) else None)
        ]
        resto = [s for s in skus if s not in set(pendientes)]

        offset = int(estado.get("recheck_offset", 0)) % max(len(resto), 1)
        lote = []
        # Primero rellenar TODOS los pendientes que quepan en el cupo
        lote.extend(pendientes[:RECHECK_POR_CORRIDA])
        cupo = max(0, RECHECK_POR_CORRIDA - len(lote))
        if resto and cupo:
            for i in range(cupo):
                lote.append(resto[(offset + i) % len(resto)])
            estado["recheck_offset"] = (offset + cupo) % len(resto)

        print(
            f"🔎 Rechequeando {len(lote)} SKUs con {WORKERS_DETAIL} hilos "
            f"(max {MAX_IN_FLIGHT} en vuelo; pendientes prioritarios: "
            f"{min(len(pendientes), len(lote))})"
        )

        t0 = time.time()
        ok = fail = errors = 0
        with ThreadPoolExecutor(max_workers=WORKERS_DETAIL) as pool:
            futures = [
                pool.submit(_recheck_sku, sku, vistos, avisos)
                for sku in lote
            ]
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                except Exception:
                    errors += 1
                    continue
                if result == "ok":
                    ok += 1
                elif result == "error":
                    errors += 1
                else:
                    fail += 1
        print(
            f"✅ Rechequeo terminado en {time.time() - t0:.1f}s "
            f"(ok={ok} | sin ficha={fail} | errores temporales={errors})"
        )

    guardar_json(ARCHIVO_HISTORIAL, vistos)
    guardar_json(ARCHIVO_ESTADO, estado)

    hora = time.strftime("%H:%M:%S")
    pendientes_final = sum(
        1 for v in vistos.values()
        if isinstance(v, dict) and not precio_valido(v.get("precio"))
    )
    print(
        f"[{hora}] Escaneo CeX finalizado. "
        f"WhatsApp nuevos: {avisos['nuevos']} | Cambios: {avisos['cambios']} | "
        f"Historial: {len(vistos)} | Sin precio aún: {pendientes_final} | "
        f"Próximo sitemap: #{estado['sitemap_index'] + 1}"
    )


if __name__ == "__main__":
    comprobar_tienda()
