import os
import re
import json
import time
import urllib.parse
import requests

API_BASE = "https://wss2.cex.es.webuy.io/v3"
WEB_BASE = "https://es.webuy.com"

FEEDS = ("hotproducts", "topsellers", "mostwanted")

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
    "juegos",
    "games",
)

SITEMAPS_PRODUCTOS = [
    f"{WEB_BASE}/sitemaps/cex/es/sitemap-es-products-{i}.xml"
    for i in range(1, 6)
]

ARCHIVO_HISTORIAL = "vistos.json"
ARCHIVO_ESTADO = "estado.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

RECHECK_POR_SHARD = int(os.getenv("RECHECK_POR_SHARD", "500"))
PAUSA_DETAIL = float(os.getenv("PAUSA_DETAIL", "0.2"))
DETAIL_TIMEOUT = 10
SHARD_INDEX = int(os.getenv("SHARD_INDEX", "0"))
SHARD_TOTAL = max(1, int(os.getenv("SHARD_TOTAL", "1")))
CEX_MODE = os.getenv("CEX_MODE", "all")  # all | prepare | recheck

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

RE_GRADO_FINAL = re.compile(
    r"(?:,\s*)?(Perfecto|Bueno|Razonable|Aceptable|A\+|A|B|C)\s*$",
    re.IGNORECASE,
)
RE_SITEMAP_ITEM = re.compile(
    r"product-detail/\?id=([^<]+)</loc>\s*"
    r"<image:image>\s*<image:loc>([^<]+)</image:loc>",
    re.IGNORECASE,
)


def enviar_telegram(mensaje):
    """Envía un mensaje por Telegram Bot API (gratis, sin tope práctico)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram no configurado (faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram limita a 4096 caracteres
    texto = mensaje if len(mensaje) <= 4000 else mensaje[:3990] + "…"
    try:
        r = SESSION.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": texto,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if r.status_code == 200 and (r.json() or {}).get("ok"):
            print("📲 Alerta enviada por Telegram correctamente.")
        else:
            print(f"⚠️ Error enviando Telegram: {r.status_code} | {r.text[:200]}")
    except Exception as e:
        print(f"❌ Error al enviar Telegram: {e}")


# Alias por compatibilidad con el resto del código
enviar_whatsapp = enviar_telegram


def api_get_result(path, params=None, timeout=25, raw_query=None):
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    if raw_query:
        url = f"{url}?{raw_query}"
        params = None
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
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


def api_get(path, params=None, timeout=25, raw_query=None):
    status, data = api_get_result(path, params=params, timeout=timeout, raw_query=raw_query)
    return data if status == "ok" else None


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
    try:
        ruta = ruta.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if any(f in ruta for f in FILTROS_EXCLUIR):
        return False
    return any(f in ruta for f in FILTROS_INCLUIR)


def descubrir_desde_sitemap(sitemap_url):
    try:
        r = SESSION.get(sitemap_url, timeout=120)
        if r.status_code != 200:
            print(f"⚠️ Sitemap no disponible: {sitemap_url} ({r.status_code})")
            return []
        encontrados = []
        for sku, img in RE_SITEMAP_ITEM.findall(r.text):
            if categoria_interesante(img):
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


def fetch_detalle_result(sku):
    status, data = api_get_result(f"/boxes/{sku}/detail", timeout=DETAIL_TIMEOUT)
    if status != "ok":
        return status, None
    details = (data or {}).get("boxDetails") or []
    if not details:
        return "not_found", None
    return "ok", details[0]


def precio_valido(precio):
    return bool(precio) and precio not in ("N/A", "PENDIENTE", "")


def procesar_ficha(ficha, vistos, avisos, avisar_nuevo=False):
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

    if not entrada["nombre"] and prev.get("nombre"):
        entrada["nombre"] = prev["nombre"]
    vistos[sku] = {**prev, **entrada}


def fase_prepare(vistos, estado, avisos, habia_historial):
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

    print("🔄 Fase 2: feeds CeX")
    for feed in FEEDS:
        for box in fetch_feed(feed):
            ficha = normalizar_box(box)
            if not ficha:
                continue
            ficha["origen"] = f"feed:{feed}"
            procesar_ficha(ficha, vistos, avisos, avisar_nuevo=habia_historial)
        time.sleep(0.3)


def construir_lote(vistos, estado):
    skus = list(vistos.keys())
    pendientes = [
        s for s in skus
        if not precio_valido(
            (vistos[s] or {}).get("precio") if isinstance(vistos[s], dict) else None
        )
    ]
    resto = [s for s in skus if s not in set(pendientes)]
    offset = int(estado.get("recheck_offset", 0)) % max(len(resto), 1)

    cupo_oleada = RECHECK_POR_SHARD * SHARD_TOTAL
    lote = []
    lote.extend(pendientes[:cupo_oleada])
    falta = max(0, cupo_oleada - len(lote))
    if resto and falta:
        for i in range(falta):
            lote.append(resto[(offset + i) % len(resto)])
        if SHARD_INDEX == 0:
            estado["recheck_offset"] = (offset + falta) % len(resto)

    return lote[SHARD_INDEX::SHARD_TOTAL], len(pendientes)


def fase_recheck(vistos, estado, avisos):
    print(
        f"🔄 Fase 3: rechequeo shard {SHARD_INDEX + 1}/{SHARD_TOTAL} "
        f"(hasta {RECHECK_POR_SHARD} SKUs, pausa {PAUSA_DETAIL}s)"
    )
    mi_lote, n_pend = construir_lote(vistos, estado)
    print(f"🔎 Rechequeando {len(mi_lote)} SKUs (pendientes globales ≈ {n_pend})")

    ok = fail = errors = 0
    errores_seguidos = 0
    t0 = time.time()
    delta = {}

    for sku in mi_lote:
        status, detalle = fetch_detalle_result(sku)
        if status == "error":
            errors += 1
            errores_seguidos += 1
            if errores_seguidos >= 8:
                time.sleep(min(15, 2 * errores_seguidos))
            time.sleep(PAUSA_DETAIL)
            continue

        errores_seguidos = 0
        if status == "not_found" or not detalle:
            prev = vistos.get(sku) if isinstance(vistos.get(sku), dict) else {}
            if not precio_valido(prev.get("precio")) or prev.get("precio") == "PENDIENTE":
                entrada = {**prev, "precio": "N/A", "precio_num": None}
                vistos[sku] = entrada
                delta[sku] = entrada
            fail += 1
        else:
            ficha = normalizar_box(detalle)
            if ficha:
                ficha["origen"] = "detail"
                procesar_ficha(ficha, vistos, avisos, avisar_nuevo=False)
                delta[sku] = vistos[sku]
                ok += 1
            else:
                fail += 1
        time.sleep(PAUSA_DETAIL)

    print(
        f"✅ Shard {SHARD_INDEX + 1} en {time.time() - t0:.1f}s "
        f"(ok={ok} | sin ficha={fail} | errores={errors})"
    )
    return delta


def comprobar_tienda():
    vistos = cargar_json(ARCHIVO_HISTORIAL, {})
    estado = cargar_json(ARCHIVO_ESTADO, {
        "sitemap_index": 0,
        "recheck_offset": 0,
    })
    avisos = {"nuevos": 0, "cambios": 0, "sitemap_nuevos": 0}
    habia_historial = len(vistos) > 0

    if CEX_MODE in ("all", "prepare"):
        fase_prepare(vistos, estado, avisos, habia_historial)
        guardar_json(ARCHIVO_HISTORIAL, vistos)
        guardar_json(ARCHIVO_ESTADO, estado)

    if CEX_MODE in ("all", "recheck"):
        if CEX_MODE == "recheck":
            vistos = cargar_json(ARCHIVO_HISTORIAL, vistos)
            estado = cargar_json(ARCHIVO_ESTADO, estado)
        delta = fase_recheck(vistos, estado, avisos)
        if CEX_MODE == "recheck":
            os.makedirs("deltas", exist_ok=True)
            delta_path = f"deltas/delta_{SHARD_INDEX}.json"
            guardar_json(delta_path, delta)
            print(f"💾 Delta guardado: {delta_path} ({len(delta)} SKUs)")
            if SHARD_INDEX == 0:
                guardar_json(ARCHIVO_ESTADO, estado)
        else:
            guardar_json(ARCHIVO_HISTORIAL, vistos)
            guardar_json(ARCHIVO_ESTADO, estado)

    hora = time.strftime("%H:%M:%S")
    pendientes_final = sum(
        1 for v in vistos.values()
        if isinstance(v, dict) and not precio_valido(v.get("precio"))
    )
    print(
        f"[{hora}] Escaneo CeX ({CEX_MODE}) finalizado. "
        f"WhatsApp nuevos: {avisos['nuevos']} | Cambios: {avisos['cambios']} | "
        f"Historial: {len(vistos)} | Sin precio aún: {pendientes_final}"
    )


if __name__ == "__main__":
    comprobar_tienda()
