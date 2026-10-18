#!/usr/bin/env bash
# Corre un escenario COMPLETO de HA01 en local y deja la evidencia en resultados/ y videos/.
#   1. FLUSHDB de Redis y Cotización recreado con la latencia del escenario (breaker en cero).
#   2. docker stats cada 5 s -> resultados/escenario-X/docker-stats.csv
#   3. k6 en la red ha01net con dashboard web (puerto 5665) y reporte HTML exportado al final.
#   4. Chrome aislado (perfil temporal, sin GPU ni DirectComposition, si no gdigrab graba negro)
#      y ffmpeg gdigrab title="k6 dashboard" a 2 fps -> videos/escenario-X.mp4
#   5. analizar.py sobre el JSON crudo, que luego se comprime.
# Uso: local/correr-escenario-local.sh <A|B>
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ESC="${1:?A|B}"
case "$ESC" in A) MIN=200; MAX=500 ;; B) MIN=800; MAX=1500 ;; *) echo "A|B" >&2; exit 1 ;; esac
FFMPEG="${FFMPEG:-ffmpeg}"   # ruta al ejecutable si no está en el PATH
PERFIL_CHROME="${PERFIL_CHROME:?ruta del perfil temporal de Chrome}"
OUT="resultados/escenario-$ESC"; VID="videos/escenario-$ESC"
mkdir -p "$OUT" videos
DURACION_VIDEO_S=1600   # 25 min de perfil + gracefulStop 30 s + margen

echo "== limpiar Redis"
echo "claves antes: $(docker exec ha01-redis redis-cli DBSIZE)"; docker exec ha01-redis redis-cli FLUSHDB >/dev/null
echo "claves después: $(docker exec ha01-redis redis-cli DBSIZE)" | tee "$OUT/redis-flush.txt"
./local/levantar-entorno.sh "$MIN" "$MAX" --solo-cotizacion

{
  echo "escenario=$ESC latencia_simulador_ms=$MIN-$MAX perfil=completo"
  echo "inicio_utc=$(date -u +%FT%TZ)"
  echo "docker=$(docker version --format '{{.Server.Version}}') host_cpus=$(docker info --format '{{.NCPU}}') host_mem=$(docker info --format '{{.MemTotal}}')"
  for c in ha01-cotizacion ha01-identidad ha01-redis ha01-pg; do
    echo "$c imagen=$(docker inspect $c --format '{{.Config.Image}}') nano_cpus=$(docker inspect $c --format '{{.HostConfig.NanoCpus}}') mem=$(docker inspect $c --format '{{.HostConfig.Memory}}')"
  done
  echo "k6=grafana/k6:0.54.0 (contenedor en ha01net, sin límite) clientes=$(python -c 'import json;print(len(json.load(open("resultados/clientes.json"))))')"
} > "$OUT/ambiente.txt"

echo "== docker stats"
( echo "timestamp_utc,contenedor,cpu_pct,mem_uso,mem_pct,net_io,block_io,pids"
  while true; do
    ts="$(date -u +%FT%TZ)"
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}},{{.PIDs}}' \
      $(docker ps -q --filter name=^ha01-) 2>/dev/null | sed "s|^|$ts,|"
    sleep 5
  done ) > "$OUT/docker-stats.csv" &
STATS_PID=$!

echo "== k6 (escenario $ESC)"
docker rm -f ha01-k6 >/dev/null 2>&1 || true
MSYS_NO_PATHCONV=1 docker run -d --name ha01-k6 --network ha01net -p 5665:5665 \
  -v "$(cygpath -w "$PWD/scripts"):/scripts:ro" \
  -v "$(cygpath -w "$PWD/resultados/clientes.json"):/datos/clientes.json:ro" \
  -v "$(cygpath -w "$PWD/$OUT"):/out" \
  -e K6_WEB_DASHBOARD=true -e K6_WEB_DASHBOARD_HOST=0.0.0.0 -e K6_WEB_DASHBOARD_PORT=5665 \
  -e K6_WEB_DASHBOARD_EXPORT="/out/reporte-k6-$ESC.html" \
  -e COTIZACION_URL=http://cotizacion:8000 -e SCENARIO="$ESC" -e PERFIL=completo -e CLIENTES_FILE=/datos/clientes.json \
  grafana/k6:0.54.0 run --tag "testid=ha01-ronda2-local-$ESC" \
    --out "json=/out/escenario-$ESC.json" --summary-export="/out/escenario-$ESC-summary.json" \
    /scripts/ha01-cotizacion.js >/dev/null
until curl -sf -o /dev/null localhost:5665; do sleep 1; done

echo "== dashboard y grabación"
powershell -NoProfile -Command "\$c=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe').'(default)'; Start-Process \$c -ArgumentList '--user-data-dir=$(cygpath -w "$PERFIL_CHROME")','--disable-gpu','--disable-gpu-compositing','--disable-direct-composition','--disable-features=Translate,DirectComposition','--no-first-run','--no-default-browser-check','--app=http://localhost:5665','--window-size=1600,1000','--window-position=40,40'"
sleep 6
"$FFMPEG" -hide_banner -loglevel error -y -f gdigrab -framerate 2 -i "title=k6 dashboard" -t "$DURACION_VIDEO_S" \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p "$VID.mkv" \
  </dev/null > "$OUT/ffmpeg.log" 2>&1 &
FF_PID=$!

docker wait ha01-k6 > "$OUT/k6_exit.txt"
docker logs ha01-k6 > "$OUT/k6.log" 2>&1
echo "fin_utc=$(date -u +%FT%TZ) k6_exit=$(cat "$OUT/k6_exit.txt")" >> "$OUT/ambiente.txt"
sleep 15; kill "$STATS_PID" 2>/dev/null
wait "$FF_PID" 2>/dev/null || true
"$FFMPEG" -hide_banner -loglevel error -y -i "$VID.mkv" -c copy -movflags +faststart "$VID.mp4" && rm -f "$VID.mkv"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { \$_.CommandLine -like '*chrome-ha01*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo "== análisis"
python -I scripts/analizar.py "$OUT/escenario-$ESC.json" --salida "$OUT/informe-$ESC.json" | tee "$OUT/analisis-$ESC.txt"
gzip -f "$OUT/escenario-$ESC.json"
docker rm ha01-k6 >/dev/null
echo "== escenario $ESC terminado"
