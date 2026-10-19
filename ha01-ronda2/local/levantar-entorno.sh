#!/usr/bin/env bash
# Entorno local de HA01 ronda 2 (corrida aprobada en Docker en lugar de AWS).
# Réplica de staging en recursos por contenedor: 1 CPU / 512Mi para Cotización e Identidad,
# pero 1 sola réplica de cada uno (staging: HPA min 2).
#
# Uso: local/levantar-entorno.sh <min_ms> <max_ms>        primera vez: red, Postgres, Redis, Identidad y Cotización
#      local/levantar-entorno.sh <min_ms> <max_ms> --solo-cotizacion   recrea solo Cotización con otra latencia
# La clave de servicio Cotización→Identidad se genera al azar y solo vive en las variables de los contenedores.
set -euo pipefail
MIN="$1"; MAX="$2"; MODO="${3:-}"
IMG_COT="${IMG_COT:-ha01-cotizacion-local:116e410}"
IMG_IDE="${IMG_IDE:-ha01-identidad-local:latest}"
DB_URL="postgresql://solventa:solventa@postgres:5432/solventa"

esperar() {  # esperar <contenedor> <puerto_host>
  for _ in $(seq 1 60); do curl -sf "localhost:$2/health" >/dev/null && return 0; sleep 2; done
  docker logs "$1" | tail -30; return 1
}

if [ "$MODO" != "--solo-cotizacion" ]; then
  docker rm -f ha01-cotizacion ha01-identidad ha01-redis ha01-pg >/dev/null 2>&1 || true
  docker network create ha01net >/dev/null 2>&1 || true
  docker run -d --name ha01-pg --network ha01net --network-alias postgres \
    -e POSTGRES_USER=solventa -e POSTGRES_PASSWORD=solventa -e POSTGRES_DB=solventa postgres:16-alpine >/dev/null
  docker run -d --name ha01-redis --network ha01net --network-alias redis redis:7-alpine >/dev/null
  until docker exec ha01-pg pg_isready -U solventa -q; do sleep 1; done
  sleep 2
  CLAVE="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')"
  docker run -d --name ha01-identidad --network ha01net --network-alias identidad \
    --cpus 1 --memory 512m -p 8011:8000 \
    -e DATABASE_URL="$DB_URL" -e SERVICE_API_KEYS="$CLAVE" \
    "$IMG_IDE" sh -c 'alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning' >/dev/null
  esperar ha01-identidad 8011
  echo "identidad lista"
else
  CLAVE="$(docker inspect ha01-identidad --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^SERVICE_API_KEYS=//p')"
fi

docker rm -f ha01-cotizacion >/dev/null 2>&1 || true
docker run -d --name ha01-cotizacion --network ha01net --network-alias cotizacion \
  --cpus 1 --memory 512m -p 8010:8000 \
  -e COTIZACION_DATABASE_URL="$DB_URL" \
  -e REDIS_URL=redis://redis:6379/0 \
  -e SOLVENTA_MODO_DESARROLLO=1 \
  -e IDENTIDAD_URL=http://identidad:8000 -e IDENTIDAD_SERVICE_KEY="$CLAVE" \
  -e PROVEEDORES_TIMEOUT_S=0.7 -e PROVEEDORES_INTERVALO_HALF_OPEN_S=30 -e PROVEEDORES_TTL_S=300 \
  -e OPEN_FINANCE_SIM_MODO=ninguno \
  -e OPEN_FINANCE_SIM_LATENCIA_MIN_MS="$MIN" -e OPEN_FINANCE_SIM_LATENCIA_MAX_MS="$MAX" \
  "$IMG_COT" sh -c 'alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning' >/dev/null
esperar ha01-cotizacion 8010
echo "cotizacion lista (Open Finance ${MIN}-${MAX} ms)"
