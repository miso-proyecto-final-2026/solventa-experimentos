"""Pruebas de analizar-ha02.py con datos sintéticos construidos a mano.

El analizador se ejecuta como en el runbook (`python -I script ...`) y se verifica el informe JSON que escribe. Los
números esperados salen de los datos de cada prueba, no de una corrida real.

Uso (desde ha02/):  python -m pytest tests
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analizar-ha02.py"
T0 = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
CABECERA_MUESTREO = (
    "timestamp_utc,hpa_actual,hpa_deseado,hpa_min,hpa_max,hpa_cpu_pct,hpa_cpu_objetivo,hpa_concurrencia,"
    "hpa_concurrencia_objetivo,pods_listos,pods_running,pods_otros,reinicios_total,cpu_total_m,mem_total_mi,"
    "pods_por_zona,detalle_pods"
)


def punto(metrica: str, valor: float, tags: dict, segundos: float = 0.0) -> str:
    # Hora como la escribe k6: nanosegundos y zona (aquí, la de Colombia).
    hora =(T0 - timedelta(hours=5) + timedelta(seconds=segundos)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "123-05:00"
    return json.dumps(
        {"type": "Point", "metric": metrica, "data": {"time": hora, "value": valor, "tags": tags}}
    )


def solicitudes(escenario: str, latencias: list[float], status: str = "201", consentimiento: str = "si",
                cada_s: float = 1.0, marcado: float | None = None) -> list[str]:
    lineas = []
    for i, ms in enumerate(latencias):
        tags = {"escenario": escenario, "consentimiento": consentimiento, "status": status}
        lineas.append(punto("http_req_duration", ms, tags, i * cada_s))
        if marcado is not None:
            lineas.append(punto("perfil_marcado", 1.0 if i < marcado * len(latencias) else 0.0, tags, i * cada_s))
    return lineas


def escribir(tmp: Path, nombre: str, lineas: list[str], comprimir: bool = False) -> Path:
    ruta = tmp / (nombre + (".json.gz" if comprimir else ".json"))
    texto = "\n".join(['{"type":"Metric","metric":"http_req_duration","data":{}}', *lineas]) + "\n"
    if comprimir:
        with gzip.open(ruta, "wt", encoding="utf-8") as f:
            f.write(texto)
    else:
        ruta.write_text(texto, encoding="utf-8")
    return ruta


def correr(tmp: Path, *argumentos: str | Path) -> dict:
    salida = tmp / "informe.json"
    subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *map(str, argumentos), "--salida", str(salida)],
        check=True,
        capture_output=True,
    )
    return json.loads(salida.read_text(encoding="utf-8"))["criterios"]


def rapidas(n: int = 100, base: float = 50.0) -> list[float]:
    return [base + i for i in range(n)]  # 50..149 ms


def test_c1_cumple_con_los_tres_escenarios_dentro_de_la_meta(tmp_path):
    k6 = escribir(tmp_path, "c1", solicitudes("miss", rapidas()) + solicitudes("hit", rapidas(base=5))
                  + solicitudes("lenta_4a", rapidas(base=250)), comprimir=True)
    c = correr(tmp_path, "--perfilamiento", k6)
    assert c["c1_latencia"]["veredicto"] == "cumple"
    assert c["c1_latencia"]["por_escenario"]["lenta_4a"]["p95_ms"] == round(250 + 99 * 0.95, 1)


def test_c1_no_cumple_si_un_escenario_pasa_el_p95_y_queda_sin_datos_si_falta_uno(tmp_path):
    lentas = [500.0] * 100  # p95 = 500 > 400
    k6 = escribir(tmp_path, "c1", solicitudes("miss", rapidas()) + solicitudes("hit", lentas)
                  + solicitudes("lenta_4a", rapidas()))
    assert correr(tmp_path, "--perfilamiento", k6)["c1_latencia"]["veredicto"] == "no_cumple"

    k6 = escribir(tmp_path, "c1b", solicitudes("miss", rapidas()) + solicitudes("hit", rapidas()))
    c = correr(tmp_path, "--perfilamiento", k6)["c1_latencia"]
    assert c["veredicto"] == "sin_datos"
    assert c["por_escenario"]["lenta_4a"]["veredicto"] == "sin_datos"


def test_c1_excluye_a_los_clientes_sin_consentimiento(tmp_path):
    bloqueos = solicitudes("hit", [2000.0] * 50, status="403", consentimiento="no")
    k6 = escribir(tmp_path, "c1", solicitudes("miss", rapidas()) + solicitudes("hit", rapidas()) + bloqueos
                  + solicitudes("lenta_4a", rapidas()))
    assert correr(tmp_path, "--perfilamiento", k6)["c1_latencia"]["veredicto"] == "cumple"


def test_c2_calcula_perfilamientos_por_hora_en_la_ventana_de_los_201(tmp_path):
    # 61 respuestas 201, una cada 0,15 s: 60 intervalos = 9 s -> 61 / (9 / 3600) = 24.400 por hora.
    meseta = solicitudes("meseta", rapidas(61), cada_s=0.15)
    meseta += solicitudes("meseta", [10.0] * 5, status="403", consentimiento="no", cada_s=0.15)
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "m", meseta))["c2_rendimiento"]
    assert c["veredicto"] == "cumple"
    assert c["perfilamientos_201"] == 61
    assert c["perfilamientos_por_hora"] == 24400


def test_c2_no_cumple_con_saturacion_o_iteraciones_descartadas(tmp_path):
    meseta = solicitudes("meseta", rapidas(61), cada_s=0.15)
    con_error = meseta + [punto("errores_saturacion", 1, {"escenario": "meseta", "consentimiento": "si"})]
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "m1", con_error))["c2_rendimiento"]
    assert c["veredicto"] == "no_cumple" and c["errores_saturacion"] == 1

    con_descarte = meseta + [punto("dropped_iterations", 3, {"scenario": "meseta"})]
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "m2", con_descarte))["c2_rendimiento"]
    assert c["veredicto"] == "no_cumple" and c["iteraciones_descartadas"] == 3


def test_c2_no_cumple_por_debajo_de_20000_por_hora(tmp_path):
    # 11 respuestas en 10 s -> 3.960 por hora.
    meseta = solicitudes("meseta", rapidas(11), cada_s=1.0)
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "m", meseta))["c2_rendimiento"]
    assert c["veredicto"] == "no_cumple" and c["perfilamientos_por_hora"] == 3960


def test_c3_compara_la_meseta_con_la_meta_y_con_la_linea_base(tmp_path):
    perf = escribir(tmp_path, "p", solicitudes("meseta", rapidas()))

    def cotizacion(nombre: str, meseta_ms: float) -> Path:
        lineas = [punto("http_req_duration", 100.0, {"fase": "base", "status": "201"}, i) for i in range(50)]
        lineas += [punto("http_req_duration", meseta_ms, {"fase": "meseta", "status": "201"}, 60 + i) for i in range(50)]
        lineas += [punto("cotizacion_aproximada", 0.0, {"fase": "meseta"}, 60)]
        return escribir(tmp_path, nombre, lineas)

    c = correr(tmp_path, "--perfilamiento", perf, "--cotizacion", cotizacion("c1", 180.0))["c3_cotizacion"]
    assert c["veredicto"] == "cumple"
    assert c["diferencia_p95_ms"] == 80.0 and c["valida"] is True

    c = correr(tmp_path, "--perfilamiento", perf, "--cotizacion", cotizacion("c2", 300.0))["c3_cotizacion"]
    assert c["veredicto"] == "no_cumple"


def test_c4_exige_la_marca_en_las_caidas_pero_no_en_la_fuente_lenta(tmp_path):
    lineas = solicitudes("lenta_4b", rapidas(base=300), marcado=0.3)  # solo las que vencieron el timeout
    lineas += solicitudes("caida_5a", rapidas(), marcado=1.0)
    lineas += solicitudes("caida_5b", rapidas(), marcado=1.0)
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "d", lineas))["c4_degradacion"]
    assert c["veredicto"] == "cumple"
    assert c["por_escenario"]["lenta_4b"]["tasa_marcadas"] == 0.3
    assert c["por_escenario"]["lenta_4b"]["marca_exigida"] is False

    lineas = solicitudes("lenta_4b", rapidas()) + solicitudes("caida_5a", rapidas(), marcado=0.9)
    lineas += solicitudes("caida_5b", rapidas(), marcado=1.0)
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "d2", lineas))["c4_degradacion"]
    assert c["veredicto"] == "no_cumple"
    assert c["por_escenario"]["caida_5a"]["veredicto"] == "no_cumple"


def test_c4_no_cumple_si_alguna_respuesta_no_es_201(tmp_path):
    lineas = solicitudes("lenta_4b", rapidas()) + solicitudes("lenta_4b", [100.0], status="503")
    lineas += solicitudes("caida_5a", rapidas(), marcado=1.0) + solicitudes("caida_5b", rapidas(), marcado=1.0)
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "d", lineas))["c4_degradacion"]
    assert c["por_escenario"]["lenta_4b"]["veredicto"] == "no_cumple"


def test_c5_cuadra_los_403_de_k6_con_la_base(tmp_path):
    lineas = solicitudes("sin_consentimiento", [20.0] * 6, status="403", consentimiento="no")
    lineas += solicitudes("meseta", rapidas(10)) + solicitudes("meseta", [20.0] * 4, status="403", consentimiento="no")
    k6 = escribir(tmp_path, "s", lineas)

    def db(intentos: int, perfiles: int) -> Path:
        ruta = tmp_path / f"db-{intentos}-{perfiles}.json"
        ruta.write_text(json.dumps({"intentos_bloqueados_carga": intentos,
                                    "perfiles_de_clientes_sin_consentimiento": perfiles}), encoding="utf-8")
        return ruta

    c = correr(tmp_path, "--perfilamiento", k6, "--db", db(10, 0))["c5_consentimiento"]
    assert c["veredicto"] == "cumple" and c["total_403_k6"] == 10
    assert correr(tmp_path, "--perfilamiento", k6, "--db", db(9, 0))["c5_consentimiento"]["veredicto"] == "no_cumple"
    assert correr(tmp_path, "--perfilamiento", k6, "--db", db(10, 1))["c5_consentimiento"]["veredicto"] == "no_cumple"
    assert correr(tmp_path, "--perfilamiento", k6)["c5_consentimiento"]["veredicto"] == "sin_datos"


def test_c5_no_cumple_si_un_cliente_sin_consentimiento_recibe_201(tmp_path):
    lineas = solicitudes("sin_consentimiento", [20.0] * 5, status="403", consentimiento="no")
    lineas += solicitudes("sin_consentimiento", [20.0], status="201", consentimiento="no")
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"intentos_bloqueados_carga": 5, "perfiles_de_clientes_sin_consentimiento": 0}))
    c = correr(tmp_path, "--perfilamiento", escribir(tmp_path, "s", lineas), "--db", db)["c5_consentimiento"]
    assert c["veredicto"] == "no_cumple"


def test_c6_revocacion_no_se_sirve_y_no_se_borra(tmp_path):
    k6 = escribir(tmp_path, "p", solicitudes("hit", rapidas()))
    rev = tmp_path / "rev.json"
    rev.write_text(json.dumps({"intervalo_s": 1.0, "resumen": {
        "clientes": 50, "bloqueados": 50, "no_bloqueados": 0, "max_s": 0.4, "mediana_s": 0.2, "limite_s": 300}}))
    redis = tmp_path / "redis.json"
    redis.write_text(json.dumps({"clientes": 50, "claves_principales": 100, "claves_respaldo": 100,
                                 "posibles_por_tipo": 100}))
    c = correr(tmp_path, "--perfilamiento", k6, "--revocacion", rev, "--redis", redis)["c6_revocacion"]
    assert c["veredicto"] == "cumple"
    assert c["lectura"] == "no se sirve, no se borra de Redis (el borrado es de V-11)"

    rev.write_text(json.dumps({"intervalo_s": 1.0, "resumen": {
        "clientes": 50, "bloqueados": 49, "no_bloqueados": 1, "max_s": 2.0, "mediana_s": 0.2, "limite_s": 300}}))
    assert correr(tmp_path, "--perfilamiento", k6, "--revocacion", rev)["c6_revocacion"]["veredicto"] == "no_cumple"


def test_c7_resume_el_muestreo_por_servicio(tmp_path):
    k6 = escribir(tmp_path, "p", solicitudes("hit", rapidas()))
    csv = tmp_path / "perf.csv"
    csv.write_text(
        CABECERA_MUESTREO + "\n"
        "2026-11-20T14:00:00Z,2,2,2,10,30,70,,,2,2,0,1,400,300,a=1;b=1;,x\n"
        "2026-11-20T14:00:05Z,3,3,2,10,75,70,,,3,3,0,1,900,420,a=2;b=1;,x\n"
        "2026-11-20T14:00:10Z,3,3,2,10,60,70,,,3,3,0,2,700,410,a=2;b=1;,x\n",
        encoding="utf-8",
    )
    c = correr(tmp_path, "--perfilamiento", k6, "--muestreo", f"perfilamiento={csv}")["c7_evidencia"]
    m = c["muestreo"]["perfilamiento"]
    assert c["veredicto"] == "cumple"
    assert (m["replicas_max"], m["cpu_total_m_max"], m["mem_total_mi_max"]) == (3, 900, 420)
    assert m["reinicios_en_ventana"] == 1
