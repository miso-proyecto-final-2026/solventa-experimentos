# HA02 — perfilamiento en línea sobre staging

Experimento de arquitectura del Sprint 3 sobre el staging de `solventa-infra` en AWS:

- **HA02** ([#43](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/43)): MS Perfilamiento enriquece el
  perfil con Open Finance y Open Data (simulados) con p95 ≤ 400 ms y p99 ≤ 800 ms por escenario, sostiene ≥ 20.000
  perfilamientos por hora sin degradar la cotización en línea, degrada de forma controlada con una fuente lenta o caída,
  bloquea a los clientes sin consentimiento y deja de servir el perfil tras una revocación en ≤ 5 minutos.

Cómo se ejecuta la sesión (compartida con la evidencia de HU24): [`RUNBOOK.md`](RUNBOOK.md). El informe con veredicto
va en esta carpeta al terminar la sesión.

| Carpeta | Contenido |
|---|---|
| `scripts/` | `ha02-perfilamiento.js` (k6, un escenario por corrida: humo, calentar, miss, hit, meseta, lenta_4a, lenta_4b, caida_5a, caida_5b y sin_consentimiento), `ha02-cotizacion.js` (k6, cotización constante con línea base y meseta), `revocacion.py` (tiempo desde la revocación hasta el bloqueo) y `analizar-ha02.py` (veredicto por criterio a partir de la evidencia cruda). |
| `ec2/` | `correr-ha02.sh`: corre cada carga en el generador y sube la evidencia al bucket. |
| `cluster/` | `borrar-cache-primaria.sh` (deja solo los respaldos para caida_5b), `claves-redis.sh` (claves de los revocados que siguen en Redis) y `consultas-db.sh` (intentos bloqueados y perfiles en la base, evidencia indirecta de c5). |
| `tests/` | Pruebas del analizador con datos sintéticos. |

Se reutiliza de las sesiones anteriores:

- El generador `../ha03-ha04/ec2-k6` (una EC2 en la VPC de staging, sin SSH, con bucket de evidencia), que ahora también
  sube los scripts de HA02; aquí se usa como `c5.large`.
- El muestreo y los eventos del clúster (`../ha03-ha04/cluster/muestrear-cluster.sh`, `eventos-cluster.sh`) y el
  vaciado de la caché (`limpiar-cache.sh`).
- La siembra de clientes `../ha01-ronda2/seed/sembrar-clientes.py`, con la opción nueva `--sin-consentimiento`.

## Validación local

Sin AWS: `node --check` de los `.js` (copiados como `.mjs`), `bash -n` de los `.sh`, `python -m py_compile` de los
`.py`, `terraform -chdir=../ha03-ha04/ec2-k6 validate` y las pruebas del analizador (`python -m pytest tests`, requiere
`pytest`). Nada de esto se ha ejecutado aún contra staging.
