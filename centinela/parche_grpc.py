"""Leer el motivo que el cliente tira a la basura cuando una orden falla.

EL PROBLEMA, Y LO QUE COSTÓ
---------------------------
El cliente no oficial hace esto al mandar una orden:

    resp = await client.post(endpoint, content=body_b64, headers=headers)
    resp.raise_for_status()
    if not resp.text:
        return b""

En gRPC-web **los errores llegan con HTTP 200** y el motivo en las cabeceras
(`grpc-status`, `grpc-message`); una respuesta de error "trailers-only" tiene el
cuerpo vacío y el porqué arriba. Como el cliente no las mira, todo error de
trading sube convertido en «respuesta vacía; resultado ambiguo», que no dice
nada de nada.

El 2026-09-29 eso costó una tarde entera: ocho compras rechazadas y ninguna
pista. Al leer las cabeceras, el motivo apareció en el primer intento:

    grpc-status  : 3
    grpc-message : Could not find instrument for id: 335

Es decir, el servicio de trading de XTB no reconocía el identificador que su
propio buscador acababa de devolver. Nada que arreglar por nuestra parte, pero
ocho intentos a ciegas frente a un mensaje claro es toda la diferencia entre
diagnosticar en un minuto y en una tarde.

POR QUÉ UN PARCHE Y NO UN FORK
------------------------------
La versión de la librería está fijada a propósito —una actualización automática
de un cliente no oficial que manda órdenes a un broker es justo lo que no
queremos— y esto es un añadido de tres líneas sobre un método interno. Mismo
criterio que `parche_otp.py`: se envuelve, no se copia, y si el día de mañana la
librería lo arregla, el parche sobra y se nota enseguida porque deja de haber
cabeceras que añadir.
"""
from __future__ import annotations

#: Lo que gRPC llama a cada código, para que el log no obligue a buscarlo.
#: https://grpc.io/docs/guides/status-codes/
CODIGOS = {
    "0": "OK", "1": "CANCELLED", "2": "UNKNOWN", "3": "INVALID_ARGUMENT",
    "4": "DEADLINE_EXCEEDED", "5": "NOT_FOUND", "6": "ALREADY_EXISTS",
    "7": "PERMISSION_DENIED", "8": "RESOURCE_EXHAUSTED",
    "9": "FAILED_PRECONDITION", "10": "ABORTED", "11": "OUT_OF_RANGE",
    "12": "UNIMPLEMENTED", "13": "INTERNAL", "14": "UNAVAILABLE",
    "15": "DATA_LOSS", "16": "UNAUTHENTICATED",
}

#: Dónde se deja el último motivo leído, para que quien traduzca la ejecución
#: pueda ponerlo en el error en vez de «respuesta vacía».
ULTIMO: dict = {}

_instalado = False


def motivo_legible(cabeceras) -> str | None:
    """'INVALID_ARGUMENT (3): Could not find instrument for id: 335'."""
    estado = cabeceras.get("grpc-status")
    if estado is None:
        return None
    nombre = CODIGOS.get(str(estado), f"código {estado}")
    mensaje = cabeceras.get("grpc-message") or "sin mensaje"
    return f"{nombre} ({estado}): {mensaje}"


def instalar() -> bool:
    """Envuelve `_grpc_call`. True si lo hizo, False si ya estaba o no se pudo."""
    global _instalado
    if _instalado:
        return False
    try:
        from xtb_api.grpc import client as gc
    except ImportError:
        return False

    original = gc.GrpcClient._grpc_call

    async def con_motivo(self, endpoint, body_b64, jwt=None):
        headers = {
            "Content-Type": gc.GRPC_WEB_TEXT_CONTENT_TYPE,
            "Accept": gc.GRPC_WEB_TEXT_CONTENT_TYPE,
            "X-Grpc-Web": "1",
            "x-user-agent": "grpc-web-javascript/0.1",
        }
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        http = await self._ensure_http()
        resp = await http.post(endpoint, content=body_b64, headers=headers)

        if not (resp.text or ""):
            motivo = motivo_legible(resp.headers)
            ULTIMO["motivo"] = motivo
            ULTIMO["endpoint"] = endpoint.rsplit("/", 1)[-1]
            if motivo:
                print(f"[grpc] {ULTIMO['endpoint']}: {motivo}", flush=True)
            else:
                print(f"[grpc] {ULTIMO['endpoint']}: cuerpo vacío y SIN "
                      f"grpc-status. Eso no es un error de XTB: es que la "
                      f"respuesta no trae nada que interpretar.", flush=True)
        else:
            ULTIMO.clear()

        # Se delega en el original para no duplicar su lógica: esto solo añade
        # la lectura de las cabeceras, no cambia lo que se devuelve.
        return await original(self, endpoint, body_b64, jwt)

    gc.GrpcClient._grpc_call = con_motivo
    _instalado = True
    return True
