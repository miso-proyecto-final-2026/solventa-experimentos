#!/usr/bin/env bash
# Guarda la evidencia de Kubernetes al terminar una corrida (HA03 c7, HA04 c8): eventos del namespace, estado del HPA,
# pods con su nodo y zona, y nodos. Se ejecuta antes del destroy: después ya no existe.
#
# Uso:  cluster/eventos-cluster.sh <carpeta_salida>
set -uo pipefail
OUT="${1:?uso: eventos-cluster.sh <carpeta_salida>}"
NS="${NS:-staging}"
mkdir -p "$OUT"

kubectl -n "$NS" get events --sort-by=.lastTimestamp -o wide > "$OUT/eventos-$NS.txt" 2>&1
kubectl -n "$NS" get hpa -o yaml > "$OUT/hpa.yaml" 2>&1
kubectl -n "$NS" describe hpa > "$OUT/hpa-describe.txt" 2>&1
kubectl -n "$NS" get pods -o wide > "$OUT/pods.txt" 2>&1
kubectl get nodes -L topology.kubernetes.io/zone -o wide > "$OUT/nodos.txt" 2>&1
kubectl -n "$NS" get deploy,rs,pdb -o wide > "$OUT/cargas.txt" 2>&1
echo "Evidencia del clúster en $OUT/"
