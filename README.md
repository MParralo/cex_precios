# Monitor CeX España

Avisa por WhatsApp cuando aparecen productos nuevos o cambian de precio en [CeX España](https://es.webuy.com/).

## Cómo funciona

1. **Sitemap** — descubre el catálogo interesante (móviles, iPad, portátiles, PS5, Switch, Xbox Series…) desde los sitemaps oficiales (~decenas de miles de SKUs).
2. **Feeds** — `hotproducts` / `topsellers` / `mostwanted` para novedades calientes (sí avisan por WhatsApp).
3. **Rechequeo de precios** — consulta `/detail` por SKU a turnos y avisa si el precio cambia.

Repo: https://github.com/MParralo/cex_precios

## Secrets del repo

GitHub → Settings → Secrets and variables → Actions:

- `WHATSAPP_PHONE` (ej. `34613484447`)
- `WHATSAPP_APIKEY` (CallMeBot)

## Notas

- El listado `/boxes` está bloqueado por Cloudflare; el sitemap + `/detail` lo sustituyen.
- La primera vez que un SKU entra por sitemap **no** spamea WhatsApp; solo avisa al **cambiar el precio** (o si aparece en feeds como novedad).
- Cada corrida rechequea ~220 precios; con el tiempo cubre todo el historial en rotación.
