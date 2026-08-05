# Monitor CeX España

Avisa por WhatsApp cuando aparecen productos nuevos o cambian de precio en [CeX España](https://es.webuy.com/).

## Cómo funciona

1. **Prepare** — sitemap + feeds.
2. **Rechequeo en 12 shards** — runners en paralelo (IPs distintas), ~500 SKUs cada uno, ritmo suave para no provocar 403.
3. **Merge** — junta deltas y guarda `vistos.json`.

Oleada: **12 × 500 = 6000 SKUs**. Con disparo cada 2 h → catálogo (~50k) en **~1 día**.

Cron: [precios_disparador](https://github.com/MParralo/precios_disparador).

## Secrets

- `WHATSAPP_PHONE`
- `WHATSAPP_APIKEY`
