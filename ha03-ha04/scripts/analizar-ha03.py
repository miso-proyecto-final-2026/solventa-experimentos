"""Análisis de HA03 sobre la salida cruda de k6 (--out json=...) y el muestreo del clúster.

De k6 calcula, por fase (etiqueta `fase`: base, subida, meseta, bajada, reposo), los percentiles de la latencia vista
por el cliente (http_req_duration) y de la interna (latencia_interna_ms), la tasa de cotizaciones fallidas, los errores
por saturación (429, 5xx, timeouts y errores de conexión) y la tasa de `aproximadas` (validez de la corrida).

Del muestreo de `muestrear-cluster.sh` calcula cada evento de escalado del HPA:
  - detección: primera muestra en que `hpa_deseado` sube respecto de la muestra anterior;
  - pods listos: primera muestra posterior en que `pods_listos` alcanza ese `hpa_deseado`;
  - scaling_time_seconds = pods listos - detección (criterio 1, meta <= 60 s);
  - qué métrica dominaba al detectar (criterio 4): la de mayor razón valor/objetivo entre las solicitudes en curso por
    pod y la CPU. El HPA pide réplicas según la métrica con la razón más alta, así que la que domina es la que dispara.
La precisión es la del intervalo de muestreo (se reporta). Si el HPA no llega a pedir más réplicas, no hay eventos y el
informe lo dice.

Uso:
    python -I analizar-ha03.py escenario.json[.gz] --muestreo muestreo.csv [--salida informe.json]

Solo usa la biblioteca estándar y lee el crudo línea a línea. Percentiles con interpolación lineal (como HA01).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FASES = ("base", "subida", "meseta", "bajada", "reposo")
METAS = {"p95_ms": 250.0, "p99_ms": 500.0, "scaling_time_s": 60.0}


def percentil(ordenados: list[float], p: float) -> float | None:
    if not ordenados:
        return None
    k = (len(ordenados) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordenados[lo]
    return ordenados[lo] + (ordenados[hi] - ordenados[lo]) * (k - lo)


def abrir(ruta: Path):
    return gzip.open(ruta, "rt", encoding="utf-8") if ruta.suffix == ".gz" else ruta.open(encoding="utf-8")


def leer_k6(ruta: Path) -> dict:
    duraciones: dict[str, list[float]] = defaultdict(list)
    internas: dict[str, list[float]] = defaultdict(list)
    rates: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0])
    saturacion: dict[str, int] = defaultdict(int)
    with abrir(ruta) as f:
        for linea in f:
            if "Point" not in linea:
                continue
            punto = json.loads(linea)
            if punto.get("type") != "Point":
                continue
            metrica = punto["metric"]
            tags = punto["data"].get("tags") or {}
            fase = tags.get("fase")
            if fase is None:
                continue
            valor = float(punto["data"]["value"])
            if metrica == "http_req_duration":
                duraciones[fase].append(valor)
            elif metrica == "latencia_interna_ms":
                internas[fase].append(valor)
            elif metrica in ("cotizacion_failed", "cotizacion_aproximada", "cache_hit_rate"):
                acc = rates[(metrica, fase)]
                acc[0] += valor
                acc[1] += 1
            elif metrica == "errores_saturacion":
                saturacion[fase] += int(valor)

    por_fase = {}
    for fase in FASES:
        cli = sorted(duraciones.get(fase, []))
        inter = sorted(internas.get(fase, []))

        def tasa(metrica: str) -> float | None:
            suma, n = rates.get((metrica, fase), (0.0, 0))
            return suma / n if n else None

        por_fase[fase] = {
            "solicitudes": len(cli),
            "cliente_ms": {f"p{int(p * 100)}": percentil(cli, p) for p in (0.5, 0.95, 0.99)},
            "interna_ms": {f"p{int(p * 100)}": percentil(inter, p) for p in (0.5, 0.95, 0.99)},
            "tasa_fallidas": tasa("cotizacion_failed"),
            "tasa_aproximadas": tasa("cotizacion_aproximada"),
            "hit_cache": tasa("cache_hit_rate"),
            "errores_saturacion": saturacion.get(fase, 0),
        }
    return por_fase


def _fecha(valor: str) -> datetime:
    return datetime.fromisoformat(valor.replace("Z", "+00:00"))


def _entero(valor: str) -> int | None:
    return int(valor) if valor not in ("", None) else None


def _cantidad(valor: str | None) -> float | None:
    """Cantidad de Kubernetes (p. ej. averageValue: "2500m", "3", "1500u") como número."""
    if valor in ("", None):
        return None
    factores = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}
    if valor[-1] in factores:
        return float(valor[:-1]) * factores[valor[-1]]
    return float(valor)


def _razon(valor: float | None, objetivo: float | None) -> float | None:
    return valor / objetivo if valor is not None and objetivo else None


def metrica_dominante(fila: dict) -> str:
    conc = _razon(fila["concurrencia"], fila["concurrencia_objetivo"])
    cpu = _razon(fila["cpu_pct"], fila["cpu_objetivo"])
    if conc is None and cpu is None:
        return "sin datos"
    if conc is None:
        return "cpu"
    if cpu is None:
        return "concurrencia"
    return "concurrencia" if conc >= cpu else "cpu"


def leer_muestreo(ruta: Path) -> dict:
    with ruta.open(encoding="utf-8", newline="") as f:
        filas = [
            {
                "t": _fecha(r["timestamp_utc"]),
                "deseado": _entero(r["hpa_deseado"]),
                "listos": _entero(r["pods_listos"]),
                "actual": _entero(r["hpa_actual"]),
                "cpu_pct": _cantidad(r.get("hpa_cpu_pct")),
                "cpu_objetivo": _cantidad(r.get("hpa_cpu_objetivo")),
                "concurrencia": _cantidad(r.get("hpa_concurrencia")),
                "concurrencia_objetivo": _cantidad(r.get("hpa_concurrencia_objetivo")),
                "reinicios": _entero(r["reinicios_total"]),
                "zonas": r.get("pods_por_zona", ""),
            }
            for r in csv.DictReader(f)
        ]
    filas = [r for r in filas if r["deseado"] is not None and r["listos"] is not None]
    intervalos = [(b["t"] - a["t"]).total_seconds() for a, b in zip(filas, filas[1:])]

    eventos = []
    for i in range(1, len(filas)):
        previo, fila = filas[i - 1], filas[i]
        if fila["deseado"] <= previo["deseado"]:
            continue
        listo = next((r for r in filas[i:] if r["listos"] >= fila["deseado"]), None)
        eventos.append(
            {
                "deteccion_utc": fila["t"].isoformat(),
                "de_replicas": previo["deseado"],
                "a_replicas": fila["deseado"],
                "listos_utc": listo["t"].isoformat() if listo else None,
                "scaling_time_seconds": (listo["t"] - fila["t"]).total_seconds() if listo else None,
                "concurrencia_por_pod": fila["concurrencia"],
                "concurrencia_objetivo": fila["concurrencia_objetivo"],
                "cpu_pct": fila["cpu_pct"],
                "cpu_objetivo": fila["cpu_objetivo"],
                "metrica_dominante": metrica_dominante(fila),
            }
        )

    return {
        "muestras": len(filas),
        "intervalo_muestreo_s": sorted(intervalos)[len(intervalos) // 2] if intervalos else None,
        "replicas_max_observadas": max((r["listos"] for r in filas), default=None),
        "concurrencia_max_por_pod": max((r["concurrencia"] for r in filas if r["concurrencia"] is not None), default=None),
        "cpu_max_pct": max((r["cpu_pct"] for r in filas if r["cpu_pct"] is not None), default=None),
        "reinicios_inicio_fin": [filas[0]["reinicios"], filas[-1]["reinicios"]] if filas else None,
        "eventos_escalado": eventos,
    }


def veredicto(valor: float | None, meta: float) -> str:
    if valor is None:
        return "sin datos"
    return "cumple" if valor <= meta else "no cumple"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("k6_json", type=Path)
    ap.add_argument("--muestreo", type=Path, required=True)
    ap.add_argument("--salida", type=Path, default=Path("informe-ha03.json"))
    args = ap.parse_args()

    fases = leer_k6(args.k6_json)
    escalado = leer_muestreo(args.muestreo)
    tiempos = [e["scaling_time_seconds"] for e in escalado["eventos_escalado"]]
    sin_listo = sum(1 for t in tiempos if t is None)
    peor = max((t for t in tiempos if t is not None), default=None)
    meseta = fases["meseta"]
    saturacion_total = sum(f["errores_saturacion"] for f in fases.values())
    fallidas = [f["tasa_fallidas"] for f in fases.values() if f["tasa_fallidas"] is not None]
    dominantes = [e["metrica_dominante"] for e in escalado["eventos_escalado"]]

    informe = {
        "origen": {"k6": args.k6_json.name, "muestreo": args.muestreo.name},
        "por_fase": fases,
        "escalado": escalado,
        "veredictos": {
            "c1 scaling_time_seconds <= 60 s (peor evento)": (
                "sin eventos de escalado"
                if not tiempos
                else f"no cumple ({sin_listo} evento(s) sin llegar a las réplicas pedidas)"
                if sin_listo
                else veredicto(peor, METAS["scaling_time_s"])
            ),
            "c4 el HPA escala por solicitudes concurrentes": (
                "sin eventos de escalado"
                if not dominantes
                else "cumple"
                if all(d == "concurrencia" for d in dominantes)
                else f"no cumple ({dominantes.count('cpu')} por CPU, {dominantes.count('sin datos')} sin datos, "
                f"de {len(dominantes)} eventos)"
            ),
            "c2 p95 cliente en meseta <= 250 ms": veredicto(meseta["cliente_ms"]["p95"], METAS["p95_ms"]),
            "c2 p99 cliente en meseta <= 500 ms": veredicto(meseta["cliente_ms"]["p99"], METAS["p99_ms"]),
            "c3 0 errores por saturación": "cumple" if saturacion_total == 0 else "no cumple",
            "c3 0 % cotizaciones fallidas": (
                "sin datos" if not fallidas else "cumple" if max(fallidas) == 0 else "no cumple"
            ),
            "validez: 0 % aproximadas en meseta": (
                "cumple" if (meseta["tasa_aproximadas"] or 0) == 0 else "no cumple"
            ),
        },
    }
    args.salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Origen: {args.k6_json.name} + {args.muestreo.name}")
    for fase, d in fases.items():
        c = d["cliente_ms"]
        print(
            f"  {fase:7} n={d['solicitudes']:>7}  p50={c['p50']}  p95={c['p95']}  p99={c['p99']}  "
            f"fallidas={d['tasa_fallidas']}  saturación={d['errores_saturacion']}  hit={d['hit_cache']}"
        )
    print(f"Escalado (muestreo cada ~{escalado['intervalo_muestreo_s']} s):")
    for e in escalado["eventos_escalado"]:
        print(
            f"  {e['deteccion_utc']}  {e['de_replicas']} -> {e['a_replicas']}  listo en {e['scaling_time_seconds']} s  "
            f"concurrencia={e['concurrencia_por_pod']}/{e['concurrencia_objetivo']}  "
            f"cpu={e['cpu_pct']}/{e['cpu_objetivo']} %  dispara={e['metrica_dominante']}"
        )
    for meta, v in informe["veredictos"].items():
        print(f"  {meta}: {v}")
    print(f"Informe: {args.salida}")


if __name__ == "__main__":
    main()
