/**
 * HA02 — Perfilamiento en línea (MS Perfilamiento, POST /v1/perfilamiento/{cliente_id}/enriquecer).
 *
 * Un escenario por corrida (variable ESCENARIO). Cada solicitud lleva las etiquetas `escenario` y `consentimiento`
 * (si | no), y el escenario de k6 se llama igual que ESCENARIO, para que analizar-ha02.py separe la latencia de los
 * clientes con consentimiento de los bloqueos (criterio 5: los bloqueos no distorsionan la medición).
 *
 * El generador corre en la EC2 dentro de la VPC y llama al NLB interno (NodePort 30086 de Perfilamiento) con la clave
 * temporal de carga en `X-Service-Key` (origen `carga` en la auditoría). El borde público queda fuera de la medición.
 *
 * Escenarios (tasa por minuto y duración por defecto; TASA y DURACION los cambian):
 *   humo                 60/min,  5m  grupo `pool`, 5 % sin consentimiento: valida la instrumentación
 *   calentar             cada cliente del grupo `pool` una vez (deja su caché y su respaldo escritos)
 *   miss                336/min, 15m  un cliente nunca enriquecido por solicitud: caché sin acierto (c1)
 *   hit                 336/min, 15m  grupo `pool` ya calentado: caché con acierto (c1)
 *   meseta              360/min, 60m  grupo `pool` + 5 % sin consentimiento: >= 20.000 con consentimiento por hora (c2)
 *   lenta_4a            120/min, 10m  clientes nuevos, Open Finance a 200-500 ms: fuente lenta bajo el timeout (c1)
 *   lenta_4b            120/min, 10m  clientes nuevos, Open Finance a 600-900 ms: alrededor del timeout de 0,7 s (c4)
 *   caida_5a            120/min, 10m  clientes nuevos, Open Finance caída, sin respaldo: valor por defecto, marcado (c4)
 *   caida_5b            120/min, 10m  grupo `pool` sin la entrada principal de Open Finance: respaldo, marcado (c4)
 *   sin_consentimiento   60/min,  5m  solo clientes sin consentimiento: 403 sin llamar a fuentes (c5)
 *
 * 360/min = 21.600/h; con el 5 % sin consentimiento quedan ~20.520 perfilamientos con consentimiento por hora.
 *
 * Variables de entorno:
 *   PERFILAMIENTO_URL      base del servicio, p. ej. http://<nlb_dns_name>:30086
 *   SERVICE_KEY            clave temporal de carga (no se imprime ni se guarda en la evidencia)
 *   ESCENARIO              uno de los de arriba
 *   CLIENTES_FILE          JSON con los cliente_id del grupo del escenario (sembrar-clientes.py)
 *   SIN_CONSENTIMIENTO_FILE  JSON con los clientes sin consentimiento (humo, meseta, sin_consentimiento)
 *   TASA, DURACION         sobrescriben la tasa por minuto y la duración
 *   REUSAR_CONEXIONES      "1" reutiliza conexiones; por defecto no (nota técnica del issue)
 */
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = (__ENV.PERFILAMIENTO_URL || 'http://localhost:8002').replace(/\/$/, '');
const ESCENARIO = __ENV.ESCENARIO || 'humo';

// modo: `azar` toma un cliente al azar del grupo; `unico` usa cada cliente una sola vez (caché sin acierto).
const ESCENARIOS = {
  humo: { tasa: 60, duracion: '5m', modo: 'azar', sin: 0.05 },
  calentar: { modo: 'todos', sin: 0 },
  miss: { tasa: 336, duracion: '15m', modo: 'unico', sin: 0 },
  hit: { tasa: 336, duracion: '15m', modo: 'azar', sin: 0 },
  meseta: { tasa: 360, duracion: '60m', modo: 'azar', sin: 0.05 },
  lenta_4a: { tasa: 120, duracion: '10m', modo: 'unico', sin: 0 },
  lenta_4b: { tasa: 120, duracion: '10m', modo: 'unico', sin: 0 },
  caida_5a: { tasa: 120, duracion: '10m', modo: 'unico', sin: 0 },
  caida_5b: { tasa: 120, duracion: '10m', modo: 'azar', sin: 0 },
  sin_consentimiento: { tasa: 60, duracion: '5m', modo: 'azar', sin: 1 },
};
const CONF = ESCENARIOS[ESCENARIO];
if (!CONF) {
  throw new Error(`ESCENARIO desconocido: ${ESCENARIO} (use ${Object.keys(ESCENARIOS).join(', ')})`);
}
if (!__ENV.SERVICE_KEY) {
  throw new Error('Falta SERVICE_KEY (la clave temporal de carga)');
}

function leer(nombre, variable, obligatorio) {
  return new SharedArray(nombre, function () {
    if (!__ENV[variable]) {
      if (obligatorio) throw new Error(`Falta ${variable}`);
      return [];
    }
    return JSON.parse(open(__ENV[variable]));
  });
}

const CLIENTES = leer('clientes', 'CLIENTES_FILE', CONF.sin < 1);
const SIN_CONSENTIMIENTO = leer('sin_consentimiento', 'SIN_CONSENTIMIENTO_FILE', CONF.sin > 0);

export const perfilFallido = new Rate('perfil_fallido');
// Saturación (c2): 429, 5xx, timeouts y errores de conexión (status 0).
export const saturacion = new Counter('errores_saturacion');
export const hitCache = new Rate('perfil_hit_cache');
export const degradado = new Rate('perfil_degradado');
// Marcado (c4): `degradado` es verdadero tanto si la fuente se sirvió del respaldo como si se usó el valor por defecto
// (proveedores/servicio.py). Los `ausentes` no cuentan: también aparecen en respuestas normales (p. ej. no_reportado).
export const marcado = new Rate('perfil_marcado');
export const conAusentes = new Rate('perfil_con_ausentes');
export const clientesAgotados = new Counter('clientes_agotados');

const escenarioK6 =
  CONF.modo === 'todos'
    ? { executor: 'shared-iterations', vus: 10, iterations: CLIENTES.length, maxDuration: '30m' }
    : {
        executor: 'constant-arrival-rate',
        rate: parseInt(__ENV.TASA || String(CONF.tasa), 10),
        timeUnit: '1m',
        duration: __ENV.DURACION || CONF.duracion,
        preAllocatedVUs: 50,
        maxVUs: 500,
      };

export const options = {
  noConnectionReuse: __ENV.REUSAR_CONEXIONES !== '1',
  scenarios: { [ESCENARIO]: escenarioK6 },
  // Un threshold incumplido es un resultado, no un error (lección de HA01): el veredicto lo da analizar-ha02.py.
  thresholds: {
    perfil_fallido: ['rate==0'],
    errores_saturacion: ['count==0'],
    'http_req_duration{consentimiento:si}': ['p(95)<400', 'p(99)<800'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function elegirCliente() {
  if (CONF.sin > 0 && Math.random() < CONF.sin) {
    return { id: SIN_CONSENTIMIENTO[Math.floor(Math.random() * SIN_CONSENTIMIENTO.length)], consiente: false };
  }
  if (CONF.modo === 'azar') {
    return { id: CLIENTES[Math.floor(Math.random() * CLIENTES.length)], consiente: true };
  }
  // `unico` y `todos`: el número de iteración global da un cliente distinto por solicitud.
  const i = exec.scenario.iterationInTest;
  return i < CLIENTES.length ? { id: CLIENTES[i], consiente: true } : null;
}

export default function () {
  const cliente = elegirCliente();
  if (cliente === null) {
    clientesAgotados.add(1);
    return;
  }
  const tags = { experimento: 'HA02', escenario: ESCENARIO, consentimiento: cliente.consiente ? 'si' : 'no' };

  const res = http.post(`${BASE_URL}/v1/perfilamiento/${encodeURIComponent(cliente.id)}/enriquecer`, null, {
    headers: { 'X-Service-Key': __ENV.SERVICE_KEY },
    timeout: '10s',
    tags,
  });

  if (res.status === 0 || res.status === 429 || res.status >= 500) {
    saturacion.add(1, tags);
  }

  if (!cliente.consiente) {
    // c5: sin consentimiento vigente se bloquea con 403 (ADR-0009, punto 5).
    perfilFallido.add(!check(res, { 'sin consentimiento: 403': (r) => r.status === 403 }), tags);
    return;
  }

  let body = null;
  try {
    body = JSON.parse(res.body);
  } catch (_) {
    body = null;
  }
  const ok = check(res, {
    'status 201': (r) => r.status === 201,
    'tiene perfil_id': () => body !== null && typeof body.perfil_id === 'string',
  });
  perfilFallido.add(!ok, tags);
  if (ok) {
    const ausentes = body.ausentes ? Object.keys(body.ausentes).length : 0;
    hitCache.add(body.hit_cache === true, tags);
    degradado.add(body.degradado === true, tags);
    marcado.add(body.degradado === true, tags);
    conAusentes.add(ausentes > 0, tags);
  }
}
