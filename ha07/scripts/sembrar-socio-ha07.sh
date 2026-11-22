#!/usr/bin/env bash
# Siembra en staging el socio de prueba de HA07 (`socio-ha07`) y comprueba que obtiene un token por el borde.
#
#   scripts/sembrar-socio-ha07.sh <archivo-del-secreto> <url-publica-de-staging>
#
# El secreto se genera al azar en <archivo-del-secreto> (permisos 600; fuera de cualquier repo) y viaja al pod por la
# entrada estándar: no aparece en la línea de comandos, en los logs ni en la salida. En la base queda solo su hash
# (scripts/sembrar_socio_demo.py de MS Socios, que ya viene en la imagen). Es idempotente: repetirlo rota el secreto.
set -euo pipefail
SECRETO=${1:?archivo del secreto}
URL=${2:?url pública de staging}
CLIENT_ID=socio-ha07
CUOTA_RPS=5

case "$(cd "$(dirname "$SECRETO")" && pwd)" in
  */solventa-hub*|*/solventa-experimentos*|*/solventa-backend*|*/solventa-infra*|*/solventa-web*)
    echo "El secreto no puede quedar dentro de un repositorio: $SECRETO" >&2; exit 1 ;;
esac

umask 077
python -c "import secrets; print(secrets.token_urlsafe(32), end='')" > "$SECRETO"

kubectl -n staging exec -i deploy/solventa-socios -- sh -c '
  IFS= read -r SOCIO_DEMO_CLIENT_SECRET || true
  export SOCIO_DEMO_CLIENT_SECRET SOCIO_DEMO_CLIENT_ID='"$CLIENT_ID"' SOCIO_DEMO_CUOTA_RPS='"$CUOTA_RPS"'
  export SOCIO_DEMO_NOMBRE="Socio de prueba HA07"
  python scripts/sembrar_socio_demo.py' < <(cat "$SECRETO"; echo)

# Comprobación por el borde: solo el código HTTP y la vigencia; nunca el token.
python - "$URL" "$CLIENT_ID" "$SECRETO" <<'EOF'
import json, sys, urllib.parse, urllib.request
url, client_id, archivo = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
datos = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                "client_secret": open(archivo, encoding="utf-8").read()}).encode()
with urllib.request.urlopen(urllib.request.Request(f"{url}/oauth/token", data=datos), timeout=20) as r:
    cuerpo = json.load(r)
    print(f"token de {client_id}: HTTP {r.status}, expires_in={cuerpo['expires_in']}, token_type={cuerpo['token_type']}")
EOF
