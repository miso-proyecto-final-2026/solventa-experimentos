"""HA02 criterio 6 — tras revocar, el perfil deja de usarse en <= 5 minutos.

Por cada uno de N clientes sintéticos:
  1. lo crea en MS Identidad y le otorga el consentimiento OPEN_FINANCE_OPEN_DATA (como sembrar-clientes.py);
  2. lo enriquece en MS Perfilamiento (201) y lee su perfil vigente (200): su caché y su respaldo quedan escritos;
  3. revoca el consentimiento con el token **del propio cliente** (Identidad no deja revocar con la clave de servicio);
  4. desde ese instante consulta `GET /perfil` y `POST /enriquecer` cada INTERVALO segundos hasta que los dos respondan
     403 o se cumpla el LIMITE; el tiempo hasta el bloqueo es la medida.

Según el código (ADR-0009, punto 6) el consentimiento se verifica en cada operación, sin caché, así que se espera un
bloqueo en la primera consulta. La resolución de la medida es el INTERVALO. El perfil cacheado en Redis **no se borra**
al revocar (eso es de V-11): `cluster/claves-redis.sh` comprueba después que las claves siguen ahí, sin servirse.

Datos 100 % sintéticos (documentos `H2RV<run><i>`, correos `.test`). Las contraseñas y los tokens solo viven en
memoria: la salida tiene los cliente_id, las horas y los estados HTTP, nada más. La clave de carga se lee de un archivo
(`--clave-archivo`) para que no quede en el historial de la terminal.

Uso (en la EC2 del generador):
    python3 revocacion.py --identidad http://<nlb>:30081 --perfilamiento http://<nlb>:30086 \\
        --clave-archivo clave-carga --n 50 --salida revocacion.json

Solo biblioteca estándar.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

TIPO = "OPEN_FINANCE_OPEN_DATA"
TIMEOUT_S = 10


def http(metodo: str, url: str, cuerpo: dict | None = None, cabeceras: dict | None = None) -> tuple[int, dict]:
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(url, data=datos, method=metodo)
    req.add_header("Content-Type", "application/json")
    for k, v in (cabeceras or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return 0, {"detail": type(exc).__name__}


def ahora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def preparar(identidad: str, perfilamiento: str, clave: str, run: str, i: int) -> tuple[str, str]:
    """Crea el cliente con consentimiento y deja su perfil enriquecido. Devuelve (cliente_id, token)."""
    sufijo = f"{run}{i:03d}"
    estado, cuerpo = http(
        "POST",
        f"{identidad}/v1/clientes",
        {
            "nombre": "Sintetico",
            "apellido": f"HA02 {sufijo}",
            "tipo_documento": "CC",
            "numero_documento": f"H2RV{sufijo}",
            "email": f"ha02-rev-{sufijo}@solventa-carga.test",
            "celular": f"3{int(sufijo, 36) % 10**9:09d}",
            "password": f"{secrets.token_urlsafe(12)}a1",
            "acepto_terminos": True,
        },
        {"Idempotency-Key": f"ha02-rev-{sufijo}"},
    )
    if estado != 201:
        raise RuntimeError(f"cliente {i}: POST /v1/clientes -> {estado}")
    cliente_id, token = cuerpo["id"], cuerpo["access_token"]
    bearer = {"Authorization": f"Bearer {token}"}

    estado, cuerpo = http(
        "POST",
        f"{identidad}/v1/clientes/{cliente_id}/consentimientos",
        {"tipo": TIPO, "version_texto": "v1", "canal": "MOVIL"},
        bearer,
    )
    if estado not in (200, 201) or cuerpo.get("estado") != "OTORGADO":
        raise RuntimeError(f"cliente {i}: consentimiento -> {estado}")

    clave_h = {"X-Service-Key": clave}
    estado, _ = http("POST", f"{perfilamiento}/v1/perfilamiento/{cliente_id}/enriquecer", None, clave_h)
    if estado != 201:
        raise RuntimeError(f"cliente {i}: enriquecer antes de revocar -> {estado}")
    estado, _ = http("GET", f"{perfilamiento}/v1/perfilamiento/{cliente_id}/perfil", None, clave_h)
    if estado != 200:
        raise RuntimeError(f"cliente {i}: perfil antes de revocar -> {estado}")
    return cliente_id, token


def revocar_y_medir(
    identidad: str, perfilamiento: str, clave: str, cliente_id: str, token: str, intervalo: float, limite: float
) -> dict:
    estado, cuerpo = http(
        "DELETE",
        f"{identidad}/v1/clientes/{cliente_id}/consentimientos/{TIPO}",
        None,
        {"Authorization": f"Bearer {token}"},
    )
    revocado_utc = ahora_utc()
    t0 = time.monotonic()
    resultado: dict = {
        "cliente_id": cliente_id,
        "revocado_utc": revocado_utc,
        "revocacion_http": estado,
        "revocacion_estado": cuerpo.get("estado"),
        "segundos_hasta_bloqueo": None,
        "consultas": 0,
        "ultimo_perfil_http": None,
        "ultimo_enriquecer_http": None,
    }
    if estado != 200:
        return resultado
    clave_h = {"X-Service-Key": clave}
    while True:
        perfil, _ = http("GET", f"{perfilamiento}/v1/perfilamiento/{cliente_id}/perfil", None, clave_h)
        enriquecer, _ = http("POST", f"{perfilamiento}/v1/perfilamiento/{cliente_id}/enriquecer", None, clave_h)
        resultado["consultas"] += 1
        resultado["ultimo_perfil_http"], resultado["ultimo_enriquecer_http"] = perfil, enriquecer
        transcurrido = time.monotonic() - t0
        if perfil == 403 and enriquecer == 403:
            resultado["segundos_hasta_bloqueo"] = round(transcurrido, 3)
            return resultado
        if transcurrido >= limite:
            return resultado
        time.sleep(intervalo)


def resumir(clientes: list[dict], limite: float) -> dict:
    tiempos = sorted(c["segundos_hasta_bloqueo"] for c in clientes if c["segundos_hasta_bloqueo"] is not None)
    return {
        "clientes": len(clientes),
        "bloqueados": len(tiempos),
        "no_bloqueados": len(clientes) - len(tiempos),
        "max_s": tiempos[-1] if tiempos else None,
        "mediana_s": tiempos[len(tiempos) // 2] if tiempos else None,
        "limite_s": limite,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--identidad", required=True, help="base de MS Identidad, p. ej. http://<nlb>:30081")
    ap.add_argument("--perfilamiento", required=True, help="base de MS Perfilamiento, p. ej. http://<nlb>:30086")
    ap.add_argument("--clave-archivo", required=True, help="archivo con la clave temporal de carga")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre consultas tras revocar")
    ap.add_argument("--limite", type=float, default=300.0, help="segundos máximos de espera (meta: 300)")
    ap.add_argument("--hilos", type=int, default=10)
    ap.add_argument("--salida", default="revocacion.json")
    args = ap.parse_args(argv)

    identidad = args.identidad.rstrip("/")
    perfilamiento = args.perfilamiento.rstrip("/")
    with open(args.clave_archivo, encoding="utf-8") as fh:
        clave = fh.read().strip()
    if not clave:
        ap.error("el archivo de la clave está vacío")
    run = secrets.token_hex(2).upper()

    inicio_utc = ahora_utc()
    preparados: list[tuple[str, str]] = []
    errores: list[str] = []
    with ThreadPoolExecutor(max_workers=args.hilos) as pool:
        futuros = [pool.submit(preparar, identidad, perfilamiento, clave, run, i) for i in range(args.n)]
        for f in futuros:
            try:
                preparados.append(f.result())
            except Exception as exc:  # se reporta y se sigue con los que sí quedaron listos
                errores.append(str(exc))
    with ThreadPoolExecutor(max_workers=args.hilos) as pool:
        clientes = list(
            pool.map(
                lambda par: revocar_y_medir(
                    identidad, perfilamiento, clave, par[0], par[1], args.intervalo, args.limite
                ),
                preparados,
            )
        )

    informe = {
        "run": run,
        "inicio_utc": inicio_utc,
        "fin_utc": ahora_utc(),
        "intervalo_s": args.intervalo,
        "errores_preparacion": errores,
        "resumen": resumir(clientes, args.limite),
        "clientes": clientes,
    }
    with open(args.salida, "w", encoding="utf-8") as fh:
        json.dump(informe, fh, indent=2)
    with open(args.salida.replace(".json", "-ids.json"), "w", encoding="utf-8") as fh:
        json.dump([c["cliente_id"] for c in clientes], fh)
    r = informe["resumen"]
    print(
        f"Revocación {run}: {r['bloqueados']}/{r['clientes']} bloqueados, máximo {r['max_s']} s "
        f"(intervalo {args.intervalo} s), {len(errores)} errores de preparación -> {args.salida}"
    )
    return 0 if not errores and r["no_bloqueados"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
