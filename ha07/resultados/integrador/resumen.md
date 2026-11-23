# Resumen de la integración — socio simulado HA07

Fecha: 2026-11-22/23 (UTC). Inicio 23:37:23, fin de A7 indicado en `registro/hoja-de-tiempos.csv`.
Programa: `socio/main.py` (recorrido y errores) y `socio/solventa.py` (cliente HTTP, reintentos y mensajes).
Uso: `python socio/main.py [--venta <id>] [--espera-max N] [--solo-errores]`.

## Qué funcionó

| Paso | Resultado |
|---|---|
| Estado del API | `GET /partners/v1/status` → 200 `operativo`. |
| Token | `POST /oauth/token` (formulario) → Bearer, 3600 s. El secreto se lee de `credenciales/secreto.txt`; ni el secreto ni el token se imprimen ni se guardan (lo verifiqué buscando en todos los archivos). |
| Cotización | 201 a la primera con el producto y los `datos` de ejemplo del contrato. |
| Venta → firma → póliza | Venta `832ccc7c…`: decisión, cobro aprobado (`tok_sim_…`), documento con el hash del PDF verificado, OTP, firma (`123456`) y póliza `H8CBQJCKZGNAR52DZK44` **EMITIDA / VIGENTE**. Repetir la firma con la misma clave → 200 con `X-Idempotent-Replayed: true`. |
| Programa de punta a punta | Venta `aae4ae8e…`, creada por el propio programa y aprobada por soporte tras quedar derivada: `python socio/main.py --venta aae4ae8e…` → **15/15 OK**, póliza `SFKMJDQ607731S6X8SKQ` **EMITIDA / VIGENTE** (00:19:28 UTC). |
| Token inválido | 401 `INVALID_TOKEN` → «El token de acceso no es válido; hay que obtener uno nuevo.» |
| Cliente que no puede comprar | `CC 1000000004` → 400 `INVALID_REQUEST` / `CONSENTIMIENTO_NO_VIGENTE`, tanto al cotizar como al vender. |
| Clave de idempotencia con otro cuerpo | 409 `IDEMPOTENCY_CONFLICT`, sin reintento. |
| Ráfaga sobre la cuota (5/s) | 15 consultas concurrentes: entre 9 y 15 respuestas 429 con `Retry-After: 1`. Cada una esperó y se reintentó, y ninguna terminó en 429. |
| Mensajes | Cada `code`/`reason` se traduce a un mensaje en español y se guarda el `trace_id`. |

## Qué no funcionó

- **La venta al cliente de pruebas «vendible» no llega sola a firma.** Las dos ventas con `CC 1000000001` quedaron en `DECISION` / `DERIVADA` («perfil incompleto, 56 %»). Según soporte, la causa es el defecto abierto backend#84. Las dos llegaron a firma solo porque soporte las aprobó a mano en la revisión asistida:
  - D-05, venta `832ccc7c…`: pregunta en el chat al quedar bloqueado (00:00:26 UTC); respuesta recibida a las 00:11:11. Unos 29 min perdidos.
  - D-08, venta `aae4ae8e…`: pregunta hacia las 00:15:25 UTC, en el mensaje de cierre («Si aprueban también `aae4ae8e…`, ejecuto `--venta` con ella»). No la anoté en `defectos.md` en ese momento; lo hice después. Respuesta recibida a las 00:19:16.

  El programa se probó de punta a punta, todo OK, pero **no en una sola corrida sin intervención de Solventa**. Detecta la derivación, informa el id y la retoma con `--venta`.
- No provoqué 500/503 (no hay forma documentada de forzarlos). El código los maneja con retroceso 1-2-4-8-16 s, pero no está probado contra la API.
- Tampoco probé el vencimiento real del token (`TOKEN_EXPIRED`), porque dura 3600 s. El código renueva el token antes de que venza y reintenta una vez si recibe un 401 `TOKEN_EXPIRED`.

## Qué me habría ahorrado tiempo

1. **Una máquina de estados cerrada de la venta** (enum de `estado`, `paso_actual` y `decision.decision`): qué combinación significa «lista para firmar» (resultó ser `COBRO_REALIZADO` + `FIRMA` + `firma.estado=PENDIENTE` + `documento_estado=GENERADO`), cuáles son los estados terminales de fallo y qué es `DERIVADA` (D-04, D-05, D-06).
2. **Datos de staging que sí lleguen a firma** sin intervención, o la instrucción de cómo pedir la revisión asistida (D-05, D-08). Esto fue lo único bloqueante.
3. **Un catálogo de productos y de los campos de `datos`** (D-03). Funcionó con el ejemplo, pero adivinando.
4. **Marcar bien `X-Idempotency-Key`** (`required: true` donde corresponda) y aclarar que `firma/codigo` no la lleva (D-02).
5. **No citar documentos internos** (ADR, «guía de integración §4.5») que el socio no recibe (D-01).
6. **Decir en qué llamada se valida cada condición del cliente** y qué significan `aproximada` y `aviso` (D-07).

El detalle de cada defecto está en `registro/defectos.md` y los tiempos en `registro/hoja-de-tiempos.csv`.
