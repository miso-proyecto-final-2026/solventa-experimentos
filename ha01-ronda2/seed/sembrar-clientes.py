"""Siembra clientes sintéticos con consentimiento de Open Finance en MS Identidad (HA01 ronda 2).

En staging, Cotización consulta a Identidad el consentimiento OPEN_FINANCE_OPEN_DATA en cada
solicitud. Si el cliente no existe o no consintió, no llama a Open Finance y la cotización sale
`aproximada`, lo que invalida la medición. Este script crea N clientes, otorga el consentimiento
a cada uno y escribe la lista de cliente_id que consume el k6 (CLIENTES_FILE).

Datos 100 % sintéticos: documentos `H1R2<run><i>`, correos en el dominio reservado `.test`
(RFC 2606) y contraseñas aleatorias de un solo uso que no se guardan. El access_token de cada
cliente solo vive en memoria durante la siembra.

Uso (desde la EC2 del generador, contra el NodePort de Identidad del NLB interno):
    python3 sembrar-clientes.py --url http://<nlb_dns_name>:30081 --n 1000 --salida clientes.json

Solo biblioteca estándar. Rerun seguro: cada corrida usa un identificador propio (--run), así
que no choca con documentos ni correos de corridas anteriores.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

TIPO = "OPEN_FINANCE_OPEN_DATA"
VERSION_TEXTO = "v1"
TIMEOUT_S = 10
REINTENTOS = 3


def _http(metodo: str, url: str, cuerpo: dict | None = None, cabeceras: dict | None = None):
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(url, data=datos, method=metodo)
    req.add_header("Content-Type", "application/json")
    for k, v in (cabeceras or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _con_reintentos(fn, *args):
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            estado, cuerpo = fn(*args)
            if estado < 500:
                return estado, cuerpo
            ultimo = (estado, cuerpo)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            ultimo = (0, {"detail": type(exc).__name__})
        time.sleep(0.5 * (intento + 1))
    return ultimo


def sembrar_uno(base: str, run: str, i: int) -> str:
    sufijo = f"{run}{i:05d}"
    cliente = {
        "nombre": "Sintetico",
        "apellido": f"HA01 {sufijo}",
        "tipo_documento": "CC",
        "numero_documento": f"H1R2{sufijo}",
        "email": f"ha01-{sufijo}@solventa-carga.test",
        "celular": f"3{int(sufijo, 36) % 10**9:09d}",
        "password": f"{secrets.token_urlsafe(12)}a1",
        "acepto_terminos": True,
    }
    estado, cuerpo = _con_reintentos(
        _http, "POST", f"{base}/v1/clientes", cliente, {"Idempotency-Key": f"ha01-{sufijo}"}
    )
    if estado != 201:
        raise RuntimeError(f"cliente {i}: POST /v1/clientes -> {estado} {cuerpo.get('detail')}")
    cliente_id, token = cuerpo["id"], cuerpo["access_token"]

    estado, cuerpo = _con_reintentos(
        _http,
        "POST",
        f"{base}/v1/clientes/{cliente_id}/consentimientos",
        {"tipo": TIPO, "version_texto": VERSION_TEXTO, "canal": "MOVIL"},
        {"Authorization": f"Bearer {token}"},
    )
    if estado not in (200, 201) or cuerpo.get("estado") != "OTORGADO":
        raise RuntimeError(f"cliente {i}: consentimiento -> {estado} {cuerpo}")
    return cliente_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="base de MS Identidad, p. ej. http://<nlb>:30081")
    ap.add_argument("--n", type=int, default=1000, help="clientes a crear (1000, como la ronda 1)")
    ap.add_argument("--salida", default="clientes.json")
    ap.add_argument("--hilos", type=int, default=16)
    ap.add_argument("--run", default=None, help="identificador alfanumérico de la siembra (4 car.)")
    args = ap.parse_args(argv)

    base = args.url.rstrip("/")
    run = (args.run or secrets.token_hex(2)).upper()
    if not run.isalnum() or len(run) > 4:
        ap.error("--run debe ser alfanumérico de hasta 4 caracteres")

    estado, _ = _http("GET", f"{base}/health")
    if estado != 200:
        print(f"Identidad no responde en {base}/health ({estado})", file=sys.stderr)
        return 1

    inicio = time.monotonic()
    ids: list[str] = []
    errores: list[str] = []
    with ThreadPoolExecutor(max_workers=args.hilos) as pool:
        futuros = [pool.submit(sembrar_uno, base, run, i) for i in range(args.n)]
        for n, f in enumerate(as_completed(futuros), 1):
            try:
                ids.append(f.result())
            except Exception as exc:  # se reporta y se sigue: el resumen dice cuántos fallaron
                errores.append(str(exc))
            if n % 100 == 0:
                print(f"  {n}/{args.n}", flush=True)

    with open(args.salida, "w", encoding="utf-8") as fh:
        json.dump(sorted(ids), fh)
    print(
        f"Siembra {run}: {len(ids)} clientes con consentimiento, {len(errores)} errores, "
        f"{time.monotonic() - inicio:.0f} s -> {args.salida}"
    )
    for e in errores[:10]:
        print(f"  error: {e}", file=sys.stderr)
    return 0 if not errores else 1


if __name__ == "__main__":
    sys.exit(main())
