# HA03 y HA04 — autoescalado y caída de zona sobre staging

Experimentos de arquitectura del Sprint 2 sobre el staging de `solventa-infra` en AWS:

- **HA03** ([#44](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/44)): el HPA de MS Cotización
  escala de 500 a 50.000 cotizaciones/min en ≤ 60 s, sin degradar la latencia (RQ-L1) ni producir errores por saturación.
- **HA04** ([#45](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/45)): los recorridos críticos siguen
  operando al perder una zona; RTO ≤ 10 min, RPO ≤ 30 s, disponibilidad frente a 99,97 % y despliegue sin interrupción.

Cómo se ejecuta la sesión (despliegue, experimentos y destroy): [`RUNBOOK.md`](RUNBOOK.md).

| Carpeta | Contenido |
|---|---|
| `scripts/` | `ha03-autoescalado.js` (k6, rampa a 50.000/min con fases etiquetadas), `ha04-trafico.js` (k6, tráfico constante con un cliente_id único por solicitud), `analizar-ha03.py` (percentiles por fase, `scaling_time_seconds` y qué métrica disparó cada escalado, desde el muestreo) y `analizar-ha04.py` (disponibilidad, recuperación por falla y lista de confirmadas para el RPO). |
| `ec2-k6/` | Terraform del generador: una `c5.large` en una subred privada de la VPC de staging, sin SSH ni IP pública (SSM), con un bucket temporal para scripts y evidencia. Estado local: vive solo durante la sesión. `correr-escenario.sh` corre una carga y sube el crudo. |
| `cluster/` | `muestrear-cluster.sh` (HPA con sus dos métricas frente a sus objetivos —solicitudes en curso por pod y CPU—, pods listos y por zona, CPU y memoria cada 5 s), `eventos-cluster.sh` (eventos, HPA, pods y nodos al cerrar) y `limpiar-cache.sh` (vacía la caché de Cotización). |

La siembra de clientes con consentimiento reutiliza `../ha01-ronda2/seed/sembrar-clientes.py`.

## Validación local

Sin AWS: `terraform -chdir=ec2-k6 validate`, `bash -n` de los `.sh`, `node --check` de los `.js` (copiados como `.mjs`)
y las pruebas de los dos analizadores con datos sintéticos (`python -m pytest tests`, requiere `pytest`): percentiles por
fase, eventos de escalado, la métrica que dispara cada uno (criterio 4), disponibilidad, recuperación y RPO de HA04.
Nada de esto se ha ejecutado aún contra staging.
