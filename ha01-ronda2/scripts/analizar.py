"""Análisis de HA01 sobre la salida cruda de k6 (--out json=...).

Calcula, por escenario (etiqueta scenario_tag), los percentiles de la latencia vista por el
cliente (http_req_duration) y de la interna (latencia_interna_ms que devuelve el servicio),
la tasa de fallos, el hit de caché en régimen y la tasa de degradadas, y emite el veredicto
por meta con el archivo de origen de cada dato.

Uso:
    python analizar.py escenario-a.json [escenario-b.json ...] [--salida informe.json]

Percentiles: interpolación lineal entre rangos (método por defecto de numpy), sobre todas
las muestras del escenario. Solo usa la biblioteca estándar: los archivos crudos pesan
cientos de MB y se leen línea a línea.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

METAS = {
    "A": {"p95": 250.0, "p99": 500.0, "hit_min": 0.60, "degradadas": None},
    "B": {"p95": 300.0, "p99": None, "hit_min": None, "degradadas": 1.0},
}
METRICAS_TREND = ("http_req_duration", "latencia_interna_ms")
METRICAS_RATE = (
    "cotizacion_failed",
    "cotizacion_degradada",
    "cotizacion_aproximada",
    "cache_hit_rate",
    "http_req_failed",
)


def percentil(ordenados: list[float], p: float) -> float | None:
    if not ordenados:
        return None
    k = (len(ordenados) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordenados[lo]
    return ordenados[lo] + (ordenados[hi] - ordenados[lo]) * (k - lo)


def leer(archivos: list[Path]):
    # (métrica, escenario) y (métrica, escenario, fase) -> muestras
    trends: dict[tuple, list[float]] = defaultdict(list)
    # (métrica, escenario, fase) -> [suma, n]
    rates: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0])
    origen: dict[str, set[str]] = defaultdict(set)

    for archivo in archivos:
        with archivo.open(encoding="utf-8") as f:
            for linea in f:
                if '"type":"Point"' not in linea:
                    continue
                punto = json.loads(linea)
                metrica = punto["metric"]
                if metrica not in METRICAS_TREND and metrica not in METRICAS_RATE:
                    continue
                tags = punto["data"].get("tags") or {}
                esc = tags.get("scenario_tag")
                if esc is None:
                    continue
                valor = float(punto["data"]["value"])
                origen[esc].add(archivo.name)
                if metrica in METRICAS_TREND:
                    trends[(metrica, esc)].append(valor)
                    trends[(metrica, esc, tags.get("fase", "-"))].append(valor)
                else:
                    acc = rates[(metrica, esc, tags.get("fase", "-"))]
                    acc[0] += valor
                    acc[1] += 1
    return trends, rates, origen


def tasa(rates, metrica: str, esc: str, fase: str | None = None) -> tuple[float | None, int]:
    suma = n = 0
    for (m, e, f), (s, c) in rates.items():
        if m == metrica and e == esc and (fase is None or f == fase):
            suma += s
            n += c
    return (suma / n if n else None), int(n)


def veredicto(valor: float | None, limite: float, mayor_es_mejor: bool = False) -> str:
    if valor is None:
        return "sin datos"
    cumple = valor >= limite if mayor_es_mejor else valor <= limite
    return "cumple" if cumple else "no cumple"


def analizar(archivos: list[Path]) -> dict:
    trends, rates, origen = leer(archivos)
    escenarios = sorted({k[1] for k in trends} | {e for (_, e, _) in rates})
    informe = {}
    for esc in escenarios:
        cliente = sorted(trends.get(("http_req_duration", esc), []))
        interna = sorted(trends.get(("latencia_interna_ms", esc), []))
        stats = {
            nombre: {
                "n": len(serie),
                **{f"p{int(p * 100)}": percentil(serie, p) for p in (0.5, 0.9, 0.95, 0.99)},
                "max": serie[-1] if serie else None,
            }
            for nombre, serie in (("cliente", cliente), ("interna", interna))
        }
        # Referencia: percentiles del cliente solo en régimen (tras el calentamiento de caché).
        regimen = sorted(trends.get(("http_req_duration", esc, "regimen"), []))
        stats["cliente_regimen"] = {
            "n": len(regimen),
            **{f"p{int(p * 100)}": percentil(regimen, p) for p in (0.5, 0.9, 0.95, 0.99)},
            "max": regimen[-1] if regimen else None,
        }
        fallidas, n_cot = tasa(rates, "cotizacion_failed", esc)
        http_fallidas, _ = tasa(rates, "http_req_failed", esc)
        hit_regimen, n_hit = tasa(rates, "cache_hit_rate", esc, "regimen")
        hit_total, _ = tasa(rates, "cache_hit_rate", esc)
        degradadas, _ = tasa(rates, "cotizacion_degradada", esc)
        aproximadas, _ = tasa(rates, "cotizacion_aproximada", esc)

        brecha = {}
        for p in ("p95", "p99"):
            c, i = stats["cliente"][p], stats["interna"][p]
            brecha[p] = None if c is None or i is None else c - i

        metas = METAS.get(esc, {})
        veredictos = {
            # Validez de la corrida, no meta de HA01: una aproximada no consultó Open Finance.
            "validez: 0 % aproximadas (sin consentimiento)": (
                "sin datos" if aproximadas is None else ("cumple" if aproximadas == 0 else "no cumple")
            ),
            "fallos == 0 %": "sin datos" if fallidas is None else ("cumple" if fallidas == 0 else "no cumple"),
        }
        if metas.get("p95") is not None:
            veredictos[f"p95 cliente <= {metas['p95']:.0f} ms"] = veredicto(stats["cliente"]["p95"], metas["p95"])
        if metas.get("p99") is not None:
            veredictos[f"p99 cliente <= {metas['p99']:.0f} ms"] = veredicto(stats["cliente"]["p99"], metas["p99"])
        if metas.get("hit_min") is not None:
            veredictos[f"hit de caché en régimen >= {metas['hit_min']:.0%}"] = veredicto(
                hit_regimen, metas["hit_min"], mayor_es_mejor=True
            )
        if metas.get("degradadas") is not None:
            veredictos["100 % degradadas"] = veredicto(degradadas, metas["degradadas"], mayor_es_mejor=True)

        informe[esc] = {
            "origen": sorted(origen[esc]),
            "solicitudes": n_cot,
            "latencia_ms": stats,
            "brecha_cliente_menos_interna_ms": brecha,
            "tasa_cotizaciones_fallidas": fallidas,
            "tasa_http_fallidas": http_fallidas,
            "hit_cache_regimen": hit_regimen,
            "muestras_hit_regimen": n_hit,
            "hit_cache_total": hit_total,
            "tasa_degradadas": degradadas,
            "tasa_aproximadas": aproximadas,
            "veredictos": veredictos,
        }
    return informe


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.2f} %"


def imprimir(informe: dict) -> None:
    for esc, r in informe.items():
        c, i = r["latencia_ms"]["cliente"], r["latencia_ms"]["interna"]
        print(f"\n== Escenario {esc}  ({', '.join(r['origen'])})")
        print(f"   solicitudes: {r['solicitudes']}")
        print(f"   {'':10} {'p50':>8} {'p90':>8} {'p95':>8} {'p99':>8} {'max':>8}")
        for nombre, s in (("cliente", c), ("interna", i), ("cli.régim.", r["latencia_ms"]["cliente_regimen"])):
            print(f"   {nombre:10} " + " ".join(f"{_ms(s[k]):>8}" for k in ("p50", "p90", "p95", "p99", "max")))
        b = r["brecha_cliente_menos_interna_ms"]
        print(f"   brecha cliente-interna: p95 {_ms(b['p95'])} ms, p99 {_ms(b['p99'])} ms")
        print(
            f"   fallidas {_pct(r['tasa_cotizaciones_fallidas'])} | http fallidas {_pct(r['tasa_http_fallidas'])}"
            f" | hit régimen {_pct(r['hit_cache_regimen'])} (total {_pct(r['hit_cache_total'])})"
            f" | degradadas {_pct(r['tasa_degradadas'])} | aproximadas {_pct(r['tasa_aproximadas'])}"
        )
        for meta, v in r["veredictos"].items():
            print(f"   [{v:^9}] {meta}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consola de Windows (cp1252)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("archivos", nargs="+", type=Path)
    ap.add_argument("--salida", type=Path, help="escribe el informe en JSON")
    args = ap.parse_args(argv)

    informe = analizar(args.archivos)
    if not informe:
        print("No se encontraron puntos con la etiqueta scenario_tag.", file=sys.stderr)
        return 1
    imprimir(informe)
    if args.salida:
        args.salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nInforme: {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
