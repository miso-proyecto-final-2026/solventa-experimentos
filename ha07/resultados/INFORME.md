# Informe de HA07: contratos de la API de socios y esfuerzo de integración

Experimento del Sprint 3 ([proyecto-final#48](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/48)), con
sesión en staging el 22 y 23 de noviembre de 2026 (UTC).

**Hipótesis:** con un contrato publicado y una suite que lo verifica, un socio nuevo se integra solo con la documentación en
≤ 5 persona-día, y el 100 % de los fallos devuelve un código estándar documentado.

## Veredicto

**Parcialmente confirmada.**

- **Sí:** la parte del contrato. Tiene conformidad en el CI, el CI detecta los cambios que rompen y el 100 % de los fallos
  provocados trae un código del catálogo.
- **Sí:** un integrador que no construyó la API, con solo la documentación, completó dos ventas hasta la póliza emitida y
  probó los errores, en **42 minutos de reloj**.
- **No se puede afirmar:** el objetivo de **≤ 5 persona-día**. El integrador fue un agente aislado, no una persona (ver
  «Desviaciones»).
- **Las dos ventas necesitaron intervención de Solventa.** Quedaron derivadas por un defecto de staging (backend#84), no de
  la documentación.

| # | Criterio | Veredicto | Evidencia |
|---|---|---|---|
| 1 | Contrato primero | **No cumple** (en el orden pedido) | HU32 a HU34 ya estaban mezcladas cuando empezó HA07: el contrato se generó desde el código. Desde HA07 queda fijado y vigilado por el CI (criterios 2 y 3). |
| 2 | Pruebas de contrato en el CI | **Cumple** | Schemathesis 4.32.7, `tests/test_conformidad_contrato.py`, job `pruebas (socios)`. Las 13 operaciones con 50 casos cada una validan esquemas, códigos, `Content-Type` y encabezados. Encontró una discrepancia real: el 202 de `firma/codigo` estaba documentado con cuerpo. |
| 3 | Un cambio que rompe hace fallar el CI; uno compatible no | **Cumple** | Job `contrato (socios)` con oasdiff 1.35.0: los 5 casos que rompen y los 4 compatibles salen bien clasificados en cada corrida ([`contrato-ci.txt`](contrato-ci.txt)). |
| 4 | Esfuerzo medido (≤ 5 persona-día) | **Medido con desviación; objetivo no verificable** | Agente aislado: 42 min 18 s de reloj, de los cuales 14 min 36 s fueron esperas de soporte. Apoyo humano: 2 respuestas de soporte, de 10 min 45 s y 3 min 51 s. No se convierte a persona-día. |
| 5 | 100 % de los fallos con código del catálogo | **Cumple** | En el CI, 883 de 883 fallos provocados ([`conformidad-errores.json`](conformidad-errores.json)). En staging, los que provocó el integrador (401, 400, 409 y 429 con `Retry-After`) salieron como dice el catálogo. |
| 6 | Preguntas del integrador anotadas como defectos | **Cumple** | 8 defectos de documentación, 2 de ellos preguntas al equipo (D-05 y D-08). Detalle abajo. |
| 7 | Informe con veredicto | **Cumple** | Este documento. |

## 1. Contrato en el CI (criterios 1, 2, 3 y 5)

Está en [solventa-backend#95](https://github.com/miso-proyecto-final-2026/solventa-backend/pull/95), mezclado el 23 de
noviembre a las 00:09 UTC con el CI en verde. El diseño está en el ADR-0030 del backend.

**Conformidad:**
- La suite genera solicitudes válidas e inválidas desde `services/socios/docs/api/openapi.json` y valida cada respuesta.
- Una verificación propia exige, en todo error de `/partners/v1`, un `code` del catálogo con su código HTTP, un `trace_id`
  y `X-Correlation-Id`.

**Conteo del CI** ([corrida 45086319160](https://github.com/miso-proyecto-final-2026/solventa-backend/actions/runs/45086319160)):

| Código | Fallos provocados |
|---|---|
| 401 `INVALID_TOKEN` | 553 |
| 400 `INVALID_REQUEST` | 139 |
| 404 `RESOURCE_NOT_FOUND` | 102 |
| 405 `INVALID_REQUEST` | 84 |
| 409 `IDEMPOTENCY_CONFLICT` | 5 |
| **Total** | **883, todos con código del catálogo** |

Los 500, 503, el circuito abierto y el token vencido no se pueden provocar por HTTP. Los cubren las pruebas de
`test_catalogo_errores.py` y `test_circuito.py`.

**Compatibilidad:** oasdiff compara el contrato con el de la rama base, o con el del commit anterior en `main`.

**Comportamiento sin cambios:** la colección de Postman de socios dio **114/114 aserciones** antes y después de los cambios
del contrato.

**Correcciones del contrato:**
- se declaró `bearerAuth`;
- el 202 de `firma/codigo` quedó sin cuerpo.

Las dos describían lo que la API ya hacía, y oasdiff las marcaba como incompatibles. Por eso se admitieron como excepciones
transitorias en `scripts/contrato/excepciones.txt`, **que se deben vaciar ahora que el PR se mezcló** (decisión 1).

## 2. Sesión del integrador (criterios 4 y 6)

**Montaje:**
- Una sesión nueva de Claude Code, abierta en `~/ha07-integrador`, con solo el kit:
  - el contrato, `errores-y-reintentos.md` y `guia-de-inicio.md`, desde la rama de HA07 (`95b1d98`); las sumas sha256
    están en [`kit-manifiesto.txt`](kit-manifiesto.txt);
  - la URL de staging;
  - las credenciales de `socio-ha07`: 4 permisos y cuota de 5 solicitudes por segundo.
- Las reglas en `CLAUDE.md` y los permisos le negaban GitHub, la búsqueda web, `gh`, `git clone` y la lectura del hub.
- Un integrante del equipo operó la sesión y respondió como soporte.

**Aislamiento verificado:** la transcripción de la sesión tiene 57 llamadas a herramientas (Bash 47, Write 5, Edit 4,
Grep 1). Ninguna fue a GitHub ni a los repositorios, y ninguna leyó rutas fuera del kit.

**Lo que construyó:** [`integrador/socio/`](integrador/socio/) tiene un cliente HTTP con token, reintentos, idempotencia,
mensajes en español por `code` y `reason`, y el recorrido completo. Su resumen está en
[`integrador/resumen.md`](integrador/resumen.md).

| Actividad | Inicio y fin (UTC) | Minutos | Resultado |
|---|---|---|---|
| A1 Leer la documentación | 23:37:23 a 23:38:09 | 1 | completada |
| A2 Estado del API y primer token | 23:38:09 a 23:38:49 | 1 | completada |
| A3 Primera cotización | 23:38:49 a 23:38:58 | 1 | completada (201 a la primera) |
| A4 Venta hasta la póliza emitida | 23:38:58 a 00:11:40 | 33 | completada, con la venta derivada (D-05) |
| A5 Errores provocados | 00:11:40 a 00:12:55 | 1 | completada (401, 400, 409, 429) |
| A6 Programa de punta a punta | 00:12:55 a 00:19:28 | 7 | completada, con la venta derivada (D-08); 15/15 OK |
| A7 Resumen | 00:15:05 a 00:19:41 | 2 | completada |
| **Total** | **23:37:23 a 00:19:41** | **42** | dos pólizas `EMITIDA` |

Las dos pólizas emitidas son `H8CBQJCKZGNAR52DZK44` y `SFKMJDQ607731S6X8SKQ`. Repetir la firma con la misma clave respondió
200 con `X-Idempotent-Replayed: true`.

### Defectos de documentación

| ID | Tipo y severidad | Qué faltaba |
|---|---|---|
| D-01 | tropiezo, menor | `errores-y-reintentos.md` enlaza un ADR y «la guía de integración §4.5», que el socio no recibe. |
| D-02 | tropiezo, menor | `X-Idempotency-Key` figura con `required: false` aunque es obligatoria. No dice si `firma/codigo` la lleva (no la lleva). |
| D-03 | tropiezo, demora | No hay catálogo de productos ni de los campos de `datos` por producto: solo el ejemplo. |
| D-04 | tropiezo, demora | No dice qué combinación de `estado` y `paso_actual` significa «lista para firmar». Los enums están abiertos. |
| D-05 | pregunta, **bloqueante** | La venta del cliente «vendible» quedó `DERIVADA`. La guía no explica `DERIVADA` ni qué hacer. Causa en staging: backend#84. |
| D-06 | discrepancia, menor | Tras la aprobación manual, `motivos` sigue diciendo «perfil incompleto» con la decisión en `APROBADA`. |
| D-07 | tropiezo, menor | No dice en qué llamada se valida cada condición del cliente. El revocado se rechaza ya al cotizar. |
| D-08 | discrepancia, demora | La segunda venta también quedó `DERIVADA`, por la misma causa de D-05. |

**Soporte:**

| Defecto | Defecto registrado | Pregunta al equipo | Respuesta recibida | Tiempo de respuesta | Tiempo perdido según el integrador |
|---|---|---|---|---|---|
| D-05 | 23:41:32 | 00:00:26 | 00:11:11 | 10 min 45 s | unos 29 min (derivada desde 23:39) |
| D-08 | 00:15:05 | 00:15:25 | 00:19:16 | 3 min 51 s | unos 6 min |

En los dos casos, la acción de soporte fue aprobar la venta en la revisión asistida (HU16) como `asesor1`. Es el camino de
negocio para una venta derivada, y no se cambiaron código ni datos a mano.

**Lectura:**
- Sin backend#84, la documentación alcanzó para todo el recorrido. El único bloqueo vino de los datos de staging.
- Aun así, D-05 también es un defecto de documentación: la guía no le dice al socio que una venta puede quedar en revisión
  ni cómo tratarla.
- Los defectos D-02, D-03 y D-04 los resolvió el agente probando contra la API. Una persona probablemente habría tardado más
  en esos puntos.

## 3. Hallazgos

### H1. Caché compartida entre Cotización y Perfilamiento ([backend#84](https://github.com/miso-proyecto-final-2026/solventa-backend/issues/84))

Es la causa de D-05 y D-08. La verifiqué en staging solo con lecturas:

- Cotización y Perfilamiento usan el mismo Redis y la misma clave, `perfil:{cliente_id}:{fuente}`.
- El simulador de Cotización entrega **5 de los 9 campos canónicos**: faltan obligaciones, productos, puntaje y zona de
  riesgo.
- Cotización consultó primero al cliente demo `CC 1000000001`: fue durante la búsqueda de la cotización asistida, a las
  22:49 UTC. Perfilamiento reutilizó esa caché.
- Los tres perfiles enriquecidos del cliente quedaron con **completitud 0,5556**.
- Las copias de respaldo en Redis tenían el TTL de Cotización (86 400 s); el de Perfilamiento es de 7 días.
- Suscripción deriva todo perfil con completitud menor a 0,8 (`reglas/v1.yaml`). Por eso **las dos ventas del integrador
  quedaron `DERIVADA`** y tuvieron que aprobarse a mano.
- Con newman en local no apareció, porque ahí el perfil se enriqueció antes de que Cotización tocara la caché.
- **Impacto:** en staging, ninguna venta de socio llega sola a la firma con los clientes demo una vez que Cotización los
  consultó. Esto afecta cualquier demo o prueba de punta a punta.

### H2. `motivos` desactualizado tras la revisión asistida (D-06)

Cuando un asesor aprueba una venta derivada, la consulta del socio muestra `decision: APROBADA` con los `motivos` de la
derivación. Se puede corregir en el contrato, documentando qué contiene `motivos`, o en la API. La decisión es aparte, porque
cambiar la respuesta es un cambio de comportamiento (ADR-0030 §1).

### H3. Despliegue desde `main` (fuera del alcance de HA07, observado en la sesión)

- **Bootstrap desactualizado:** el bootstrap de la cuenta no se había vuelto a aplicar desde que HU32 agregó MS Socios. El
  apply de la plataforma falló por el rol de despliegue de `solventa-socios`, y se corrigió aplicando el bootstrap (2
  recursos).
- **Imagen de Identidad sin `scripts/`:** el Dockerfile de Identidad en `main` no copia `scripts/` y la siembra de asesores
  falla. Se usó una imagen temporal con `COPY scripts ./scripts`, la línea de backend#71 (abierto), igual que en la sesión de
  HU24.

## 4. Decisiones derivadas

1. **Vaciar `scripts/contrato/excepciones.txt`** en un PR aparte, ahora que backend#95 está en `main`: la rama base ya tiene
   el contrato nuevo.
2. **Corregir la documentación del socio con los defectos D-01 a D-08:**
   - una máquina de estados cerrada (enums de `estado`, `paso_actual` y `decision`) y el estado «lista para firmar»;
   - qué es `DERIVADA` y cómo tratarla;
   - `X-Idempotency-Key` como obligatoria donde lo es;
   - el catálogo de productos y de los campos de `datos`;
   - quitar las referencias internas;
   - en qué llamada se valida cada condición del cliente.
   Las correcciones del contrato pasan por oasdiff.
3. **Priorizar backend#84** antes de cualquier demo de socios en staging. Mientras tanto, la guía debe decir que en pruebas
   una venta puede quedar en revisión.
4. **Repetir la medición con una persona** que no haya construido la API, cuando se pueda, para verificar el objetivo de
   ≤ 5 persona-día. Esta corrida da la línea base de defectos.
5. **Mezclar backend#71** (`COPY scripts ./scripts` en Identidad), para que el baseline despliegue desde `main` sin imágenes
   temporales.

## 5. Desviaciones declaradas

- **Integrador simulado con un agente aislado, no una persona externa.** Mide si la documentación alcanza, no el esfuerzo
  humano. El agente lee y programa a otra velocidad, así que sus 42 minutos no se convierten en persona-día. Por eso **no se
  declara cumplido el objetivo de ≤ 5 persona-día**. El aislamiento fue por configuración y se verificó en la transcripción.
- **El contrato se escribió después del código** (criterio 1).
- **El kit salió de la rama de HA07** (`95b1d98`), porque el PR no estaba mezclado al preparar el kit. Staging corría `main`
  (`101d23b`), que tiene el mismo comportamiento: el PR solo cambia el contrato y la colección de Postman pasa igual.
- **La conformidad se prueba con dobles**, no contra staging. La sesión del integrador cubre lo que pasa entre servicios
  reales.
- **Identidad corrió la imagen temporal `101d23b-scripts`**, y el bootstrap se volvió a aplicar (H3).

## 6. Sesión en AWS

| Paso | UTC |
|---|---|
| Apply de la plataforma (falla al final, en el access entry de socios) | 22 nov, 21:39 a 22:12 |
| Bootstrap (rol de despliegue de socios) y plataforma completa | 22:16 a 22:28 |
| Imágenes en ECR | 22:30 |
| Baseline (falla la siembra de asesores) y segundo apply con la imagen `-scripts` | 22:32 a 22:47 |
| Consola web, clientes demo, evidencia de HU26, socio y kit | 22:48 a 22:51 |
| Sesión del integrador | 23:37 a 00:19 |
| Destroy del baseline (111 recursos) | 23 nov, 00:23 a 00:24 |
| Destroy de la plataforma (323 recursos) | 00:24 a 00:37 |

**Corte:** el destroy debía empezar antes de las 02:39 UTC, 5 h después del inicio. Empezó a las 00:23.

**Encendido:** 2 h 58 min, de 21:39 a 00:37, con un costo estimado de unos USD 1,6. Es la tarifa estimada del RUNBOOK, no la factura.

**`verificar-teardown.sh`** respondió «FALLO» las dos veces que se corrió (00:38 y 00:40), por una lista que viene de la API
de etiquetas de AWS, que tarda en olvidar lo borrado. Se comprobó cada recurso directamente:

| Recurso | Estado comprobado |
|---|---|
| 4 instancias | `terminated` |
| 6 llaves KMS | `PendingDeletion` |
| Volúmenes, ENI, grupo de seguridad y endpoint de VPC | ya no existen |
| NAT | `deleted` |
| VPC, RDS, ElastiCache, MSK, repositorios ECR, IP elásticas, NLB, API Gateway, CloudFront y buckets de staging | ninguno |
| Grupo de logs de RDS (`/aws/rds/instance/solventa-staging-postgres/postgresql`) | volvió a quedar y **se borró a mano** |

No queda nada que facture.

## Archivos

| Archivo | Contenido |
|---|---|
| [`integrador/hoja-de-tiempos.csv`](integrador/hoja-de-tiempos.csv) | Tiempos por actividad, del integrador |
| [`integrador/defectos.md`](integrador/defectos.md) | Los 8 defectos, con las preguntas y respuestas textuales |
| [`integrador/resumen.md`](integrador/resumen.md) | Qué funcionó, qué no y qué le habría ahorrado tiempo |
| [`integrador/socio/`](integrador/socio/) | El programa del socio, sin credenciales (las lee de `credenciales/`, que no se copió) |
| [`conformidad-errores.json`](conformidad-errores.json) | Conteo de la suite de conformidad del CI (artefacto `pytest-reports-socios`) |
| [`contrato-ci.txt`](contrato-ci.txt) | Resultado del job `contrato (socios)` del PR #95 |
| [`kit-manifiesto.txt`](kit-manifiesto.txt) | Rama, commit y sha256 de la documentación entregada |
