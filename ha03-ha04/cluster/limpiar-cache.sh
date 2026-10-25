#!/usr/bin/env bash
# Vacía la base 0 de Redis (la de MS Cotización) antes de cada corrida de HA03, como en HA01:
# así cada corrida arranca con la caché fría y la fase `base` es comparable entre corridas.
# ElastiCache de staging exige TLS y token; la URL completa (rediss://:<token>@host:port/0)
# la lee el pod desde el Secret del servicio, sin pasar por esta terminal.
#
# Uso: cluster/limpiar-cache.sh
set -euo pipefail
NS="${NS:-staging}"
SECRET="${SECRET:-solventa-backend-connection}"
POD="ha03-redis-flush-$(date +%s)"

kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  labels: { app.kubernetes.io/part-of: ha03-ha04 }
spec:
  restartPolicy: Never
  containers:
    - name: flush
      image: redis:7.4-alpine
      env:
        - name: REDISCLI_NO_AUTH_WARNING
          value: "1"
        - name: REDIS_URL
          valueFrom: { secretKeyRef: { name: $SECRET, key: REDIS_URL } }
      command:
        - sh
        - -c
        - |
          echo "claves antes: \$(redis-cli --tls -u "\$REDIS_URL" DBSIZE)"
          redis-cli --tls -u "\$REDIS_URL" FLUSHDB
          echo "claves después: \$(redis-cli --tls -u "\$REDIS_URL" DBSIZE)"
EOF

kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$POD" --timeout=120s \
  || { kubectl -n "$NS" logs "$POD"; kubectl -n "$NS" delete pod "$POD" --wait=false; exit 1; }
kubectl -n "$NS" logs "$POD"
kubectl -n "$NS" delete pod "$POD" --wait=false
