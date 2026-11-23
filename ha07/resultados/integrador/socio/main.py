"""Programa del socio simulado: recorrido completo contra staging de Solventa.

Uso:
    python main.py                    # todo: estado, token, cotización, venta, firma, errores
    python main.py --venta <id>       # retoma una venta existente (p. ej. tras una revisión asistida)
    python main.py --espera-max 120   # segundos máximos esperando que la venta quede lista para firmar
    python main.py --solo-errores

Nunca imprime el secreto ni el token.
"""
import argparse
import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import solventa
from solventa import Cliente, ErrorSolventa

CLIENTE_OK = {"tipo_documento": "CC", "numero_documento": "1000000001"}
CLIENTE_SIN_CONSENTIMIENTO = {"tipo_documento": "CC", "numero_documento": "1000000004"}
PRODUCTO = "microseguro_vida"
DATOS = {"edad": 35, "suma_asegurada": 50000000}
OTP_STAGING = "123456"

resumen = []


def paso(nombre, ok, detalle):
    resumen.append((nombre, "OK" if ok else "FALLÓ", detalle))
    print(f"[{'OK' if ok else 'XX'}] {nombre}: {detalle}")


def cotizar(c, cliente, ref):
    body = {"producto": PRODUCTO, "referencia_cliente": ref, "datos": DATOS}
    if cliente:
        body["cliente"] = cliente
    return c.llamar("POST", "/cotizaciones", json=body, idem=c.clave())


def lista_para_firmar(p):
    pol = p.get("poliza") or {}
    return p.get("paso_actual") == "FIRMA" and (p.get("firma") or {}).get("estado") == "PENDIENTE" \
        and pol.get("documento_estado") == "GENERADO"


def esperar_firma(c, sid, espera_max):
    t0 = time.time()
    ultimo = None
    while time.time() - t0 < espera_max:
        p = c.llamar("GET", f"/polizas/{sid}")
        estado = (p["estado"], p["paso_actual"], (p.get("decision") or {}).get("decision"))
        if estado != ultimo:
            print(f"  venta {sid}: estado={estado[0]} paso={estado[1]} decisión={estado[2]}")
            ultimo = estado
        if lista_para_firmar(p) or p["estado"] in ("FIRMADA", "EMITIDA"):
            return p
        dec = p.get("decision") or {}
        if dec.get("decision") == "RECHAZADA" or (p.get("cobro") or {}).get("estado") == "RECHAZADO":
            raise RuntimeError(f"La venta no prosperó: decisión={dec}, cobro={p.get('cobro')}")
        time.sleep(3)
    p = c.llamar("GET", f"/polizas/{sid}")
    dec = p.get("decision") or {}
    if dec.get("decision") == "DERIVADA":
        raise RuntimeError(
            f"La venta {sid} quedó derivada a revisión asistida en Solventa ({'; '.join(dec.get('motivos', []))}). "
            f"Contacte a soporte con el id de la venta y retome con: python main.py --venta {sid}")
    raise RuntimeError(f"La venta {sid} no quedó lista para firmar en {espera_max}s (estado={p['estado']}, paso={p['paso_actual']}).")


def firmar_y_emitir(c, sid):
    doc = c.llamar("GET", f"/suscripciones/{sid}/documento-firma")
    pdf = c.llamar("GET", f"/suscripciones/{sid}/documento-firma/pdf", crudo=True).content
    if hashlib.sha256(pdf).hexdigest() != doc["documento_sha256"]:
        raise RuntimeError("El PDF descargado no coincide con el hash del documento: no se firma.")
    paso("Documento de firma", True, f"póliza {doc['numero_poliza']}, PDF {len(pdf)} bytes, hash verificado")
    if doc["estado"] != "FIRMADA":
        c.llamar("POST", f"/suscripciones/{sid}/firma/codigo", crudo=True)
        paso("Código de firma", True, "Solventa envió el OTP al cliente (staging: fijo)")
        body = {"documento_sha256": doc["documento_sha256"], "documento_revisado": True,
                "codigo": OTP_STAGING, "canal": "WEB", "dispositivo": "socio-simulado-ha07"}
        clave = c.clave()
        f = c.llamar("POST", f"/suscripciones/{sid}/firma", json=body, idem=clave)
        paso("Firma", True, f"estado tras firmar={f['estado']}, método={f['metodo_autenticacion']}")
        r = c.llamar("POST", f"/suscripciones/{sid}/firma", json=body, idem=clave, crudo=True)
        paso("Reintento idempotente de la firma", r.headers.get("X-Idempotent-Replayed") == "true",
             f"HTTP {r.status_code}, X-Idempotent-Replayed={r.headers.get('X-Idempotent-Replayed')}")
    for _ in range(40):
        p = c.llamar("GET", f"/polizas/{sid}")
        if p["estado"] == "EMITIDA":
            pol = p["poliza"]
            paso("Póliza emitida", True, f"{pol['numero_poliza']} {pol['estado']} desde {pol['vigencia_desde']} "
                                         f"hasta {pol['fecha_vencimiento']}")
            return
        time.sleep(2)
    paso("Póliza emitida", False, f"la venta sigue en {p['estado']}")


def venta(c, venta_id, espera_max):
    if venta_id:
        sid = venta_id
        paso("Venta", True, f"se retoma la venta {sid}")
    else:
        q = cotizar(c, CLIENTE_OK, "cli-000001")
        paso("Cotización", True, f"{q['id']} {q['producto']} prima anual {q['prima_anual']} {q['moneda']} "
                                 f"(degradado={q['degradado']}, aproximada={q['aproximada']})")
        v = c.llamar("POST", "/suscripciones", idem=c.clave(), json={
            "cotizacion_id": q["id"], "cliente": CLIENTE_OK,
            "metodo_pago": f"tok_sim_{c.clave()}", "acepta_terminos": True})
        sid = v["id"]
        paso("Venta iniciada", True, f"{sid} estado={v['estado']} paso={v['paso_actual']}")
    esperar_firma(c, sid, espera_max)
    paso("Lista para firmar", True, sid)
    firmar_y_emitir(c, sid)


def esperado(nombre, fn, code, reason=None):
    try:
        fn()
        paso(nombre, False, f"se esperaba {code} y la llamada tuvo éxito")
    except ErrorSolventa as e:
        ok = e.code == code and (reason is None or e.reason == reason)
        paso(nombre, ok, e.legible())


def errores(c):
    # 1. Token inválido
    esperado("Token inválido", lambda: c.llamar("GET", "/polizas/no-existe", token="token-invalido"), "INVALID_TOKEN")

    # 2. Venta a un cliente que no puede comprar (consentimiento revocado)
    esperado("Cotización para cliente sin consentimiento",
             lambda: cotizar(c, CLIENTE_SIN_CONSENTIMIENTO, "cli-000004"), "INVALID_REQUEST", "CONSENTIMIENTO_NO_VIGENTE")
    q = cotizar(c, CLIENTE_OK, "cli-000001")
    esperado("Venta a cliente sin consentimiento", lambda: c.llamar("POST", "/suscripciones", idem=c.clave(), json={
        "cotizacion_id": q["id"], "cliente": CLIENTE_SIN_CONSENTIMIENTO,
        "metodo_pago": f"tok_sim_{c.clave()}", "acepta_terminos": True}), "INVALID_REQUEST")

    # 3. Misma clave de idempotencia con otro cuerpo
    clave = c.clave()
    body = {"producto": PRODUCTO, "referencia_cliente": "cli-000009", "datos": DATOS}
    c.llamar("POST", "/cotizaciones", json=body, idem=clave)
    otro = dict(body, datos={"edad": 40, "suma_asegurada": 50000000})
    esperado("Misma clave con otro cuerpo", lambda: c.llamar("POST", "/cotizaciones", json=otro, idem=clave),
             "IDEMPOTENCY_CONFLICT")

    # 4. Ráfaga por encima de la cuota (5/s): 15 consultas concurrentes; cada 429 espera Retry-After
    time.sleep(1.2)
    c.obtener_token()
    vistos = []
    lock = threading.Lock()

    def bitacora(msg):
        with lock:
            vistos.append(msg)

    rafaga = Cliente(bitacora=bitacora)
    rafaga._token, rafaga._vence = c._token, c._vence

    def consulta(_):
        try:
            rafaga.llamar("GET", "/polizas/no-existe")
        except ErrorSolventa as e:
            return e.code
        return "200"

    t0 = time.time()
    with ThreadPoolExecutor(15) as ex:
        finales = list(ex.map(consulta, range(15)))
    n429 = sum("RATE_LIMIT_EXCEEDED" in m for m in vistos)
    ok = n429 > 0 and "RATE_LIMIT_EXCEEDED" not in finales
    paso("Ráfaga sobre la cuota", ok,
         f"{n429} respuestas 429 esperaron Retry-After y se reintentaron; resultados finales {sorted(set(finales))} "
         f"(404 esperado: recurso inexistente) en {time.time() - t0:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venta")
    ap.add_argument("--espera-max", type=int, default=90)
    ap.add_argument("--solo-errores", action="store_true")
    a = ap.parse_args()

    c = Cliente()
    st, est = c.estado()
    paso("Estado del API", st == 200 and est["estado"] == "operativo", f"HTTP {st} {est['estado']} {est['dependencias']}")
    try:
        t = c.obtener_token()
        paso("Token", True, f"{t['token_type']}, vence en {t['expires_in']}s, alcance: {t['scope']}")
    except ErrorSolventa as e:
        paso("Token", False, e.legible())
        return 1

    if not a.solo_errores:
        try:
            venta(c, a.venta, a.espera_max)
        except (ErrorSolventa, RuntimeError) as e:
            paso("Venta completa", False, str(e))
    errores(c)

    print("\n===== RESUMEN =====")
    for n, r, d in resumen:
        print(f"{r:6} {n}: {d}")
    return 0 if all(r == "OK" for _, r, _ in resumen) else 2


if __name__ == "__main__":
    sys.exit(main())
