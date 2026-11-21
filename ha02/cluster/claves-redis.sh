#!/usr/bin/env bash
# HA02 criterio 6: tras revocar, el perfil no se sirve, pero ¿sigue en Redis? (el borrado es de V-11).
# Cuenta, para los clientes revocados, cuántas claves de caché y de respaldo siguen existiendo y escribe un JSON para
# analizar-ha02.py (--redis). No lee los valores: solo EXISTS.
#
# Uso: cluster/claves-redis.sh <revocacion-ids.json> <salida.json>
set -euo pipefail
IDS_JSON="${1:?uso: claves-redis.sh <revocacion-ids.json> <salida.json>}"
OUT="${2:?uso: claves-redis.sh <revocacion-ids.json> <salida.json>}"
NS="${NS:-staging}"
SECRET="${SECRET:-solventa-perfilamiento-connection}"
POD="ha02-claves-redis-$(date +%s)"
IDS="$(python -c 'import json,sys;print(" ".join(json.load(open(sys.argv[1]))))' "$IDS_JSON")"
[ -n "$IDS" ] || { echo "No hay cliente_id en $IDS_JSON" >&2; exit 1; }

kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  labels: { app.kubernetes.io/part-of: ha02 }
spec:
  restartPolicy: Never
  containers:
    - name: claves
      image: redis:7.4-alpine
      env:
        - name: REDISCLI_NO_AUTH_WARNING
          value: "1"
        - name: REDIS_URL
          valueFrom: { secretKeyRef: { name: $SECRET, key: REDIS_URL } }
        - name: IDS
          value: "$IDS"
      command:
        - sh
        - -c
        - |
          set -e
          URL="\$(echo "\$REDIS_URL" | sed 's#^\(rediss*\)://:#\1://default:#')"
          n=0; principal=0; respaldo=0
          for id in \$IDS; do
            n=\$((n+1))
            for f in open_finance open_data; do
              principal=\$((principal + \$(redis-cli --tls -u "\$URL" EXISTS "perfil:\$id:\$f")))
              respaldo=\$((respaldo + \$(redis-cli --tls -u "\$URL" EXISTS "perfil:\$id:\$f:respaldo")))
            done
          done
          echo "{\"clientes\": \$n, \"claves_principales\": \$principal, \"claves_respaldo\": \$respaldo, \"posibles_por_tipo\": \$((n*2))}"
EOF
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$POD" --timeout=180s
kubectl -n "$NS" logs "$POD" | tail -1 > "$OUT"
kubectl -n "$NS" delete pod "$POD" --wait=false > /dev/null
echo "Claves de los clientes revocados: $(cat "$OUT") -> $OUT"
