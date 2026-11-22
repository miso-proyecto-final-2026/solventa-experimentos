# Tarea del integrador — socio simulado de Solventa

Usted es el ingeniero de un **socio de distribución** que va a ofrecer los seguros de Solventa en la app de su empresa.
Solventa le entregó lo que hay en esta carpeta y nada más. Su trabajo es integrar un socio simulado contra el ambiente de
pruebas (staging) de Solventa.

## Lo que tiene

| Archivo | Qué es |
|---|---|
| `documentacion/openapi.json` | El contrato de la API de socios. |
| `documentacion/errores-y-reintentos.md` | El catálogo de errores y cuándo reintentar. |
| `documentacion/guia-de-inicio.md` | La guía de inicio de Solventa para socios. |
| `credenciales/ambiente.txt` | La URL del ambiente de pruebas y su `client_id`. |
| `credenciales/secreto.txt` | Su `client_secret`. **Nunca lo imprima ni lo copie a otro archivo**: léalo desde ahí. |

## Lo que debe construir

Un programa (en el lenguaje que prefiera; `python` está disponible) en la carpeta `socio/` que:

1. compruebe el estado del API y obtenga un token;
2. cotice para el cliente de pruebas que se puede vender;
3. venda esa cotización, espere a que la venta esté lista para firmar, firme y consulte la póliza hasta que quede emitida;
4. provoque y maneje, con un mensaje comprensible para cada uno:
   - un token inválido;
   - una venta a un cliente que no puede comprar;
   - la misma clave de idempotencia con otro cuerpo;
   - una ráfaga que supere su cuota, respetando `Retry-After`;
5. imprima un resumen de lo que pasó, **sin el secreto ni el token**.

Cuando termine, escriba `resumen.md`: qué funcionó, qué no y qué le habría ahorrado tiempo.

## Reglas del experimento

- **Solo esta carpeta.** No lea archivos fuera de ella, no busque Solventa en internet ni en GitHub y no clone repositorios.
  La documentación que tiene es toda la que hay.
- **Tiempo.** Anote en `registro/hoja-de-tiempos.csv` la hora UTC (`date -u +%H:%M:%S`) de inicio y fin de cada actividad,
  en el momento en que empieza y termina, no al final.
- **Preguntas y tropiezos.** Cada vez que la documentación no le alcance, regístrelo en `registro/defectos.md` **antes** de
  seguir:
  - si no puede avanzar, escriba la pregunta y pregúntele al operador en el chat: él responde como lo haría el soporte de
    Solventa;
  - si avanzó por prueba y error o adivinando, regístrelo como tropiezo;
  - si la API hace algo distinto de lo que dice la documentación, regístrelo como discrepancia.
- **Sin atajos.** No le pida al operador código ni pasos; pregunte solo lo que la documentación debió decir.
