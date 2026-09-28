#!/usr/bin/env python
"""Renueva la sesión de XTB desde la web de GitHub. Sin terminal.

POR QUÉ HACE FALTA
------------------
XTB no ofrece TOTP: sus métodos de segundo factor son SMS, notificación push y
correo, y los tres necesitan que una persona lea un código. La sesión (TGT)
dura 8 horas, así que cada cierto tiempo el ejecutor se encuentra con que XTB
le pide un código y no hay quien se lo dé. Ese run muere en rojo con la marca
`XTB_REQUIERE_CODIGO` y esto es lo que lo arregla.

DOS FASES, PORQUE EL CÓDIGO LLEGA DESPUÉS DE PEDIRLO
-----------------------------------------------------
No se puede pegar un código antes de tenerlo, así que:

  FASE 1 (sin código).  Inicia el login. XTB manda el correo y devuelve un
                        `login_ticket`, que se guarda. Termina diciendo "ahora
                        relanza con el código".
  FASE 2 (con código).  Recupera el ticket guardado y lo canjea por un TGT de
                        8 horas, que queda en la caché para los demás runs.

La mayoría de las veces la fase 1 sobra: cuando el ejecutor falla con
`XTB_REQUIERE_CODIGO`, XTB **ya ha enviado el correo** en ese intento, y ese
run guardó su ticket. Así que basta con lanzar la fase 2 pegando el código.

El ticket caduca en minutos. Si ya no vale, la fase 2 lo dice claramente y hay
que empezar por la fase 1 — nunca se queda a medias en silencio.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, parche_otp  # noqa: E402

#: Donde vive el ticket entre las dos fases. Junto a la sesión, porque el mismo
#: `actions/cache` lo lleva y lo trae.
ARCHIVO_TICKET = Path.home() / ".centinela_xtb_ticket.json"
#: Un ticket de XTB caduca en minutos. Pasado esto no se intenta canjear: se
#: dice que hay que pedir uno nuevo, en vez de fallar con un error del servidor.
TICKET_VALIDO_SEG = 15 * 60


def log(msg: str) -> None:
    print(f"[renovar] {msg}", flush=True)


def _guardar_ticket(ticket: str, tipo: str) -> None:
    ARCHIVO_TICKET.write_text(json.dumps({
        "login_ticket": ticket, "tipo": tipo, "cuando": time.time(),
    }), encoding="utf-8")
    ARCHIVO_TICKET.chmod(0o600)


def _cargar_ticket() -> dict | None:
    if not ARCHIVO_TICKET.exists():
        return None
    try:
        d = json.loads(ARCHIVO_TICKET.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if time.time() - float(d.get("cuando", 0)) > TICKET_VALIDO_SEG:
        return None
    return d


def _guardar_sesion(tgt: str, expira: float) -> None:
    """Mismo formato EXACTO que escribe la librería: marcas de tiempo ISO.

    Guardarlas como número hace que su `_load_session_file` reviente con
    "fromisoformat: argument must be str" y el ejecutor se quede sin sesión sin
    decir por qué. Pasó ya una vez.
    """
    bx.ARCHIVO_SESION.write_text(json.dumps({
        "tgt": tgt,
        "extracted_at": datetime.now(UTC).isoformat(),
        "expires_at": datetime.fromtimestamp(expira, tz=UTC).isoformat(),
    }), encoding="utf-8")
    bx.ARCHIVO_SESION.chmod(0o600)


async def _fase1(cred: bx.Credenciales) -> int:
    """Pide el código: hace login hasta que XTB manda el correo."""
    from xtb_api.auth.cas_client import CASClient, CASClientConfig
    from xtb_api.types.websocket import CASLoginTwoFactorRequired

    cas = CASClient(CASClientConfig(cookies_file=bx.ARCHIVO_COOKIES))
    log("iniciando sesión para que XTB envíe el código...")
    r = await cas.login_with_browser(cred.email, cred.password, headless=True)

    if not isinstance(r, CASLoginTwoFactorRequired):
        # XTB no pidió segundo factor: la sesión ya vale y no hay nada que
        # renovar. Se guarda y se termina en verde.
        _guardar_sesion(r.tgt, getattr(r, "expires_at", time.time() + 8 * 3600))
        log("XTB no pidió código: la sesión ya era válida y queda guardada.")
        return 0

    _guardar_ticket(r.login_ticket, r.two_factor_auth_type)
    log(f"XTB pide código por {r.two_factor_auth_type!r}.")
    log("")
    log("  REVISA TU CORREO y vuelve a lanzar este mismo workflow,")
    log("  esta vez PEGANDO el código de 6 dígitos en el campo.")
    log("")
    return 0


async def _fase2(cred: bx.Credenciales, codigo: str) -> int:
    """Canjea el código por una sesión de 8 horas."""
    from xtb_api.auth.cas_client import CASClient, CASClientConfig
    from xtb_api.types.websocket import CASLoginTwoFactorRequired

    guardado = _cargar_ticket()
    cas = CASClient(CASClientConfig(cookies_file=bx.ARCHIVO_COOKIES))

    if guardado is None:
        # Sin ticket vigente hay que rehacer el login, y eso dispara OTRO
        # correo: el código que el usuario acaba de pegar ya no servirá. Se
        # dice sin rodeos en vez de fallar con un error del servidor.
        log("No hay ningún ticket vigente (o caducó: duran unos minutos).")
        log("Pidiendo uno nuevo — XTB va a enviarte OTRO código.")
        await _fase1(cred)
        log("")
        log("  El código que pegaste ya no vale. Usa el NUEVO correo y")
        log("  vuelve a lanzar el workflow.")
        return 1

    log(f"canjeando el código (ticket pedido hace "
        f"{(time.time() - guardado['cuando']) / 60:.1f} min)...")
    r = await cas.login_with_two_factor(
        guardado["login_ticket"], codigo, guardado.get("tipo", "EMAIL"))

    if isinstance(r, CASLoginTwoFactorRequired):
        log("XTB volvió a pedir segundo factor: el código no era válido o ya "
            "se usó. Lanza el workflow SIN código para pedir uno nuevo.")
        return 1

    _guardar_sesion(r.tgt, getattr(r, "expires_at", time.time() + 8 * 3600))
    ARCHIVO_TICKET.unlink(missing_ok=True)   # de un solo uso
    log("SESIÓN RENOVADA. Vale 8 horas y los demás runs ya la encontrarán.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codigo", default="",
                    help="código de 6 dígitos del correo (vacío = pedir uno)")
    args = ap.parse_args()

    codigo = "".join(c for c in args.codigo if c.isdigit())
    cred = bx.credenciales_del_entorno_o_llavero()
    log(f"cuenta {cred.cuenta}")
    parche_otp.aplicar()

    try:
        if codigo:
            log(f"fase 2: canjeando un código de {len(codigo)} dígitos")
            return asyncio.run(_fase2(cred, codigo))
        log("fase 1: pidiendo el código (no se recibió ninguno)")
        return asyncio.run(_fase1(cred))
    except Exception as exc:
        log(f"FALLO: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
