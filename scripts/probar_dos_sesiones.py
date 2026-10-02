#!/usr/bin/env python
"""¿Admite XTB dos sesiones simultáneas con la misma cuenta?

De esta respuesta depende todo el diseño del vigilante de precios:

  SÍ  -> el vigilante vive en su propio workflow, conectado toda la sesión, y
         los demás momentos (verificación de apertura, ventas por tiempo)
         siguen entrando cuando les toca, cada uno con su login.
  NO  -> mientras el vigilante esté conectado nadie más puede hacer login, así
         que el vigilante tiene que asumir también esas tareas, y los demás
         workflows han de saber cuándo callarse.

No se puede contestar leyendo documentación: XTB cerró su API oficial y lo que
queda es un cliente de ingeniería inversa. Se mide.

Solo LEE (saldo y una cotización). No manda ninguna orden.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx  # noqa: E402

SIMBOLO = "AAPL.US"


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    cred = bx.credenciales_del_entorno_o_llavero()

    _log("== sesión A: conectando…")
    t0 = time.monotonic()
    a = bx.BrokerXTB(cred, demo=True)
    a.conectar()
    saldo_a = a.saldo()
    _log(f"   A conectada en {time.monotonic() - t0:.1f} s | cuenta "
         f"DEMO | saldo "
         f"{saldo_a['saldo']:,.2f}")

    _log("== sesión B: conectando SIN cerrar A…")
    t1 = time.monotonic()
    b = None
    try:
        b = bx.BrokerXTB(cred, demo=True)
        b.conectar()
        saldo_b = b.saldo()
        _log(f"   B conectada en {time.monotonic() - t1:.1f} s | saldo "
             f"{saldo_b['saldo']:,.2f}")
    except Exception as exc:  # noqa: BLE001 — la pregunta es justo si falla
        _log(f"   B NO pudo conectar: {type(exc).__name__}: {str(exc)[:200]}")
        _log("")
        _log("VEREDICTO: DOS SESIONES SIMULTÁNEAS = NO")
        a.desconectar()
        return 0

    # Conectar no basta: hay que comprobar que A SIGUE viva. El modo de fallo
    # más probable no es que B sea rechazada, sino que B eche a A sin avisar, y
    # eso solo se ve preguntándole a A después.
    _log("== ¿sigue viva A después de abrir B?")
    try:
        saldo_a2 = a.saldo()
        _log(f"   A responde: saldo {saldo_a2['saldo']:,.2f}")
        a_viva = True
    except Exception as exc:  # noqa: BLE001
        _log(f"   A ha muerto: {type(exc).__name__}: {str(exc)[:200]}")
        a_viva = False

    # Y que las dos reciban precios, que es para lo que hace falta la segunda.
    if a_viva:
        _log("== cotizaciones por las dos a la vez")
        for nombre, sesion in (("A", a), ("B", b)):
            try:
                q = sesion.cotizacion(SIMBOLO)
                _log(f"   {nombre}: {SIMBOLO} bid={q['bid']} ask={q['ask']}")
            except Exception as exc:  # noqa: BLE001
                _log(f"   {nombre}: sin cotización — {type(exc).__name__}: "
                     f"{str(exc)[:150]}")
                a_viva = a_viva and nombre != "A"

    _log("")
    _log(f"VEREDICTO: DOS SESIONES SIMULTÁNEAS = "
         f"{'SÍ' if a_viva else 'NO (la segunda echa a la primera)'}")

    for sesion in (b, a):
        try:
            sesion.desconectar()
        except Exception as exc:  # noqa: BLE001
            _log(f"   al desconectar: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
