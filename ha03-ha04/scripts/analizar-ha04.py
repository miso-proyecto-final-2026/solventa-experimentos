"""Análisis de HA04 sobre la salida cruda de k6 de `ha04-trafico.js` y el registro de fallas del simulacro.

Calcula:
  - disponibilidad: solicitudes con 201 / total, global y por falla (criterio 4, objetivo 99,97 %);
  - por cada falla del registro (`eventos.csv`: nombre,inicio_utc), el primer y el último segundo con errores dentro de
    su ventana (hasta la falla siguiente o el fin) y el tiempo de recuperación = último segundo con error - inicio
    (criterio 2). Si no hubo errores en la ventana, la recuperación es 0;
  - la lista de cliente_id confirmados (201), para contrastarla con la base (criterio 3, RPO). Imprime la consulta.

Uso:
    python -I analizar-ha04.py trafico.json[.gz] --eventos eventos.csv [--salida informe-ha04.json]

Solo biblioteca estándar; lee el crudo línea a línea.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

OBJETIVO_DISPONIBILIDAD = 0.9997


def abrir(ruta: Path):
    return gzip.open(ruta, "rt", encoding="utf-8") if ruta.suffix == ".gz" else ruta.open(encoding="utf-8")


def fecha(valor: str) -> datetime:
    # k6 escribe nanosegundos; fromisoformat admite hasta microsegundos.
    valor = re.sub(r"(\.\d{6})\d+", r"\1", valor.replace("Z", "+00:00"))
    return datetime.fromisoformat(valor)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("k6_json", type=Path)
    ap.add_argument("--eventos", type=Path, required=True, help="CSV nombre,inicio_utc de cada falla inyectada")
    ap.add_argument("--salida", type=Path, default=Path("informe-ha04.json"))
    ap.add_argument("--confirmadas", type=Path, default=Path("confirmadas.txt"))
    args = ap.parse_args()

    por_segundo: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])  # segundo -> [ok, total]
    confirmadas: list[str] = []
    with abrir(args.k6_json) as f:
        for linea in f:
            if "http_reqs" not in linea:
                continue
            punto = json.loads(linea)
            if punto.get("type") != "Point" or punto.get("metric") != "http_reqs":
                continue
            tags = punto["data"].get("tags") or {}
            segundo = fecha(punto["data"]["time"]).replace(microsecond=0)
            ok = tags.get("status") == "201"
            por_segundo[segundo][1] += 1
            if ok:
                por_segundo[segundo][0] += 1
                confirmadas.append(tags.get("cliente_id", ""))

    segundos = sorted(por_segundo)
    if not segundos:
        raise SystemExit("El archivo no tiene puntos http_reqs")
    total = sum(v[1] for v in por_segundo.values())
    ok_total = sum(v[0] for v in por_segundo.values())

    with args.eventos.open(encoding="utf-8", newline="") as f:
        eventos = sorted(
            ({"nombre": r["nombre"], "inicio": fecha(r["inicio_utc"])} for r in csv.DictReader(f)),
            key=lambda e: e["inicio"],
        )

    resultados = []
    for i, e in enumerate(eventos):
        fin = eventos[i + 1]["inicio"] if i + 1 < len(eventos) else segundos[-1] + timedelta(seconds=1)
        ventana = [s for s in segundos if e["inicio"] <= s < fin]
        con_error = [s for s in ventana if por_segundo[s][0] < por_segundo[s][1]]
        n = sum(por_segundo[s][1] for s in ventana)
        ok = sum(por_segundo[s][0] for s in ventana)
        resultados.append(
            {
                "falla": e["nombre"],
                "inicio_utc": e["inicio"].isoformat(),
                "solicitudes": n,
                "disponibilidad": ok / n if n else None,
                "primer_error_utc": con_error[0].isoformat() if con_error else None,
                "ultimo_error_utc": con_error[-1].isoformat() if con_error else None,
                "recuperacion_s": (con_error[-1] - e["inicio"]).total_seconds() + 1 if con_error else 0,
                "segundos_con_error": len(con_error),
            }
        )

    args.confirmadas.write_text("\n".join(confirmadas) + "\n", encoding="utf-8")
    prefijo = confirmadas[0].rsplit("-", 1)[0] if confirmadas else "<RUN_ID>"
    disponibilidad = ok_total / total
    informe = {
        "origen": {"k6": args.k6_json.name, "eventos": args.eventos.name},
        "inicio_utc": segundos[0].isoformat(),
        "fin_utc": segundos[-1].isoformat(),
        "solicitudes": total,
        "confirmadas": ok_total,
        "disponibilidad": disponibilidad,
        "veredicto_disponibilidad": "cumple" if disponibilidad >= OBJETIVO_DISPONIBILIDAD else "no cumple",
        "fallas": resultados,
        "rpo_consulta": f"SELECT count(*) FROM cotizacion.cotizaciones WHERE cliente_id LIKE '{prefijo}-%';",
        "rpo_esperado": ok_total,
    }
    args.salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Solicitudes {total}, confirmadas {ok_total}, disponibilidad {disponibilidad:.5%}")
    for r in resultados:
        print(
            f"  {r['falla']:24} disponibilidad={r['disponibilidad']}  recuperación={r['recuperacion_s']} s  "
            f"segundos con error={r['segundos_con_error']}"
        )
    print(f"RPO: {informe['rpo_consulta']}  -> debe dar {ok_total}; la diferencia son cotizaciones perdidas")
    print(f"Informe: {args.salida}   Confirmadas: {args.confirmadas}")


if __name__ == "__main__":
    main()
