import os
import re
import json
import time
import urllib.parse
import requests

API_BASE = "https://wss2.cex.es.webuy.io/v3"
WEB_BASE = "https://es.webuy.com"

# Feeds públicos que suelen estar accesibles sin Cloudflare
FEEDS = ("hotproducts", "topsellers", "mostwanted")

# Categorías prioritarias (IDs reales de CeX ES) para el barrido rotatorio
CATEGORIAS = [
    {"id": 921, "nombre": "Móviles - iPhone"},
    {"id": 984, "nombre": "Móviles - Android"},
    {"id": 920, "nombre": "Móviles - Libre"},
    {"id": 967, "nombre": "Apple iPad"},
    {"id": 1078, "nombre": "PS5 Consolas"},
    {"id": 1079, "nombre": "PS5 Juegos"},
    {"id": 1032, "nombre": "Switch Consolas"},
    {"id": 1031, "nombre": "Switch Juegos"},
    {"id": 1112, "nombre": "Switch 2 Consolas"},
    {"id": 1113, "nombre": "Switch 2 Juegos"},
    {"id": 1081, "nombre": "Xbox Series Consolas"},
    {"id": 1083, "nombre": "Xbox Series Juegos"},
    {"id": 850, "nombre": "Portátiles - Apple"},
    {"id": 1085, "nombre": "PC Gaming Portátil"},
]

ARCHIVO_HISTORIAL = "vistos.json"
ARCHIVO_ESTADO = "estado.json"

WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE", "34613484447")
WHATSAPP_APIKEY = os.getenv("WHATSAPP_APIKEY", "4010754")

# Por corrida: páginas de catálogo (si /boxes responde) + rechequeo de SKUs conocidos
BOXES_POR_PAGINA = 50
PAGINAS_POR_CORRIDA = 3
RECHECK_POR_CORRIDA = 40

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


def enviar_whatsapp(mensaje):
    """Envía un mensaje formateado por WhatsApp mediante CallMeBot."""
    mensaje_encoded = urllib.parse.quote(mensaje)
    url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={WHATSAPP_PHONE}&text={mensaje_encoded}&apikey={WHATSAPP_APIKEY}"
    )
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            print("📲 Alerta enviada por WhatsApp correctamente.")
        else:
            print(f"⚠️ Error enviando WhatsApp: {r.status_code}")
    except Exception as e:
        print(f"❌ Error al enviar mensaje: {e}")


def api_get(path, params=None, timeout=25, raw_query=None):
    """GET a la API WeBuy ES. Devuelve data o None si falla.

    raw_query: querystring literal (sin encodear), necesario para arrays tipo [1,2,3].
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    if raw_query:
        url = f"{url}?{raw_query}"
        params = None
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        if "application/json" not in (r.headers.get("content-type") or ""):
            return None
        payload = r.json()
        resp = payload.get("response") or {}
        if resp.get("ack") != "Success":
            return None
        return resp.get("data")
    except Exception as e:
        print(f"⚠️ API error ({path}): {e}")
        return None


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
    """Unifica feeds y /boxes en una ficha común."""
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


def fetch_feed(nombre):
    data = api_get(f"/boxlists/{nombre}")
    if not data:
        print(f"⚠️ Feed {nombre} no disponible")
        return []
    boxes = data.get("boxlistsBoxes") or []
    print(f"📦 Feed {nombre}: {len(boxes)} productos")
    return boxes


def fetch_boxes_categoria(category_id, first_record=1, count=BOXES_POR_PAGINA):
    """Intenta listar productos de una categoría. Puede fallar por Cloudflare."""
    q = (
        f"categoryIds=[{category_id}]"
        f"&firstRecord={first_record}"
        f"&count={count}"
        f"&sortBy=relevance&sortOrder=desc"
    )
    data = api_get("/boxes", raw_query=q)
    if not data:
        return None
    return data.get("boxes") or []


def fetch_detalle(sku):
    data = api_get(f"/boxes/{sku}/detail")
    if not data:
        return None
    details = data.get("boxDetails") or []
    return details[0] if details else None


def procesar_ficha(ficha, vistos, primer_arranque, avisos):
    """Actualiza historial y decide si avisar (nuevo / cambio de precio)."""
    sku = ficha["sku"]
    precio = ficha["precio"]
    entrada = {
        "precio": precio,
        "precio_num": ficha.get("precio_num"),
        "nombre": ficha["nombre"],
        "grado": ficha["grado"],
        "categoria": ficha.get("categoria", ""),
        "link": ficha["link"],
    }

    if sku not in vistos:
        if not primer_arranque:
            print(f"🆕 Nuevo: {ficha['nombre']} ({precio}) [SKU {sku}]")
            msg = (
                f"📦 [CeX] Producto nuevo: {ficha['nombre']}\n"
                f"🔢 SKU: {sku}\n"
                f"🏷️ Grado: {ficha['grado']}\n"
                f"📁 Categoría: {ficha.get('categoria') or 'N/A'}\n"
                f"💰 Precio: {precio}\n\n"
                f"🔗 Link: {ficha['link']}"
            )
            enviar_whatsapp(msg)
            avisos["nuevos"] += 1
            time.sleep(1)
        vistos[sku] = entrada
        return

    prev = vistos[sku] if isinstance(vistos[sku], dict) else {}
    precio_prev = prev.get("precio")
    if (
        precio_prev
        and precio_prev != "N/A"
        and precio != "N/A"
        and precio != precio_prev
    ):
        print(f"📉 Cambio: {ficha['nombre']} ({precio_prev} ➡️ {precio}) [SKU {sku}]")
        msg = (
            f"📉 [CeX] ¡CAMBIO DE PRECIO! 📉\n\n"
            f"📦 Producto: {ficha['nombre']}\n"
            f"🔢 SKU: {sku}\n"
            f"🏷️ Grado: {ficha['grado']}\n"
            f"💵 Precio anterior: {precio_prev}\n"
            f"💰 Nuevo precio: {precio}\n\n"
            f"🔗 Link: {ficha['link']}"
        )
        enviar_whatsapp(msg)
        avisos["cambios"] += 1
        time.sleep(1)

    vistos[sku] = entrada


def comprobar_tienda():
    vistos = cargar_json(ARCHIVO_HISTORIAL, {})
    estado = cargar_json(ARCHIVO_ESTADO, {
        "cat_index": 0,
        "first_record": 1,
        "recheck_offset": 0,
        "boxes_disponible": None,
    })

    primer_arranque = len(vistos) == 0
    avisos = {"nuevos": 0, "cambios": 0}
    vistos_boxes = []

    # ----------------------------------------------------
    # FASE 1: Feeds públicos (hot / topsellers / mostwanted)
    # ----------------------------------------------------
    print("🔄 Fase 1: feeds CeX")
    for feed in FEEDS:
        for box in fetch_feed(feed):
            ficha = normalizar_box(box)
            if ficha:
                vistos_boxes.append(ficha)
        time.sleep(0.4)

    # ----------------------------------------------------
    # FASE 2: Barrido rotatorio por categoría (/boxes)
    # ----------------------------------------------------
    print("🔄 Fase 2: catálogo por categorías")
    cat_idx = estado.get("cat_index", 0) % len(CATEGORIAS)
    first_record = max(1, int(estado.get("first_record", 1)))
    cat = CATEGORIAS[cat_idx]
    print(
        f"📁 Categoría: {cat['nombre']} "
        f"(id {cat['id']}, desde registro {first_record})"
    )

    boxes_ok = False
    paginas_ok = 0
    for i in range(PAGINAS_POR_CORRIDA):
        fr = first_record + i * BOXES_POR_PAGINA
        boxes = fetch_boxes_categoria(cat["id"], first_record=fr)
        if boxes is None:
            print("⚠️ /boxes no disponible (posible Cloudflare). Se omite catálogo en esta corrida.")
            estado["boxes_disponible"] = False
            break
        boxes_ok = True
        estado["boxes_disponible"] = True
        if not boxes:
            estado["cat_index"] = (cat_idx + 1) % len(CATEGORIAS)
            estado["first_record"] = 1
            paginas_ok = 0
            break
        for box in boxes:
            ficha = normalizar_box(box)
            if ficha:
                vistos_boxes.append(ficha)
        paginas_ok += 1
        time.sleep(0.5)
    else:
        if boxes_ok:
            estado["cat_index"] = cat_idx
            estado["first_record"] = first_record + paginas_ok * BOXES_POR_PAGINA

    # ----------------------------------------------------
    # FASE 3: Rechequeo de SKUs ya conocidos vía /detail
    # ----------------------------------------------------
    print("🔄 Fase 3: rechequeo de precios por SKU")
    skus = list(vistos.keys())
    if skus:
        offset = int(estado.get("recheck_offset", 0)) % len(skus)
        lote = []
        for i in range(min(RECHECK_POR_CORRIDA, len(skus))):
            lote.append(skus[(offset + i) % len(skus)])
        estado["recheck_offset"] = (offset + len(lote)) % len(skus)

        for sku in lote:
            detalle = fetch_detalle(sku)
            if not detalle:
                continue
            ficha = normalizar_box(detalle)
            if ficha:
                vistos_boxes.append(ficha)
            time.sleep(0.25)

    # Deduplicar por SKU (última aparición gana)
    unicos = {}
    for ficha in vistos_boxes:
        unicos[ficha["sku"]] = ficha

    print(f"🧮 Productos a procesar en esta corrida: {len(unicos)}")
    for ficha in unicos.values():
        procesar_ficha(ficha, vistos, primer_arranque, avisos)

    guardar_json(ARCHIVO_HISTORIAL, vistos)
    guardar_json(ARCHIVO_ESTADO, estado)

    hora = time.strftime("%H:%M:%S")
    if primer_arranque:
        print(f"[{hora}] Base inicial CeX cargada con {len(vistos)} productos (sin spam WhatsApp).")
    else:
        print(
            f"[{hora}] Escaneo CeX finalizado. "
            f"Nuevos: {avisos['nuevos']} | Cambios: {avisos['cambios']} | "
            f"Historial: {len(vistos)}"
        )


if __name__ == "__main__":
    comprobar_tienda()
