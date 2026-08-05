# Monitor CeX España

Avisa por WhatsApp cuando aparecen productos nuevos o cambian de precio en [CeX España](https://es.webuy.com/).

## Cómo funciona

1. **Sitemap** — descubre el catálogo interesante (móviles, iPad, portátiles, PS5, Switch, Xbox Series…) desde los sitemaps oficiales (~decenas de miles de SKUs).
2. **Feeds** — `hotproducts` / `topsellers` / `mostwanted` para novedades calientes (sí avisan por WhatsApp).
3. **Rechequeo de precios** — consulta `/detail` en paralelo controlado (~4000 SKUs/corrida). Con el disparador cada 2 h, el catálogo se cubre en **~1 día**.

El cron lo lanza [precios_disparador](https://github.com/MParralo/precios_disparador).

## Secrets del repo

GitHub → Settings → Secrets and variables → Actions:

- `WHATSAPP_PHONE` (ej. `34613484447`)
- `WHATSAPP_APIKEY` (CallMeBot)

## Notas

- El listado `/boxes` está bloqueado por Cloudflare; el sitemap + `/detail` lo sustituyen.
- La primera vez que un SKU entra por sitemap **no** spamea WhatsApp; solo avisa al **cambiar el precio** (o si aparece en feeds como novedad).
- Errores temporales (403/429) no marcan el SKU como `N/A`; se reintenta en la siguiente corrida.
