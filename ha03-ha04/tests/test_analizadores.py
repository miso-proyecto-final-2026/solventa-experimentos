"""Pruebas de analizar-ha03.py y analizar-ha04.py con datos sintéticos construidos a mano.

Cada analizador se ejecuta como en el runbook (`python -I script ...`) y se verifica el informe JSON que escribe.
Los números esperados salen de los datos de cada prueba, no de una corrida real.

Uso (desde ha03-ha04/):  python -m pytest tests
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

CABECERA_MUESTREO = (
    "timestamp_utc,hpa_actual,hpa_deseado,hpa_min,hpa_max,hpa_cpu_pct,hpa_cpu_objetivo,hpa_concurrencia,"
    "hpa_concurrencia_objetivo,pods_listos,pods_running,pods_otros,reinicios_total,cpu_total_m,mem_total_mi,"
    "pods_por_zona,detalle_pods"
)
CABECERA_MUESTREO_SOLO_CPU = (
    "timestamp_utc,hpa_actual,hpa_deseado,hpa_min,hpa_max,hpa_cpu_pct,pods_listos,pods_running,pods_otros,"
    "reinicios_total,cpu_total_m,mem_total_mi,pods_por_zona,detalle_pods"
)


def correr(script: str, *argumentos: Path | str) -> dict:
    salida = Path(argumentos[-1])
    subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / script), *map(str, argumentos[:-1]), "--salida", str(salida)],
        check=True,
        capture_output=True,
        cwd=salida.parent,
    )
    return json.loads(salida.read_text(encoding="utf-8"))


def punto(metrica: str, valor: float, **tags: str) -> str:
    return json.dumps({"type": "Point", "metric": metrica, "data": {"value": valor, "tags": tags}})


# --------------------------------------------------------------------------------------------------- HA03


def k6_ha03(ruta: Path) -> Path:
    lineas = [json.dumps({"type": "Metric", "metric": "http_req_duration", "data": {}})]  # se ignora
    for v in range(1, 101):  # meseta: 1..100 ms -> p95 = 95,05 ms
        lineas.append(punto("http_req_duration", v, fase="meseta"))
        lineas.append(punto("cotizacion_failed", 0, fase="meseta"))
        lineas.append(punto("cotizacion_aproximada", 0, fase="meseta"))
    for v in (10, 20, 30):
        lineas.append(punto("http_req_duration", v, fase="base"))
    lineas.append(punto("errores_saturacion", 2, fase="subida"))
    lineas.append(punto("cotizacion_failed", 1, fase="subida"))
    lineas.append(punto("cotizacion_failed", 0, fase="subida"))
    lineas.append(punto("http_req_duration", 5))  # sin fase: se ignora
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    return ruta


def muestreo(ruta: Path, filas: list[tuple], solo_cpu: bool = False) -> Path:
    """filas: (mm:ss, deseado, listos, cpu_pct, concurrencia_por_pod); objetivos 70 % y 3."""
    salida = [CABECERA_MUESTREO_SOLO_CPU if solo_cpu else CABECERA_MUESTREO]
    for t, deseado, listos, cpu, conc in filas:
        ts = f"2026-10-25T10:{t}Z"
        if solo_cpu:
            salida.append(f"{ts},{listos},{deseado},2,10,{cpu},{listos},{listos},0,0,100,100,a=1;,")
        else:
            salida.append(f"{ts},{listos},{deseado},2,10,{cpu},70,{conc},3,{listos},{listos},0,0,100,100,a=1;,")
    ruta.write_text("\n".join(salida) + "\n", encoding="utf-8")
    return ruta


def test_ha03_percentiles_saturacion_y_fallidas_por_fase(tmp_path):
    informe = correr(
        "analizar-ha03.py",
        k6_ha03(tmp_path / "ha03.json.gz"),
        "--muestreo",
        muestreo(tmp_path / "m.csv", [("00:00", 2, 2, 10, "1"), ("00:05", 2, 2, 10, "1")]),
        tmp_path / "informe.json",
    )
    meseta = informe["por_fase"]["meseta"]
    assert meseta["solicitudes"] == 100
    assert abs(meseta["cliente_ms"]["p95"] - 95.05) < 1e-9
    assert informe["por_fase"]["base"]["cliente_ms"]["p50"] == 20
    assert informe["por_fase"]["subida"]["errores_saturacion"] == 2
    assert informe["por_fase"]["subida"]["tasa_fallidas"] == 0.5
    v = informe["veredictos"]
    assert v["c2 p95 cliente en meseta <= 250 ms"] == "cumple"
    assert v["c3 0 errores por saturación"] == "no cumple"
    assert v["c3 0 % cotizaciones fallidas"] == "no cumple"
    assert v["c1 scaling_time_seconds <= 60 s (peor evento)"] == "sin eventos de escalado"
    assert v["c4 el HPA escala por solicitudes concurrentes"] == "sin eventos de escalado"


def test_ha03_escalado_por_concurrencia_cumple_c1_y_c4(tmp_path):
    filas = [
        ("00:00", 2, 2, 10, "1200m"),
        ("00:05", 6, 2, 12, "7500m"),  # concurrencia 7,5/3 = 2,5 frente a CPU 12/70: dispara la concurrencia
        ("00:35", 6, 6, 15, "2"),  # listos 30 s después
        ("00:40", 6, 6, 15, "2"),
    ]
    informe = correr(
        "analizar-ha03.py",
        k6_ha03(tmp_path / "ha03.json.gz"),
        "--muestreo",
        muestreo(tmp_path / "m.csv", filas),
        tmp_path / "informe.json",
    )
    (evento,) = informe["escalado"]["eventos_escalado"]
    assert (evento["de_replicas"], evento["a_replicas"]) == (2, 6)
    assert evento["scaling_time_seconds"] == 30
    assert evento["concurrencia_por_pod"] == 7.5  # "7500m" interpretado como cantidad de Kubernetes
    assert evento["metrica_dominante"] == "concurrencia"
    assert informe["escalado"]["concurrencia_max_por_pod"] == 7.5
    assert informe["veredictos"]["c1 scaling_time_seconds <= 60 s (peor evento)"] == "cumple"
    assert informe["veredictos"]["c4 el HPA escala por solicitudes concurrentes"] == "cumple"


def test_ha03_un_escalado_por_cpu_no_cumple_c4(tmp_path):
    filas = [
        ("00:00", 2, 2, 10, "2"),
        ("00:05", 6, 2, 10, "7500m"),  # concurrencia
        ("00:10", 6, 6, 12, "2"),
        ("00:15", 8, 6, 90, "2500m"),  # CPU 90/70 = 1,29 frente a 2,5/3 = 0,83: dispara la CPU
        ("00:20", 8, 8, 40, "2"),
    ]
    informe = correr(
        "analizar-ha03.py",
        k6_ha03(tmp_path / "ha03.json.gz"),
        "--muestreo",
        muestreo(tmp_path / "m.csv", filas),
        tmp_path / "informe.json",
    )
    assert [e["metrica_dominante"] for e in informe["escalado"]["eventos_escalado"]] == ["concurrencia", "cpu"]
    assert informe["veredictos"]["c4 el HPA escala por solicitudes concurrentes"] == (
        "no cumple (1 por CPU, 0 sin datos, de 2 eventos)"
    )


def test_ha03_evento_que_no_llega_a_las_replicas_pedidas_no_cumple_c1(tmp_path):
    filas = [("00:00", 2, 2, 10, "1"), ("00:05", 10, 2, 10, "9"), ("00:10", 10, 6, 10, "5")]
    informe = correr(
        "analizar-ha03.py",
        k6_ha03(tmp_path / "ha03.json.gz"),
        "--muestreo",
        muestreo(tmp_path / "m.csv", filas),
        tmp_path / "informe.json",
    )
    assert informe["escalado"]["eventos_escalado"][0]["scaling_time_seconds"] is None
    assert informe["veredictos"]["c1 scaling_time_seconds <= 60 s (peor evento)"] == (
        "no cumple (1 evento(s) sin llegar a las réplicas pedidas)"
    )


def test_ha03_muestreo_solo_cpu_no_inventa_la_metrica(tmp_path):
    filas = [("00:00", 2, 2, 10, ""), ("00:05", 4, 2, 80, ""), ("00:10", 4, 4, 50, "")]
    informe = correr(
        "analizar-ha03.py",
        k6_ha03(tmp_path / "ha03.json.gz"),
        "--muestreo",
        muestreo(tmp_path / "m.csv", filas, solo_cpu=True),
        tmp_path / "informe.json",
    )
    (evento,) = informe["escalado"]["eventos_escalado"]
    assert evento["metrica_dominante"] == "sin datos"
    assert evento["scaling_time_seconds"] == 5
    assert informe["veredictos"]["c4 el HPA escala por solicitudes concurrentes"].startswith("no cumple")


# --------------------------------------------------------------------------------------------------- HA04


def k6_ha04(ruta: Path) -> Path:
    """10 solicitudes/s durante 60 s desde 10:00:00. Falla A a los 10 s: errores en 10:00:12-10:00:14 (1 por
    segundo). Falla B a los 40 s: sin errores."""
    lineas = []
    n = 0
    for s in range(60):
        for _ in range(10):
            n += 1
            status = "500" if 12 <= s <= 14 and _ == 0 else "201"
            lineas.append(
                json.dumps(
                    {
                        "type": "Point",
                        "metric": "http_reqs",
                        "data": {
                            "time": f"2026-10-25T10:00:{s:02d}.123456789Z",  # nanosegundos, como k6
                            "value": 1,
                            "tags": {"status": status, "cliente_id": f"run7-{n}"},
                        },
                    }
                )
            )
    lineas.append(json.dumps({"type": "Point", "metric": "http_req_duration", "data": {"value": 3, "tags": {}}}))
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def test_ha04_disponibilidad_recuperacion_y_rpo(tmp_path):
    eventos = tmp_path / "eventos.csv"
    eventos.write_text(
        "nombre,inicio_utc\nfailover-redis,2026-10-25T10:00:40Z\nfailover-rds,2026-10-25T10:00:10Z\n",
        encoding="utf-8",
    )
    informe = correr(
        "analizar-ha04.py",
        k6_ha04(tmp_path / "ha04.json"),
        "--eventos",
        eventos,
        tmp_path / "informe.json",
    )
    assert informe["solicitudes"] == 600
    assert informe["confirmadas"] == 597
    assert informe["disponibilidad"] == 597 / 600
    assert informe["veredicto_disponibilidad"] == "no cumple"  # 99,5 % < 99,97 %

    rds, redis = informe["fallas"]  # ordenadas por inicio, no por el orden del CSV
    assert rds["falla"] == "failover-rds"
    assert rds["solicitudes"] == 300  # 10:00:10 hasta 10:00:40
    assert rds["segundos_con_error"] == 3
    assert rds["recuperacion_s"] == 5  # último segundo con error (14) - inicio (10) + 1
    assert redis["recuperacion_s"] == 0 and redis["disponibilidad"] == 1.0

    confirmadas = (tmp_path / "confirmadas.txt").read_text(encoding="utf-8").split()
    assert len(confirmadas) == 597 and "run7-121" not in confirmadas  # la primera solicitud del segundo 12
    assert informe["rpo_esperado"] == 597
    assert "LIKE 'run7-%'" in informe["rpo_consulta"]
