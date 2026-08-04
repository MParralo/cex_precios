# Monitor CeX España

Avisa por WhatsApp cuando aparecen productos nuevos o bajan/suben de precio en [CeX España](https://es.webuy.com/).

## Cómo funciona

- Lee la API WeBuy (`wss2.cex.es.webuy.io`)
- Feeds: `hotproducts`, `topsellers`, `mostwanted`
- Intenta barrido por categorías (`/boxes`); si Cloudflare lo bloquea, sigue con feeds + rechequeo por SKU
- Guarda historial en `vistos.json` (clave = SKU CeX)
- GitHub Actions cada 10 minutos

## Secrets del repo

En GitHub → Settings → Secrets and variables → Actions:

- `WHATSAPP_PHONE` (ej. `34613484447`)
- `WHATSAPP_APIKEY` (CallMeBot)

## Primer arranque

La primera corrida solo carga la base (no spamea WhatsApp). Después avisa de novedades y cambios de precio.
