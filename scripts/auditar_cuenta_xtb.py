#!/usr/bin/env python
"""Qué tiene la cuenta demo de verdad, y si alguna orden ambigua llegó a entrar.

POR QUÉ EL SALDO ES LA PRUEBA
-----------------------------
El cliente no oficial no expone el historial de operaciones: solo posiciones
abiertas, órdenes en cola y saldo. Así que para saber si una compra que volvió
`ambigua` llegó a ejecutarse hay tres caminos, y los tres se recorren aquí:

  1. ¿Está la posición abierta ahora? -> se ve en posiciones.
  2. ¿Quedó una orden esperando? -> se ve en órdenes en cola.
  3. ¿Se ejecutó y ya se cerró? -> NO se ve en ninguna de las dos, pero deja
     huella en el SALDO: cada ida y vuelta se come el spread. Comparando el
     saldo con el que debería haber, un viaje que no está registrado aparece
     como un hueco.

El tercero es el que importa, porque es el único caso en que una orden ambigua
podría haber movido dinero sin que quede constancia en ningún sitio.

Solo LEE, salvo que se le pase `--cerrar`, que cierra las posiciones que no
deberían estar ahí.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (broker_xtb as bx, config, estado as est_mod,  # noqa: E402
                       ordenes as ords)


def log(msg: str) -> None:
    print(msg, flush=True)


def esperadas() -> set[str]:
    """Los símbolos que el simulador dice que XTB debería tener.

    Las heredadas del paper trading no cuentan: son anteriores al arranque del
    ejecutor y nunca existieron en el broker.
    """
    estado = est_mod.cargar()
    return {
        ords.simbolo_xtb(p["ticker"])
        for p in estado.get("posiciones", {}).get(config.CARTERA_BROKER, [])
        if str(p.get("fecha_entrada", "")) >= config.EJECUCION_DESDE
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cerrar", action="store_true",
                    help="cierra las posiciones que no deberían estar")
    ap.add_argument("--saldo-esperado", type=float, default=None,
                    help="saldo que debería haber según la bitácora")
    args = ap.parse_args()

    problemas: list[str] = []
    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        saldo = b.saldo()
        log(f"cuenta {saldo['cuenta']} (DEMO)")
        log(f"  saldo   {saldo['saldo']:,.2f} {saldo['divisa']}")
        log(f"  equity  {saldo['equity']:,.2f}")

        posiciones = [p for p in b.posiciones() if p["lado"] == "buy"]
        cola = b.ordenes_pendientes()
        debe_tener = esperadas()

        log("")
        log(f"POSICIONES ABIERTAS EN XTB: {len(posiciones)}")
        for p in posiciones:
            marca = "ok" if p["ticker"] in debe_tener else "NO DEBERÍA ESTAR"
            log(f"  {p['ticker']:10} x{p['acciones']:<6.0f} entrada "
                f"{p['precio_entrada']:>9.2f}  actual {p['precio_actual']:>9.2f}"
                f"  P&L {p['pnl']:>8.2f}   [{marca}]")
        log(f"  el simulador espera: {sorted(debe_tener) or '(ninguna)'}")

        log("")
        log(f"ÓRDENES EN COLA: {len(cola)}")
        for o in cola:
            log(f"  {o['ticker']:10} x{o['acciones']:<6.0f} {o['lado']:6} "
                f"precio {o['precio']}  orden {o['orden']}")

        sobrantes = [p for p in posiciones if p["ticker"] not in debe_tener]
        if sobrantes and args.cerrar:
            log("")
            log("CERRANDO LO QUE NO DEBERÍA ESTAR")
            for p in sobrantes:
                log(f"  cerrando {p['acciones']:.0f} de {p['ticker']}...")
                e = b.vender(p["ticker"], int(p["acciones"]))
                log(f"    -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
                    + (f" ERROR: {e.error}" if e.error else ""))
                if not e.ok:
                    problemas.append(
                        f"NO se pudo cerrar {p['ticker']}: {e.error}. Ciérrala "
                        f"a mano en xStation 5.")
        elif sobrantes:
            problemas.append(
                f"{len(sobrantes)} posición(es) que el simulador no espera: "
                + ", ".join(p["ticker"] for p in sobrantes)
                + ". Lánzalo con --cerrar para cerrarlas.")

        if cola and args.cerrar:
            log("")
            log("CANCELANDO ÓRDENES EN COLA")
            for o in cola:
                log(f"  cancelando {o['orden']} de {o['ticker']}: "
                    f"{b.cancelar(o['orden'])}")

        # --- La huella del dinero ------------------------------------------
        if args.saldo_esperado is not None:
            hueco = saldo["saldo"] - args.saldo_esperado
            log("")
            log(f"SALDO: hay {saldo['saldo']:,.2f}, se esperaba "
                f"{args.saldo_esperado:,.2f} (diferencia {hueco:+,.2f})")
            # Un céntimo de margen: XTB redondea. Más que eso es un viaje de
            # ida y vuelta sin registrar.
            if abs(hueco) > 0.01:
                problemas.append(
                    f"El saldo no cuadra por {hueco:+,.2f}. Si es negativo, "
                    f"alguna orden ambigua SÍ se ejecutó y se cerró sin quedar "
                    f"registrada en bitacora_broker.csv.")
            else:
                log("  cuadra: ninguna orden ambigua movió dinero sin registrar.")

    log("")
    if problemas:
        for p in problemas:
            print(f"::error::{p}", flush=True)
        log(f"❌ {len(problemas)} problema(s).")
        return 1
    log("✅ la cuenta está limpia y cuadra con lo que el simulador espera.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
