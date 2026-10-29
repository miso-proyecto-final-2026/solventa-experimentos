/**
 * HA04 — Tráfico constante de cotizaciones durante el simulacro de caída de zona (solventa-backend).
 *
 * Sustituye a `solventa-infra/scripts/drill-trafico.sh` para la sesión: aquel usa `kubectl port-forward`, que se ata a
 * un solo pod y se corta cuando ese pod se mueve, justo lo que provoca el simulacro. Este corre en la EC2 dentro de la
 * VPC contra el NLB interno y conserva el método de RPO del script original: cada solicitud usa un cliente_id único
 * `<RUN_ID>-<n>`, y las confirmadas (201) se contrastan con las filas de `cotizacion.cotizaciones` (criterio 3).
 *
 * Con cliente_id no sembrados, Cotización responde `aproximada` (sin consentimiento): no importa aquí, porque HA04 mide
 * disponibilidad y pérdida de datos, no el precio.
 *
 * Variables de entorno:
 *   COTIZACION_URL  base del servicio, p. ej. http://<nlb_dns_name>:30080
 *   RUN_ID          prefijo de los cliente_id (alfanumérico; lo fija correr-escenario.sh)
 *   TASA            solicitudes por segundo (por defecto 10)
 *   DURACION        duración total (por defecto 90m; cubre los tres simulacros y el despliegue bajo carga)
 */
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Rate } from 'k6/metrics';

const BASE_URL = (__ENV.COTIZACION_URL || 'http://localhost:8000').replace(/\/$/, '');
const RUN_ID = __ENV.RUN_ID;
if (!RUN_ID || !/^[A-Za-z0-9-]{1,40}$/.test(RUN_ID)) {
  throw new Error('RUN_ID obligatorio: letras, dígitos o guiones (máx. 40)');
}

export const disponibilidad = new Rate('cotizacion_ok');

export const options = {
  scenarios: {
    trafico: {
      executor: 'constant-arrival-rate',
      rate: parseInt(__ENV.TASA || '10', 10),
      timeUnit: '1s',
      duration: __ENV.DURACION || '90m',
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const clienteId = `${RUN_ID}-${exec.scenario.iterationInTest}`;
  const res = http.post(
    `${BASE_URL}/v1/cotizaciones`,
    JSON.stringify({
      cliente_id: clienteId,
      producto: 'microseguro_vida',
      datos: { edad: 35, suma_asegurada: 50000000 },
    }),
    // cliente_id como etiqueta: analizar-ha04.py lista las confirmadas para el conteo de RPO.
    { headers: { 'Content-Type': 'application/json' }, timeout: '5s', tags: { experimento: 'HA04', cliente_id: clienteId } },
  );
  disponibilidad.add(check(res, { 'status 201': (r) => r.status === 201 }));
}
