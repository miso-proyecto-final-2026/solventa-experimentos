# Defectos de documentación

Un bloque por cada vez que la documentación no alcanzó. Copie la plantilla, numere en orden (D-01, D-02...) y llénelo en el
momento.

- **Tipo:**
  - `pregunta`: no pudo avanzar y le preguntó al operador;
  - `tropiezo`: avanzó por prueba y error o adivinando;
  - `discrepancia`: la API hizo algo distinto de lo que dice la documentación.
- **Severidad:**
  - `bloqueante`: no podía seguir sin resolverlo;
  - `demora`: siguió, pero perdió tiempo;
  - `menor`: confuso, sin costo real.

---

## D-00 (plantilla)

- **Hora UTC:**
- **Actividad:** A1 … A7
- **Tipo:** pregunta | tropiezo | discrepancia
- **Severidad:** bloqueante | demora | menor
- **Qué pasó:**
- **Qué buscó y dónde** (archivo y sección):
- **Qué debió decir la documentación:**
- **Pregunta al operador** (textual, si la hubo):
- **Respuesta del operador** (textual):
- **Minutos perdidos:**

## D-01

- **Hora UTC:** 23:38:09
- **Actividad:** A1
- **Tipo:** tropiezo
- **Severidad:** menor
- **Qué pasó:** `errores-y-reintentos.md` enlaza a un ADR (`../../../../docs/adr/0027-...`) y dice reemplazar «la guía de integración §4.5»; ninguno de los dos está en la carpeta entregada al socio.
- **Qué buscó y dónde** (archivo y sección): `errores-y-reintentos.md`, encabezado.
- **Qué debió decir la documentación:** No referenciar documentos internos que el socio no recibe.
- **Pregunta al operador** (textual, si la hubo): —
- **Respuesta del operador** (textual): —
- **Minutos perdidos:** 0

## D-02

- **Hora UTC:** 23:38:09
- **Actividad:** A1
- **Tipo:** tropiezo
- **Severidad:** menor
- **Qué pasó:** En `openapi.json` la cabecera `X-Idempotency-Key` figura con `required: false` (y admite `null`), pero su descripción y la guía §3 dicen que es obligatoria. Además, `POST .../firma/codigo` no declara la cabecera ni cuerpo, y la guía §3 no la nombra entre las escrituras que la exigen: no queda claro si la lleva.
- **Qué buscó y dónde** (archivo y sección): `openapi.json` parámetros de los POST; `guia-de-inicio.md` §3.
- **Qué debió decir la documentación:** Marcar la cabecera `required: true` donde lo sea y decir explícitamente si `firma/codigo` la necesita.
- **Pregunta al operador** (textual, si la hubo): —
- **Respuesta del operador** (textual): —
- **Minutos perdidos:** 0

## D-03

- **Hora UTC:** 23:38:09
- **Actividad:** A1
- **Tipo:** tropiezo
- **Severidad:** demora
- **Qué pasó:** No hay catálogo de productos ni del contenido de `datos` para cotizar. Solo existen los ejemplos `producto: microseguro_vida` y `datos: {edad: 35, suma_asegurada: 50000000}`; voy a usarlos tal cual.
- **Qué buscó y dónde** (archivo y sección): `openapi.json` `SolicitudCotizacionIn`; `guia-de-inicio.md` §4 y §5.
- **Qué debió decir la documentación:** Lista de productos habilitados y campos requeridos de `datos` por producto (tipos, rangos).
- **Pregunta al operador** (textual, si la hubo): —
- **Respuesta del operador** (textual): —
- **Minutos perdidos:** 0 (a confirmar al cotizar)

## D-04

- **Hora UTC:** 23:38:09
- **Actividad:** A1
- **Tipo:** tropiezo
- **Severidad:** demora
- **Qué pasó:** La guía §4.3 dice «consulte hasta que la venta esté lista para firmar», pero no dice qué valor de `estado`/`paso_actual` significa eso. `SuscripcionIniciadaOut.estado` lista «EN_PROGRESO, APROBADA, COBRO_REALIZADO, FIRMADA, EMITIDA...» (lista abierta) y `PolizaOut.documento_estado = GENERADO` «cuando ya se puede firmar». Tampoco hay estados terminales de fallo (rechazo, cobro rechazado, cancelada) ni intervalo de consulta recomendado.
- **Qué buscó y dónde** (archivo y sección): `guia-de-inicio.md` §4; `openapi.json` `PolizaConsultaOut`, `SuscripcionIniciadaOut`, `PolizaOut`.
- **Qué debió decir la documentación:** Máquina de estados cerrada (enum) con el estado «listo para firmar», los terminales de éxito/fallo y un intervalo de sondeo.
- **Pregunta al operador** (textual, si la hubo): —
- **Respuesta del operador** (textual): —
- **Minutos perdidos:** 0 (a confirmar; usaré `poliza.documento_estado == GENERADO` o `firma` no nula)

## D-05

- **Hora UTC:** 23:41:32
- **Actividad:** A4
- **Tipo:** pregunta
- **Severidad:** bloqueante
- **Qué pasó:** Con el cliente de pruebas vendible (`CC 1000000001`), cotización con `microseguro_vida` / `{edad: 35, suma_asegurada: 50000000}` y `metodo_pago = tok_sim_<uuid>`, la venta `832ccc7c-6da9-4dbc-ac9c-635db59a371c` queda en `estado: EN_PROGRESO`, `paso_actual: DECISION`, `decision: {decision: "DERIVADA", motivos: ["El perfil está incompleto (completitud 56 %)."]}` y no avanza tras más de 2 minutos de consultas. La guía dice que ese cliente es vendible y no menciona decisiones derivadas.
- **Qué buscó y dónde** (archivo y sección): `guia-de-inicio.md` §4 y §5; `openapi.json` `DecisionOut` (sin enum ni descripción), `SolicitudCotizacionIn.datos`, `PolizaConsultaOut`; `errores-y-reintentos.md`.
- **Qué debió decir la documentación:** Los valores posibles de `decision.decision` (APROBADA, RECHAZADA, DERIVADA...), qué significa DERIVADA, si se resuelve sola (y en cuánto tiempo) o qué debe enviar el socio para completar el perfil (p. ej. campos de `datos`), y datos de prueba que lleguen a firma.
- **Pregunta al operador** (textual, si la hubo): «La venta al cliente de pruebas CC 1000000001 se queda en paso DECISION con decisión DERIVADA ("El perfil está incompleto (completitud 56 %)") y no avanza. ¿Qué significa DERIVADA, se resuelve sola, y qué debe enviar el socio (o qué dato de prueba debe usar) para que la decisión sea aprobada y la venta llegue a firma?»
- **Respuesta del operador** (textual): Recibida a las 00:11:11 UTC: «La venta 832ccc7c… ya está lista para firmar. Aprobación: a las 00:08 UTC, asesor1 aprobó la venta en la revisión asistida, con la misma llamada que hace la consola. Respondió 200. La justificación interna cita backend#84. Estado de la venta: a las 00:08:34, la saga está en COBRO_REALIZADO, paso FIRMA. El cobro con su tok_sim_… salió aprobado, así que el integrador puede seguir con el paso 4 de la guía (documento, código y firma).» (Causa según soporte: defecto abierto backend#84, «Cotización y Perfilamiento comparten la caché de proveedores»; DERIVADA = derivada a revisión asistida por un asesor, que no se resuelve sola.)
- **Minutos perdidos:** ~29 (venta derivada desde 23:39:00; bloqueado esperando desde 00:00:26; desbloqueado 00:11:11)

## D-06

- **Hora UTC:** 00:11:40
- **Actividad:** A4
- **Tipo:** discrepancia
- **Severidad:** menor
- **Qué pasó:** Después de la aprobación manual, `decision.decision` es `APROBADA` pero `motivos` sigue diciendo «El perfil está incompleto (completitud 56 %).», lo que contradice la decisión. Por otro lado, `POST .../firma/codigo` sin `X-Idempotency-Key` y sin cuerpo respondió 202 (esto resuelve la duda de D-02: no la exige). La venta se pudo firmar con `estado = COBRO_REALIZADO`, `paso_actual = FIRMA`, `firma.estado = PENDIENTE` y `poliza.documento_estado = GENERADO`: eso es, en la práctica, «lista para firmar» (D-04). `POST .../firma` devolvió directamente `estado: EMITIDA`.
- **Qué buscó y dónde** (archivo y sección): `openapi.json` `DecisionOut`, `firma/codigo`; `guia-de-inicio.md` §4.3–4.5.
- **Qué debió decir la documentación:** Qué contiene `motivos` según la decisión; que `firma/codigo` no lleva clave; la combinación de estado y paso que indica «lista para firmar».
- **Pregunta al operador** (textual, si la hubo): —
- **Respuesta del operador** (textual): —
- **Minutos perdidos:** 0

## D-07

- **Hora UTC:** 00:12:55
- **Actividad:** A5
- **Tipo:** tropiezo
- **Severidad:** menor
- **Qué pasó:** Para «una venta a un cliente que no puede comprar», la guía §5 da `CC 1000000004` (consentimiento revocado), pero no dice en qué paso se rechaza. La API lo rechaza ya en `POST /cotizaciones` con `cliente` (400 `CONSENTIMIENTO_NO_VIGENTE`), así que no se puede obtener una cotización vendible para ese cliente. Para provocar el error en la venta usé una cotización de `1000000001` con `cliente = 1000000004` en `POST /suscripciones`: también responde 400 `CONSENTIMIENTO_NO_VIGENTE`. Además, la cotización informativa (sin `cliente`) vuelve con `aproximada: true` y un `aviso`, y eso no está documentado.
- **Qué buscó y dónde** (archivo y sección): `guia-de-inicio.md` §4.1 y §5.
- **Qué debió decir la documentación:** En qué llamada se valida cada condición del cliente (registro, KYC, consentimiento) y qué `reason` devuelve; qué significa `aproximada`.
- **Pregunta al operador** (textual, si la hubo): —
- **Respuesta del operador** (textual): —
- **Minutos perdidos:** 1

## D-08

- **Hora UTC:** 00:15:05
- **Actividad:** A6
- **Tipo:** discrepancia
- **Severidad:** demora
- **Qué pasó:** En la prueba de punta a punta del programa, la venta nueva `aae4ae8e-a9a0-40b0-9ba7-67118c895bc8` (mismo cliente `CC 1000000001`, mismos datos) volvió a quedar en `DECISION` con decisión `DERIVADA` («El perfil está incompleto (completitud 56 %).»). Es la misma causa de D-05 (backend#84, según soporte). El cliente de pruebas que la guía §5 presenta como vendible no llega a firma sin la intervención manual de Solventa. El programa lo detecta, informa el id de la venta y permite retomarla con `--venta <id>`. Ese camino lo probé con la venta ya emitida `832ccc7c…`.
- **Qué buscó y dónde** (archivo y sección): `guia-de-inicio.md` §5.
- **Qué debió decir la documentación:** Que staging puede derivar ventas a revisión asistida y cómo pedirla, o bien que el cliente de pruebas llegue a firma sin intervención.
- **Pregunta al operador** (textual, si la hubo): ~00:15:25 UTC, en el mensaje de cierre al operador (no la registré aquí en ese momento): «Si aprueban también `aae4ae8e…`, ejecuto `--venta` con ella y A6 queda completo.»
- **Respuesta del operador** (textual): recibida a las 00:19:16 UTC: «Soporte aprobó también la venta aae4ae8e-a9a0-40b0-9ba7-67118c895bc8.» La retomé con `--venta`: firmada y póliza `SFKMJDQ607731S6X8SKQ` EMITIDA a las 00:19:28.
- **Minutos perdidos:** ~6 (derivada desde ~00:13:00; espera de soporte 00:15:25–00:19:16)
