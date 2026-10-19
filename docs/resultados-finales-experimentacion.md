# Resultados Finales de Experimentación

*Validación de las hipótesis de diseño — Sprint 1*

*Solventa — Plataforma Insurtech sobre Open Finance*

*MISW4501 · Proyecto Final · Semana 7 · Grupo 10*

**Integrantes**

Esteban Leal · Andrés Eugenio Gómez Hernández

Stiven Cardona Monsalve · Juan Manuel Domínguez Osorio

Universidad de los Andes — Maestría en Ingeniería de Software — 2026

---

# 1. Resumen ejecutivo

Este documento presenta los resultados finales de los dos experimentos de arquitectura diseñados en la Semana 5, el análisis de si las hipótesis de diseño se cumplieron, y las decisiones derivadas.

Ambos experimentos se ejecutaron sobre **infraestructura real de AWS** —EKS, ElastiCache, RDS y Network Load Balancer, provisionada íntegramente con Terraform— y no sobre un entorno simulado. Esa decisión resultó determinante: los dos hallazgos de mayor peso de esta ronda se originan en el comportamiento de la infraestructura real y no habrían aparecido en una ejecución local con contenedores.

| Experimento | Hipótesis de diseño | ASR | Resultado | Veredicto |
| --- | --- | --- | --- | --- |
| HA01 — Latencia de cotización | Cache-aside + circuit breaker + degradación controlada | RQ-L1, RQ-D2 | 0 % de cotizaciones fallidas en ambos escenarios; p95 de 357,6 ms en normal y 123,9 ms en degradado | **Parcialmente validada** |
| HA02 — Throughput de perfilamiento | Cache-aside + timeout + HPA | RQ-E1.2 | 20.002 perfilamientos/hora sostenidos, 0 % de fallos, degradación de latencia de −1,0 % | **Validada** |

**Los cuatro resultados de fondo:**

**La resiliencia ante degradación del proveedor quedó validada sin reservas.** Con Open Finance respondiendo entre 800 y 1.500 ms, el 100 % de las cotizaciones se resolvieron y ninguna falló. Ese es el propósito por el que HA01 existe.

**Las metas de latencia en operación normal no se cumplieron**, pero el análisis muestra que la causa no está donde el diseño la anticipaba: el mayor contribuyente al exceso es el transporte entre el cliente y el borde del sistema, no el servicio. Separar ambas brechas es el resultado analítico más importante de HA01 (§3.5).

**HA02 cumplió todas sus metas.** Throughput sostenido, latencia estable, caché por encima del objetivo, sin reinicios ni agotamiento de memoria.

**Pero el autoescalado nunca se activó**, ni siquiera bajo una corrida de capacidad a 30 veces la carga del ASR. La causa es estructural y constituye el hallazgo de arquitectura más relevante del ciclo: el patrón cache-aside desacopla el consumo de CPU del volumen de solicitudes, de modo que un HPA gobernado por CPU es ciego a la carga de este servicio (§5).

---

# 2. Ambiente de ejecución

Todo el ambiente se levantó bajo demanda con Terraform y se destruyó al terminar, por control de créditos. La destrucción se verificó recurso por recurso: 75 recursos eliminados, sin elementos huérfanos facturando.

| Componente | Configuración |
| --- | --- |
| Cluster | Amazon EKS, node group administrado de 3 nodos `t3.medium` (x86_64), multi-AZ sobre 2 zonas |
| Caché | ElastiCache Redis |
| Base de datos | RDS PostgreSQL |
| Exposición de servicios | Network Load Balancer por servicio, `internet-facing` |
| Observabilidad | Muestreo del estado del cluster cada 10-15 s hacia archivo (ver §2.1) |
| Generador de carga | k6, ejecutado **fuera de la VPC**, contra el DNS público del balanceador |
| Proveedor externo | Stub de Open Finance con latencia inyectable, 1 réplica |

El node group se dimensionó en 3 nodos en lugar de los 2 del diseño original. El motivo: con 2 nodos y descontando lo ya desplegado, el margen de CPU sólo admitía unas 5 réplicas de `ms-perfilamiento`, frente a las 10 que declara el HPA. Con 3 nodos el margen sube a ~4.590 milicores, es decir ~9 réplicas, y `maxReplicas` deja de ser inalcanzable por construcción — condición necesaria para que la corrida de capacidad (§4.4) pudiera medir un techo real.

## 2.1 Desviación respecto del diseño: la observabilidad prevista no estuvo operativa

El diseño de la Semana 5 comprometía tres gráficas por experimento a partir de tableros de Grafana alimentados por OpenTelemetry. **Ese material no se produjo.** El colector de OpenTelemetry se desplegó correctamente, pero exporta trazas y métricas a dos servicios —un backend de trazas y uno de métricas— que nunca se desplegaron en el cluster. Los tableros versionados en el repositorio nunca recibieron datos.

Se registra como desviación explícita porque condiciona qué evidencia pudo producirse. La consecuencia práctica fue distinta para cada experimento:

Para **HA01** resultó suficiente, porque sus métricas principales —latencia, tasa de fallos, hit de caché— las produce el propio generador de carga.

Para **HA02 no lo habría sido**, porque sus métricas principales —número de réplicas en el tiempo, CPU y memoria por réplica, ausencia de reinicios— sólo existen del lado del cluster. Ejecutarlo sin resolver esto habría producido una corrida sin evidencia sobre su propia hipótesis. Se construyó por ello un muestreador que registra cada 10-15 segundos el estado del HPA, las réplicas listas, el consumo de CPU y memoria por pod, los reinicios acumulados y los pods pendientes de asignación, hacia un archivo de resultados por corrida. Toda la evidencia de autoescalado de la §4 proviene de ahí.

---

# 3. Experimento 1 — HA01 · Latencia de cotización con Open Finance degradado

## 3.1 Hipótesis y metas

**Hipótesis de diseño:** el llamado a Open Finance dentro del flujo de cotización se protege con un timeout duro de 700 ms y se degrada a un valor cacheado o por defecto, en vez de esperar la respuesta del proveedor o fallar la cotización.

| Meta | Escenario A (normal) | Escenario B (degradado) |
| --- | --- | --- |
| p95 de latencia extremo a extremo | ≤ 250 ms | ≤ 300 ms |
| p99 de latencia extremo a extremo | ≤ 500 ms | Sin meta definida |
| Tasa de cotizaciones fallidas | 0 % | 0 % |
| Hit de caché desde el minuto 3 | ≥ 60 % | — |

## 3.2 Configuración real

| Componente | Planificado | Real | Desviación |
| --- | --- | --- | --- |
| MS Cotización | 1 pod; CPU 500m/1000m; Mem 512Mi/1Gi | Conforme | Ninguna |
| TTL de caché | 5 min | 300 s | Ninguna |
| Timeout hacia Open Finance | 700 ms | 700 ms | Ninguna |
| Circuit breaker | 5 fallos consecutivos | 5 fallos; reintento cada 30 s | Ninguna |
| Mock Open Finance | Esc. A 200-500 ms; Esc. B 800-1500 ms | Conforme | Ninguna |
| Carga | Rampa 500→5.000 sol/min (15 min) + meseta 10 min | Conforme | Ninguna |
| Exposición del servicio | **No especificada en el diseño** | Network Load Balancer público | **Sí** |
| Ubicación del generador | **No especificada en el diseño** | Fuera de la VPC | **Sí** |

Las dos últimas filas son desviaciones que el diseño no anticipó porque **no especificaba estos aspectos**. Su impacto sobre el resultado es sustancial, lo que constituye en sí mismo una lección: un diseño experimental que no fija dónde se mide ni cómo se expone el servicio deja fuera de control variables que determinan el veredicto.

## 3.3 Resultados

Corrida completa de ambos escenarios, 91.249 solicitudes por escenario.

| Métrica | Meta | Escenario A (normal) | Veredicto | Escenario B (degradado) | Veredicto |
| --- | --- | --- | --- | --- | --- |
| Tasa de cotizaciones fallidas | 0 % | **0 %** | Cumple | **0 %** | Cumple |
| p95 de latencia (cliente) | ≤ 250 / ≤ 300 ms | **357,6 ms** | No cumple | **123,9 ms** | Cumple |
| p99 de latencia (cliente) | ≤ 500 ms (solo normal) | **565,3 ms** | No cumple | 829,7 ms | Sin meta |
| p95 de latencia (interno) | Referencia | **261 ms** | — | — | — |
| Hit de caché | ≥ 60 % desde min. 3 | **94,4 %** | Cumple | 0 % | Esperado tras limpieza |
| Cotizaciones degradadas | Referencia | **0 %** | Correcto: circuito cerrado | **100 %** | Correcto: circuito abierto |

El p99 no viene en el resumen estándar del generador de carga, que sólo exporta p90 y p95. Se calculó aparte filtrando el archivo crudo por métrica y etiqueta de escenario, y computando el percentil sobre los 91.249 valores de cada escenario.

## 3.4 Hallazgos durante la ejecución

**Hallazgo 1 — Fallos de conexión por el idle timeout del balanceador.** Las dos primeras corridas presentaron fallos de solicitud del tipo `EOF` —conexiones TCP cortadas a mitad de camino— que contaban directamente contra la meta de 0 % de cotizaciones fallidas (0,61 % y 0,58 %).

El diagnóstico atravesó dos hipótesis. La primera atribuía el corte al health check del balanceador: con una sola réplica, un bache momentáneo podría marcarla como no sana y, dado que la terminación de conexiones ante destino no sano está habilitada por defecto, el balanceador cortaría en seco el tráfico en curso. Se relajaron los umbrales del health check y se repitió la corrida.

**El fallo reapareció en el segundo 608, frente al 621 de la corrida anterior.** Trece segundos de diferencia entre dos corridas independientes indican un fenómeno determinístico, no un blip aleatorio de CPU: si la causa hubiera sido el health check, relajar los umbrales —que pasaron a exigir unos 150 s de mal comportamiento sostenido— habría cambiado sustancialmente la frecuencia o el momento del fallo.

La causa raíz verificada es distinta: los Network Load Balancer de AWS tienen un **idle timeout de conexión TCP fijo en 350 segundos, no configurable**. El generador reutilizaba conexiones keep-alive; las abiertas temprano en la rampa y no reutilizadas durante más de 350 s ya habían sido cerradas del lado del balanceador, y el cliente sólo se enteraba al intentar reutilizarlas. Los tiempos cuadran: conexiones abiertas hacia el segundo 250-260, más 350 s de timeout, dan el segundo 600-610.

La mitigación fue deshabilitar la reutilización de conexiones en el generador. El costo es un handshake TCP adicional por solicitud —que se suma a la latencia observada— a cambio de eliminar los fallos. Tras aplicarla, la tasa de fallos bajó a 0 % en ambos escenarios.

**Hallazgo 2 — El escenario degradado no se ejecutaba por un defecto de la instrumentación.** Ninguna de las dos primeras corridas llegó al Escenario B. La causa: el generador de carga retorna un código de salida distinto de cero cuando incumple un umbral —lo que es un **resultado experimental válido**, no un error— y el script de orquestación tenía activada la terminación ante error, de modo que abortaba antes de reconfigurar el proveedor simulado.

**Hallazgo 3 — Brecha entre la latencia medida por el cliente y la medida dentro de la aplicación.** El p95 observado por el generador (357,6 ms) supera en unos 96 ms al de la instrumentación interna (261 ms). Se analiza en la §3.5 porque es determinante para el veredicto.

## 3.5 Análisis: ¿se cumplió la hipótesis?

### Lo que sí se cumplió: resiliencia ante degradación

Con el proveedor respondiendo entre 800 y 1.500 ms, el 100 % de las cotizaciones se resolvieron correctamente, todas marcadas como degradadas, y **ninguna falló**. El p95 de 123,9 ms quedó muy por debajo de la meta de 300 ms.

**Por qué funciona.** Los tres patrones cooperan como se previó. El timeout duro impide que una solicitud quede bloqueada esperando al proveedor. El circuit breaker, tras cinco fallos consecutivos, deja de intentar la llamada — lo que explica el p95 excepcionalmente bajo: en régimen degradado la mayoría de las solicitudes ni siquiera intentan salir a la red, se resuelven desde caché o valor por defecto. La degradación controlada convierte en respuesta marcada lo que habría sido un error. La evidencia de que la cobertura de caminos de fallo es completa es que el 100 % salió marcado y **ninguna solicitud cayó fuera de ese camino**.

### Lo que no se cumplió: el presupuesto de latencia en operación normal

El p95 y el p99 exceden la meta: 357,6 ms frente a 250 ms, y 565,3 ms frente a 500 ms. La tabla de decisiones de la Semana 5 prescribía, ante este resultado, revisar en orden el TTL de la caché, el calentamiento de caché al arranque y la política de escritura a la base de datos.

**El análisis muestra que esa prescripción no aplica**, y por qué:

| Medida prescrita | Evidencia | ¿Aplica? |
| --- | --- | --- |
| Revisar el TTL de la caché | Hit de caché del 94,4 % frente a una meta del 60 % | **No.** La caché supera ampliamente lo previsto; ajustar el TTL no mejora un 94 % |
| Calentamiento de caché al arranque | El hit supera el 60 % desde el minuto 3, como se esperaba | **No.** Resolvería un problema de arranque que no se observó |
| Escritura a la base de datos en modo desatendido | La persistencia no aparece como contribuyente relevante al p95 | **No.** Ya está fuera del camino crítico |

**Dónde está realmente la brecha.** Al separar la medición interna de la externa, los 107,6 ms de exceso se descomponen en dos partes que exigen decisiones distintas:

| Componente | Magnitud | Naturaleza | Decisión |
| --- | --- | --- | --- |
| Tramo cliente ↔ borde | ~96 ms | Transporte: red pública más balanceador, con el generador fuera de la VPC | No es optimizable desde el código. Exige precisar dónde se mide el ASR |
| Exceso interno del servicio | 11 ms (261 frente a 250) | Desempeño propio bajo carga plena | Margen real de mejora, pero pequeño |

A esto se suma que la mitigación del Hallazgo 1 **introduce un handshake TCP adicional por solicitud**, que forma parte de los 96 ms atribuidos al transporte. Parte del exceso de latencia es, por tanto, el precio pagado por eliminar los fallos. Ese intercambio es deliberado y defendible —un 0,6 % de cotizaciones fallidas es peor que unos milisegundos de latencia— pero se registra como tal y no como un resultado neutro.

**Decisión derivada, y en qué se aparta de lo prescrito.** En lugar de las tres medidas de la tabla original se adoptan dos: fijar que RQ-L1 **se mide sobre el tiempo que observa el cliente** —porque el usuario real también paga la latencia de transporte, y medirla internamente produciría un cumplimiento ficticio— y repetir la medición con el generador dentro de la misma región para separar el costo de red inherente al producto del costo de haber medido desde una máquina de desarrollo.

Esta es la corrección de mayor calado del experimento: la tabla de decisiones asumía que un fallo de latencia tendría origen interno, y por eso todas sus ramas apuntan a componentes del servicio. El experimento mostró que el mayor contribuyente está fuera de él.

### Punto de atención: cola larga en escenario degradado

El p99 del Escenario B es de 829,7 ms frente a un p95 de 123,9 ms. Una dispersión así con un p95 tan bajo señala un fenómeno periódico, no una degradación general.

La hipótesis más probable, no verificada con trazas en esta ronda, es el reintento del circuit breaker: cada 30 segundos el circuito pasa a estado de prueba y realiza una llamada real al proveedor, que sigue tardando entre 800 y 1.500 ms y vuelve a fallar. Como esa prueba ocurre **dentro del camino de una solicitud de usuario**, esa solicitud paga el costo completo. Sobre 25 minutos de corrida son unas 50 solicitudes penalizadas: suficientes para mover el p99 sin afectar al p95.

No incumple ninguna meta —HA01 no define p99 para el escenario degradado— pero una cola de 830 ms es material para la experiencia de usuario. La acción propuesta es trasladar el sondeo de recuperación a segundo plano, de modo que ninguna solicitud de usuario pague el costo de descubrir que el proveedor sigue caído.

**Ronda 2 (2026-10-18, local):** con el sondeo de recuperación en segundo plano, el p99 del Escenario B bajó a 106,5 ms y desapareció la solicitud lenta cada 30 segundos, lo que es consistente con la hipótesis. Sigue sin confirmarse con trazas. Ver [`ha01-ronda2/resultados/VEREDICTO.md`](../ha01-ronda2/resultados/VEREDICTO.md).

## 3.6 Veredicto HA01

**HIPÓTESIS PARCIALMENTE VALIDADA.**

| Dimensión | ASR | Veredicto | Acción |
| --- | --- | --- | --- |
| Disponibilidad ante degradación | RQ-D2 | **Validada** | Se mantiene el diseño |
| Latencia en operación degradada | RQ-L1 | **Validada** (123,9 ms ≤ 300 ms) | Se mantiene el diseño |
| Eficacia de la caché | RQ-L1 | **Validada** (94,4 % ≥ 60 %) | Se mantiene, con dispersión en el TTL |
| Latencia en operación normal | RQ-L1 | **Refutada** (357,6 ms > 250 ms) | Segunda medición (local, generador en la misma red): p95 de 301,5 ms, sigue sin cumplir, y la brecha cliente–interna bajó a 28 ms. El p95 lo explican los miss de caché (8,2 %), no el punto de medición |

---

# 4. Experimento 2 — HA02 · Throughput de perfilamiento en línea

## 4.1 Hipótesis y metas

**Hipótesis de diseño:** el enriquecimiento en línea del perfil se puede ejecutar protegido por el mismo patrón de caché + timeout que Cotización, sin degradar el desempeño del sistema, con autoescalado horizontal absorbiendo las ráfagas.

| Meta | Valor |
| --- | --- |
| Throughput sostenido | ≥ 20.000 perfilamientos/hora durante 30 min |
| Degradación del p95 frente a la línea base | ≤ 10 % |
| Hit de caché en régimen | ≥ 40 % |
| Estabilidad de recursos | Réplicas estables, sin agotamiento de memoria ni estrangulamiento sostenido de CPU |

## 4.2 Defectos de instrumentación corregidos antes de ejecutar

La preparación de la corrida identificó cinco defectos que habrían invalidado los resultados. Se documentan porque explican por qué la corrida final produjo datos utilizables al primer intento, a diferencia de HA01.

| # | Defecto | Consecuencia si no se corrige |
| --- | --- | --- |
| 1 | El conjunto de datos sintéticos estaba excluido del control de versiones | La corrida aborta en la inicialización, sin generar un solo dato |
| 2 | El etiquetado de fases derivaba el tiempo de un reloj local por usuario virtual | Cada usuario virtual creado a mitad de corrida etiquetaba sus solicitudes con la fase equivocada, invalidando el único umbral significativo |
| 3 | Nada registraba el estado del HPA, las réplicas ni el consumo de recursos | La corrida terminaría sin evidencia sobre el autoescalado, que es la hipótesis a validar |
| 4 | Al servicio le faltaban las anotaciones de health check aplicadas a Cotización tras el Hallazgo 1 | Riesgo de reproducir los fallos de conexión durante el escalado |
| 5 | No existía forma de validar el encadenamiento completo sin consumir los 70 minutos de la corrida real | Repetir el patrón de HA01: quemar jornadas completas por defectos de instrumentación |

Se añadió además un modo de ejecución comprimido que recorre las cuatro fases en unos 5 minutos, usado para verificar el encadenamiento completo antes de comprometer la corrida real.

Durante el despliegue apareció un sexto problema, ajeno al diseño: las imágenes construidas en una máquina Apple Silicon son de arquitectura `arm64`, mientras que los nodos `t3.medium` son `x86_64`. Los pods fallaban al descargar la imagen. Se corrigió forzando la plataforma de destino en la construcción.

## 4.3 Resultados — corrida del diseño

Cuatro fases, 70 minutos, **19.166 solicitudes, 0 % de fallos**.

| Fase | Solicitudes | Duración | Perfilamientos/hora | p95 | p99 | Hit de caché |
| --- | --- | --- | --- | --- | --- | --- |
| Rampa | 1.874 | 879 s | 7.679 | 443,7 ms | 491,8 ms | 16,8 % |
| Línea base | 1.459 | 300 s | 17.512 | 418,3 ms | 440,6 ms | 40,2 % |
| **Meseta** | **10.000** | **1.800 s** | **20.002** | **414,0 ms** | **449,9 ms** | **49,7 %** |
| Sobrecarga | 3.750 | 600 s | 22.502 | 426,1 ms | 450,1 ms | 51,6 % |
| Recuperación | 2.083 | 589 s | 12.721 | 432,7 ms | 465,6 ms | 51,0 % |

La fase de sobrecarga alcanzó 22.502/hora frente a los 25.000 nominales. No es un déficit: el generador rampa de 20.000 a 25.000 durante esos 10 minutos, de modo que el promedio esperado de la ventana es ~22.500.

**Cumplimiento de metas:**

| Meta | Objetivo | Observado | Veredicto |
| --- | --- | --- | --- |
| Throughput sostenido en meseta | ≥ 20.000/hora | **20.002/hora** | Cumple |
| Degradación del p95 (meseta frente a línea base) | ≤ +10 % | **−1,0 %** | Cumple |
| Tasa de solicitudes fallidas | 0 % | **0 %** (0 de 19.166) | Cumple |
| Hit de caché en régimen | ≥ 40 % | **49,7 %** | Cumple |
| Estabilidad de recursos | Sin reinicios ni agotamiento | **0 reinicios, 0 pods pendientes, 0 eventos de error**; memoria en 119 Mi de 1 Gi por pod | Cumple |

La latencia es notablemente plana: entre la línea base y la sobrecarga el p95 se mueve 8 milisegundos.

**Comportamiento del autoescalado (224 muestras del cluster):**

| Indicador | Valor |
| --- | --- |
| Réplicas | **2 en todo momento** (mínimo configurado 2, máximo 10) |
| Utilización de CPU frente al umbral del 70 % | Media **4,2 %**, pico **7 %** |
| CPU total en 2 réplicas | Media 47 m, pico 74 m |
| Eventos de reescalado emitidos por el HPA | **Ninguno** |

## 4.4 Corrida de capacidad

Dado que la corrida del diseño no ejerció el autoescalado, se ejecutó una segunda corrida complementaria cuyo único propósito era **forzar el escalado y localizar el punto de saturación**. La rampa no apuntó a una tasa calculada: subió sostenidamente muy por encima de cualquier estimación y dejó que el muestreo registrara dónde ocurría el escalado real.

| Fase | Solicitudes | Perfilamientos/hora | p95 |
| --- | --- | --- | --- |
| Rampa (12 min) | 61.992 | 309.759 | 400,4 ms |
| **Meseta máxima (5 min)** | **50.000** | **598.391** | **381,4 ms** |
| Recuperación (3 min) | 15.007 | 302.540 | 408,6 ms |

**126.999 solicitudes, 0 % de fallos, a 30 veces la carga del ASR.** El p95 en la meseta máxima (381,4 ms) es *mejor* que el de la corrida del diseño (414,0 ms): el sistema no se degradó bajo carga extrema, mejoró.

| Indicador | Valor |
| --- | --- |
| Réplicas | **2 en todo momento**; ningún evento de reescalado |
| Utilización de CPU frente al umbral del 70 % | Media 26 %, pico **52 %** |
| Pods pendientes de asignación / reinicios | 0 / 0 |
| Hit de caché | **93,4 %** |
| CPU del proveedor simulado | Media 12 m, pico 32 m sobre un límite de 1.000 m |

El último dato descarta que el stub fuera el cuello de botella: a 166 solicitudes/segundo consumía el 3 % de su límite. Y el generador alcanzó la tasa objetivo en todo momento, lo que descarta también que el cliente lo fuera.

## 4.5 Análisis: ¿se cumplió la hipótesis?

**La hipótesis de HA02 se cumple en sus tres criterios.** El throughput objetivo se sostiene, la latencia no se degrada —de hecho mejora levemente— y los recursos permanecen estables sin reinicios ni pods sin asignar. La reutilización del patrón de Cotización en Perfilamiento, que era el punto de sensibilidad declarado en el diseño, resultó viable: el patrón de llegada distinto no cambió el comportamiento.

**Por qué funciona.** El caché absorbe la mayoría de las solicitudes y saca al proveedor externo del camino crítico, exactamente como en HA01. Con 5.000 clientes sintéticos y un TTL de 15 minutos, a la tasa de la meseta cada clave se vuelve a consultar dentro de su ventana de vigencia en cerca de la mitad de los casos, lo que da el 49,7 % observado.

**Pero el autoescalado no se validó, porque nunca se ejerció.** Y la corrida de capacidad demostró que no se trata de haber elegido mal la carga de prueba: es estructural. Se analiza en la §5.

## 4.6 Veredicto HA02

**HIPÓTESIS VALIDADA**, con una salvedad sobre el componente de autoescalado.

| Dimensión | ASR | Veredicto |
| --- | --- | --- |
| Throughput sostenido | RQ-E1.2 | **Validada** (20.002/hora) |
| Estabilidad de latencia | RQ-E1.2 | **Validada** (−1,0 % frente a ≤ +10 %) |
| Eficacia de la caché | RQ-E1.2 | **Validada** (49,7 % ≥ 40 %) |
| Estabilidad de recursos | RQ-E1.2 | **Validada** (sin reinicios ni pods pendientes) |
| Autoescalado horizontal | RQ-E1.2 | **No ejercido** — ver §5 |

---

# 5. Hallazgo transversal: el caché desacopla el consumo de CPU del volumen de solicitudes

Este es el hallazgo de arquitectura más relevante del ciclo, y surgió de comparar las dos corridas de HA02.

| Carga | Hit de caché | CPU por solicitud/segundo |
| --- | --- | --- |
| 5,6 sol/s (meta del ASR) | 49,7 % | **14,6 m** |
| 166 sol/s (corrida de capacidad) | **93,4 %** | **1,8 m** |

A mayor tasa, más solicitudes caen dentro de la ventana de vigencia del caché, el hit sube al 93 % y casi ninguna solicitud llega a Open Finance. Una solicitud servida desde caché cuesta **ocho veces menos CPU** que una que sale a la red. Se ve con claridad en la latencia interna de la corrida de capacidad: mediana de **0 ms** y p90 de 2 ms, porque la mayoría se resuelve desde Redis casi instantáneamente.

**La consecuencia es contraintuitiva: el umbral del autoescalado se aleja a medida que sube la carga.** Extrapolando desde la meseta máxima, el HPA necesitaría unas 380 solicitudes/segundo —cerca de 1.366.000 perfilamientos/hora, unas 68 veces la meta del ASR— para alcanzar el 70 % de utilización de CPU.

**Decisión de arquitectura derivada.** La elección del HPA frente a alternativas sin servidor sigue siendo válida en sus términos generales. Lo que este hallazgo cuestiona es **su parametrización**: la utilización de CPU es un disparador estructuralmente inadecuado para un servicio cuyo consumo está dominado por espera de entrada/salida y cuyo caché absorbe el crecimiento del volumen. El servicio puede estar saturado de solicitudes concurrentes con la CPU casi ociosa. Si se quiere que el autoescalado reaccione a este perfil de carga, la métrica debe ser el número de solicitudes concurrentes o en cola, no la CPU.

Y plantea una pregunta legítima para el Proyecto Final 2: **con dos réplicas sosteniendo 600.000 perfilamientos/hora sin degradarse, el autoescalado no es lo que satisface RQ-E1.2 en el Sprint 1.** Conviene mantenerlo como red de seguridad ante fallos de réplica, pero no presentarlo como el mecanismo que cumple el ASR de escalabilidad, porque la evidencia dice que no interviene.

---

# 6. Síntesis: estado de las hipótesis de diseño

| ID | Hipótesis | ASR | Resultado | Estado |
| --- | --- | --- | --- | --- |
| HA01 | Cache-aside + circuit breaker + degradación controlada | RQ-L1, RQ-D2 | 0 % de fallos en ambos escenarios; p95 de 357,6 ms (normal) y 123,9 ms (degradado); caché 94,4 % | **Parcialmente validada** |
| HA02 | Cache-aside + timeout + HPA | RQ-E1.2 | 20.002/hora sostenidos; degradación −1,0 %; caché 49,7 %; sin reinicios | **Validada** (autoescalado no ejercido) |

| ASR | Meta | Evidencia | Veredicto |
| --- | --- | --- | --- |
| RQ-L1 (latencia, normal) | p95 ≤ 250 ms, p99 ≤ 500 ms | 357,6 ms / 565,3 ms desde el cliente; 261 ms interno | **No cumple** |
| RQ-L1 (latencia, degradado) | p95 ≤ 300 ms | 123,9 ms | **Cumple** |
| RQ-L1 (eficacia de caché) | ≥ 60 % desde min. 3 | 94,4 % | **Cumple** |
| RQ-D2 (disponibilidad ante degradación) | 0 % de cotizaciones fallidas | 0 % en ambos escenarios | **Cumple** |
| RQ-E1.2 (escalabilidad) | ≥ 20.000 perfilamientos/hora | 20.002/hora sostenidos; 598.391/hora sin degradarse | **Cumple** |

---

# 7. Impacto sobre los modelos de arquitectura

El detalle completo está en el documento *Documento de Arquitectura Ajustado*, que acompaña a este. En resumen:

| Observación | Modelo afectado | Impacto | Acción |
| --- | --- | --- | --- |
| El tramo cliente ↔ borde no estaba representado | Vista funcional, flujo de cotización | Ajuste menor | Presupuesto de latencia explícito por etapa |
| El balanceador no figuraba como nodo | Vista de despliegue | **Ajuste estructural** | Se incorpora con sus propiedades operativas, incluido el idle timeout no configurable |
| El registro de latencia no distingue interna de extremo a extremo | Vista de información | Ajuste menor | Dos campos nuevos |
| Cache-aside, circuit breaker y degradación validados | Patrones | Ninguno | Se marcan como validados; se añade dispersión al TTL. En la ronda 2, el jitter suavizó pero no eliminó las oleadas de expiración cada ~5 min |
| La cotización depende síncronamente de Identidad (consentimiento) | Vista funcional, flujo de cotización | Ajuste menor | Presupuesto de latencia por etapa: p95 ≈ 6,5 ms, medido aislado en la ronda 2 |
| El autoescalado no se activa ni a 30× la carga del ASR | Patrones | **Ajuste de parametrización** | La métrica de escalado deja de ser CPU |
| La observabilidad prevista no estaba operativa | Plataforma de experimentación | Ajuste de proceso | Captura de estado del cluster implementada |

**Ningún resultado obliga a modificar el modelo de componentes**: no se agrega, elimina ni reasigna ningún componente del sistema. La arquitectura lógica resistió la confrontación con la evidencia; lo que cambió fue la comprensión del entorno en el que se despliega.

---

# 8. Lecciones de método experimental

Se registran porque el valor de un experimento está tanto en el resultado como en la confianza que merece el procedimiento que lo produjo.

**Una mitigación que no cambia el síntoma es información válida.** La primera hipótesis sobre los fallos de conexión se descartó con evidencia —el mismo instante de fallo en dos corridas independientes— y no por intuición. La mitigación aplicada se conservó porque sigue siendo buena práctica con una sola réplica, aunque no fuera la causa.

**Hay que distinguir "el script falló" de "el sistema no cumplió una meta".** Un umbral incumplido es exactamente la señal que el experimento existe para capturar, y tratarlo como excepción que aborta el proceso costó dos jornadas completas de infraestructura.

**Validar la instrumentación antes de comprometer la corrida.** El modo comprimido de 5 minutos introducido para HA02 detectó, en la práctica, que el encadenamiento completo funcionaba, y evitó repetir el patrón de HA01.

**Un diseño experimental debe fijar dónde se mide.** Las dos desviaciones de mayor impacto en HA01 fueron aspectos que el diseño no especificaba, no aspectos en los que la ejecución se apartara del diseño.

---

# 9. Evidencias

## 9.1 Experimento 2 — HA02

Se entregan tres paquetes de evidencia, uno por corrida. Cada uno contiene la salida cruda completa del generador de carga, el muestreo del estado del cluster, el estado del autoescalado antes y después, los eventos del namespace, la asignación de recursos de los nodos y los logs del servicio.

Carpeta con los tres paquetes: [evidencias-experimentos](https://drive.google.com/drive/folders/17CoSIKl8j3tvbVwXmZI7ye9bcqAtSNfY)

| Paquete | Corrida | Contenido destacado |
| --- | --- | --- |
| [ha02-corrida-diseno-4-fases.zip](https://drive.google.com/file/d/1i2m6xrDKJo61YU1DAAsTzw9ZskO-vohN/view?usp=sharing) | Corrida del diseño, 70 min, 19.166 solicitudes | Datos que sustentan el cumplimiento de las cuatro metas de RQ-E1.2 (§4.3) |
| [ha02-corrida-capacidad.zip](https://drive.google.com/file/d/1S6hfGiPSkHNakhs_qPz72cSfukGfCoiY/view?usp=sharing) | Corrida de capacidad, 20 min, 126.999 solicitudes | Datos que sustentan el hallazgo transversal sobre el autoescalado (§5); incluye el muestreo del proveedor simulado |
| [ha02-humo-validacion-instrumentacion.zip](https://drive.google.com/file/d/1T5UZKxtSIMw0jZUxwfSqkly9NXFA6w6F/view?usp=sharing) | Validación de instrumentación, 5 min | Evidencia de que el encadenamiento completo se verificó antes de comprometer la corrida real |

Archivos incluidos en cada paquete:

| Archivo | Qué sustenta |
| --- | --- |
| `cluster-samples.csv` | Réplicas del HPA, utilización de CPU frente al umbral, CPU y memoria por pod, reinicios y pods pendientes, muestreados cada 10-15 s. **Es la evidencia central del comportamiento del autoescalado** |
| `mock-samples.csv` | Consumo del proveedor simulado (solo en la corrida de capacidad). Descarta que el stub fuera el cuello de botella |
| `perfilamiento-summary.json` / `capacidad-summary.json` | Resumen de métricas y veredicto de umbrales del generador de carga |
| `perfilamiento.json` / `capacidad.json` | Salida cruda punto por punto, con la etiqueta de fase en cada solicitud. Permite recalcular percentiles por fase |
| `hpa-antes.txt` / `hpa-despues.txt` | Estado y configuración del autoescalado al inicio y al cierre de la corrida |
| `events.txt` | Eventos del namespace. Sustenta la ausencia de reescalados, reinicios y fallos de asignación |
| `nodes.txt` / `nodes-allocated-*.txt` | Capacidad de los nodos y recursos reservados. Sustenta el cálculo del techo de réplicas (§2) |
| `ms-perfilamiento.log` | Logs del servicio durante la corrida |

## 9.2 Evidencia común

| Evidencia | Ubicación | Descripción |
| --- | --- | --- |
| Bitácora de hallazgos | Repositorio de experimentos, `docs/hallazgos.md` | Registro cronológico de cada síntoma, su causa raíz verificada y la mitigación aplicada |
| Código de infraestructura y experimentos | Repositorio de experimentos | Infraestructura como código, microservicios, stub del proveedor y scripts de ejecución |
| Video con evidencias de ejecución | *Pendiente — enlace a incorporar* | Fragmentos de las corridas reales sobre AWS |

## 9.3 Experimento 1 — HA01

*Pendiente de incorporar.* Los resultados crudos de la corrida completa de HA01 del 2026-09-12 (`results/archive/2026-09-12_ha01-completo-con-fixes/`) están en la máquina del integrante que ejecutó ese experimento y se anexarán como paquete equivalente a los de la §9.1.

Los valores reportados en la §3 de este documento provienen de la bitácora de hallazgos, que los registró en el momento de la ejecución junto con la ruta de los archivos de origen.

Nota para el cierre de la entrega: la corrida de HA01 es anterior a la instrumentación de muestreo del cluster descrita en la §2.1, de modo que su paquete no incluirá `cluster-samples.csv`. Para HA01 eso no representa una pérdida de evidencia, porque sus métricas principales —latencia, tasa de fallos, hit de caché y estado del circuito— las produce el propio generador de carga.
