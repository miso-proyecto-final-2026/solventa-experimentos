/**
 * HA01 ronda 2 — Latencia del motor de cotización (solventa-backend, POST /v1/cotizaciones).
 *
 * Mismo perfil de carga que la ronda 1 (solventa-experimentos/k6/exp1-cotizacion.js):
 *   rampa 500 -> 5.000 sol/min en 15 min + meseta de 5.000 sol/min durante 10 min,
 *   1.000 client_id sintéticos, sin reutilización de conexiones (idle timeout de 350 s del NLB).
 *
 * El escenario (A normal / B degradado) se fija FUERA del script, cambiando la latencia
 * del simulador de Open Finance del servicio (OPEN_FINANCE_SIM_LATENCIA_MIN_MS/MAX_MS);
 * aquí solo se etiqueta con SCENARIO para separar las muestras en el análisis.
 *
 * Variables de entorno:
 *   COTIZACION_URL  base del servicio (sin /v1/cotizaciones). Por defecto http://localhost:8000
 *   SCENARIO        A | B (etiqueta scenario_tag)
 *   PERFIL          completo (25 min, por defecto) | humo (5 min, misma forma comprimida)
 *   CACHE_WARMUP_S  segundos desde el inicio tras los cuales cuenta el hit de caché
 *                   (por defecto 180 en completo, 60 en humo)
 *   N_CLIENTES      número de client_id distintos (por defecto 1000, como la ronda 1)
 *   CLIENTES_FILE   JSON con la lista de cliente_id sembrados en Identidad (sembrar-clientes.py).
 *                   Obligatorio en staging: sin consentimiento, Cotización no llama a Open
 *                   Finance y devuelve la cotización como `aproximada`. Sin él se usan ids
 *                   sintéticos (solo válido en local con el stub de consentimiento).
 *
 * Metas: p95 <= 250 ms / p99 <= 500 ms (A), p95 <= 300 ms (B), 0 % de fallos,
 * hit de caché >= 60 % desde el minuto 3 (A), 100 % degradadas (B).
 * El veredicto lo da analizar.py sobre la salida cruda; los thresholds solo sirven de aviso.
 */
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = (__ENV.COTIZACION_URL || 'http://localhost:8000').replace(/\/$/, '');
const SCENARIO = __ENV.SCENARIO || 'A';
const PERFIL = __ENV.PERFIL || 'completo';
const N_CLIENTES = parseInt(__ENV.N_CLIENTES || '1000', 10);
const CLIENTES = new SharedArray('clientes', function () {
  if (!__ENV.CLIENTES_FILE) {
    return Array.from({ length: N_CLIENTES }, (_, i) => `client-exp1-${String(i).padStart(5, '0')}`);
  }
  return JSON.parse(open(__ENV.CLIENTES_FILE));
});

const PERFILES = {
  completo: { rampa: '15m', meseta: '10m', warmup: 180 },
  humo: { rampa: '3m', meseta: '2m', warmup: 60 },
};
if (!PERFILES[PERFIL]) {
  throw new Error(`PERFIL desconocido: ${PERFIL} (use completo o humo)`);
}
const CACHE_WARMUP_S = parseInt(__ENV.CACHE_WARMUP_S || String(PERFILES[PERFIL].warmup), 10);

export const cotizacionFailed = new Rate('cotizacion_failed');
export const cotizacionDegradada = new Rate('cotizacion_degradada');
// Cotización sin consentimiento vigente (perfil por defecto): invalida la medición si es > 0.
export const cotizacionAproximada = new Rate('cotizacion_aproximada');
export const cacheHitRate = new Rate('cache_hit_rate');
export const latenciaInterna = new Trend('latencia_interna_ms', true);

export const options = {
  noConnectionReuse: true,
  scenarios: {
    cotizacion: {
      executor: 'ramping-arrival-rate',
      startRate: 500,
      timeUnit: '1m',
      preAllocatedVUs: 300,
      maxVUs: 1500,
      stages: [
        { target: 5000, duration: PERFILES[PERFIL].rampa },
        { target: 5000, duration: PERFILES[PERFIL].meseta },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.001'],
    cotizacion_failed: ['rate==0'],
    'http_req_duration{scenario_tag:A}': ['p(95)<250', 'p(99)<500'],
    'http_req_duration{scenario_tag:B}': ['p(95)<300'],
  },
  // p99 también en el resumen; el análisis igual lo recalcula desde el crudo.
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
  // La fase se deriva del reloj global del escenario, no de un reloj por VU
  // (defecto 2 de la ronda 1 de HA02: los VU creados a mitad de corrida etiquetaban mal).
  const transcurridoS = (Date.now() - exec.scenario.startTime) / 1000;
  const fase = transcurridoS < CACHE_WARMUP_S ? 'calentamiento' : 'regimen';
  const tags = { scenario_tag: SCENARIO, experimento: 'HA01', fase };

  const res = http.post(`${BASE_URL}/v1/cotizaciones`, JSON.stringify(solicitud()), {
    headers: { 'Content-Type': 'application/json' },
    tags,
  });

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
    cotizacionDegradada.add(body.degradado === true, tags);
    cotizacionAproximada.add(body.aproximada === true, tags);
    if (body.metadata) {
      cacheHitRate.add(body.metadata.hit_cache === true, tags);
      if (body.metadata.latencia_interna_ms !== null) {
        latenciaInterna.add(body.metadata.latencia_interna_ms, tags);
      }
    }
  }
}
