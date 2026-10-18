# HA01 — ronda 2 (corrida local)

Segunda medición de HA01 (latencia de cotización con cache-aside y circuit breaker) sobre el MS Cotización de
`solventa-backend`, después de los cambios que salieron de la ronda 1: sondeo half-open en segundo plano y jitter en el
TTL de la caché. El equipo decidió correrla en Docker local en lugar de AWS. El veredicto y las desviaciones están
en [`resultados/VEREDICTO.md`](resultados/VEREDICTO.md).

| Carpeta | Contenido |
|---|---|
| `scripts/` | `ha01-cotizacion.js`: k6 con el mismo perfil de la ronda 1 (rampa 500→5000 solicitudes/min en 15 min + 10 min de meseta). `analizar.py`: p50–p99 de cliente e interna, tasas y veredicto por meta sobre el JSON crudo. `levantar-servicio.sh`: solo Cotización, para pruebas rápidas. |
| `seed/` | `sembrar-clientes.py`: crea clientes sintéticos en MS Identidad y les otorga el consentimiento de Open Finance. Sin consentimiento, Cotización responde `aproximada` y la corrida no es válida. |
| `local/` | `levantar-entorno.sh`: Postgres, Redis, Identidad y Cotización en la red `ha01net`, con Cotización e Identidad a 1 CPU y 512Mi. `correr-escenario-local.sh <A\|B>`: limpia Redis, corre k6 con dashboard web, graba el dashboard con ffmpeg, muestrea `docker stats` y analiza. |
| `resultados/` | Veredicto, informe consolidado y reportes HTML de k6. |

## Reproducir

Requiere Docker y Python 3. Para grabar el dashboard también hacen falta ffmpeg y Chrome en Windows.
Las imágenes se construyen desde `solventa-backend`: la corrida usó `services/cotizacion` en `116e410`
(`ha01-cotizacion-local:116e410`) y `services/identidad` (`ha01-identidad-local`).

```bash
./local/levantar-entorno.sh 200 500
python -I seed/sembrar-clientes.py --url http://localhost:8011 --n 1000 --salida resultados/clientes.json
PERFIL_CHROME=/ruta/perfil-temporal-chrome ./local/correr-escenario-local.sh A
PERFIL_CHROME=/ruta/perfil-temporal-chrome ./local/correr-escenario-local.sh B
docker stop ha01-cotizacion ha01-identidad ha01-redis ha01-pg
```

Cada escenario tarda unos 26 minutos. El JSON crudo de k6 sin comprimir ocupa unos 550 MB por escenario.
