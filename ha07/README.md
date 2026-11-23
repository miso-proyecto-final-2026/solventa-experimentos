# HA07 — contratos de la API de socios y esfuerzo de integración

Experimento de arquitectura del Sprint 3 ([#48](https://github.com/miso-proyecto-final-2026/proyecto-final/issues/48)).
**Hipótesis:** con un contrato publicado y una suite que lo verifica, un socio nuevo se integra solo con la documentación en
≤ 5 persona-día, y el 100 % de los fallos devuelve un código del catálogo.

Las dos partes:

- **Contrato en el CI** (criterios 1, 2, 3 y 5), en `solventa-backend`: Schemathesis valida las respuestas de MS Socios contra
  su OpenAPI y cuenta los fallos provocados; oasdiff hace fallar un cambio incompatible. Diseño en el ADR-0030 del backend.
- **Integrador ajeno** (criterios 4, 6 y 7), aquí: alguien que no construyó la API integra un socio simulado contra staging
  solo con la documentación publicada y deja su tiempo y sus preguntas.

Cómo se ejecuta la sesión: [`RUNBOOK.md`](RUNBOOK.md). El informe con veredicto va en `resultados/INFORME.md` al terminar.

| Carpeta | Contenido |
|---|---|
| `kit/` | Plantillas del kit del integrador: la tarea (`TAREA.md`), las reglas para su sesión de Claude Code (`CLAUDE-integrador.md`, que el kit copia como `CLAUDE.md`, y `settings.json`), la hoja de tiempos y el registro de defectos de documentación. |
| `scripts/` | `preparar-kit.sh` (arma la carpeta aislada del integrador) y `sembrar-socio-ha07.sh` (siembra el socio de prueba en staging sin imprimir el secreto). |
| `resultados/` | Lo que deja el integrador (hoja, defectos, su código y su resumen), el conteo de la suite de contrato y el informe. |

## Desviación declarada: el integrador es un agente aislado

El criterio 4 pide «una persona que no construyó la API». En esta sesión el integrador es **una sesión nueva de Claude Code,
abierta en una carpeta que solo contiene el kit** y sin acceso a los repositorios, operada por un integrante del equipo. Lo
que eso cambia:

- **Mide la suficiencia de la documentación, no el esfuerzo humano.** El agente no tiene el conocimiento previo del equipo,
  pero lee y programa a otra velocidad que una persona: su tiempo no se convierte en persona-día. El informe da el tiempo del
  agente y el tiempo humano de apoyo (las respuestas del operador), y **no** declara cumplido el objetivo de ≤ 5 persona-día
  con esta medición.
- **Las preguntas y tropiezos sí son comparables:** cada vez que el agente no puede avanzar con la documentación, es un defecto
  de la documentación, igual que con una persona (criterio 6).
- **El aislamiento es por configuración** (carpeta propia, reglas en `CLAUDE.md` y permisos que niegan los repositorios y
  GitHub). El repositorio del backend es público: si el agente lo consulta, la corrida se invalida y se declara.
