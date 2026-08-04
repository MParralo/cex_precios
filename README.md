# Monitor CeX España

Avisa por WhatsApp cuando aparecen productos nuevos o bajan/suben de precio en [CeX España](https://es.webuy.com/).

## Cómo funciona

- Lee la API WeBuy (`wss2.cex.es.webuy.io`)
- Feeds: `hotproducts`, `topsellers`, `mostwanted` (~275 productos calientes)
- Intenta barrido por categorías (`/boxes`); Cloudflare suele bloquearlo, y entonces sigue con feeds + rechequeo de precios por SKU (`/detail`)
- Guarda historial en `vistos.json` (clave = SKU CeX)
- GitHub Actions cada 10 minutos
- Repo: https://github.com/MParralo/cex_precios

## Secrets del repo

En GitHub → Settings → Secrets and variables → Actions:

- `WHATSAPP_PHONE` (ej. `34613484447`)
- `WHATSAPP_APIKEY` (CallMeBot)

## Primer arranque

La primera corrida solo carga la base (no spamea WhatsApp). Después avisa de novedades y cambios de precio.
