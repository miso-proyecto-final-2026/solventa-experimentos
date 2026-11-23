"""Cliente mínimo de la API de socios de Solventa (staging).

El secreto se lee de credenciales/secreto.txt y nunca se imprime; el token tampoco.
"""
import re
import time
import uuid
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
TIMEOUT = 10  # s, según errores-y-reintentos.md

MENSAJES = {
    "INVALID_REQUEST": "La solicitud no es válida o no cumple una regla de negocio.",
    "INVALID_TOKEN": "El token de acceso no es válido; hay que obtener uno nuevo.",
    "TOKEN_EXPIRED": "El token de acceso venció; se pide uno nuevo.",
    "ENDPOINT_NOT_AUTHORIZED": "Nuestro contrato con Solventa no habilita esta operación.",
    "RESOURCE_NOT_FOUND": "La cotización, venta o póliza no existe.",
    "IDEMPOTENCY_CONFLICT": "Esta operación ya se envió antes con otros datos (clave de idempotencia repetida).",
    "RATE_LIMIT_EXCEEDED": "Se superó la cuota de solicitudes por segundo; se espera y se reintenta.",
    "INTERNAL_ERROR": "Solventa tuvo un fallo interno; se reintenta.",
    "SERVICE_UNAVAILABLE": "Un servicio de Solventa no responde; se reintenta.",
    "SERVICE_DEGRADED": "Un servicio de Solventa está degradado; se reintenta más tarde.",
}
MOTIVOS = {
    "CLIENTE_NO_REGISTRADO": "el cliente no está registrado en Solventa",
    "KYC_NO_VERIFICADO": "el cliente no ha verificado su identidad",
    "CONSENTIMIENTO_NO_VIGENTE": "el cliente no tiene un consentimiento vigente para compartir sus datos",
    "COTIZACION_YA_USADA": "la cotización ya se usó en otra venta",
    "COTIZACION_VENCIDA": "la cotización venció; hay que cotizar de nuevo",
    "COTIZACION_SIN_CUENTA": "la cotización no se puede suscribir",
    "SIN_METODO_PAGO": "no hay un medio de pago válido",
    "POLIZA_NO_LISTA": "la póliza todavía no está lista",
    "FIRMA_NO_DISPONIBLE": "la venta no está en el paso de firma",
    "FIRMA_VENCIDA": "venció el plazo para firmar",
    "FIRMA_FALLIDA": "no se pudo verificar la identidad al firmar; la venta se canceló",
    "DOCUMENTO_ALTERADO": "el documento firmado no coincide con el emitido",
    "OTP_INVALIDO": "el código de firma no es válido",
    "CERTIFICADO_NO_DISPONIBLE": "el certificado no está disponible",
    "SOLICITUD_EN_PROCESO": "la solicitud original sigue en curso",
}


class ErrorSolventa(Exception):
    def __init__(self, status, code, reason, trace_id, correlation_id, details=None, retry_after=None):
        self.status, self.code, self.reason = status, code, reason
        self.trace_id, self.correlation_id = trace_id, correlation_id
        self.details, self.retry_after = details or [], retry_after
        super().__init__(self.legible())

    def legible(self):
        txt = MENSAJES.get(self.code, f"Error {self.code}.")
        if self.reason:
            txt += f" Motivo: {MOTIVOS.get(self.reason, self.reason)}."
        if self.details:
            txt += " Campos: " + ", ".join(f"{d.get('campo')} ({d.get('motivo')})" for d in self.details) + "."
        return f"[HTTP {self.status} {self.code}{'/' + self.reason if self.reason else ''}] {txt} (trace_id={self.trace_id})"


def _leer_ambiente():
    txt = (RAIZ / "credenciales" / "ambiente.txt").read_text(encoding="utf-8")
    url = re.search(r"URL base:\s*(\S+)", txt).group(1).rstrip("/")
    cid = re.search(r"client_id:\s*(\S+)", txt).group(1)
    return url, cid


def _secreto():
    return (RAIZ / "credenciales" / "secreto.txt").read_text(encoding="utf-8").strip()


class Cliente:
    def __init__(self, bitacora=print):
        self.base, self.client_id = _leer_ambiente()
        self.s = requests.Session()
        self._token = None
        self._vence = 0
        self.log = bitacora
        self.eventos = []  # (operación, resultado) para el resumen

    # ---------- autenticación ----------
    def estado(self):
        r = self.s.get(f"{self.base}/partners/v1/status", timeout=TIMEOUT)
        return r.status_code, r.json()

    def obtener_token(self):
        r = self.s.post(
            f"{self.base}/oauth/token",
            data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": _secreto()},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            try:
                j = r.json()
            except ValueError:
                j = {}
            raise ErrorSolventa(r.status_code, j.get("error", "oauth_error"), None, j.get("trace_id"),
                                r.headers.get("X-Correlation-Id"))
        j = r.json()
        self._token = j["access_token"]
        self._vence = time.time() + int(j.get("expires_in", 0)) - 30
        return {"token_type": j.get("token_type"), "expires_in": j.get("expires_in"), "scope": j.get("scope")}

    def _auth(self):
        if not self._token or time.time() >= self._vence:
            self.obtener_token()
        return {"Authorization": f"Bearer {self._token}"}

    # ---------- llamada genérica con política de reintentos ----------
    def llamar(self, metodo, ruta, json=None, idem=None, token=None, crudo=False, max_intentos=6):
        """token: None = el propio; str = forzar ese token (para pruebas)."""
        url = f"{self.base}/partners/v1{ruta}"
        renovado = False
        backoff = 1
        for intento in range(1, max_intentos + 1):
            h = {"Authorization": f"Bearer {token}"} if token is not None else self._auth()
            if idem:
                h["X-Idempotency-Key"] = idem
            try:
                r = self.s.request(metodo, url, json=json, headers=h, timeout=TIMEOUT)
            except requests.Timeout:
                self.log(f"  sin respuesta en {TIMEOUT}s; reintento {intento} con la misma clave")
                continue
            if r.status_code < 400:
                if crudo:
                    return r
                return r.json() if r.content else {}
            try:
                e = r.json().get("error", {})
            except ValueError:
                e = {}
            ra = r.headers.get("Retry-After")
            err = ErrorSolventa(r.status_code, e.get("code"), e.get("reason"), e.get("trace_id"),
                                r.headers.get("X-Correlation-Id"), e.get("details"),
                                float(ra) if ra else None)
            if err.code == "TOKEN_EXPIRED" and token is None and not renovado:
                self.log("  " + err.legible()); self._token = None; renovado = True; continue
            if err.code == "RATE_LIMIT_EXCEEDED" or (err.code == "IDEMPOTENCY_CONFLICT" and err.reason == "SOLICITUD_EN_PROCESO") \
                    or (err.code == "SERVICE_DEGRADED" and ra):
                espera = err.retry_after if err.retry_after is not None else backoff
                self.log(f"  {err.legible()} -> espero Retry-After={espera}s")
                self.eventos.append(("reintento", f"{err.code} esperó {espera}s"))
                time.sleep(espera); continue
            if err.code in ("INTERNAL_ERROR", "SERVICE_UNAVAILABLE", "SERVICE_DEGRADED") and intento < max_intentos:
                self.log(f"  {err.legible()} -> retroceso {backoff}s"); time.sleep(backoff); backoff *= 2; continue
            raise err
        raise RuntimeError(f"{metodo} {ruta}: se agotaron los intentos")

    @staticmethod
    def clave():
        return str(uuid.uuid4())
