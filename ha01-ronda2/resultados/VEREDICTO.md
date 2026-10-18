# HA01 ronda 2 — veredicto de la corrida local (2026-10-18)

Corrida completa (15 min de rampa 500→5000 solicitudes/min + 10 min de meseta, mismo perfil de la ronda 1)
del MS Cotización de `solventa-backend@116e410`, en Docker local. 91.249 solicitudes por escenario.
Fuente de las cifras: `informe-ronda2.json` (salida de `scripts/analizar.py` sobre el JSON crudo de k6).

## Veredicto por meta

| Meta | Ronda 1 (AWS, sem. 7) | Ronda 2 (local) | Veredicto ronda 2 |
|---|---|---|---|
| **A** — p95 cliente ≤ 250 ms | 357,6 ms | **301,5 ms** | No cumple |
| **A** — p99 cliente ≤ 500 ms | 565,3 ms | **484,8 ms** | Cumple |
| **A** — p95 interno (referencia) | 261 ms | 273,6 ms | — |
| **A** — brecha cliente − interna en p95 | ~96 ms | 27,9 ms | — |
| **A** — hit de caché en régimen ≥ 60 % | 94,4 % | **91,8 %** | Cumple |
| **A** — cotizaciones fallidas = 0 % | 0 % | **0 %** | Cumple |
| **B** — p95 cliente ≤ 300 ms | 123,9 ms | **63,9 ms** | Cumple |
| **B** — p99 cliente (sin meta) | 829,7 ms | **106,5 ms** | — |
| **B** — 100 % degradadas, 0 % fallidas | 100 % / 0 % | **100 % / 0 %** | Cumple |
| Validez — 0 % cotizaciones `aproximada` | n/a | 0 % / 0 % | Cumple |

## Lectura

- **A, p95.** En régimen fallan en caché el 8,2 % de las solicitudes. Como es más del 5 %, el p95 cae dentro de
  las que pagan la latencia del simulador (200–500 ms). Con este perfil, para cumplir los 250 ms hace falta un hit
  de caché por encima del 95 % o un proveedor más rápido. La brecha cliente–interna bajó de ~96 a 28 ms al poner
  el generador en la misma red que el servicio.
- **A, TTL con jitter.** El p95 por minuto muestra oleadas cada ~5 min (TTL de 300 s): por ejemplo, 22:29–22:31
  y 22:35–22:36 UTC. El jitter de ±10–15 % las suaviza pero no las elimina, porque la caché se llena durante la
  rampa y sus claves expiran por tandas. Es una observación sobre la latencia por minuto, no verificada clave por clave.
- **B, cola p99.** El p99 bajó de 829,7 a 106,5 ms, y el patrón de una solicitud lenta cada 30 s no aparece.
  Esto es consistente con que el sondeo half-open ahora corre en segundo plano. La confirmación es a nivel de
  cliente; no hay trazas (sin OTel).
- **Picos de CPU.** Hubo un pico de ~10 s por escenario con el CPU de Cotización al 94–101 % de su límite:
  A a las 22:40:27 (p95 del minuto 534 ms) y B a las 23:16:27–23:16:36 (160 solicitudes de más de 300 ms,
  máximo 686 ms). La causa no está identificada. El primer minuto de B (p95 719 ms) es esperado: son las
  llamadas que agotan el timeout de 0,7 s antes de que se abra el breaker.

## Desviaciones frente a la ronda 1 y frente al plan en AWS

1. **Corrida local en Docker en vez de AWS** (decisión del equipo). Cotización e Identidad tienen 1 CPU y 512Mi
   cada uno, como los límites de staging, pero **una sola réplica** de cada servicio (staging tiene mínimo 2 con
   HPA) y no hay autoescalado. Postgres y Redis corren sin límite de recursos, sin TLS y sin réplicas. k6 0.54.0
   corre en un contenedor sin límite, en la misma red de Docker. Host: Windows 11 con Docker Desktop 28.0.1,
   20 CPU lógicas y ~7,6 GiB para Docker.
2. **Sin OTel.** No hay trazas. La latencia interna es `metadata.latencia_interna_ms` de la propia respuesta,
   y el análisis de la cola de B se hace sobre las latencias del cliente.
3. **Consulta síncrona a Identidad, que la ronda 1 no tenía.** Va en el camino de cada solicitud, antes de la
   caché, y está incluida en la latencia interna. No se mide por separado en la corrida. Medida aparte, Identidad
   sola a 83 solicitudes/s, desde la misma red y con los mismos clientes, responde en p50 5,2 ms, p95 6,5 ms y
   p99 11,3 ms. Comparado con la latencia interna de A, equivale a ≈ 60 % del p50 y ≈ 2 % del p95. Es una
   aproximación: son dos mediciones distintas.
4. **Datos sembrados.** 1000 clientes sintéticos con consentimiento `OPEN_FINANCE_OPEN_DATA` (`clientes.json`).
   En la ronda 1 los clientes eran IDs sintéticos sin Identidad.
5. **Presión de memoria y log de k6 de B.**
   - El host se quedó sin memoria a las 23:30:20 UTC (~1 GB libre de 16 GB), ocho minutos después del fin de la
     carga de B (23:21:42). Claude Code dejó de seguir el script orquestador.
   - Antes de eso, k6 había terminado las 91.249 iteraciones, pero no salió del proceso hasta las 23:38:38
     (código 0); la causa del atraso no se verificó.
   - Validación de que los datos sirven: el JSON cubre 22:56:43–23:21:42 completo, la memoria de Cotización
     llegó como máximo a 88 MiB de 512 MiB, y la presión no coincidió con ningún tramo de carga.
   - **Se perdió el log de consola de k6 de B** (ver `escenario-B/k6.log.PERDIDO.txt`). El resumen de k6 está
     completo en `escenario-B-summary.json` y `reporte-k6-B.html`.
   - Las muestras de `docker-stats.csv` de B posteriores a las 23:22 UTC son de reposo.
6. **Grabación.** `gdigrab title=` capturaba negro con el Chrome normal, que dibuja por GPU. Se grabó una
   instancia aparte de Chrome (perfil temporal, `--app`, sin GPU ni DirectComposition), con la ventana
   "k6 dashboard", a 2 fps.

## Archivos

En el repositorio:

- `informe-ronda2.json` y `analisis-ronda2.txt`: salida de `scripts/analizar.py` sobre los dos escenarios.
- `escenario-A/reporte-k6-A.html` y `escenario-B/reporte-k6-B.html`: reporte exportado por el dashboard web de k6.

Fuera del repositorio (no se versionan por tamaño):

- JSON crudo de k6 (`escenario-X.json.gz`, ~16 MB cada uno).
- Grabaciones del dashboard (`escenario-X.mp4`, 26,7 min cada una).
- `docker-stats.csv`, `ambiente.txt`, summaries de k6 y `clientes.json`.
