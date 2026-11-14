#!/usr/bin/env bash
# HA02 criterio 5 (evidencia indirecta, declarada en el informe): no hay un contador de llamadas a las fuentes, así que
# se prueba que un cliente sin consentimiento no llega a ellas con lo que deja en la base de Perfilamiento:
#   - cada 403 escribe un intento en `intentos_bloqueados` (ADR-0009, punto 5): intentos de origen `carga` = 403 de k6;
#   - un cliente sin consentimiento no tiene ningún perfil en `perfiles_enriquecidos` (el perfil se escribe después de
#     consultar las fuentes, en la misma transacción que su linaje).
# También cuenta los perfiles y las filas de linaje de la ventana (2 por perfil: c2 sin pérdidas).
#
# Como scripts/sembrar-HU19.sh de solventa-backend: corre Python dentro del pod del servicio, con su propia conexión;
# ninguna credencial pasa por esta terminal. Solo lee.
#
# Uso: cluster/consultas-db.sh <desde_utc> <hasta_utc> <sin-consentimiento.json> <salida.json>
#      cluster/consultas-db.sh 2026-11-20T14:00:00Z 2026-11-20T18:00:00Z grupos/sin-consentimiento.json db.json
set -euo pipefail
DESDE="${1:?desde_utc}"
HASTA="${2:?hasta_utc}"
SIN_JSON="${3:?sin-consentimiento.json}"
OUT="${4:?salida.json}"
NS="${NS:-staging}"
DEPLOY="${DEPLOY:-solventa-perfilamiento}"
IDS="$(python -c 'import json,sys;print(",".join(json.load(open(sys.argv[1]))))' "$SIN_JSON")"

kubectl -n "$NS" exec -i "deploy/$DEPLOY" -- python - "$DESDE" "$HASTA" "$IDS" > "$OUT" <<'PY'
import json, os, sys
import psycopg

desde, hasta, ids = sys.argv[1], sys.argv[2], [i for i in sys.argv[3].split(",") if i]
url = (os.environ.get("PERFILAMIENTO_DATABASE_URL") or os.environ["DATABASE_URL"]).replace(
    "postgresql+psycopg://", "postgresql://"
)
with psycopg.connect(url) as c:
    def uno(sql, *params):
        return c.execute(sql, params).fetchone()[0]

    ventana = "BETWEEN %s::timestamptz AND %s::timestamptz"
    print(json.dumps({
        "desde_utc": desde,
        "hasta_utc": hasta,
        "intentos_bloqueados_carga": uno(
            f"SELECT count(*) FROM perfilamiento.intentos_bloqueados WHERE origen = 'carga' AND ocurrido_en {ventana}",
            desde, hasta,
        ),
        "clientes_sin_consentimiento": len(ids),
        "perfiles_de_clientes_sin_consentimiento": uno(
            "SELECT count(*) FROM perfilamiento.perfiles_enriquecidos WHERE cliente_id = ANY(%s)", ids
        ),
        "perfiles_en_ventana": uno(
            f"SELECT count(*) FROM perfilamiento.perfiles_enriquecidos WHERE creado_en {ventana}", desde, hasta
        ),
        "lineas_linaje_en_ventana": uno(
            "SELECT count(*) FROM perfilamiento.linaje_fuentes l JOIN perfilamiento.perfiles_enriquecidos p"
            f" ON p.id = l.perfil_id WHERE p.creado_en {ventana}",
            desde, hasta,
        ),
    }))
PY
echo "Conteos de la base: $(cat "$OUT") -> $OUT"
