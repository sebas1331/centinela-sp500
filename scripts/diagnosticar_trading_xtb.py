#!/usr/bin/env python
"""Por qué el endpoint de trading de XTB devuelve "respuesta vacía".

LA HIPÓTESIS, Y POR QUÉ ES BUENA
---------------------------------
El cliente hace esto al llamar al endpoint de trading:

    resp = await client.post(endpoint, content=body_b64, headers=headers)
    resp.raise_for_status()
    if not resp.text:
        return b""

O sea: el HTTP es 200 —si no, `raise_for_status` habría saltado— y el cuerpo
viene vacío. Pero en gRPC-web **los errores se devuelven con HTTP 200**, con el
motivo en los *trailers* (`grpc-status`, `grpc-message`), y una respuesta de
error de tipo "trailers-only" tiene exactamente eso: cuerpo vacío y el motivo en
las cabeceras. El cliente no las mira, así que el error real se pierde y lo que
sube es un "ambiguous" que no dice nada.

Este script envuelve esa llamada y, cuando el cuerpo viene vacío, imprime lo que
el cliente tira: el código HTTP, TODAS las cabeceras y el cuerpo en crudo. Con
eso, "respuesta vacía" se convierte en un motivo con nombre.

NO MANDA NINGUNA ORDEN por su cuenta: hay que pasarle `--comprar` para que lo
intente, y compra una sola acción del ticker que se le diga.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, ordenes as ords  # noqa: E402

#: Cabeceras que nunca se imprimen: llevan el token de sesión.
SECRETAS = {"authorization", "cookie", "set-cookie", "x-auth-token"}


def log(msg: str) -> None:
    print(msg, flush=True)


def instalar_espia() -> dict:
    """Envuelve `_grpc_call` para quedarse con lo que el cliente descarta."""
    from xtb_api.grpc import client as gc

    visto: dict = {"llamadas": []}
    original = gc.GrpcClient._grpc_call

    async def espiado(self, endpoint, body_b64, jwt=None):
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

        registro = {
            "endpoint": endpoint,
            "http": resp.status_code,
            "cuerpo_bytes": len(resp.text or ""),
            "cabeceras": {k: v for k, v in resp.headers.items()
                          if k.lower() not in SECRETAS},
            "cuerpo": (resp.text or "")[:400],
            "peticion_bytes": len(body_b64),
        }
        visto["llamadas"].append(registro)

        if not (resp.text or ""):
            log("")
            log("=== RESPUESTA VACÍA: esto es lo que el cliente descarta ===")
            log(f"  endpoint : {endpoint}")
            log(f"  HTTP     : {resp.status_code} {resp.reason_phrase}")
            log(f"  petición : {len(body_b64)} bytes de cuerpo enviados")
            for k, v in registro["cabeceras"].items():
                log(f"  {k:24}: {v}")
            log("=== fin ===")
            log("")

        return await original(self, endpoint, body_b64, jwt)

    gc.GrpcClient._grpc_call = espiado
    return visto


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", default="F")
    ap.add_argument("--comprar", action="store_true",
                    help="intenta comprar 1 acción (si no, solo lee)")
    args = ap.parse_args()

    visto = instalar_espia()
    simbolo = ords.simbolo_xtb(args.ticker)

    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        saldo = b.saldo()
        log(f"cuenta {saldo['cuenta']} (DEMO): saldo {saldo['saldo']:,.2f}")
        log(f"posiciones abiertas: {len([p for p in b.posiciones() if p['lado'] == 'buy'])}")

        q = b.cotizacion(simbolo)
        log(f"{simbolo}: bid {q['bid']} / ask {q['ask']}")

        if not args.comprar:
            log("(sin --comprar: no se manda ninguna orden)")
            return 0

        log(f"intentando comprar 1 acción de {simbolo}...")
        e = b.comprar(simbolo, 1)
        log(f"  -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
            + (f" (orden {e.orden})" if e.orden else "")
            + (f" ERROR: {e.error}" if e.error else ""))

        # Si entró, se cierra en el acto: esto es un diagnóstico, no una prueba
        # de estrategia, y la cuenta no debe quedarse con nada.
        import time
        time.sleep(3)
        abiertas = [p for p in b.posiciones()
                    if p["lado"] == "buy" and p["ticker"] == simbolo]
        if abiertas:
            log(f"la orden SÍ entró: cerrando {len(abiertas)} posición(es)...")
            for p in abiertas:
                c = b.vender(simbolo, int(p["acciones"]))
                log(f"  -> {c.estado}" + (f" ERROR: {c.error}" if c.error else ""))

    log("")
    log(f"RESUMEN: {len(visto['llamadas'])} llamada(s) gRPC")
    vacias = [ll for ll in visto["llamadas"] if ll["cuerpo_bytes"] == 0]
    log(f"  con cuerpo vacío: {len(vacias)}")
    for ll in vacias:
        estado = ll["cabeceras"].get("grpc-status")
        mensaje = ll["cabeceras"].get("grpc-message")
        log(f"  {ll['endpoint'].rsplit('/', 1)[-1]}: HTTP {ll['http']}"
            + (f" | grpc-status={estado}" if estado else " | SIN grpc-status")
            + (f" | {mensaje}" if mensaje else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
