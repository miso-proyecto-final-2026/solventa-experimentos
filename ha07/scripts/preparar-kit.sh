#!/usr/bin/env bash
# Arma la carpeta aislada del integrador de HA07: solo la documentación publicada, las credenciales y el registro.
#
#   scripts/preparar-kit.sh <carpeta-destino> <url-publica-de-staging> <archivo-del-secreto> [<repo-backend>]
#
# - <carpeta-destino> debe estar fuera del hub y de workspace/ (p. ej. ~/ha07-integrador) y no existir o estar vacía.
# - La documentación sale de <repo-backend> (por defecto ../../solventa-backend, desde esta carpeta): el contrato, el catálogo
#   de errores y la guía de inicio, tal como están en la rama que tenga en ese momento.
# - El secreto se copia a credenciales/secreto.txt sin mostrarlo.
# - Deja en ../resultados/kit-manifiesto.txt la rama, el commit y el sha256 de cada documento entregado, para el informe.
set -euo pipefail
AQUI=$(cd "$(dirname "$0")/.." && pwd)
DESTINO=${1:?carpeta destino}
URL=${2:?url pública de staging}
SECRETO=${3:?archivo del secreto}
BACKEND=$(cd "${4:-$AQUI/../../solventa-backend}" && pwd)
DOCS="$BACKEND/services/socios/docs/api"

DESTINO=$(realpath -m "$DESTINO")
case "$DESTINO" in
  */solventa-hub*|*/workspace/*|*/solventa-experimentos*) echo "El kit debe quedar fuera del hub y de workspace/: $DESTINO" >&2; exit 1 ;;
esac
mkdir -p "$DESTINO"
[ -z "$(ls -A "$DESTINO")" ] || { echo "La carpeta destino no está vacía: $DESTINO" >&2; exit 1; }

mkdir -p "$DESTINO"/{documentacion,credenciales,registro,socio,.claude}
cp "$DOCS/openapi.json" "$DOCS/errores-y-reintentos.md" "$DOCS/guia-de-inicio.md" "$DESTINO/documentacion/"
cp "$AQUI/kit/TAREA.md" "$DESTINO/"
# En el repo no se llama CLAUDE.md porque el .gitignore de experimentos lo excluye.
cp "$AQUI/kit/CLAUDE-integrador.md" "$DESTINO/CLAUDE.md"
cp "$AQUI/kit/settings.json" "$DESTINO/.claude/settings.json"
cp "$AQUI/kit/hoja-de-tiempos.csv" "$AQUI/kit/defectos.md" "$DESTINO/registro/"

umask 077
cp "$SECRETO" "$DESTINO/credenciales/secreto.txt"
cat > "$DESTINO/credenciales/ambiente.txt" <<EOF
Ambiente de pruebas (staging) de la API de socios de Solventa

URL base:      ${URL%/}
client_id:     socio-ha07
client_secret: en credenciales/secreto.txt

Su contrato API habilita: cotizaciones:crear, suscripciones:crear, suscripciones:firmar, polizas:consultar
Cuota: 5 solicitudes por segundo
EOF
printf 'credenciales/\n' > "$DESTINO/.gitignore"

{
  echo "Kit del integrador HA07 — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  sucio=$(git -C "$BACKEND" status --porcelain -- services/socios/docs/api | grep -q . && echo " (con cambios sin commit en docs/api)" || true)
  echo "backend: $(git -C "$BACKEND" branch --show-current) $(git -C "$BACKEND" rev-parse --short HEAD)$sucio"
  (cd "$DESTINO" && sha256sum documentacion/* TAREA.md CLAUDE.md .claude/settings.json)
} > "$AQUI/resultados/kit-manifiesto.txt"

echo "Kit listo en $DESTINO"
(cd "$DESTINO" && find . -type f | sort)
