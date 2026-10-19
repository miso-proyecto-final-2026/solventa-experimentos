#!/usr/bin/env bash
# Levanta (o recrea) el cotizacion del backend en local con la latencia del simulador indicada.
# Uso: levantar-servicio.sh <min_ms> <max_ms> <imagen>
set -euo pipefail
MIN="$1"; MAX="$2"; IMAGEN="$3"
docker rm -f ha01-cotizacion >/dev/null 2>&1 || true
docker run -d --name ha01-cotizacion --network ha01net --network-alias cotizacion \
  --cpus 1 --memory 512m -p 8010:8000 \
  -e COTIZACION_DATABASE_URL=postgresql://solventa:solventa@postgres:5432/solventa \
  -e REDIS_URL=redis://redis:6379/0 \
  -e SOLVENTA_MODO_DESARROLLO=1 \
  -e PROVEEDORES_TIMEOUT_S=0.7 \
  -e OPEN_FINANCE_SIM_LATENCIA_MIN_MS="$MIN" -e OPEN_FINANCE_SIM_LATENCIA_MAX_MS="$MAX" \
  "$IMAGEN" sh -c 'alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning' >/dev/null
for _ in $(seq 1 30); do
  curl -sf localhost:8010/health >/dev/null && { echo "servicio listo (Open Finance ${MIN}-${MAX} ms)"; exit 0; }
  sleep 2
done
docker logs ha01-cotizacion | tail -30; exit 1
