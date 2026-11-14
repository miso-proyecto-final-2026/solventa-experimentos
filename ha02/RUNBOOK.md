# Runbook — sesión de HA02 (y evidencia de HU24) sobre staging en AWS

Historias: **HA02** ([#43](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/43), perfilamiento en
línea) y la evidencia en staging de **HU24** ([#79](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/79),
linaje para el asesor), en **una sola sesión** (D14). Operador: la persona dueña de la cuenta de AWS. Ambiente: staging
de `solventa-infra`, levantado bajo demanda y **destruido al terminar**.

> **Nada de este runbook se ha ejecutado todavía en AWS.** Se validó en local: sintaxis (`bash -n`, `node --check`,
> `python -m py_compile`), `terraform validate` del generador y las pruebas del analizador con datos sintéticos
> (`python -m pytest tests`). Tiempos y costos: estimaciones a precio de lista de `us-east-1`; contrástalos con Cost
> Explorer al cerrar.

Los pasos que no cambian respecto de HA03 (bootstrap, plataforma, imágenes, baseline, destroy) remiten a
[`../ha03-ha04/RUNBOOK.md`](../ha03-ha04/RUNBOOK.md); aquí van las diferencias y todo lo propio de HA02.

## Decisiones de la sesión

| # | Decisión | Valor |
|---|---|---|
| D1 | Desde dónde se opera | **Local**, con el perfil de AWS del operador (como HA03 D1) |
| D2 | Origen del despliegue | **`main`**, con los PRs de HU24 ya mezclados (solventa-backend#82, solventa-web#18, solventa-infra#37 y #39). Sin ramas sin fusionar; los SHA van a `sesion.log` |
| D3 | Bus de eventos | `enable_events = true` (Suscripción y Siniestros no arrancan sin brokers) |
| D4 | Ruta de la carga | k6 en la EC2 de `../ha03-ha04/ec2-k6` contra el **NLB interno**: Perfilamiento 30086, Cotización 30080, Identidad 30081. El borde queda **fuera de la medición**. Si para la sesión ya se quitó el borde TEMPORAL de `/v1/perfilamiento` (sin NodePort 30086), k6 corre como Job dentro del clúster: **se decide ese día** |
| D5 | Nodos | `node_count = 6` (t3.medium), fijos, sin cluster-autoscaler (como HA03) |
| D6 | Generador | `c5.large` (`-var instance_type=c5.large`): la carga es de menos de 15 solicitudes/s, aun sin reutilizar conexiones |
| D7 | Simuladores de Perfilamiento | Por defecto (20–80 ms) en humo, miss, hit y meseta. **4a**: Open Finance 200–500 ms (bajo el timeout de 0,7 s). **4b**: 600–900 ms (alrededor del timeout). **5a/5b**: Open Finance en `error_5xx`. Se cambian con `kubectl set env` |
| D8 | Cotización durante la meseta (c3) | 500/min constantes, con su simulador por defecto (20–80 ms) y un grupo de clientes **propio**; 10 min de línea base sola antes de la meseta. **La evidencia principal de c3 es la diferencia de p95 de Cotización entre la línea base y la meseta**, medidas con el mismo simulador. Su p95 absoluto se informa, pero **no es comparable con HA01**, que usó Open Finance a 200–500 ms |
| D9 | Meseta | **60 min a 360/min** (21.600/h), 5 % sin consentimiento: ~20.520 perfilamientos con consentimiento por hora |
| D10 | Clave de carga | Clave **temporal** `PERFILAMIENTO_API_KEY_CARGA` (origen `carga` en la auditoría), creada y borrada en la sesión. Depende de solventa-backend#82 (una clave por llamador) |
| D11 | Caché compartida (solventa-backend#84) | Grupos de clientes **disjuntos** para Cotización y Perfilamiento, y caché vacía antes de miss. Se declara como hallazgo |
| D12 | Revocación | 50 clientes, consulta cada 1 s hasta 300 s. Se informa como «**no se sirve, no se borra de Redis**»; el borrado es de V-11 |
| D13 | Evidencia de c5 | **Indirecta**, sin contador de llamadas a fuentes: intentos de origen `carga` en la base = 403 de k6, y ningún perfil de esos clientes. Se declara en el informe |
| D14 | Sesión compartida | Evidencia de HU24 y HA02 en la misma sesión de AWS, cuando estén mezclados los PRs de HU24 |

## Costo y duración estimados

Igual que HA03 salvo el generador (`c5.large`, ≈ USD 0,085/h): **≈ USD 0,67/h encendido**.

| Bloque | Duración |
|---|---|
| Plataforma, imágenes y baseline (pasos 1–4 de HA03) | ≈ 1 h 15 min |
| Evidencia de HU24 | ≈ 15 min |
| Generador, clave de carga y siembra de ~11.800 clientes (bcrypt en Identidad) | ≈ 40 min |
| Humo, miss, calentar + hit, sin consentimiento y revocación | ≈ 1 h |
| Meseta (10 min de línea base + 60 min) | ≈ 1 h 15 min |
| Fuente lenta (4a, 4b) y caída (5a, 5b), con sus rollouts | ≈ 1 h |
| Evidencia, limpieza y destroy | ≈ 45 min |
| **Total** | **≈ 6–6,5 h, unos USD 4–5** |

Si se olvida el destroy: **≈ USD 16 por día**.

## Convenciones

Las de HA03: Git Bash, repos uno al lado del otro, marcadores `< >`, `sesion.log` con la hora UTC de cada paso y, si
algo falla 20 min, al destroy. Además:

- **La clave de carga nunca se escribe** en `sesion.log`, en la evidencia ni en un commit. Solo vive en el Secret del
  clúster, en la variable de la terminal del paso 6 y en `/opt/ha03/clave-carga` de la EC2 (que se destruye).
- **No se vuelve a aplicar el baseline** a mitad de sesión: quitaría las variables puestas con `kubectl set env`
  (clave de carga y simuladores), como pasó en HA03.

## 0. Antes de la sesión (sin costo)

- [ ] Mezclados en `main`: solventa-backend#82, solventa-web#18, solventa-infra#37 y #39. Comprueba si el bloque
      `edge` de Perfilamiento (TEMPORAL) sigue en `services.yaml` (D4).
- [ ] `solventa-backend`, `solventa-infra` y `solventa-experimentos` en `main` actualizado, con el árbol limpio:

```sh
for r in solventa-backend solventa-infra solventa-experimentos; do
  git -C ../$r switch main && git -C ../$r pull --ff-only && git -C ../$r status --short
done
echo "backend $(git -C ../solventa-backend rev-parse HEAD)  infra $(git -C ../solventa-infra rev-parse HEAD)" >> sesion.log
```

- [ ] Herramientas, cuota de vCPU (≥ 14: 6 nodos de 2 vCPU + generador de 2) y credenciales: paso 0 de HA03.
- [ ] Analizador probado en local: `python -m pytest tests` (desde `ha02/`).

## 1–4. Bootstrap, plataforma, imágenes y baseline

Pasos 1 a 4 de [`../ha03-ha04/RUNBOOK.md`](../ha03-ha04/RUNBOOK.md), con estas diferencias:

- Las imágenes se construyen desde **`main`** de `solventa-backend` (D2), no desde la rama de HA03.
- En el humo del paso 4, comprueba también Perfilamiento y el linaje de HU24:

```sh
kubectl -n staging get pods -l app.kubernetes.io/name=solventa-perfilamiento    # 2 réplicas Ready
kubectl -n staging logs deploy/solventa-backend | grep -c linaje_sin_configurar  # 0: Cotización tiene URL y clave
```

## 5. Evidencia de HU24 (≈ 15 min)

1. Enriquece un cliente de prueba (sintético, con consentimiento) y abre la consola web como asesor: cotización asistida
   de ese cliente, pestaña **«Linaje de fuentes»**. Captura de pantalla.
2. `GET /v1/asesor/clientes/<cliente_id>/linaje` por el borde con el JWT del asesor: guarda el JSON (sin valores del
   cliente, solo fuentes, fechas, TTL y marcas).
3. La fila de `perfilamiento.consultas_linaje` de esa consulta (origen `cotizacion` y el id del asesor), con un pod
   efímero de `psql` o con `kubectl exec` en Perfilamiento, como `cluster/consultas-db.sh`.
4. Adjunta las tres piezas al issue #79 **sin tokens ni datos personales** (solo identificadores sintéticos).

## 6. Generador, clave de carga y siembra (≈ 40 min)

```sh
cd ../solventa-experimentos/ha03-ha04/ec2-k6
terraform init && terraform plan -var instance_type=c5.large -out=k6.tfplan && terraform apply k6.tfplan
NLB=$(terraform -chdir=../../../solventa-infra/environments/staging/platform output -raw nlb_dns_name)
cd ../../ha02
```

**Clave temporal de carga (D10).** En tu máquina; el valor no se imprime:

```sh
CLAVE=$(openssl rand -hex 24)
kubectl -n staging create secret generic ha02-clave-carga --from-literal=PERFILAMIENTO_API_KEY_CARGA="$CLAVE"
kubectl -n staging set env deploy/solventa-perfilamiento --from=secret/ha02-clave-carga
kubectl -n staging rollout status deploy/solventa-perfilamiento
```

En la EC2 (`terraform -chdir=../ha03-ha04/ec2-k6 output -raw conectar`), cuando exista `/opt/ha03/LISTO`, guarda la
clave pegándola en la entrada estándar (no queda en el historial) y borra la variable de tu terminal:

```sh
cd /opt/ha03 && umask 077 && cat > clave-carga     # pega la clave, Enter y Ctrl+D
```

```sh
unset CLAVE                                        # en tu máquina
```

**Siembra** en la EC2. Un archivo por grupo (D11); en total ~11.800 clientes sintéticos:

```sh
mkdir -p grupos
S="python3 sembrar-clientes.py --url http://<NLB>:30081 --hilos 32"
$S --n 5600 --salida grupos/miss.json             # miss: 336/min x 15 min = 5.040, con margen
$S --n 1000 --salida grupos/pool.json             # hit, meseta y caida_5b
$S --n 1300 --salida grupos/lenta-4a.json         # 120/min x 10 min = 1.200, con margen
$S --n 1300 --salida grupos/lenta-4b.json
$S --n 1300 --salida grupos/caida-5a.json
$S --n 300  --sin-consentimiento --salida grupos/sin-consentimiento.json
$S --n 1000 --salida grupos/cotizacion.json       # solo para Cotización (D8)
```

Cada corrida imprime cuántos clientes creó y cuántos errores hubo; con errores, repite ese grupo (otro `--run`).

**Muestreo** en tu máquina, una terminal por servicio, desde `ha02/` (déjalos corriendo hasta el paso 9):

```sh
APP=solventa-perfilamiento ../ha03-ha04/cluster/muestrear-cluster.sh resultados/muestreo-perfilamiento.csv 5
APP=solventa-backend       ../ha03-ha04/cluster/muestrear-cluster.sh resultados/muestreo-cotizacion.csv 5
APP=solventa-identidad     ../ha03-ha04/cluster/muestrear-cluster.sh resultados/muestreo-identidad.csv 5
```

Anota en `sesion.log` la hora UTC de inicio de la carga (para `cluster/consultas-db.sh`).

## 7. Escenarios de HA02

En la EC2, `P=http://<NLB>:30086`. Cada corrida sube su crudo al bucket. Antes de cada escenario anota la hora.

1. **Humo (5 min):** `./correr-ha02.sh perfilamiento humo $P`. Comprueba en el resumen que hay 201 y 403, que
   `perfil_fallido` es 0 y que no hay `dropped_iterations`. Si algo falla, corrige antes de seguir.
2. **Caché sin acierto (15 min):** en tu máquina `../ha03-ha04/cluster/limpiar-cache.sh` (vacía la base 0, que
   comparten Cotización y Perfilamiento); en la EC2 `./correr-ha02.sh perfilamiento miss $P`.
3. **Caché con acierto:** `./correr-ha02.sh perfilamiento calentar $P` y, **enseguida**,
   `./correr-ha02.sh perfilamiento hit $P` (15 min). La entrada de Open Finance dura 3.600 s con una dispersión de
   ±10–15 %: el hit tiene que terminar antes de ~50 min desde el calentamiento.
4. **Sin consentimiento (5 min):** `./correr-ha02.sh perfilamiento sin_consentimiento $P`.
5. **Revocación (≈ 10 min):** `./correr-ha02.sh revocacion http://<NLB>:30081 $P`. Al terminar, en tu máquina, con
   `revocacion-ids.json` descargado del bucket: `cluster/claves-redis.sh revocacion-ids.json resultados/redis.json`.
6. **Meseta (70 min):** `./correr-ha02.sh meseta $P http://<NLB>:30080`. Cotización corre sola 10 min y después las
   dos juntas 60 min. Revisa a mitad de la corrida que el muestreo de Perfilamiento y de Cotización sigue escribiendo.
7. **Fuente lenta 4a (10 min):**

   ```sh
   kubectl -n staging set env deploy/solventa-perfilamiento OPEN_FINANCE_SIM_LATENCIA_MIN_MS=200 OPEN_FINANCE_SIM_LATENCIA_MAX_MS=500
   kubectl -n staging rollout status deploy/solventa-perfilamiento && sleep 60
   ```

   y en la EC2 `./correr-ha02.sh perfilamiento lenta_4a $P`.
8. **Fuente lenta 4b (10 min):** lo mismo con `600` y `900`, y `./correr-ha02.sh perfilamiento lenta_4b $P`.
9. **Fuente caída 5a (10 min):**

   ```sh
   kubectl -n staging set env deploy/solventa-perfilamiento OPEN_FINANCE_SIM_LATENCIA_MIN_MS- OPEN_FINANCE_SIM_LATENCIA_MAX_MS- OPEN_FINANCE_SIM_MODO=error_5xx
   kubectl -n staging rollout status deploy/solventa-perfilamiento && sleep 60
   ```

   y `./correr-ha02.sh perfilamiento caida_5a $P`: clientes nuevos, sin respaldo, así que el valor es el por defecto.
10. **Fuente caída 5b (10 min):** con Open Finance todavía caída, en tu máquina `cluster/borrar-cache-primaria.sh`
    (deja solo los respaldos) y en la EC2 `./correr-ha02.sh perfilamiento caida_5b $P`: el grupo `pool` responde
    desde el respaldo, marcado como degradado.
11. **Simulador por defecto:** `kubectl -n staging set env deploy/solventa-perfilamiento OPEN_FINANCE_SIM_MODO-`.

## 8. Evidencia y análisis (≈ 15 min)

En tu máquina, desde `ha02/`:

```sh
# detén los tres muestreos (Ctrl+C)
../ha03-ha04/cluster/eventos-cluster.sh resultados/cluster
BUCKET=$(terraform -chdir=../ha03-ha04/ec2-k6 output -raw bucket_evidencia)
aws s3 sync "s3://$BUCKET/results/ha02/" resultados/k6/
cluster/consultas-db.sh <inicio_carga_utc> "$(date -u +%FT%TZ)" \
  "$(ls resultados/k6/*-sin_consentimiento/sin-consentimiento.json | head -1)" resultados/db.json
python -I scripts/analizar-ha02.py \
  --perfilamiento resultados/k6/*/perfilamiento.json* \
  --cotizacion resultados/k6/*-meseta/cotizacion.json* \
  --revocacion resultados/k6/*-revocacion/revocacion.json --redis resultados/redis.json --db resultados/db.json \
  --muestreo perfilamiento=resultados/muestreo-perfilamiento.csv \
  --muestreo cotizacion=resultados/muestreo-cotizacion.csv \
  --muestreo identidad=resultados/muestreo-identidad.csv \
  --salida resultados/informe-ha02.json
```

`correr-ha02.sh` sube con cada corrida la lista de clientes sin consentimiento (sintéticos) que usa `consultas-db.sh`.
Copia `resultados/`
al paquete de evidencia **fuera de git**; al repo solo van `informe-ha02.json` y el informe escrito.

## 9. Limpieza y destroy (≈ 30 min)

```sh
kubectl -n staging set env deploy/solventa-perfilamiento PERFILAMIENTO_API_KEY_CARGA-
kubectl -n staging delete secret ha02-clave-carga
```

Después, el paso 9 de HA03 (generador → baseline → plataforma → verificación) y su lista de verificación final.

## Desviaciones que se declaran en el informe

- La carga entra por el NLB interno (D4): el borde público queda fuera de la medición.
- Nodos fijos, sin cluster-autoscaler (D5).
- Cotización y Perfilamiento comparten la caché de proveedores (solventa-backend#84): grupos disjuntos y caché vacía
  antes de miss (D11). Hallazgo, no parte del alcance de HA02.
- c3 se juzga por la diferencia de p95 de Cotización entre la línea base y la meseta (mismo simulador, 20–80 ms); el
  p95 absoluto no es comparable con HA01, que midió con Open Finance a 200–500 ms (D8).
- c5 con evidencia indirecta (D13).
- c6: «no se sirve, no se borra de Redis» (D12); la resolución de la medida es 1 s.
- La meseta reutiliza el grupo `pool`: sus entradas de caché vencen durante la hora (TTL de 3.600 s con dispersión), así
  que la proporción de aciertos cambia a lo largo de la meseta. Se informa la tasa de aciertos.
- Fuente caída solo en modo `error_5xx` (5a, 5b); el modo `timeout` lo cubre en parte 4b.
