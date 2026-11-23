# Runbook — sesión de HA07 (y evidencia de HU26) sobre staging en AWS

Historias: **HA07** ([#48](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/48), contratos de la API de
socios y esfuerzo de integración) y la evidencia en staging de **HU26**
([#81](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/81), oferta de vida hipotecario), en **una sola
sesión**. Operador: la persona dueña de la cuenta de AWS. Ambiente: staging de `solventa-infra`, levantado bajo demanda y
**destruido al terminar**.

> **Nada de este runbook se ha ejecutado todavía en AWS.** Se validó en local: `bash -n` de los scripts, `preparar-kit.sh`
> contra una carpeta temporal y la colección de Postman de socios con newman contra los servicios en local.

Los pasos que no cambian respecto de HA03 (plataforma, imágenes, baseline, destroy) remiten a
[`../ha03-ha04/RUNBOOK.md`](../ha03-ha04/RUNBOOK.md); aquí van las diferencias y todo lo propio de HA07.

## Decisiones de la sesión

| # | Decisión | Valor |
|---|---|---|
| D1 | Desde dónde se opera | **Local**, con el perfil de AWS del operador (como HA03 D1) |
| D2 | Origen del despliegue | **`main`** de `solventa-backend`, `solventa-infra` y `solventa-web`: HU24, HU25, HU26 y HU32 a HU34 ya están mezcladas. Los SHA van a `sesion.log` |
| D3 | Nodos | Los de `staging.tfvars` (4 × t3.medium): no hay carga |
| D4 | Bus de eventos | `enable_events = true`: la venta del socio necesita el cobro (Suscripción → Pagos → Suscripción por Kafka) |
| D5 | Documentación del kit | La de la rama de HA07 (`feature/HA07-contratos-socios`), si su PR aún no está mezclado: el contrato agrega `bearerAuth` y quita el cuerpo vacío del 202 de `firma/codigo`, sin cambiar el comportamiento. La colección de Postman de socios pasa igual con `main` y con la rama (114 de 114 aserciones, en local). El manifiesto del kit anota la rama y el commit |
| D6 | Integrador | **Un agente aislado**: una sesión nueva de Claude Code en una carpeta con solo el kit (`~/ha07-integrador`), sin acceso a los repositorios. Lo opera un integrante del equipo, que responde las preguntas como el soporte de Solventa. Ver «Desviaciones» |
| D7 | Socio de prueba | `socio-ha07`, con los cuatro endpoints y 5 solicitudes por segundo. Su secreto nace en `~/ha07-secreto.txt` (fuera de los repos) y no se imprime |
| D8 | Corte | **El destroy empieza a más tardar 5 h después del inicio del apply de la plataforma**, aunque falte algo. Lo pendiente se declara |

## Costo y duración estimados

Como HA03 sin el generador ni los nodos extra: **≈ USD 0,55/h encendido** a precio de lista de `us-east-1`. Contrástalo con
Cost Explorer al cerrar.

| Bloque | Duración |
|---|---|
| Plataforma | 30–40 min |
| Imágenes | 15–20 min |
| Baseline y humo | 15–20 min |
| Consola web | 10 min |
| Evidencia de HU26 | 20–30 min |
| Socio de prueba y kit | 10 min |
| Sesión del integrador | **hasta 2 h** |
| Destroy y verificación | 25–35 min |
| **Total** | **≈ 4 h 30 min**, dentro del corte de 5 h |

## Convenciones

Las de HA03: Git Bash, repos uno al lado del otro, marcadores `< >`, `sesion.log` con la hora UTC de cada paso y, si algo
falla 20 min, al destroy. Además:

- **Ningún token ni secreto se imprime** ni se escribe en `sesion.log`, en la evidencia o en un commit. El secreto del socio
  vive solo en `~/ha07-secreto.txt` y en `~/ha07-integrador/credenciales/` (fuera de los repos), y en la base como hash.
- **Cada plan de Terraform se revisa antes de aplicarlo.**

## 0. Antes de la sesión (sin costo)

- [ ] `solventa-backend`, `solventa-infra`, `solventa-web` y `solventa-experimentos` en `main` actualizado, con el árbol limpio
      (para el kit, la rama de HA07 en `solventa-backend` si su PR no está mezclado, D5):

```sh
for r in solventa-backend solventa-infra solventa-web solventa-experimentos; do
  git -C ../$r switch main && git -C ../$r pull --ff-only && git -C ../$r status --short
done
echo "backend $(git -C ../solventa-backend rev-parse HEAD)  infra $(git -C ../solventa-infra rev-parse HEAD)  web $(git -C ../solventa-web rev-parse HEAD)" >> sesion.log
```

- [ ] Herramientas y credenciales: paso 0 de HA03 (Docker Desktop, `kubectl` ≥ 1.34, `terraform` ≥ 1.10, AWS CLI v2, perfil
      `solventa`, `OPERADOR_ARN` y `MI_IP`).
- [ ] Node 24 y las dependencias de `solventa-web` (`npm ci`) para construir la consola.
- [ ] `~/ha07-integrador` no existe (o está vacía).

## 1–3. Plataforma, imágenes y baseline

Pasos 2 a 4 de [`../ha03-ha04/RUNBOOK.md`](../ha03-ha04/RUNBOOK.md), con estas diferencias:

- Plataforma **sin** `-var node_count=6` (D3). Anota la hora de inicio del apply: desde ahí corre el corte de 5 h (D8).
- Las imágenes se construyen desde **`main`** de `solventa-backend` (D2), **los ocho servicios** de
  `.github/deploy-services.json`, y `TAGS` lleva los ocho.
- En el humo del paso 4, comprueba también MS Socios por el borde:

```sh
PUBLIC=$(terraform -chdir=environments/staging/platform output -raw public_url)
curl -s "$PUBLIC/partners/v1/status"                       # 200 y "estado": "operativo"
kubectl -n staging get jobs | grep socios                  # migrate y seed Complete
kubectl -n staging logs deploy/solventa-backend | grep -c linaje_sin_configurar   # 0 (HU24/HU26)
```

## 4. Consola web (≈ 10 min)

Desde `solventa-web` en `main`, como el workflow `deploy-staging.yml`, con dry-run antes de publicar:

```sh
npx ng build --configuration production --localize
BUCKET=<bucket web del output de platform>; DISTRIBUCION=<id de la distribución>
aws s3 sync dist/solventa-web/browser "s3://$BUCKET" --delete --dryrun | tail
aws s3 sync dist/solventa-web/browser "s3://$BUCKET" --delete
aws cloudfront create-invalidation --distribution-id "$DISTRIBUCION" --paths '/es-CO/*' '/en-US/*'
```

## 5. Evidencia de HU26 (≈ 20–30 min)

Con el asesor demo y los clientes sintéticos de la siembra de Identidad (`CC 1000000001` con consentimiento, `CC 1000000004`
revocado). Todo va a `C:\Users\esteb\solventa-evidencias\HU26\`, **sin tokens ni datos personales**:

1. Consola, cotización asistida de `1000000001`: perfil → «Ofrecer vida hipotecario» → oferta (prima, coberturas y pasos del
   precio) → recalcular con otra suma → pestaña «Linaje de fuentes» → «El cliente acepta: iniciar suscripción». Una captura
   por paso, en es-CO, y la oferta en en-US.
2. Cliente `1000000004`: el perfil explica que no hay oferta sin consentimiento.
3. Por el borde con el JWT del asesor: el JSON de `POST /api/cotizacion/v1/asesor/clientes/{id}/ofertas-vida-hipotecario`
   (200, sin cifras del cliente) y el **409 `CONSENTIMIENTO_REQUERIDO`** del cliente revocado.
4. La cotización de `vida_hipotecario` que leyó la suscripción asistida (`GET /v1/cotizaciones/{id}` por túnel).

## 6. Socio de prueba y kit del integrador (≈ 10 min)

```sh
cd ../solventa-experimentos/ha07
scripts/sembrar-socio-ha07.sh ~/ha07-secreto.txt "$PUBLIC"     # «token de socio-ha07: HTTP 200, expires_in=3600»
scripts/preparar-kit.sh ~/ha07-integrador "$PUBLIC" ~/ha07-secreto.txt
cat resultados/kit-manifiesto.txt >> ../../sesion.log
```

No se hace ninguna venta con `socio-ha07` antes del experimento: el integrador empieza desde cero.

## 7. Sesión del integrador (hasta 2 h)

1. El operador abre una terminal **nueva**, entra en `~/ha07-integrador` y arranca Claude Code ahí. Primer mensaje: «Lee
   TAREA.md y haz la tarea». Anota la hora en `sesion.log`.
2. Mientras trabaja, el operador:
   - responde las preguntas como el soporte de Solventa: lo mínimo que la documentación debió decir, sin código ni pasos;
   - comprueba que cada pregunta quedó en `registro/defectos.md` antes de responderla;
   - **no** abre los repositorios en esa sesión ni le pega contenido que no esté en el kit.
3. Si el agente intenta leer fuera de la carpeta o consultar GitHub (aunque los permisos lo nieguen), se anota en
   `sesion.log`. Si llegó a ver código o documentación de los repositorios, la corrida se invalida y se declara.
4. Se corta a las 2 h o cuando el agente termine `resumen.md`, lo que ocurra primero. Se anota la hora y se cierra la sesión.

## 8. Resultados (≈ 10 min)

```sh
mkdir -p resultados/integrador
cp -r ~/ha07-integrador/{registro,socio,resumen.md} resultados/integrador/      # nunca credenciales/
grep -rl "$(cat ~/ha07-secreto.txt)" resultados/ && echo "¡el secreto quedó en los resultados!"   # debe no imprimir nada
```

Además, el conteo de la suite de contrato del CI del PR de HA07 (`conformidad-errores.json`, artefacto
`pytest-reports-socios`) va a `resultados/`.

## 9. Destroy y verificación (≈ 25–35 min)

Paso 9 de HA03 sin el generador: **baseline → plataforma → verificación**, con las mismas variables de la plataforma
(sin `node_count`). Después:

```sh
scripts/verificar-teardown.sh us-east-1      # desde solventa-infra; debe salir con 0
rm -f ~/ha07-secreto.txt                     # el socio ya no existe; el kit conserva su copia para el informe
```

Lista de verificación final: la de HA03.

## Informe

`resultados/INFORME.md`, con el veredicto de cada criterio de HA07:

| Criterio | Fuente |
|---|---|
| 1. Contrato | `openapi.json` versionado (versión en la ruta y en git), catálogo como `enum` |
| 2. Pruebas de contrato en el CI | Job `pruebas (socios)`: `test_conformidad_contrato.py` |
| 3. Cambios que rompen | Job `contrato (socios)`: los 9 casos de muestra y el chequeo contra la rama base |
| 4. Esfuerzo | Hoja de tiempos del integrador y tiempo humano de apoyo (con la desviación D6) |
| 5. Errores estándar | `conformidad-errores.json`: fallos provocados con un código del catálogo, sobre el total |
| 6. Documentación suficiente | `defectos.md`: preguntas, tropiezos y discrepancias, cada uno con su corrección |
| 7. Veredicto | Lo anterior, con las decisiones que salgan |

## Desviaciones que se declaran en el informe

- **Integrador simulado con un agente aislado, no una persona externa (D6).** Mide si la documentación alcanza, no el esfuerzo
  humano. El tiempo del agente no se convierte en persona-día: el informe da el tiempo del agente, el tiempo humano de apoyo y
  el número de defectos, y no declara cumplido el objetivo de ≤ 5 persona-día con esta medición. El aislamiento es por
  configuración (carpeta, `CLAUDE.md` y permisos), y el backend es público: si el agente lo consultó, se declara.
- **El contrato se escribió después del código.** HU32 a HU34 ya estaban mezcladas cuando empezó HA07: el «contrato primero»
  del criterio 1 no se cumplió en el orden pedido. Desde HA07, oasdiff impide que el código se aleje del contrato sin que se
  note.
- **Documentación del kit desde la rama de HA07 (D5)**, si su PR no estaba mezclado: mismo comportamiento, contrato más
  completo.
- **La conformidad se prueba con dobles**, no contra staging. Lo que pasa entre servicios reales lo cubren la colección de
  Postman y la sesión del integrador.
