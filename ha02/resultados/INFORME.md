# HA02 — informe de la medición en staging (2026-11-15)

**Medición:** perfilamiento en línea con MS Perfilamiento real (HU23 + HU24) sobre staging, según el `RUNBOOK.md`.

**Fuente de las cifras:** `informe-ha02.json`, la salida de `scripts/analizar-ha02.py` sobre la evidencia cruda.

**Evidencia cruda:** k6, muestreo con `kubectl`, eventos del clúster, consultas a la base, Redis y avisos de los logs.
Está en el paquete de evidencia de la sesión, fuera de git, junto con `sesion.log` con las horas UTC. Las métricas
de la RDS en CloudWatch, consultadas después del destroy, sí van en git: `cloudwatch-rds-por-minuto.csv`.

Las decisiones que se derivan van en el ADR-0024 de `solventa-backend`.

## Montaje

**Desplegado** desde ramas sin fusionar (D2), con los SHA anotados:

| Repo | Rama | SHA |
|---|---|---|
| backend | feature/HU24-linaje-asesor | `4a4f898` |
| infra | feature/HU24-linaje-asesor | `647e4e9` |
| experimentos | feature/HA02-perfilamiento-en-linea | `09eb3e2` (scripts) |

Identidad usó la imagen `4a4f898-scripts`: `4a4f898` más `scripts/`, para la siembra de asesores del baseline.

**Infraestructura:**
- EKS con 6 nodos fijos `t3.medium` (D5, sin cluster-autoscaler).
- RDS `db.t4g.micro` (una instancia para todos los servicios).
- Redis `cache.t4g.micro`.

**Carga:**
- k6 0.54.0 desde una `c5.large` en la VPC, contra el NLB interno (D4), sin reutilizar conexiones.
- Clave de carga propia y temporal (`PERFILAMIENTO_API_KEY_CARGA`), retirada al terminar.

**Datos:** solo clientes sintéticos (`@solventa-carga.test`): ~11.800 en 7 grupos.

**Escenarios** (una corrida por escenario):

| Escenario | Inicio (UTC) | Tasa | Duración | Observación |
|---|---|---|---|---|
| humo | 16:42 | 60/min | 5 min | |
| miss | 17:03 | 336/min | 15 min | caché vaciada antes |
| calentar | 17:18 | — | 1 min | |
| hit | 17:20 | 336/min | 15 min | |
| sin_consentimiento | 17:35 | 60/min | 5 min | |
| meseta | 17:43 | Cotización 500/min + Perfilamiento 360/min | 70 min | Cotización sola 10 min y luego Perfilamiento desde el minuto 10, con 5 % sin consentimiento |
| lenta_4a | 18:55 | 120/min | 10 min | Open Finance a 200–500 ms |
| lenta_4b | 19:08 | 120/min | 10 min | Open Finance a 600–900 ms |
| caida_5a | 19:20 | 120/min | 10 min | Open Finance con `error_5xx` |
| caida_5b | 19:31 | 120/min | 10 min | ídem, con la caché primaria borrada (solo respaldos) |
| revocación | 19:43 | — | — | 50 clientes, consulta cada 1 s |

## Veredicto por criterio

| Criterio | Veredicto | Evidencia |
|---|---|---|
| c1 — p95 ≤ 400 ms y p99 ≤ 800 ms (con consentimiento) | **No cumple** | miss p95 112,1 / p99 195,7 ms y hit 36,7 / 102,3 ms **cumplen**; lenta_4a 529,3 / 3.968,2 ms no cumple |
| c2 — ≥ 20.000 perfilamientos/h sin saturación | **No cumple** | 18.121 respuestas 201 con consentimiento en la hora de meseta; 4 errores por saturación (estado 0); 0 iteraciones descartadas. Con los 2.431 falsos bloqueos habrían sido 20.552 |
| c3 — la cotización conserva su meta durante la carga | **No cumple** | p95 de la línea base (Cotización sola) 111,1 ms → meseta 456,7 ms: **+345,6 ms**. p99 219,5 → 2.754 ms. La corrida **no es válida** para c3: 12,6 % de cotizaciones `aproximada` en la meseta (0,02 % en la base) |
| c4 — degradación controlada | **No cumple** | lenta_4b: 1.201 de 1.201 en 201, 92,3 % degradadas, pero p95 724,4 ms > 400. caida_5a: 1.159 respuestas 201 y 42 respuestas 403, 100 % marcadas. caida_5b: 1.161 respuestas 201 y 40 respuestas 403, 100 % marcadas |
| c5 — bloqueo sin consentimiento | **No cumple** (comprobación estricta) | Los clientes sin consentimiento recibieron 403 en el 100 % (1.358 de 1.358) y ninguno tiene perfil (0). La igualdad con la base no cuadra: 4.028 intentos frente a 1.358, porque la base también registra los falsos bloqueos (ver abajo) |
| c6 — revocación ≤ 5 min | **Cumple** | 49 de 49 bloqueados, máximo 0,168 s, mediana 0,084 s (resolución 1 s; 1 de los 50 falló al prepararse). En Redis siguen 98 claves principales y 98 de respaldo: «no se sirve, no se borra» (V-11) |
| c7 — evidencia cruda | **Cumple** | k6, muestreo de Perfilamiento, Cotización e Identidad cada 5 s (929 muestras), eventos, base, Redis y avisos |

### Lectura por criterio

- **c1:** el perfilamiento cumple con caché y sin caché cuando las fuentes responden rápido. En lenta_4a, con todas las
  consultas sin acierto y Open Finance hasta 500 ms, el p95 no puede bajar de ~500 ms. **Con el diseño actual, la
  meta de c1 para 4a es inalcanzable** salvo que haya aciertos de caché. Además, 51 falsos bloqueos y colas de 4–9 s
  suben el p99.
- **c4:** en lenta_4b la degradación funciona: todas responden 201 y el 92 % sale marcada como degradada. El timeout de
  la fuente (0,7 s), sin embargo, está por encima de la latencia que exige c1 (400 ms).
- **c2, c3 y c4 con caída:** no fallan por la capacidad de Perfilamiento, que llegó como máximo a 2 réplicas y 69m de
  CPU en la meseta. Fallan por los falsos bloqueos y las paradas periódicas que se explican abajo.

## Falsos bloqueos (aparte de los errores por saturación)

**Definición:** un falso bloqueo es un 403 a un cliente **con** consentimiento.

**Mecanismo:** Perfilamiento falla cerrado.
- Si la consulta de consentimiento a Identidad pasa de 300 ms (`IDENTIDAD_TIMEOUT_S`), responde 5xx o tiene el circuit
  breaker abierto, trata al cliente como sin consentimiento.
- Responde el mismo `403 CONSENTIMIENTO_NO_VIGENTE` que en un bloqueo legítimo.

No son errores por saturación: no entran en `errores_saturacion` ni en c2.

| Escenario | Falsos bloqueos | Solicitudes con consentimiento | Frecuencia | Avisos `identidad_no_disponible` |
|---|---|---|---|---|
| humo | 1 | 289 | 0,35 % | 1 |
| miss | 1 | 5.041 | 0,02 % | 1 |
| calentar | 1 | 993 | 0,10 % | 1 |
| hit | 0 | 5.041 | 0 % | 0 |
| meseta | **2.431** | 20.556 | **11,83 %** | 488 |
| lenta_4a | 51 | 1.201 | 4,25 % | 20 |
| lenta_4b | 0 | 1.201 | 0 % | 0 |
| caida_5a | 42 | 1.201 | 3,50 % | 12 |
| caida_5b | 40 | 1.201 | 3,33 % | 11 |
| **Total** | **2.567** | **36.724** | **6,99 %** | 534 |

**Hay menos avisos que falsos bloqueos** porque, con el circuito abierto, Perfilamiento bloquea sin escribir nada en el
log: el breaker abre tras 5 fallos y se queda abierto 15 s. Los avisos son solo los timeouts reales. Un aviso más cayó
fuera de los escenarios.

**Cotización tiene el mismo mecanismo:**
- Usa el mismo cliente de consentimiento, con 300 ms de timeout y el breaker de 5 fallos y 15 s.
- Cuando el cliente de consentimiento falla, Cotización responde una cotización `aproximada` (perfil por defecto).
- En la meseta hubo 3.787, el 12,6 % del total.

**c5 y la base:**
- k6 registró 1.358 bloqueos legítimos y 2.567 falsos: 3.925 en total, frente a 4.028 intentos en la base.
- La diferencia de 103 es compatible con las consultas de la revocación, que usan la misma clave de carga. No se
  verificó fila por fila.

## Causa raíz

### No es la CPU de Identidad

El máximo de 5,5 vCPU y 6 réplicas de Identidad ocurrió durante la **siembra** (16:26–16:42):
- la siembra registra ~11.800 clientes por la API de Identidad, con bcrypt, desde 32 hilos;
- además agotó las conexiones de la RDS: 117 errores «remaining connection slots are reserved».

Durante la meseta, Identidad estuvo holgada:

| Ventana (UTC) | CPU de Identidad (mediana / máx.) | Réplicas | CPU del HPA (máx.) |
|---|---|---|---|
| Línea base de Cotización, 17:44–17:54 | 47 / 54 m | 2 | 10 % |
| Meseta, 17:54–18:54 | 78 / 108 m | 2 | 21 % |

### Paradas periódicas que afectan a la vez a Perfilamiento y a Cotización

Series por minuto de la meseta (crudo de k6):

- **Línea base y primeros 10 min de meseta, hasta las 18:03:**
  - p95 de Cotización entre 106 y 154 ms, con un pico de 525 ms a las 18:00;
  - p95 de Perfilamiento entre 27 y 255 ms;
  - entre 0 y 7 falsos bloqueos por minuto.
- **Desde las 18:04, un ciclo de ~3 min:**
  - minutos con paradas a las 18:04, 18:07, 18:10, 18:13, 18:16, 18:20, 18:23…;
  - en cada parada, ~100 falsos bloqueos y ~140 cotizaciones aproximadas en un minuto;
  - el p95 de Perfilamiento sube a 0,6–3,3 s y el de Cotización a 1–9,8 s;
  - entre paradas, los dos vuelven a ~110 ms.
- **Empeora hacia el final de la hora:** a las 18:36 y 18:46, el p95 de Cotización fue de 9,8 y 6,9 s.
- **Ráfagas:** 59 ráfagas de falsos bloqueos, con una duración mediana de 3 s y máxima de 32 s.

**Lectura:**
- Las latencias de varios segundos en **los dos** servicios superan con creces el timeout de 300 ms de la consulta a
  Identidad, así que la parada no está solo en Identidad: es una dependencia que los tres comparten.
- En cada parada, la consulta a Identidad pasa de 300 ms en cada pod de Perfilamiento y de Cotización. Al quinto fallo
  se abre el breaker, y durante 15 s todo sale bloqueado (Perfilamiento) o aproximado (Cotización). Eso explica las
  ráfagas de ~100 y la diferencia entre avisos y falsos bloqueos.

### Dependencia compartida: la RDS `db.t4g.micro` (verificada con CloudWatch: el ciclo sigue como hipótesis)

**Por qué se sospechó de ella:**
- **Es la única base de todos los servicios.**
  - En cada consulta de consentimiento, Identidad hace 3 viajes a la base: `pool_pre_ping`, `Cliente` y
    `Consentimiento`.
  - Perfilamiento escribe en cada enriquecimiento un perfil, dos filas de linaje y, por cada 403, un intento. Hubo
    34.203 perfiles y 68.406 filas de linaje en la ventana.
  - Cotización escribe en cada solicitud la cotización, su latencia y la trazabilidad.
- **Es una clase burstable con 1 GB de RAM.** Su CPU depende de créditos y su `max_connections` es bajo. La siembra
  agotó las conexiones.
- **Cada pod de Python abre hasta 15 conexiones:** el pool por defecto de SQLAlchemy, 5 más 10.
- **Ya pasó en HA03 con la misma clase** (ADR-0015, 2026-10-25): la RDS llegó a 153 conexiones y 89 % de CPU, e
  Identidad dejó 9.741 `ReadTimeout` en Cotización. Con `db.t4g.medium`, el humo 2 de HA03 no tuvo fallos y las
  aproximadas bajaron de 27,4 % a 0,04 %. Staging volvió a `db.t4g.micro` (`staging.tfvars`).

**Verificación con CloudWatch** (solo lectura, 16:30–19:50 UTC, resolución de 1 min; créditos cada 5 min). La serie
completa está en `resultados/cloudwatch-rds-por-minuto.csv`:

| Ventana (UTC) | CPU máx. | Conexiones máx. | Créditos mín. | Latencia lectura / escritura máx. | Memoria libre mín. | Swap |
|---|---|---|---|---|---|---|
| Siembra (16:30–16:41) | 17,7 % | 68 | 7,8 | 1,0 / 4,8 ms | 55 MB | 14 → 41 MB |
| Escenarios previos (16:42–17:42) | 10,4 % | 51 | 7,9 | 1,8 / 28,9 ms | 70 MB | 37–43 MB |
| Línea base de Cotización y meseta (17:43–18:54) | 42,1 % | 59 | 4,0 | 4,0 / 3,3 ms | 54 MB | 43 → 230 MB |
| Después (18:55–19:50) | 19,9 % | 49 | 3,2 | 1,8 / 13,3 ms | 52 MB | 153–205 MB |

`DiskQueueDepth` no pasó de 0,13 y `WriteIOPS` de ~50 en toda la ventana.

**Lo que las métricas descartan o no confirman:**
- **Créditos de CPU:** bajaron de 11,7 (18:00) a 3,2 (18:55), pero **nunca llegaron a 0**. No hubo estrangulamiento
  de la CPU por créditos.
- **Conexiones:** el máximo por minuto en la meseta fue 59, por debajo de las 68 de la siembra, cuando sí se agotaron.
  No hay señal de agotamiento en la meseta, aunque el máximo por minuto puede ocultar picos de segundos.
- **CPU de la RDS como causa del ciclo:** la correlación entre los falsos bloqueos del minuto *t* y la CPU es −0,03 en
  el mismo minuto y −0,15 en el anterior, pero **0,85 en el minuto siguiente**. Los picos de CPU (20–42 %) llegan un
  minuto **después** de cada parada, no antes: son consecuencia (probablemente la cola acumulada y los reintentos), no
  causa.
- **Disco:** latencias de pocos milisegundos y cola casi vacía en la meseta.

**Lo que sí confirman:**
- **Presión de memoria.** Con 1 GB de RAM, la memoria libre estuvo entre 52 y 114 MB toda la sesión, y el swap pasó
  de 43 MB al empezar la meseta a 139 MB a las 18:10 y a 230 MB como máximo. Es coherente con que la degradación
  empeore con el tiempo, pero las métricas por minuto no muestran que cause cada parada de ~3 min.

**Conclusión:** CloudWatch **no confirma** que la RDS cause el ciclo de ~3 min: no hubo agotamiento de créditos ni de
conexiones en la meseta, y la CPU sube después de las paradas. La causa del ciclo **sigue siendo una hipótesis**. Sí
queda confirmado que la instancia trabajó al límite de memoria y en swap creciente, lo mismo que la dejó corta en HA03.

**Sin explicar:**
- **El ciclo de ~3 min.** Quedan como candidatos checkpoints de PostgreSQL, el autovacuum o pausas en los propios
  servicios (pool de conexiones, GC). Distinguirlos pide Performance Insights o los logs de PostgreSQL
  (`log_checkpoints`, `log_autovacuum_min_duration`), que no estaban activos.
- **Los timeouts sueltos fuera de la meseta,** a las 16:45:00, 17:12:00 y 17:19:00, siempre en el segundo :00, con
  Identidad respondiendo en 307–370 ms. No hay CronJobs activos ni tareas periódicas en el código de Identidad.

### Por qué una parada corta afecta tanto

El consentimiento se consulta **síncronamente, sin caché, en cada operación de dos servicios del camino crítico**, con
un timeout de 300 ms y un breaker que falla cerrado sin dejar registro. Cualquier parada de más de 300 ms en Identidad
o en la base se convierte en bloqueos (Perfilamiento) y en cotizaciones aproximadas (Cotización), aunque Perfilamiento
y Cotización tengan capacidad de sobra.

## Hallazgos adicionales

- **`revocacion.py` usaba `datetime.UTC`.** Esa constante no existe en el Python 3.9 del generador (AL2023). Las
  revocaciones de las 17:42 y 19:38 fallaron al arrancar; se corrigió en `ed8d394` y se repitió a las 19:43.
- **Caché compartida entre Cotización y Perfilamiento** (solventa-backend#84). Se aisló en la medición con grupos
  disjuntos y vaciando la caché antes de miss.
- **Diferencia persistente de `rds.force_ssl`** en el parameter group de la plataforma.

## Desviaciones declaradas

- **Carga por el NLB interno (D4):** el borde público queda fuera.
- **Nodos fijos (D5).**
- **Despliegue desde ramas sin fusionar (D2),** con la imagen de Identidad `4a4f898-scripts`.
- **c3 se juzga por la diferencia entre la línea base y la meseta (D8).** Esta corrida no es válida para c3 por las
  aproximadas.
- **c5 con evidencia indirecta (D13).**
- **c6:** «no se sirve, no se borra» (D12), con resolución de 1 s.
- **La meseta reutiliza el grupo `pool`:** tasa de aciertos de 94,5 %.
- **Incidente de operación.** Claude Code avisó que había detenido por falta de memoria el orquestador local de la
  secuencia, pero el proceso siguió vivo y completó todos los escenarios. Una reanudación lanzada a las 19:38 solo
  alcanzó a correr una revocación, que falló al importar: no hubo carga superpuesta con caida_5b.
