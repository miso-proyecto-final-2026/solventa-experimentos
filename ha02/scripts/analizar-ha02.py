"""Análisis de HA02 (perfilamiento en línea) sobre la evidencia cruda de la sesión.

Entradas (todas opcionales salvo --perfilamiento; un criterio sin su evidencia queda `sin_datos`, no `cumple`):
  --perfilamiento  salidas crudas de k6 de ha02-perfilamiento.js (--out json, .json o .json.gz), una por corrida. Cada
                   muestra trae las etiquetas `escenario` y `consentimiento` (si | no) y el `status` HTTP.
  --cotizacion     salida cruda de ha02-cotizacion.js de la corrida de meseta (etiqueta `fase`: base | meseta).
  --revocacion     revocacion.json de revocacion.py.
  --redis          JSON de cluster/claves-redis.sh (claves de los clientes revocados que siguen en Redis).
  --db             JSON de cluster/consultas-db.sh (intentos bloqueados y perfiles en la base).
  --muestreo       servicio=archivo.csv de muestrear-cluster.sh (repetible): réplicas, CPU, memoria y reinicios.
  --sin-datos-por-tiempo  escenarios que no se corrieron por falta de tiempo en la sesión: quedan `sin_datos` con
                   ese motivo (no `no_cumple`), y el criterio que los exige queda `sin_datos`.
  --avisos         líneas WARNING/ERROR de los pods de Perfilamiento («<pod> <hora> <json>», como las guarda el
                   recolector de la sesión): atribuye la causa de los falsos bloqueos por escenario.

Falsos bloqueos (aparte de los criterios): un 403 a un cliente CON consentimiento. Perfilamiento falla cerrado: si
Identidad no responde (`identidad_no_disponible`, p. ej., ReadTimeout), responde 5xx o el circuito está abierto, trata
al cliente como sin consentimiento y devuelve el mismo 403 `CONSENTIMIENTO_NO_VIGENTE`. No son errores por saturación
(no entran en `errores_saturacion` ni en c2) y se informan por escenario con su frecuencia. Con el circuito abierto no
se escribe ningún aviso, así que los avisos pueden ser menos que los falsos bloqueos.

Veredicto por criterio del issue #43:
  c1  p95 <= 400 ms y p99 <= 800 ms en miss, hit y lenta_4a (solo clientes con consentimiento).
  c2  meseta: >= 20.000 perfilamientos con consentimiento (201) por hora, sin errores por saturación (429, 5xx,
      timeouts, conexión) ni iteraciones descartadas por el generador.
  c3  evidencia principal: la diferencia de p95 de la cotización entre la línea base y la meseta (mismo simulador,
      20-80 ms), en `diferencia_p95_ms`; el informe la juzga. `veredicto` solo compara el p95 de la meseta con la meta
      de RQ-L1 (250 ms) como referencia: no es comparable con HA01, que midió con Open Finance a 200-500 ms.
  c4  lenta_4b, caida_5a y caida_5b: 100 % de 201 dentro de la latencia de c1; en caida_5a y caida_5b, además, 100 %
      marcadas como degradadas (respaldo o valor por defecto). En lenta_4b la marca solo aplica a las que vencieron el
      timeout, así que se informa sin exigirla.
  c5  100 % de 403 para los clientes sin consentimiento; en la base, intentos de origen `carga` = 403 de k6 y ningún
      perfil de esos clientes (evidencia indirecta de que no se llamó a las fuentes). La latencia de c1 ya los excluye.
  c6  todos los revocados bloqueados en <= 300 s; se informa cuántas claves siguen en Redis («no se sirve, no se borra»).
  c7  resumen del muestreo por servicio (réplicas, CPU, memoria, reinicios) y lista de la evidencia usada.
El criterio 8 (informe con veredicto) es el documento que se escribe con esta salida.

Uso:
    python -I analizar-ha02.py --perfilamiento r1/perfilamiento.json.gz r2/perfilamiento.json.gz ... \\
        --cotizacion meseta/cotizacion.json.gz --revocacion revocacion.json --redis redis.json --db db.json \\
        --muestreo perfilamiento=perf.csv --muestreo cotizacion=cot.csv --muestreo identidad=iden.csv \\
        --avisos logs-avisos/perfilamiento-avisos.log --salida informe-ha02.json

Solo biblioteca estándar; lee el crudo línea a línea. Percentiles con interpolación lineal (como HA01 y HA03).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

METAS = {
    "perfilamiento_p95_ms": 400.0,
    "perfilamiento_p99_ms": 800.0,
    "perfilamientos_por_hora": 20000,
    "cotizacion_p95_ms": 250.0,
    "revocacion_max_s": 300.0,
}
LATENCIA = ("miss", "hit", "lenta_4a")
DEGRADACION = ("lenta_4b", "caida_5a", "caida_5b")
EXIGE_MARCA = ("caida_5a", "caida_5b")
RATES = ("perfil_fallido", "perfil_hit_cache", "perfil_degradado", "perfil_marcado", "perfil_con_ausentes")


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


_FRACCION = re.compile(r"(\.\d{6})\d+")


def instante(texto: str) -> float:
    """Segundos epoch de una hora de k6 (RFC 3339 con nanosegundos y zona)."""
    return datetime.fromisoformat(_FRACCION.sub(r"\1", texto.replace("Z", "+00:00"))).timestamp()


def puntos(ruta: Path):
    with abrir(ruta) as f:
        for linea in f:
            if '"Point"' not in linea:
                continue
            p = json.loads(linea)
            if p.get("type") == "Point":
                yield p["metric"], p["data"]


def resumen_latencia(valores: list[float]) -> dict:
    v = sorted(valores)
    redondo = lambda x: None if x is None else round(x, 1)
    return {
        "n": len(v),
        "p50_ms": redondo(percentil(v, 0.50)),
        "p95_ms": redondo(percentil(v, 0.95)),
        "p99_ms": redondo(percentil(v, 0.99)),
        "max_ms": redondo(v[-1] if v else None),
    }


def leer_perfilamiento(rutas: list[Path]) -> dict:
    """Por escenario y consentimiento: latencias, estados, tasas, saturación, descartadas y ventana de los 201."""
    dur: dict[tuple[str, str], list[float]] = defaultdict(list)
    estados: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rates: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0])
    saturacion: dict[str, int] = defaultdict(int)
    descartadas: dict[str, int] = defaultdict(int)
    agotados: dict[str, int] = defaultdict(int)
    ventana: dict[str, list[float]] = {}
    solicitudes: dict[str, list[float]] = {}
    for ruta in rutas:
        for metrica, data in puntos(ruta):
            tags = data.get("tags") or {}
            valor = float(data["value"])
            if metrica == "dropped_iterations":
                descartadas[tags.get("scenario", "?")] += int(valor)
                continue
            esc = tags.get("escenario")
            if esc is None:
                continue
            cons = tags.get("consentimiento", "si")
            if metrica == "http_req_duration":
                dur[(esc, cons)].append(valor)
                estado = tags.get("status", "?")
                estados[(esc, cons)][estado] += 1
                t = instante(data["time"])
                s = solicitudes.setdefault(esc, [t, t])
                s[0], s[1] = min(s[0], t), max(s[1], t)
                if cons == "si" and estado == "201":
                    v = ventana.setdefault(esc, [t, t])
                    v[0], v[1] = min(v[0], t), max(v[1], t)
            elif metrica == "errores_saturacion":
                saturacion[esc] += int(valor)
            elif metrica == "clientes_agotados":
                agotados[esc] += int(valor)
            elif metrica in RATES:
                r = rates[(esc, metrica)]
                r[0] += valor
                r[1] += 1
    escenarios: dict[str, dict] = {}
    for esc in sorted({e for e, _ in dur} | set(saturacion)):
        info: dict = {
            "con_consentimiento": resumen_latencia(dur.get((esc, "si"), [])),
            "estados_con_consentimiento": dict(estados.get((esc, "si"), {})),
            "errores_saturacion": saturacion.get(esc, 0),
            "iteraciones_descartadas": descartadas.get(esc, 0),
            "clientes_agotados": agotados.get(esc, 0),
        }
        for metrica in RATES:
            suma, n = rates.get((esc, metrica), (0.0, 0))
            info[metrica] = None if n == 0 else round(suma / n, 4)
        if (esc, "no") in dur:
            info["sin_consentimiento"] = resumen_latencia(dur[(esc, "no")])
            info["estados_sin_consentimiento"] = dict(estados[(esc, "no")])
        if esc in ventana:
            info["ventana_201_s"] = round(ventana[esc][1] - ventana[esc][0], 1)
        if esc in solicitudes:
            info["ventana_solicitudes_utc"] = [
                datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds") for t in solicitudes[esc]
            ]
        escenarios[esc] = info
    return escenarios


_AVISO = re.compile(r"^(\S+) (\S+) (\{.*\})\s*$")


def leer_avisos(ruta: Path, esc: dict) -> dict:
    """Cuenta los avisos de Identidad de Perfilamiento por escenario, según la ventana de solicitudes de k6 (±5 s)."""
    ventanas = {
        e: (instante(i["ventana_solicitudes_utc"][0]) - 5, instante(i["ventana_solicitudes_utc"][1]) + 5)
        for e, i in esc.items()
        if "ventana_solicitudes_utc" in i
    }
    cuenta: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    vistos: set[str] = set()
    with abrir(ruta) as f:
        for linea in f:
            m = _AVISO.match(linea)
            if not m or linea in vistos:
                continue
            vistos.add(linea)
            try:
                mensaje = json.loads(m.group(3)).get("message", "")
            except json.JSONDecodeError:
                continue
            if not mensaje.startswith("identidad_"):
                continue
            t = instante(m.group(2))
            esc_aviso = next((e for e, (a, b) in ventanas.items() if a <= t <= b), "fuera_de_escenarios")
            cuenta[esc_aviso][mensaje] += 1
    return {e: dict(v) for e, v in sorted(cuenta.items())}


def falsos_bloqueos(esc: dict, avisos: dict | None) -> dict:
    """403 a clientes con consentimiento, por escenario y con su frecuencia; separados de la saturación."""
    por_escenario = {}
    for e, info in esc.items():
        estados = info["estados_con_consentimiento"]
        total = sum(estados.values())
        n = estados.get("403", 0)
        por_escenario[e] = {
            "falsos_bloqueos": n,
            "solicitudes_con_consentimiento": total,
            "frecuencia": round(n / total, 6) if total else None,
            "errores_saturacion": info["errores_saturacion"],
        }
        if avisos is not None:
            por_escenario[e]["avisos_identidad"] = avisos.get(e, {})
    total_fb = sum(v["falsos_bloqueos"] for v in por_escenario.values())
    total_si = sum(v["solicitudes_con_consentimiento"] for v in por_escenario.values())
    return {
        "definicion": "403 a un cliente con consentimiento: Perfilamiento falla cerrado si Identidad no responde "
        "(identidad_no_disponible), responde 5xx o tiene el circuito abierto. No son errores por saturación.",
        "total": total_fb,
        "solicitudes_con_consentimiento": total_si,
        "frecuencia": round(total_fb / total_si, 6) if total_si else None,
        "por_escenario": por_escenario,
        "avisos_fuera_de_escenarios": (avisos or {}).get("fuera_de_escenarios"),
        "nota": None if avisos is not None else "sin --avisos: la causa no se atribuye con los logs",
    }


def leer_cotizacion(ruta: Path) -> dict:
    dur: dict[str, list[float]] = defaultdict(list)
    fallidas: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    aproximadas: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    saturacion: dict[str, int] = defaultdict(int)
    for metrica, data in puntos(ruta):
        fase = (data.get("tags") or {}).get("fase")
        if fase is None:
            continue
        valor = float(data["value"])
        if metrica == "http_req_duration":
            dur[fase].append(valor)
        elif metrica == "errores_saturacion":
            saturacion[fase] += int(valor)
        elif metrica in ("cotizacion_failed", "cotizacion_aproximada"):
            r = (fallidas if metrica == "cotizacion_failed" else aproximadas)[fase]
            r[0] += valor
            r[1] += 1
    tasa = lambda r: None if r[1] == 0 else round(r[0] / r[1], 4)
    return {
        fase: {
            **resumen_latencia(dur[fase]),
            "tasa_fallidas": tasa(fallidas[fase]),
            "tasa_aproximadas": tasa(aproximadas[fase]),
            "errores_saturacion": saturacion.get(fase, 0),
        }
        for fase in sorted(dur)
    }


def leer_muestreo(ruta: Path) -> dict:
    with ruta.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))

    def numeros(col: str) -> list[float]:
        out = []
        for fila in filas:
            try:
                out.append(float(fila.get(col) or ""))
            except ValueError:
                continue
        return out

    maximo = lambda col: max(numeros(col), default=None)
    reinicios = numeros("reinicios_total")
    return {
        "muestras": len(filas),
        "desde_utc": filas[0]["timestamp_utc"] if filas else None,
        "hasta_utc": filas[-1]["timestamp_utc"] if filas else None,
        "replicas_max": maximo("hpa_actual"),
        "pods_listos_min": min(numeros("pods_listos"), default=None),
        "pods_listos_max": maximo("pods_listos"),
        "cpu_total_m_max": maximo("cpu_total_m"),
        "mem_total_mi_max": maximo("mem_total_mi"),
        "hpa_cpu_pct_max": maximo("hpa_cpu_pct"),
        "reinicios_en_ventana": (reinicios[-1] - reinicios[0]) if reinicios else None,
    }


def veredicto(valor: bool | None) -> str:
    return "sin_datos" if valor is None else ("cumple" if valor else "no_cumple")


def dentro_de_latencia(lat: dict) -> bool | None:
    if not lat or lat.get("n", 0) == 0:
        return None
    return lat["p95_ms"] <= METAS["perfilamiento_p95_ms"] and lat["p99_ms"] <= METAS["perfilamiento_p99_ms"]


def todos(valores: list[bool | None]) -> bool | None:
    if not valores or any(v is None for v in valores):
        return None
    return all(valores)


def solo_201(estados: dict) -> bool:
    return bool(estados) and set(estados) == {"201"}


def criterios(esc: dict, cot: dict | None, rev: dict | None, redis: dict | None, db: dict | None,
              omitidos: frozenset[str] = frozenset()) -> dict:
    c: dict = {}

    por_escenario = {e: dentro_de_latencia(esc.get(e, {}).get("con_consentimiento", {})) for e in LATENCIA}
    c["c1_latencia"] = {
        "veredicto": veredicto(todos(list(por_escenario.values()))),
        "por_escenario": {
            e: {**esc.get(e, {}).get("con_consentimiento", {}), "veredicto": veredicto(por_escenario[e])}
            for e in LATENCIA
        },
    }

    m = esc.get("meseta")
    if m:
        ok_201 = m["estados_con_consentimiento"].get("201", 0)
        ventana_h = (m.get("ventana_201_s") or 0) / 3600
        por_hora = round(ok_201 / ventana_h) if ventana_h > 0 else None
        cumple2 = (
            por_hora is not None
            and por_hora >= METAS["perfilamientos_por_hora"]
            and m["errores_saturacion"] == 0
            and m["iteraciones_descartadas"] == 0
        )
        c["c2_rendimiento"] = {
            "veredicto": veredicto(cumple2),
            "perfilamientos_201": ok_201,
            "ventana_s": m.get("ventana_201_s"),
            "perfilamientos_por_hora": por_hora,
            "errores_saturacion": m["errores_saturacion"],
            "iteraciones_descartadas": m["iteraciones_descartadas"],
        }
    else:
        c["c2_rendimiento"] = {"veredicto": "sin_datos"}

    if cot and "meseta" in cot:
        base, meseta = cot.get("base", {}), cot["meseta"]
        c["c3_cotizacion"] = {
            "veredicto": veredicto(meseta["p95_ms"] is not None and meseta["p95_ms"] <= METAS["cotizacion_p95_ms"]),
            "meta_p95_ms": METAS["cotizacion_p95_ms"],
            "evidencia_principal": "diferencia_p95_ms",
            "nota": "el veredicto es solo la referencia frente a RQ-L1; el p95 absoluto no es comparable con HA01 "
            "(simulador a 200-500 ms); c3 se juzga por la diferencia entre la línea base y la meseta",
            "base": base,
            "meseta": meseta,
            "diferencia_p95_ms": (
                round(meseta["p95_ms"] - base["p95_ms"], 1) if base.get("p95_ms") is not None else None
            ),
            "valida": not (meseta.get("tasa_aproximadas") or 0) > 0,
        }
    else:
        c["c3_cotizacion"] = {"veredicto": "sin_datos"}

    detalle4, valores4 = {}, []
    for e in DEGRADACION:
        info = esc.get(e)
        if not info:
            detalle4[e] = {"veredicto": "sin_datos"}
            if e in omitidos:
                detalle4[e]["motivo"] = "sin datos por tiempo"
            valores4.append(None)
            continue
        ok = solo_201(info["estados_con_consentimiento"]) and bool(
            dentro_de_latencia(info["con_consentimiento"])
        )
        if e in EXIGE_MARCA:
            ok = ok and info.get("perfil_marcado") == 1.0
        valores4.append(ok)
        detalle4[e] = {
            "veredicto": veredicto(ok),
            **info["con_consentimiento"],
            "estados": info["estados_con_consentimiento"],
            "tasa_marcadas": info.get("perfil_marcado"),
            "marca_exigida": e in EXIGE_MARCA,
        }
    c["c4_degradacion"] = {"veredicto": veredicto(todos(valores4)), "por_escenario": detalle4}

    bloqueos = {e: i["estados_sin_consentimiento"] for e, i in esc.items() if "estados_sin_consentimiento" in i}
    total_403 = sum(s.get("403", 0) for s in bloqueos.values())
    todos_403 = bool(bloqueos) and all(set(s) == {"403"} for s in bloqueos.values())
    if bloqueos:
        cumple5: bool | None = todos_403
        if db is not None:
            cumple5 = (
                cumple5
                and db.get("intentos_bloqueados_carga") == total_403
                and db.get("perfiles_de_clientes_sin_consentimiento") == 0
            )
        else:
            cumple5 = None
        c["c5_consentimiento"] = {
            "veredicto": veredicto(cumple5),
            "estados_por_escenario": bloqueos,
            "total_403_k6": total_403,
            # Si la base también registra los falsos bloqueos como intentos, la diferencia con la base es esta cifra.
            "falsos_bloqueos_k6": sum(i["estados_con_consentimiento"].get("403", 0) for i in esc.values()),
            "base": db,
            "evidencia": "indirecta: intentos bloqueados en la base y ningún perfil de esos clientes (sin contador)",
        }
    else:
        c["c5_consentimiento"] = {"veredicto": "sin_datos"}

    if rev:
        r = rev["resumen"]
        cumple6 = r["no_bloqueados"] == 0 and r["max_s"] is not None and r["max_s"] <= METAS["revocacion_max_s"]
        c["c6_revocacion"] = {
            "veredicto": veredicto(cumple6),
            **r,
            "resolucion_s": rev.get("intervalo_s"),
            "redis_tras_revocar": redis,
            "lectura": "no se sirve, no se borra de Redis (el borrado es de V-11)"
            if redis and (redis.get("claves_principales", 0) + redis.get("claves_respaldo", 0)) > 0
            else None,
        }
    else:
        c["c6_revocacion"] = {"veredicto": "sin_datos"}
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--perfilamiento", nargs="+", required=True, type=Path)
    ap.add_argument("--cotizacion", type=Path)
    ap.add_argument("--revocacion", type=Path)
    ap.add_argument("--redis", type=Path)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--muestreo", action="append", default=[], metavar="SERVICIO=CSV")
    ap.add_argument("--avisos", type=Path)
    ap.add_argument("--sin-datos-por-tiempo", nargs="+", default=[], metavar="ESCENARIO")
    ap.add_argument("--salida", type=Path, default=Path("informe-ha02.json"))
    args = ap.parse_args(argv)

    leer_json = lambda p: None if p is None else json.loads(p.read_text(encoding="utf-8"))
    esc = leer_perfilamiento(args.perfilamiento)
    cot = leer_cotizacion(args.cotizacion) if args.cotizacion else None
    muestreo = {}
    for par in args.muestreo:
        servicio, _, ruta = par.partition("=")
        if not ruta:
            ap.error(f"--muestreo espera servicio=archivo.csv, no {par}")
        muestreo[servicio] = leer_muestreo(Path(ruta))

    omitidos = frozenset(args.sin_datos_por_tiempo)
    con_datos = sorted(omitidos & set(esc))
    if con_datos:
        ap.error(f"--sin-datos-por-tiempo incluye escenarios que sí tienen datos: {', '.join(con_datos)}")
    resultado = criterios(
        esc, cot, leer_json(args.revocacion), leer_json(args.redis), leer_json(args.db), omitidos
    )
    resultado["c7_evidencia"] = {
        "veredicto": veredicto(bool(muestreo) or None),
        "muestreo": muestreo,
        "archivos": {
            "perfilamiento": [str(p) for p in args.perfilamiento],
            "cotizacion": str(args.cotizacion) if args.cotizacion else None,
            "revocacion": str(args.revocacion) if args.revocacion else None,
            "redis": str(args.redis) if args.redis else None,
            "db": str(args.db) if args.db else None,
            "avisos": str(args.avisos) if args.avisos else None,
        },
    }
    fb = falsos_bloqueos(esc, leer_avisos(args.avisos, esc) if args.avisos else None)
    informe = {"metas": METAS, "escenarios_sin_datos_por_tiempo": sorted(omitidos), "criterios": resultado, "falsos_bloqueos": fb, "escenarios": esc, "cotizacion": cot}
    args.salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    for nombre, valor in resultado.items():
        print(f"{nombre:<22} {valor['veredicto']}")
    print(f"{'falsos_bloqueos':<22} {fb['total']} de {fb['solicitudes_con_consentimiento']} (aparte de la saturación)")
    print(f"Informe en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
