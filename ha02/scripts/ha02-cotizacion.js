/**
 * HA02 criterio 3 — la cotización en línea no se degrada mientras corre el perfilamiento masivo.
 *
 * Carga constante a MS Cotización (POST /v1/cotizaciones, NodePort 30080 del NLB interno) durante 70 min. Los primeros
 * BASE_S segundos (10 min) son la línea base, con la cotización sola; después arranca la meseta de perfilamiento
 * (correr-ha02.sh la lanza a los BASE_S segundos) y las muestras pasan a la fase `meseta`. La fase se deriva del reloj
 * global del escenario, no de un reloj por VU (lección de HA02 en el prototipo).
 *
 * Usa un grupo de clientes propio (`cotizacion.json`), disjunto de los de Perfilamiento: los dos servicios comparten la
 * caché de proveedores en Redis (solventa-backend#84) y un cliente común falsearía los aciertos de caché de ambos.
 *
 * Variables de entorno:
 *   COTIZACION_URL   base del servicio, p. ej. http://<nlb_dns_name>:30080
 *   CLIENTES_FILE    JSON con los cliente_id del grupo de cotización (con consentimiento)
 *   TASA             cotizaciones por minuto (por defecto 500, la línea base de HA03)
 *   DURACION         por defecto 70m
 *   BASE_S           segundos de línea base (por defecto 600)
 *   REUSAR_CONEXIONES  "1" reutiliza conexiones; por defecto no
 */
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = (__ENV.COTIZACION_URL || 'http://localhost:8000').replace(/\/$/, '');
const BASE_S = parseInt(__ENV.BASE_S || '600', 10);
const CLIENTES = new SharedArray('clientes', function () {
  if (!__ENV.CLIENTES_FILE) {
    throw new Error('Falta CLIENTES_FILE: siembra el grupo de cotización con sembrar-clientes.py');
  }
  return JSON.parse(open(__ENV.CLIENTES_FILE));
});

export const cotizacionFailed = new Rate('cotizacion_failed');
export const saturacion = new Counter('errores_saturacion');
// Validez de la corrida: una cotización `aproximada` no consultó el perfil real (cliente sin consentimiento).
export const cotizacionAproximada = new Rate('cotizacion_aproximada');
export const cacheHitRate = new Rate('cache_hit_rate');

export const options = {
  noConnectionReuse: __ENV.REUSAR_CONEXIONES !== '1',
  scenarios: {
    cotizacion: {
      executor: 'constant-arrival-rate',
      rate: parseInt(__ENV.TASA || '500', 10),
      timeUnit: '1m',
      duration: __ENV.DURACION || '70m',
      preAllocatedVUs: 50,
      maxVUs: 500,
    },
  },
  // Meta de la cotización en línea (HA01 / RQ-L1). Un threshold incumplido es un resultado, no un error.
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
  const transcurridoS = (Date.now() - exec.scenario.startTime) / 1000;
  const tags = { experimento: 'HA02', fase: transcurridoS < BASE_S ? 'base' : 'meseta' };

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
    }
  }
}
