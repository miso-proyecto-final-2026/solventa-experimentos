#!/usr/bin/env bash
# Corre una carga de HA03 o HA04 desde el generador y sube la evidencia al bucket.
# Uso (en la EC2, dentro de /opt/ha03):
#   ./correr-escenario.sh ha03 <humo|completo> <url_cotizacion>
#   ./correr-escenario.sh ha04 <duración k6, p. ej. 90m> <url_cotizacion> [tasa_por_segundo]
#   ./correr-escenario.sh ha03 humo http://<nlb_dns_name>:30080
# Un threshold incumplido es un resultado, no un error: el script no aborta por eso (lección de HA01).
# El análisis completo de HA03 necesita el muestreo del clúster, que se toma en la máquina del operador:
# aquí solo se calcula el resumen de k6 y se sube el crudo.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./entorno
ulimit -n 65535

EXP="${1:?ha03|ha04}"
PERFIL="${2:?humo|completo (ha03) o duración (ha04)}"
URL="${3:?url de cotización}"
TASA="${4:-10}"
ID="$(date -u +%Y%m%dT%H%M%SZ)-$EXP-$PERFIL"
OUT="results/$ID"
mkdir -p "$OUT"

case "$EXP" in
  ha03)
    if [ ! -s clientes.json ]; then
      echo "Falta clientes.json: corre sembrar-clientes.py primero" >&2
      exit 1
    fi
    SCRIPT=ha03-autoescalado.js
    ENTORNO=(COTIZACION_URL="$URL" PERFIL="$PERFIL" CLIENTES_FILE=clientes.json)
    ;;
  ha04)
    SCRIPT=ha04-trafico.js
    RUN_ID="ha04-$(date -u +%m%d%H%M)"
    ENTORNO=(COTIZACION_URL="$URL" RUN_ID="$RUN_ID" DURACION="$PERFIL" TASA="$TASA")
    ;;
  *)
    echo "Experimento desconocido: $EXP (use ha03 o ha04)" >&2
    exit 1
    ;;
esac

imds() {
  local token
  token="$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
  curl -s -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/$1"
}

{
  echo "corrida=$ID"
  echo "url=$URL"
  echo "inicio_utc=$(date -u +%FT%TZ)"
  echo "k6=$(k6 version | head -1)"
  echo "instancia=$(imds instance-id) $(imds instance-type) $(imds placement/availability-zone) $(imds local-ipv4)"
  printf '%s\n' "${ENTORNO[@]}" | grep -v '^COTIZACION_URL='
  [ -s clientes.json ] && echo "clientes=$(python3 -c 'import json;print(len(json.load(open("clientes.json"))))')"
} > "$OUT/ambiente.txt"

env "${ENTORNO[@]}" k6 run --tag "testid=$ID" \
  --out "json=$OUT/$EXP.json" \
  --summary-export="$OUT/$EXP-summary.json" \
  "$SCRIPT" 2>&1 | tee "$OUT/k6.log"
echo "k6_exit=${PIPESTATUS[0]}" >> "$OUT/ambiente.txt"
echo "fin_utc=$(date -u +%FT%TZ)" >> "$OUT/ambiente.txt"

gzip "$OUT/$EXP.json"
aws s3 cp --recursive "$OUT/" "s3://$BUCKET/results/$ID/"
echo "Evidencia en s3://$BUCKET/results/$ID/"
