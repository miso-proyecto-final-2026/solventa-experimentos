#!/usr/bin/env bash
# HA02 escenario caida_5b: borra solo la entrada principal de Open Finance (`perfil:<cliente>:open_finance`) y deja el
# respaldo (`perfil:<cliente>:open_finance:respaldo`, 7 días). Con Open Finance caída, Perfilamiento debe responder
# entonces desde el respaldo, marcado como degradado (criterio 4), en lugar del valor por defecto.
#
# El patrón de Redis abarca la clave completa, así que `perfil:*:open_finance` no toca las que terminan en `:respaldo`.
# La URL de Redis (con TLS y token) la lee el pod desde el Secret del servicio; no pasa por esta terminal.
# Ojo: Cotización usa la misma base y la misma clave (solventa-backend#84); en HA02 sus clientes son otros.
#
# Uso: cluster/borrar-cache-primaria.sh
set -euo pipefail
NS="${NS:-staging}"
SECRET="${SECRET:-solventa-perfilamiento-connection}"
FUENTE="${FUENTE:-open_finance}"
POD="ha02-borrar-primaria-$(date +%s)"
kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  labels: { app.kubernetes.io/part-of: ha02 }
spec:
  restartPolicy: Never
  containers:
    - name: borrar
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
          set -e
          # Igual que limpiar-cache.sh de HA03: redis-cli necesita el usuario default explícito.
          URL="\$(echo "\$REDIS_URL" | sed 's#^\(rediss*\)://:#\1://default:#')"
          redis-cli --tls -u "\$URL" --scan --pattern 'perfil:*:$FUENTE' > /tmp/claves
          echo "principales de $FUENTE: \$(wc -l < /tmp/claves)"
          echo "respaldos de $FUENTE: \$(redis-cli --tls -u "\$URL" --scan --pattern 'perfil:*:$FUENTE:respaldo' | wc -l)"
          [ -s /tmp/claves ] && xargs -n 500 redis-cli --tls -u "\$URL" DEL < /tmp/claves > /dev/null
          echo "principales tras borrar: \$(redis-cli --tls -u "\$URL" --scan --pattern 'perfil:*:$FUENTE' | wc -l)"
EOF
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$POD" --timeout=180s
kubectl -n "$NS" logs "$POD"
kubectl -n "$NS" delete pod "$POD" --wait=false > /dev/null
