# Runbook — sesión de HA03 y HA04 sobre staging en AWS

Historias: **HA03** ([#44](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/44), autoescalado de
cotización) y **HA04** ([#45](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/45), tolerancia a la
caída de una zona). Operador: la persona dueña de la cuenta de AWS. Ambiente: staging de `solventa-infra` (EKS
`solventa-staging`, RDS Multi-AZ, Redis con réplica, MSK y borde), levantado bajo demanda y **destruido al terminar**.

> **Se despliega desde ramas sin fusionar (D10).** Primero el experimento y después los PRs: los de backend e infra se
> abren (o vuelven a revisión) al cerrar la tarea, con la evidencia de esta sesión.
>
> **Nada de este runbook se ha ejecutado todavía.** Los scripts se validaron en local: sintaxis (`bash -n`, `node
> --check`), `terraform validate` de `ec2-k6/` y los analizadores con datos sintéticos. Los tiempos y costos son
> estimaciones a precio de lista de `us-east-1`; contrástalos con Cost Explorer al cerrar.

## Decisiones de la sesión

| # | Decisión | Valor |
|---|---|---|
| D1 | Desde dónde se opera | **Local**, con el perfil de AWS del operador. No se tocan las variables de GitHub: el CI sigue apuntando a la cuenta anterior |
| D2 | ARN de administrador del clúster e IP del operador | Por `-var` en el plan; sin cambiar `staging.tfvars` |
| D3 | Bus de eventos | `enable_events = true` (Suscripción y Siniestros no arrancan sin brokers) |
| D4 | Métrica del HPA (HA03 c4) | **Solicitudes en curso por pod** (`http_requests_in_flight`), aprobado por Stiven (opción B): Prometheus mínimo + Prometheus Adapter + HPA nativo, con la CPU de respaldo (ADR-0014 de `solventa-backend`, en estado Propuesta). Subida abierta solo para Cotización: hasta el doble o +4 pods cada 15 s, sin estabilización |
| D5 | Ruta de la carga | k6 en una EC2 dentro de la VPC contra el **NLB interno** (NodePort). El borde (CloudFront, WAF, API Gateway) queda **fuera de la medición** |
| D6 | Nodos | `node_count = 6` (t3.medium). Los nodos son **fijos**: no hay cluster-autoscaler, se declara |
| D7 | Orden | HA03 primero; HA04 después en la misma sesión. **Corte:** si al terminar HA03 se pasó del tiempo, se destruye y HA04 va otro día |
| D8 | Latencia del simulador de Open Finance | 200–500 ms, como el escenario A de HA01, para que la meta de RQ-L1 sea comparable. En staging el valor por defecto es 20–80 ms; se cambia con `kubectl set env` durante la sesión |
| D9 | Perfil de carga de HA03 | 5 min a 500/min → subida a 50.000/min en 5 min → 10 min de meseta → bajada en 1 min → 8 min a 500/min. **Propuesta**: el issue no fija la forma |
| D10 | Origen del despliegue | **Ramas, sin fusionar.** Imagen de Cotización (y de los demás servicios) construida desde `feature/HA03-metrica-solicitudes-en-curso` de `solventa-backend` (borrador solventa-backend#44). Infra aplicada desde una rama **local** `sesion/HA03-HA04-local` = `feature/HA03-hpa-solicitudes-concurrentes` + `infra/HA04-dispersion-donotschedule` (solventa-infra#18), que no se sube. Se anotan los SHA exactos |
| D11 | Objetivo de concurrencia | **3 por pod** de partida (estimación por la ley de Little). Se recalibra con el humo (paso 6.3) antes de la corrida completa; el valor usado queda en `sesion.log` y en el ADR-0014 |

## Costo y duración estimados

| Recurso | USD/h aprox. |
|---|---|
| EKS (plano de control) | 0,10 |
| 6 × t3.medium | 0,25 |
| NAT gateway | 0,045 |
| RDS t4g.micro Multi-AZ | 0,03 |
| Redis con réplica (2 × t4g.micro) | 0,03 |
| MSK (2 × kafka.t3.small) | 0,09 |
| NLB, WAF, CloudFront en reposo | 0,03 |
| Generador `c5.large` + 30 GB gp3 | 0,09 |
| **Total encendido** | **≈ 0,67** |

Sesión completa: **4,5–6 h, unos USD 4–7** (más transferencia por el NAT y el uso de ráfaga de CPU de las t3). Si se
olvida el destroy: **≈ USD 16 por día**.

## Convenciones

- Todo en **Git Bash** (no PowerShell). Repos clonados uno al lado del otro: `solventa-infra`, `solventa-backend`,
  `solventa-experimentos`. Los comandos de infra corren desde `solventa-infra/`.
- Los valores entre `< >` son marcadores: se reemplazan **sin** los `< >`.
- Anota en `sesion.log` (en tu máquina) la hora UTC de inicio y fin de cada paso, y cualquier desviación.
- Si un paso falla, **no improvises recursos a mano**: anota el error, consulta «Errores frecuentes» del README de infra
  y, si no se resuelve en 20 min, salta al destroy.

## 0. Antes de la sesión (sin costo)

- [ ] Prueba en kind de la cadena app → Prometheus → adaptador → HPA terminada y anotada (bitácora de HA03).
- [ ] Ramas preparadas (D10), con el árbol limpio y los SHA anotados en `sesion.log`:

```sh
git -C ../solventa-backend fetch origin
git -C ../solventa-backend switch feature/HA03-metrica-solicitudes-en-curso
git -C ../solventa-backend pull --ff-only
git fetch origin                                   # en solventa-infra
git switch -C sesion/HA03-HA04-local origin/feature/HA03-hpa-solicitudes-concurrentes
git merge --no-edit origin/infra/HA04-dispersion-donotschedule    # no debe haber conflictos
git status --short                                 # vacío
echo "backend $(git -C ../solventa-backend rev-parse HEAD)  infra $(git rev-parse HEAD)" >> sesion.log
```

  La rama `sesion/HA03-HA04-local` **no se sube** (`git push` no): solo existe para aplicar las dos ramas juntas.
- [ ] Docker Desktop corriendo; `kubectl` ≥ 1.34 (el clúster es 1.35); `terraform` ≥ 1.10; AWS CLI v2.
- [ ] Cuota de vCPU `L-1216C47A` ≥ 16 (se usan 14: 6 nodos + generador).
- [ ] Credenciales limpias y perfil fijado:

```sh
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE=solventa AWS_REGION=us-east-1
aws sts get-caller-identity                     # la cuenta y el usuario esperados
OPERADOR_ARN=$(aws sts get-caller-identity --query Arn --output text)
MI_IP=$(curl -s https://checkip.amazonaws.com)
```

## 1. Bootstrap (≈ 10 min, una vez por cuenta)

```sh
terraform -chdir=bootstrap init
terraform -chdir=bootstrap plan -out=bootstrap.tfplan      # revisa: solo `+`
terraform -chdir=bootstrap apply bootstrap.tfplan
STATE_BUCKET=$(terraform -chdir=bootstrap output -raw state_bucket)
```

- Migra el estado al bucket: descomenta el bloque `backend "s3"` de `bootstrap/versions.tf` **sin commitear** y
  `terraform -chdir=bootstrap init -migrate-state -backend-config="bucket=$STATE_BUCKET" -backend-config="region=us-east-1"`.
- `cp environments/staging/backend.hcl.example environments/staging/backend.hcl` y completa `bucket = "$STATE_BUCKET"`.
- El bootstrap **no se destruye** al final: no cuesta y evita repetir esto.

## 2. Plataforma (≈ 30–40 min)

```sh
terraform -chdir=environments/staging/platform init -backend-config=../backend.hcl
terraform -chdir=environments/staging/platform plan -var-file=staging.tfvars \
  -var "cluster_admin_principal_arns=[\"$OPERADOR_ARN\"]" \
  -var "api_allowed_cidrs=[\"$MI_IP/32\"]" \
  -var node_count=6 \
  -out=platform.tfplan
terraform -chdir=environments/staging/platform apply platform.tfplan
aws eks update-kubeconfig --name solventa-staging --region us-east-1
kubectl get nodes -L topology.kubernetes.io/zone       # 6 nodos repartidos en 2 AZ
```

Anota la hora en que termina el apply: desde aquí corre el costo completo.

## 3. Imágenes (≈ 15–20 min)

Una imagen por servicio, construida desde `services/<carpeta>` del monorepo (`.github/deploy-services.json`), con
`solventa-backend` en la rama `feature/HA03-metrica-solicitudes-en-curso` (D10; comprueba `git -C ../solventa-backend
branch --show-current` antes de construir). La imagen de Cotización debe responder `/metrics`:

```sh
REGISTRY=$(terraform -chdir=environments/staging/platform output -raw registry_url)
SHA=$(git -C ../solventa-backend rev-parse HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
for par in cotizacion:solventa-backend identidad:solventa-identidad perfilamiento:solventa-perfilamiento \
           suscripcion:solventa-suscripcion siniestros:solventa-siniestros; do
  carpeta=${par%%:*}; servicio=${par##*:}
  docker build --platform linux/amd64 -t "$REGISTRY/$servicio:$SHA" "../solventa-backend/services/$carpeta" \
    && docker push "$REGISTRY/$servicio:$SHA"
done
```

## 4. Baseline y humo (≈ 15 min)

```sh
TAGS=$(printf '{"solventa-backend":"%s","solventa-identidad":"%s","solventa-perfilamiento":"%s","solventa-suscripcion":"%s","solventa-siniestros":"%s"}' $SHA $SHA $SHA $SHA $SHA)
terraform -chdir=environments/staging/baseline init -backend-config=../backend.hcl
terraform -chdir=environments/staging/baseline plan -var-file=staging.tfvars \
  -var "state_bucket=$STATE_BUCKET" -var "image_tags=$TAGS" -out=baseline.tfplan
terraform -chdir=environments/staging/baseline apply baseline.tfplan
kubectl -n staging get jobs                        # migraciones Complete
kubectl -n staging get pods -o wide                # ≥ 2 réplicas por servicio, en ambas AZ (HA04 c6)
kubectl -n monitoring get pods                     # prometheus-server y prometheus-adapter Running
kubectl get apiservice v1beta1.custom.metrics.k8s.io   # Available=True
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/staging/pods/*/http_requests_in_flight"
kubectl -n staging describe hpa solventa-backend   # dos métricas: http_requests_in_flight (objetivo 3) y cpu (70 %)
kubectl -n staging get pods -l app.kubernetes.io/name=solventa-backend \
  --field-selector=status.phase=Succeeded          # vacío (ver nota)
```

Notas de la prueba en kind:

- El adaptador solo publica la métrica de los pods nuevos en su siguiente relist (~1 min). Si `get --raw` devuelve una
  lista vacía o el HPA muestra `<unknown>` justo después del apply, espera 1-2 min antes de declarar falla.
- Si la consulta de pods `Succeeded` no queda vacía, el pod del Job de migración comparte la etiqueta del selector: el
  HPA lo cuenta con concurrencia 0 (diluye el promedio) y, como no tiene request de CPU, la métrica de CPU queda en
  `<unknown>`. Anótalo en `sesion.log` como desviación y no lances la carga hasta resolverlo (arreglo pendiente en infra).

Humo por túnel (desde tu máquina):

```sh
kubectl -n staging port-forward svc/solventa-backend 8000:80 &
curl -s localhost:8000/health                                                          # 200
curl -s localhost:8000/metrics | grep http_requests_in_flight                          # la serie existe
kill %1
PUBLIC=$(terraform -chdir=environments/staging/platform output -raw public_url)
curl -s -o /dev/null -w '%{http_code}
' "$PUBLIC/metrics"                              # 404/403: no se publica
curl -s --path-as-is -o /dev/null -w '%{http_code}
' "$PUBLIC/v1/cotizaciones/../metrics"  # 404/403
```

Guarda las dos últimas respuestas en `resultados/borde-metrics.txt` (evidencia de que `/metrics` no sale por el borde).

## 5. Generador y siembra (≈ 15 min)

```sh
cd ../solventa-experimentos/ha03-ha04/ec2-k6
terraform init && terraform plan -out=k6.tfplan && terraform apply k6.tfplan
terraform output                                   # instance_id, availability_zone, bucket_evidencia, conectar
NLB=$(terraform -chdir=../../../solventa-infra/environments/staging/platform output -raw nlb_dns_name)
echo "$NLB"                                        # para usarlo dentro de la EC2
```

En la EC2 (`terraform output -raw conectar`), cuando exista `/opt/ha03/LISTO`:

```sh
cd /opt/ha03
python3 sembrar-clientes.py --url http://<NLB>:30081 --n 1000 --salida clientes.json
```

Anota la AZ del generador y la de cada réplica de Cotización (HA03 c2 / HA01: «dónde se mide»).

## 6. HA03 — autoescalado (≈ 60–75 min)

1. **Latencia del simulador (D8)** y caché fría:

   ```sh
   kubectl -n staging set env deploy/solventa-backend OPEN_FINANCE_SIM_LATENCIA_MIN_MS=200 OPEN_FINANCE_SIM_LATENCIA_MAX_MS=500
   kubectl -n staging rollout status deploy/solventa-backend
   cluster/limpiar-cache.sh
   ```

2. **Muestreo** en tu máquina, en otra terminal, desde `solventa-experimentos/ha03-ha04/`:

   ```sh
   cluster/muestrear-cluster.sh resultados/ha03-humo-muestreo.csv 5
   ```

3. **Humo (5 min)** en la EC2: `./correr-escenario.sh ha03 humo http://<NLB>:30080`. Comprueba que el muestreo tiene
   filas (con `hpa_concurrencia` y `hpa_cpu_pct` llenas), que `cotizacion_aproximada` es 0 y que no hay
   `dropped_iterations` (el generador da abasto). Si algo falla, corrige antes de la corrida completa.

   **Calibración del objetivo (D11).** En la meseta del humo (5.000/min ≈ 83 solicitudes/s) toma del muestreo la
   concurrencia por pod y las réplicas listas; la concurrencia total es su producto. Por la ley de Little, a 50.000/min
   (≈ 833/s) y con la misma latencia sería unas 10 veces mayor. El objetivo es esa concurrencia total esperada dividida
   entre las réplicas que se quieren en la meseta (8 de un máximo de 10), redondeado y como mínimo 1:

   `objetivo ≈ max(1, round(concurrencia_por_pod × pods_listos × 10 / 8))`

   Si sale distinto de 3, cámbialo en `environments/staging/services.yaml` de la rama local
   (`concurrent_requests_target`), vuelve a planear el baseline (solo debe cambiar el HPA de `solventa-backend`),
   aplícalo y anota en `sesion.log` el valor, la cuenta y la hora. **Es una extrapolación**: si en la corrida completa
   el HPA llega al máximo sin que la latencia mejore, el límite es la capacidad (D6), no el objetivo.
4. **Completa (29 min):** detén el muestreo, vuelve a vaciar la caché, arranca un muestreo nuevo
   (`resultados/ha03-completo-muestreo.csv`) y en la EC2: `./correr-escenario.sh ha03 completo http://<NLB>:30080`.
5. Al terminar: detén el muestreo y guarda la evidencia del clúster: `cluster/eventos-cluster.sh resultados/ha03-cluster`.
6. **Análisis**: descarga el crudo del bucket y corre
   `python -I scripts/analizar-ha03.py <ha03.json.gz> --muestreo resultados/ha03-completo-muestreo.csv --salida resultados/informe-ha03.json`.
7. Vuelve a dejar el simulador por defecto: `kubectl -n staging set env deploy/solventa-backend OPEN_FINANCE_SIM_LATENCIA_MIN_MS- OPEN_FINANCE_SIM_LATENCIA_MAX_MS-`.

**Punto de corte (D7):** si en este momento llevas más de **4 h** desde el inicio del paso 2, salta al paso 9.

## 7. HA04 — caída de zona (≈ 60–90 min)

1. Tráfico constante en la EC2 (10 solicitudes/s; cada cliente_id es único para el conteo de RPO):
   `./correr-escenario.sh ha04 90m http://<NLB>:30080 10`.
2. Muestreo en tu máquina: `cluster/muestrear-cluster.sh resultados/ha04-muestreo.csv 5` (y uno por servicio crítico
   si quieres más detalle: `APP=solventa-identidad ...`).
3. Inyecta las fallas **una a la vez**, dejando que se recupere antes de la siguiente, y anota cada inicio en
   `resultados/eventos.csv` (`nombre,inicio_utc`, con `date -u +%FT%TZ`):

   | Falla | Comando | Objetivo |
   |---|---|---|
   | Failover de RDS | `aws rds reboot-db-instance --db-instance-identifier solventa-staging-postgres --force-failover` | < 5 min |
   | Failover de Redis | `aws elasticache test-failover --replication-group-id solventa-staging-cache --node-group-id 0001` | < 1 min |
   | Pérdida de una AZ | `kubectl cordon` y `kubectl drain --ignore-daemonsets --delete-emptydir-data` de los nodos de una AZ | pods < 2 min |
   | Despliegue bajo carga (c5) | `kubectl -n staging rollout restart deploy/solventa-backend` y `rollout status` | 0 errores |

   Tras la pérdida de AZ, comprueba que las réplicas se reprogramaron en la otra zona (`kubectl -n staging get pods -o
   wide`); si alguna queda `Pending`, anota sus eventos. Al terminar, `kubectl uncordon` de esos nodos.
4. Al terminar el tráfico: `cluster/eventos-cluster.sh resultados/ha04-cluster` y, con el crudo descargado,
   `python -I scripts/analizar-ha04.py <ha04.json.gz> --eventos resultados/eventos.csv --salida resultados/informe-ha04.json`.
5. **RPO (c3):** ejecuta la consulta que imprime el analizador desde un pod efímero con `psql` en `solventa-system`
   (ver «Acceso privado al backend» en el README de infra). El conteo debe igualar las confirmadas.

## 8. Evidencia (≈ 10 min)

```sh
BUCKET=$(terraform -chdir=ec2-k6 output -raw bucket_evidencia)
aws s3 sync "s3://$BUCKET/results/" resultados/k6/
```

Copia `resultados/` al paquete de evidencia **fuera de git** (los crudos pesan cientos de MB). Al repo solo van los
informes JSON y los reportes pequeños.

## 9. Destroy y verificación (≈ 25–35 min)

Orden estricto: **generador → baseline → plataforma → verificación**. El bootstrap se conserva.

```sh
terraform -chdir=ec2-k6 destroy                                  # desde solventa-experimentos/ha03-ha04
cd ../../solventa-infra
terraform -chdir=environments/staging/baseline destroy -var-file=staging.tfvars \
  -var "state_bucket=$STATE_BUCKET" -var check_images_exist=false
terraform -chdir=environments/staging/platform destroy -var-file=staging.tfvars \
  -var "cluster_admin_principal_arns=[\"$OPERADOR_ARN\"]" -var "api_allowed_cidrs=[\"$MI_IP/32\"]" -var node_count=6
scripts/verificar-teardown.sh us-east-1                          # debe salir con 0
```

### Lista de verificación final

- [ ] `terraform -chdir=ec2-k6 destroy` terminó sin errores (bucket de evidencia vaciado y borrado).
- [ ] `baseline destroy` terminó sin errores.
- [ ] `platform destroy` terminó sin errores. Si falló a medias, **repítelo** hasta que converja.
- [ ] `scripts/verificar-teardown.sh us-east-1` salió con **código 0**.
- [ ] Consola, en `us-east-1`: sin clústeres EKS, sin instancias EC2 (tampoco `solventa-ha03-k6`), sin RDS ni snapshots
      manuales, sin ElastiCache, sin MSK, sin NAT gateways, sin Elastic IPs, sin NLB, sin distribución de CloudFront
      activa y sin web ACL de WAF.
- [ ] `aws ec2 describe-addresses` y `aws ec2 describe-nat-gateways --filter Name=state,Values=available` vacíos.
- [ ] Solo quedan el bucket de estado, el proveedor OIDC y los roles de CI (sin costo).
- [ ] Cost Explorer (filtro por la etiqueta `Project = solventa`) al día siguiente: el gasto dejó de crecer. Anota el total.
- [ ] `bootstrap/versions.tf` y `backend.hcl` locales sin commitear (`git status` limpio en `solventa-infra`).
- [ ] Si cambiaste el objetivo (D11), el cambio de `services.yaml` vive solo en `sesion/HA03-HA04-local`: llévalo como
      commit a `feature/HA03-hpa-solicitudes-concurrentes` antes de abrir el PR. La rama local no se sube.
- [ ] Hora de fin del destroy anotada en `sesion.log` y en la bitácora del hub.

## Desviaciones que se declaran en los informes

- HA03 c4: objetivo de concurrencia calibrado con el humo, extrapolando por la ley de Little (D11); el ADR-0014 sigue
  en Propuesta hasta esta medición.
- Despliegue desde ramas sin fusionar (D10), con los SHA anotados; los PRs se abren al cierre con esta evidencia.
- `/metrics` no se publica por el borde, pero dentro de la VPC es alcanzable por el NodePort y el Service (mismo
  puerto que la app; sin NetworkPolicy). Pendiente de decisión.
- HA03 c2 y HA04: el borde público queda fuera de la medición; la carga entra por el NLB interno (D5).
- HA03 c5: nodos **fijos** (6 × t3.medium), sin cluster-autoscaler (D6).
- HA04: con 2 zonas, «perder una zona» se simula acordonando y drenando sus nodos; RDS y Redis se prueban con su
  failover gestionado, no apagando la zona.
- Observabilidad (HA03 nota técnica): sin trazas ni tableros; la evidencia sale de k6, del muestreo con `kubectl` y de
  los eventos del clúster.
