#!/usr/bin/env bash
# Corre una carga de HA02 desde el generador (la EC2 de ../ha03-ha04/ec2-k6) y sube la evidencia al bucket.
# Uso (en la EC2, dentro de /opt/ha03):
#   ./correr-ha02.sh perfilamiento <escenario> <url_perfilamiento>     # humo, calentar, miss, hit, lenta_4a, ...
#   ./correr-ha02.sh meseta <url_perfilamiento> <url_cotizacion>        # 10 min de cotización sola + 60 min juntas
#   ./correr-ha02.sh revocacion <url_identidad> <url_perfilamiento>
#   ./correr-ha02.sh perfilamiento miss http://<nlb_dns_name>:30086
#
# Necesita, en /opt/ha03:
#   - clave-carga   la clave temporal de carga (chmod 600). Se pasa a k6 por el entorno; nunca va a la evidencia.
#   - grupos/*.json los cliente_id de cada grupo (sembrar-clientes.py): miss, pool, lenta-4a, lenta-4b, caida-5a,
#                   sin-consentimiento y cotizacion.
# Un threshold incumplido es un resultado, no un error: el script no aborta por eso (lección de HA01).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./entorno
ulimit -n 65535

MODO="${1:?perfilamiento|meseta|revocacion}"
[ -s clave-carga ] || { echo "Falta clave-carga (la clave temporal de carga, chmod 600)" >&2; exit 1; }
SERVICE_KEY="$(cat clave-carga)"

grupo() {  # escenario -> archivo del grupo de clientes
  case "$1" in
    miss) echo grupos/miss.json ;;
    lenta_4a) echo grupos/lenta-4a.json ;;
    lenta_4b) echo grupos/lenta-4b.json ;;
    caida_5a) echo grupos/caida-5a.json ;;
    *) echo grupos/pool.json ;;
  esac
}

imds() {
  local token
  token="$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
  curl -s -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/$1"
}

contar() { [ -s "$1" ] && python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))))' "$1" || echo 0; }

ambiente() {  # carpeta, descripción
  {
    echo "corrida=$(basename "$1")"
    echo "descripcion=$2"
    echo "inicio_utc=$(date -u +%FT%TZ)"
    echo "k6=$(k6 version | head -1)"
    echo "instancia=$(imds instance-id) $(imds instance-type) $(imds placement/availability-zone) $(imds local-ipv4)"
    for g in grupos/*.json; do echo "grupo_$(basename "$g" .json)=$(contar "$g")"; done
  } > "$1/ambiente.txt"
}

# k6 de Perfilamiento: escenario, url, carpeta. La clave va por el entorno del proceso, no por la línea de comandos.
k6_perfilamiento() {
  SERVICE_KEY="$SERVICE_KEY" PERFILAMIENTO_URL="$2" ESCENARIO="$1" CLIENTES_FILE="$(grupo "$1")" \
    SIN_CONSENTIMIENTO_FILE=grupos/sin-consentimiento.json \
    k6 run --tag "testid=$(basename "$3")" --out "json=$3/perfilamiento.json" \
      --summary-export="$3/perfilamiento-summary.json" ha02-perfilamiento.js > "$3/k6-perfilamiento.log" 2>&1
  echo "k6_perfilamiento_exit=$?" >> "$3/ambiente.txt"
}

subir() {
  echo "fin_utc=$(date -u +%FT%TZ)" >> "$1/ambiente.txt"
  # Los cliente_id sin consentimiento (sintéticos) los necesita cluster/consultas-db.sh en la máquina del operador.
  [ -s grupos/sin-consentimiento.json ] && cp grupos/sin-consentimiento.json "$1/"
  find "$1" -name '*.json' -size +1M -exec gzip {} +
  aws s3 cp --recursive "$1/" "s3://$BUCKET/results/ha02/$(basename "$1")/"
  echo "Evidencia en s3://$BUCKET/results/ha02/$(basename "$1")/"
}

case "$MODO" in
  perfilamiento)
    ESC="${2:?escenario}"
    URL="${3:?url de perfilamiento}"
    OUT="results/$(date -u +%Y%m%dT%H%M%SZ)-ha02-$ESC"
    mkdir -p "$OUT"
    ambiente "$OUT" "perfilamiento $ESC"
    k6_perfilamiento "$ESC" "$URL" "$OUT"
    tail -40 "$OUT/k6-perfilamiento.log"
    subir "$OUT"
    ;;
  meseta)
    URL_P="${2:?url de perfilamiento}"
    URL_C="${3:?url de cotización}"
    BASE_S="${BASE_S:-600}"
    OUT="results/$(date -u +%Y%m%dT%H%M%SZ)-ha02-meseta"
    mkdir -p "$OUT"
    ambiente "$OUT" "meseta: cotización 70 min (10 min solas) + perfilamiento 60 min desde el minuto 10"
    COTIZACION_URL="$URL_C" CLIENTES_FILE=grupos/cotizacion.json BASE_S="$BASE_S" \
      k6 run --tag "testid=$(basename "$OUT")-cotizacion" --out "json=$OUT/cotizacion.json" \
        --summary-export="$OUT/cotizacion-summary.json" ha02-cotizacion.js > "$OUT/k6-cotizacion.log" 2>&1 &
    PID_C=$!
    echo "cotizacion_inicio_utc=$(date -u +%FT%TZ)" >> "$OUT/ambiente.txt"
    sleep "$BASE_S"
    echo "perfilamiento_inicio_utc=$(date -u +%FT%TZ)" >> "$OUT/ambiente.txt"
    k6_perfilamiento meseta "$URL_P" "$OUT"
    wait "$PID_C"
    echo "k6_cotizacion_exit=$?" >> "$OUT/ambiente.txt"
    tail -25 "$OUT/k6-perfilamiento.log"
    tail -25 "$OUT/k6-cotizacion.log"
    subir "$OUT"
    ;;
  revocacion)
    URL_I="${2:?url de identidad}"
    URL_P="${3:?url de perfilamiento}"
    OUT="results/$(date -u +%Y%m%dT%H%M%SZ)-ha02-revocacion"
    mkdir -p "$OUT"
    ambiente "$OUT" "revocación: 50 clientes, consulta cada 1 s hasta 300 s"
    python3 revocacion.py --identidad "$URL_I" --perfilamiento "$URL_P" --clave-archivo clave-carga \
      --n "${N_REVOCACION:-50}" --salida "$OUT/revocacion.json" 2>&1 | tee "$OUT/revocacion.log"
    echo "revocacion_exit=${PIPESTATUS[0]}" >> "$OUT/ambiente.txt"
    subir "$OUT"
    ;;
  *)
    echo "Modo desconocido: $MODO (use perfilamiento, meseta o revocacion)" >&2
    exit 1
    ;;
esac
