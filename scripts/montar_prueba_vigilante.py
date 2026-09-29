#!/usr/bin/env python
"""Monta (y desmonta) la prueba real del vigilante de precios.

Compra UNA acción barata y calcula un nivel pegado al precio de mercado, de
forma que el vigilante lo cruce en cuestión de segundos. Devuelve por la salida
del step la especificación que el vigilante espera.

No toca `estado/estado.json` ni la bitácora del sistema: la posición se le pasa
al vigilante por argumento y la bitácora se desvía a un temporal.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, ordenes as ords  # noqa: E402

#: Cuánto se separa el nivel del precio actual. Lo justo para que el nivel esté
#: del lado que dispara y no dependa de que el precio se mueva: si se pusiera
#: exactamente en el bid, un movimiento de un céntimo en contra dejaría la
#: prueba esperando sin que nada esté mal.
MARGEN = 0.02


def log(msg: str) -> None:
    print(msg, flush=True)


def publicar(clave: str, valor: str) -> None:
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write(f"{clave}={valor}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", default="F")
    ap.add_argument("--disparo", choices=["objetivo", "stop"], default="objetivo")
    ap.add_argument("--sin-cotizacion", action="store_true",
                    help="no pedir cotización antes de comprar (diagnóstico)")
    ap.add_argument("--limpiar", action="store_true",
                    help="cierra la posición de prueba si sigue abierta")
    args = ap.parse_args()

    simbolo = ords.simbolo_xtb(args.ticker)
    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        saldo = b.saldo()
        log(f"cuenta {saldo['cuenta']} (DEMO): equity {saldo['equity']:,.2f}")

        abiertas = [p for p in b.posiciones()
                    if p["lado"] == "buy" and p["ticker"] == simbolo]

        if args.limpiar:
            # Las ÓRDENES EN COLA también. Una compra que volvió "ambigua" pudo
            # haberse colocado igualmente: el cliente dice que el POST entró y
            # que el cuerpo vino vacío, así que no saber si hay posición no es
            # lo mismo que saber que no hay nada. Mirar solo posiciones dejaría
            # una orden viva esperando a que abra el mercado.
            en_cola = [o for o in b.ordenes_pendientes()
                       if o.get("ticker") == simbolo]
            for o in en_cola:
                log(f"limpieza: cancelando orden en cola {o.get('orden')} de "
                    f"{simbolo}...")
                log(f"  -> {b.cancelar(o['orden'])}")

            if not abiertas and not en_cola:
                log(f"limpieza: no queda ninguna posición ni orden de {simbolo}. "
                    f"La cuenta está como estaba.")
                return 0
            for p in abiertas:
                log(f"limpieza: cerrando {p['acciones']} de {simbolo}...")
                e = b.vender(simbolo, int(p["acciones"]))
                log(f"  -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
                    + (f" ERROR: {e.error}" if e.error else ""))
                if not e.ok:
                    raise RuntimeError(
                        f"NO se pudo cerrar la posición de prueba de {simbolo}. "
                        f"Ciérrala a mano en xStation 5 antes de nada más.")
            return 0

        if abiertas:
            raise RuntimeError(
                f"Ya hay una posición abierta de {simbolo} en la cuenta. La "
                f"prueba no se monta encima de algo que no puso ella: ciérrala "
                f"antes, o usa otro ticker.")

        # ORDEN DELIBERADO. `cotizacion()` usa `get_quote` del cliente, que por
        # dentro se SUSCRIBE y se DESUSCRIBE del símbolo. La compra que sí
        # entró el 28/09 nunca hacía eso, porque ese método no existía. Con
        # `--sin-cotizacion` se compra primero y se pregunta el precio después,
        # que es lo único que distingue "XTB no deja operar" de "lo rompí yo".
        if not args.sin_cotizacion:
            q = b.cotizacion(simbolo)
            log(f"{simbolo}: bid {q['bid']} / ask {q['ask']}")
        else:
            log("sin pedir cotización antes de comprar (a propósito).")
        log("comprando 1 acción...")
        e = b.comprar(simbolo, 1)
        log(f"  -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
            + (f" (orden {e.orden})" if e.orden else "")
            + (f" ERROR: {e.error}" if e.error else ""))
        if not e.ok:
            raise RuntimeError(f"La compra de prueba no entró: {e.error}")

        # El nivel se pone del lado que dispara, con margen: el objetivo por
        # DEBAJO del bid (se vende cuando bid >= objetivo) y el stop por encima.
        bid = b.cotizacion(simbolo)["bid"]
        if args.disparo == "objetivo":
            spec = f"{args.ticker}:{bid - MARGEN:.2f}:"
            log(f"objetivo en {bid - MARGEN:.2f} (bid {bid}): debe disparar ya.")
        else:
            spec = f"{args.ticker}::{bid + MARGEN:.2f}"
            log(f"stop en {bid + MARGEN:.2f} (bid {bid}): debe disparar ya.")

    publicar("spec", spec)
    log(f"spec para el vigilante: {spec}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
