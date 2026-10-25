#!/usr/bin/env bash
# Muestrea un servicio de staging durante HA03/HA04 (evidencia cruda, HA03 c7): réplicas del HPA, sus dos métricas
# frente a sus objetivos (solicitudes en curso por pod y CPU, HA03 c4), pods listos y por zona, reinicios y CPU/memoria
# por pod. Adaptado de ha01-ronda2 (paquete local de evidencia): añade `pods_listos` (para scaling_time_seconds),
# `pods_por_zona` (HA04 c6) y las columnas de concurrencia (ADR-0014). Un HPA solo por CPU deja esas columnas vacías.
#
# Uso:  cluster/muestrear-cluster.sh <salida.csv> [intervalo_s]   (Ctrl+C para detener)
#       APP=solventa-identidad cluster/muestrear-cluster.sh identidad.csv 5
set -uo pipefail
OUT="${1:?uso: muestrear-cluster.sh <salida.csv> [intervalo_s]}"
INTERVALO="${2:-5}"
NS="${NS:-staging}"
APP="${APP:-solventa-backend}"

echo "timestamp_utc,hpa_actual,hpa_deseado,hpa_min,hpa_max,hpa_cpu_pct,hpa_cpu_objetivo,hpa_concurrencia,hpa_concurrencia_objetivo,pods_listos,pods_running,pods_otros,reinicios_total,cpu_total_m,mem_total_mi,pods_por_zona,detalle_pods" > "$OUT"
trap 'exit 0' TERM INT

# nodo -> zona; se refresca en cada muestra porque HA04 acordona y drena nodos.
zonas_de_nodos() {
  kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.labels.topology\.kubernetes\.io/zone}{"\n"}{end}' 2>/dev/null
}

while true; do
  ts="$(date -u +%FT%TZ)"
  # Separador `|`: con espacios, una métrica ausente correría las demás columnas. Las métricas se filtran por tipo
  # porque el orden de currentMetrics depende del HPA (con concurrencia, la CPU ya no es la primera).
  IFS='|' read -r h_act h_des h_min h_max h_cpu h_cpu_obj h_conc h_conc_obj <<<"$(kubectl -n "$NS" get hpa "$APP" -o jsonpath='{.status.currentReplicas}|{.status.desiredReplicas}|{.spec.minReplicas}|{.spec.maxReplicas}|{.status.currentMetrics[?(@.type=="Resource")].resource.current.averageUtilization}|{.spec.metrics[?(@.type=="Resource")].resource.target.averageUtilization}|{.status.currentMetrics[?(@.type=="Pods")].pods.current.averageValue}|{.spec.metrics[?(@.type=="Pods")].pods.target.averageValue}' 2>/dev/null)"
  # fase listo reinicios nodo
  pods="$(kubectl -n "$NS" get pods -l "app.kubernetes.io/name=$APP" -o jsonpath='{range .items[*]}{.status.phase} {.status.containerStatuses[0].ready} {.status.containerStatuses[0].restartCount} {.spec.nodeName}{"\n"}{end}' 2>/dev/null)"
  running="$(awk '$1=="Running"' <<<"$pods" | grep -c . || true)"
  listos="$(awk '$1=="Running" && $2=="true"' <<<"$pods" | grep -c . || true)"
  otros="$(awk 'NF && $1!="Running"' <<<"$pods" | grep -c . || true)"
  reinicios="$(awk '{s+=$3} END {print s+0}' <<<"$pods")"
  por_zona="$(awk 'NR==FNR {z[$1]=$2; next} $1=="Running" && $4!="" {n[z[$4]]++} END {for (k in n) printf "%s=%d;", k, n[k]}' <(zonas_de_nodos) <(printf '%s\n' "$pods"))"
  top="$(kubectl -n "$NS" top pod -l "app.kubernetes.io/name=$APP" --no-headers 2>/dev/null)"
  cpu="$(awk '{gsub("m","",$2); s+=$2} END {print s+0}' <<<"$top")"
  mem="$(awk '{gsub("Mi","",$3); s+=$3} END {print s+0}' <<<"$top")"
  detalle="$(awk '{printf "%s=%s/%s;", $1, $2, $3}' <<<"$top")"
  echo "$ts,${h_act:-},${h_des:-},${h_min:-},${h_max:-},${h_cpu:-},${h_cpu_obj:-},${h_conc:-},${h_conc_obj:-},$listos,$running,$otros,$reinicios,$cpu,$mem,$por_zona,$detalle" >> "$OUT"
  sleep "$INTERVALO"
done
