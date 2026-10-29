/**
 * HA03 — Autoescalado de cotización de 500 a 50.000 solicitudes/min (solventa-backend, POST /v1/cotizaciones).
 *
 * Mide si el HPA de MS Cotización escala a tiempo (criterio 1: <= 60 s entre la detección y los pods listos), sin
 * degradar la latencia (criterio 2: metas de RQ-L1 desde el cliente) y sin errores por saturación (criterio 3).
 * El generador corre en una EC2 dentro de la VPC y llama al NLB interno (NodePort 30080): el borde público
 * (CloudFront, WAF y API Gateway) queda fuera de la medición, y así se declara en el informe.
 *
 * Perfil `completo` (29 min, propuesta a confirmar en el runbook):
 *   5 min a 500/min (línea base, HPA en el mínimo) -> subida a 50.000/min en 5 min -> 10 min de meseta
 *   -> bajada a 500/min en 1 min -> 8 min a 500/min para observar la reducción (criterio 6; el HPA espera 300 s).
 * Perfil `humo` (5 min): la misma forma comprimida, hasta 5.000/min, para validar el encadenamiento.
 * Perfil `kind` (12,5 min): prueba local de la cadena métrica -> HPA, de 120 a 1.800/min, con 7 min de reposo para
 *   ver la bajada (300 s de estabilización + 1 pod/min). Con el simulador a 200-500 ms y cliente_id casi únicos (fallos
 *   de caché), 1.800/min dan ~10 solicitudes en curso: por encima del objetivo de 3 por pod con 2 pods.
 *
 * Cada muestra lleva la etiqueta `fase` (base, subida, meseta, bajada, reposo) derivada del reloj global del
 * escenario, para que analizar-ha03.py calcule percentiles por fase.
 *
 * Variables de entorno:
 *   COTIZACION_URL  base del servicio (sin /v1/cotizaciones), p. ej. http://<nlb_dns_name>:30080
 *   PERFIL          completo (por defecto) | humo | kind
 *   CLIENTES_FILE   JSON con los cliente_id sembrados en Identidad (sembrar-clientes.py). Obligatorio en staging:
 *                   sin consentimiento, Cotización responde `aproximada` y la corrida no es válida.
 *   REUSAR_CONEXIONES  "1" reutiliza conexiones. Por defecto no (como HA01: el NLB corta a los 350 s de inactividad).
 *   MAX_VUS         tope de VUs (por defecto 3000)
 */
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = (__ENV.COTIZACION_URL || 'http://localhost:8000').replace(/\/$/, '');
const PERFIL = __ENV.PERFIL || 'completo';
const MAX_VUS = parseInt(__ENV.MAX_VUS || '3000', 10);
const CLIENTES = new SharedArray('clientes', function () {
  if (!__ENV.CLIENTES_FILE) {
    throw new Error('Falta CLIENTES_FILE: siembra los clientes con sembrar-clientes.py');
  }
  return JSON.parse(open(__ENV.CLIENTES_FILE));
});

// Duraciones en segundos; `fases` debe sumar lo mismo que `stages`.
const PERFILES = {
  completo: {
    pico: 50000,
    stages: [
      { target: 500, duration: '5m' },
      { target: 50000, duration: '5m' },
      { target: 50000, duration: '10m' },
      { target: 500, duration: '1m' },
      { target: 500, duration: '8m' },
    ],
    fases: [
      ['base', 300],
      ['subida', 300],
      ['meseta', 600],
      ['bajada', 60],
      ['reposo', 480],
    ],
  },
  kind: {
    pico: 1800,
    stages: [
      { target: 120, duration: '1m' },
      { target: 1800, duration: '1m' },
      { target: 1800, duration: '3m' },
      { target: 120, duration: '30s' },
      { target: 120, duration: '7m' },
    ],
    fases: [
      ['base', 60],
      ['subida', 60],
      ['meseta', 180],
      ['bajada', 30],
      ['reposo', 420],
    ],
  },
  humo: {
    pico: 5000,
    stages: [
      { target: 500, duration: '1m' },
      { target: 5000, duration: '1m' },
      { target: 5000, duration: '2m' },
      { target: 500, duration: '30s' },
      { target: 500, duration: '30s' },
    ],
    fases: [
      ['base', 60],
      ['subida', 60],
      ['meseta', 120],
      ['bajada', 30],
      ['reposo', 30],
    ],
  },
};
if (!PERFILES[PERFIL]) {
  throw new Error(`PERFIL desconocido: ${PERFIL} (use completo, humo o kind)`);
}

function faseActual(transcurridoS) {
  let limite = 0;
  for (const [nombre, duracion] of PERFILES[PERFIL].fases) {
    limite += duracion;
    if (transcurridoS < limite) return nombre;
  }
  return 'reposo';
}

export const cotizacionFailed = new Rate('cotizacion_failed');
// Saturación (criterio 3): 429, 5xx, timeouts y errores de conexión (status 0).
export const saturacion = new Counter('errores_saturacion');
export const cotizacionAproximada = new Rate('cotizacion_aproximada');
export const cacheHitRate = new Rate('cache_hit_rate');
export const latenciaInterna = new Trend('latencia_interna_ms', true);

export const options = {
  noConnectionReuse: __ENV.REUSAR_CONEXIONES !== '1',
  scenarios: {
    cotizacion: {
      executor: 'ramping-arrival-rate',
      startRate: PERFILES[PERFIL].stages[0].target,
      timeUnit: '1m',
      preAllocatedVUs: 300,
      maxVUs: MAX_VUS,
      stages: PERFILES[PERFIL].stages,
    },
  },
  thresholds: {
    cotizacion_failed: ['rate==0'],
    errores_saturacion: ['count==0'],
    'http_req_duration{fase:meseta}': ['p(95)<250', 'p(99)<500'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function solicitud() {
  return {
    cliente_id: CLIENTES[Math.floor(Math.random() * CLIENTES.length)],
    producto: 'microseguro_vida',
    datos: {
      edad: 18 + Math.floor(Math.random() * 48), // 18..65
      suma_asegurada: 5000000 + Math.floor(Math.random() * 96) * 1000000, // 5M..100M
    },
  };
}

export default function () {
  // La fase se deriva del reloj global del escenario, no de un reloj por VU (lección de HA02).
  const transcurridoS = (Date.now() - exec.scenario.startTime) / 1000;
  const tags = { experimento: 'HA03', fase: faseActual(transcurridoS) };

  const res = http.post(`${BASE_URL}/v1/cotizaciones`, JSON.stringify(solicitud()), {
    headers: { 'Content-Type': 'application/json' },
    timeout: '10s',
    tags,
  });

  if (res.status === 0 || res.status === 429 || res.status >= 500) {
    saturacion.add(1, tags);
  }

  let body = null;
  try {
    body = JSON.parse(res.body);
  } catch (_) {
    body = null;
  }

  const ok = check(res, {
    'status 201': (r) => r.status === 201,
    'tiene prima_mensual': () => body !== null && body.prima_mensual > 0,
  });
  cotizacionFailed.add(!ok, tags);

  if (ok) {
    cotizacionAproximada.add(body.aproximada === true, tags);
    if (body.metadata) {
      cacheHitRate.add(body.metadata.hit_cache === true, tags);
      if (body.metadata.latencia_interna_ms !== null) {
        latenciaInterna.add(body.metadata.latencia_interna_ms, tags);
      }
    }
  }
}
