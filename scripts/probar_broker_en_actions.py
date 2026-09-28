#!/usr/bin/env python
"""Prueba del broker desde un runner de GitHub Actions.

Responde una sola pregunta y la responde con evidencia: ¿puede este ejecutor
operar la demo de XTB desde la nube, sin ningún ordenador de por medio?

Lo que hace, en orden, y con TODO el ruido necesario para diagnosticar cuando
falle:

  1. login (REST y, si el WAF lo bloquea, navegador);
  2. candado de cuenta demo;
  3. saldo, posiciones y órdenes;
  4. si --operar: compra 1 acción de un ticker barato y la cierra.

El paso 4 va detrás de una bandera EXPLÍCITA. Una prueba que abre posiciones
por defecto acaba abriéndolas cuando alguien solo quería mirar.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, config  # noqa: E402

#: Ticker de la prueba: barato, líquido y del universo del sistema.
TICKER = "F.US"

_t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:6.1f}s] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operar", action="store_true",
                    help="además de leer, abre y cierra 1 acción")
    args = ap.parse_args()

    log(f"cuenta permitida por configuración: {config.CUENTA_DEMO} "
        f"({config.TIPO_CUENTA_BROKER})")
    try:
        cred = bx.credenciales_del_entorno_o_llavero()
    except bx.ErrorBroker as exc:
        log(f"SIN CREDENCIALES: {exc}")
        return 1
    log(f"credenciales cargadas para la cuenta {cred.cuenta}")

    try:
        with bx.BrokerXTB(cred) as b:
            log("CONECTADO — candado de cuenta demo superado")
            s = b.saldo()
            log(f"SALDO {s['saldo']:,.2f} {s['divisa']} | equity {s['equity']:,.2f} "
                f"| cuenta {s['cuenta']}")
            pos = b.posiciones()
            log(f"POSICIONES ABIERTAS: {len(pos)}")
            for p in pos:
                log(f"   {p['ticker']} x{p['acciones']} @ {p['precio_entrada']}")
            log(f"ÓRDENES PENDIENTES: {len(b.ordenes_pendientes())}")

            if not args.operar:
                log("OK — prueba de solo lectura completada")
                return 0

            log(f"COMPRANDO 1 x {TICKER}...")
            e = b.comprar(TICKER, 1)
            log(f"   estado={e.estado} precio={e.precio} orden={e.orden} "
                f"error={e.error}")
            if not e.ok:
                log("la compra no llegó; no hay nada que cerrar")
                return 1

            time.sleep(4)
            tras = [p for p in b.posiciones()
                    if p["ticker"].replace(".US", "") == TICKER.replace(".US", "")]
            log(f"posiciones del ticker tras la compra: {len(tras)}")

            if tras:
                log("CERRANDO la posición de prueba...")
                c = b.vender(TICKER, int(tras[0]["acciones"]))
                log(f"   estado={c.estado} precio={c.precio} error={c.error}")
            elif e.orden:
                log(f"quedó EN COLA; cancelando la orden {e.orden}...")
                log(f"   {b.cancelar(e.orden)}")

            time.sleep(4)
            quedan = [p for p in b.posiciones()
                      if p["ticker"].replace(".US", "") == TICKER.replace(".US", "")]
            s2 = b.saldo()
            log(f"DESPUÉS — saldo {s2['saldo']:,.2f} "
                f"(variación {s2['saldo'] - s['saldo']:+.2f}) | "
                f"posiciones del ticker: {len(quedan)}")
            if quedan:
                log("¡ATENCIÓN! quedó una posición abierta de la prueba.")
                return 1
            log("OK — prueba completa: se abrió y se cerró sin dejar nada")
            return 0

    except bx.CuentaNoDemo as exc:
        log(f"CANDADO ACTIVADO (no se envió nada): {exc}")
        return 2
    except Exception:
        log("FALLO:")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
