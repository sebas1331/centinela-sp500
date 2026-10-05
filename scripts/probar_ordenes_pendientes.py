#!/usr/bin/env python
"""Prueba REAL de las órdenes pendientes de venta en la demo de XTB.

Responde con evidencia, sobre una posición de prueba de 1 acción barata:

  1. ¿XTB acepta a la vez una Limitada de venta y una Stop de venta por la
     MISMA cantidad, o la primera reserva las acciones y rechaza la segunda?
  2. ¿Se pueden modificar de precio y cancelar?
  3. Cuando una se ejecuta (se fuerza con un nivel artificial pegado al
     mercado), ¿qué pasa con la otra: XTB la cancela, la rechaza o la deja viva?

Y, con `--revisar`, la parte del día siguiente: lista lo que sigue vivo.

LA LISTA QUE VALE ES LA DE `OrderService` (ordenes_contado), no la de
`getAllOrders`: la primera ronda (run 37338861588) demostró que getAllOrders no
ve las órdenes de contado, y que "aceptada" en la respuesta NO significa viva:
la stop puesta después de la limitada se aceptó y luego no se pudo ni
modificar ni cancelar ("Order modification not allowed"). Por eso cada paso
enseña el ESTADO de cada orden según XTB unos segundos después.

Al terminar deja la cuenta como estaba: sin la posición de prueba y sin
órdenes vivas suyas.

Solo se lanza a mano (workflow "Probar órdenes pendientes XTB"), y operar exige
`--operar`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx  # noqa: E402

TICKER = "F.US"
_t0 = time.time()
CONCLUSIONES: dict = {}


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:6.1f}s] {msg}", flush=True)


def _mismo(ticker: str) -> bool:
    return str(ticker).upper().replace(".US", "") == TICKER.upper().replace(".US", "")


def foto(b, etiqueta: str, solo: set[int] | None = None) -> dict:
    """Posiciones y órdenes de F.US, con el estado de cada orden según XTB.

    Devuelve {orden: estado} de las órdenes de F.US (todas, vivas o no)."""
    pos = [p for p in b.posiciones() if _mismo(p["ticker"])]
    log(f"--- {etiqueta}: posiciones {TICKER}="
        f"{[(p['acciones'], p['lado'], p['orden']) for p in pos]}")
    oc = b.ordenes_contado()
    estados = {}
    for o in oc["todas"]:
        if not _mismo(o["ticker"]):
            continue
        if solo is not None and o["orden"] not in solo:
            continue
        estados[o["orden"]] = o["estado"]
        log(f"      orden {o['orden']} {o['tipo']}/{o['lado']} x{o['acciones']} "
            f"@ {o['precio']} -> {o['estado']}")
    if TICKER in oc["reglas"]:
        log(f"      reglas {TICKER}: {oc['reglas'][TICKER]}")
    return {"estados": estados, "posiciones": pos, "vivas": [
        o for o in oc["ordenes"] if _mismo(o["ticker"])]}


def esperar(cond, segundos: int, cada: float = 5.0) -> bool:
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(cada)
    return cond()


def estado_de(b, numero) -> str | None:
    if not numero:
        return None
    for o in b.ordenes_contado()["todas"]:
        if o["orden"] == numero:
            return o["estado"]
    return "NO_LISTADA"


def poner(b, tipo: str, precio: float):
    o = b.poner_orden_venta(TICKER, 1, tipo, precio)
    log(f"PONER {tipo} venta 1 @ {precio}: {o.estado} orden={o.orden} error={o.error}")
    time.sleep(4)
    o.xtb = estado_de(b, o.orden)
    log(f"   estado en XTB a los 4 s: {o.xtb}")
    return o


def limpiar(b) -> bool:
    """Cancela las órdenes vivas de F.US y vende lo que quede."""
    for intento in range(3):
        f = foto(b, f"limpieza {intento + 1}")
        vivas = [o["orden"] for o in f["vivas"]]
        if vivas:
            log(f"   cancelando vivas {vivas}: {b.cancelar_ordenes(vivas)}")
            time.sleep(4)
            f = foto(b, "tras cancelar")
        for p in f["posiciones"]:
            if p["lado"] == "buy":
                c = b.vender(TICKER, int(p["acciones"]))
                log(f"   venta de limpieza: {c.estado} precio={c.precio} error={c.error}")
        time.sleep(5)
        f = foto(b, "comprobación final")
        if not any(p["lado"] == "buy" for p in f["posiciones"]) and not f["vivas"]:
            return True
    return False


def hay_posicion(b) -> bool:
    return any(p["lado"] == "buy" for p in b.posiciones() if _mismo(p["ticker"]))


def prueba(b) -> int:
    f = foto(b, "antes de empezar")
    if f["vivas"] or any(p["lado"] == "buy" for p in f["posiciones"]):
        log("Hay restos de una prueba anterior; se limpian primero.")
        if not limpiar(b):
            log("NO se pudo limpiar; no se empieza.")
            return 1

    q = b.cotizacion(TICKER)
    bid = q["bid"]
    log(f"cotización {TICKER}: bid={bid} ask={q['ask']}")

    e = b.comprar(TICKER, 1)
    log(f"COMPRA 1 x {TICKER}: {e.estado} precio={e.precio} orden={e.orden} error={e.error}")
    if not e.ok or not esperar(lambda: hay_posicion(b), 30):
        log("la compra no aparece como posición; se para")
        return 1

    lejos_lim, lejos_stop = round(bid * 1.30, 2), round(bid * 0.70, 2)

    # --- 1. las dos sobre la misma acción --------------------------------
    lim = poner(b, "limitada", lejos_lim)
    stp = poner(b, "stop", lejos_stop)
    time.sleep(6)
    f = foto(b, "las dos puestas, +10 s")
    lim.xtb, stp.xtb = f["estados"].get(lim.orden), f["estados"].get(stp.orden)
    vivas = {"ACCEPTED", "NEW", "PENDING_NEW"}
    CONCLUSIONES["A_limitada_luego_stop"] = {"limitada": lim.xtb, "stop": stp.xtb}

    # --- 2. el orden inverso: stop primero -------------------------------
    if lim.xtb in vivas:
        r = b.cancelar_ordenes([lim.orden])
        log(f"CANCELAR limitada {lim.orden}: {r}")
        time.sleep(4)
        CONCLUSIONES["cancelar_limitada"] = {"respuesta": r.get(lim.orden),
                                             "estado": estado_de(b, lim.orden)}
    if stp.xtb in vivas:
        r = b.cancelar_ordenes([stp.orden])
        log(f"CANCELAR stop {stp.orden}: {r}")
        time.sleep(4)
        CONCLUSIONES["cancelar_stop_con_limitada"] = {"respuesta": r.get(stp.orden),
                                                      "estado": estado_de(b, stp.orden)}
    foto(b, "tras cancelar las dos")
    stp = poner(b, "stop", lejos_stop)
    lim = poner(b, "limitada", lejos_lim)
    time.sleep(6)
    f = foto(b, "stop primero y limitada después, +10 s")
    lim.xtb, stp.xtb = f["estados"].get(lim.orden), f["estados"].get(stp.orden)
    CONCLUSIONES["B_stop_luego_limitada"] = {"stop": stp.xtb, "limitada": lim.xtb}
    ambas = lim.xtb in vivas and stp.xtb in vivas
    CONCLUSIONES["dos_a_la_vez"] = "aceptadas" if ambas else (
        "solo una" if (lim.xtb in vivas or stp.xtb in vivas) else "ninguna")
    log(f"==> DOS ÓRDENES SOBRE LA MISMA CANTIDAD: {CONCLUSIONES['dos_a_la_vez']}")

    # --- 3. modificar ----------------------------------------------------
    for o, tipo, nuevo in ((lim, "limitada", round(bid * 1.25, 2)),
                           (stp, "stop", round(bid * 0.75, 2))):
        if o.xtb not in vivas:
            continue
        m = b.modificar_orden(o.orden, tipo, nuevo)
        log(f"MODIFICAR {tipo} {o.orden} -> {nuevo}: {m.estado} orden={m.orden} error={m.error}")
        time.sleep(4)
        precio = next((x["precio"] for x in b.ordenes_contado()["todas"]
                       if x["orden"] == o.orden), None)
        CONCLUSIONES[f"modificar_{tipo}"] = {"respuesta": m.estado, "error": m.error,
                                             "mismo_numero": m.orden == o.orden,
                                             "precio_en_xtb": precio,
                                             "estado": estado_de(b, o.orden)}
    foto(b, "tras modificar")

    # --- 4. OCO: forzar la limitada y mirar la stop ---------------------
    if lim.xtb in vivas:
        q = b.cotizacion(TICKER)
        nivel = round(q["bid"] * 0.99, 2)
        m = b.modificar_orden(lim.orden, "limitada", nivel)
        log(f"FORZAR limitada a {nivel} (bid {q['bid']}): {m.estado} error={m.error}")
        if not m.ok:
            # Si no deja modificarla, se cancela y se pone otra ya ejecutable.
            r = b.cancelar_ordenes([lim.orden])
            log(f"   no se pudo modificar; cancelo {lim.orden}: {r}")
            time.sleep(3)
            lim = poner(b, "limitada", nivel)
        ejecutada = esperar(lambda: not hay_posicion(b), 90)
        f = foto(b, "tras forzar la limitada")
        CONCLUSIONES["limitada_forzada"] = {"posicion_cerrada": ejecutada,
                                            "estado_limitada": f["estados"].get(lim.orden)}
        if ejecutada:
            evol = []
            for i in range(4):
                time.sleep(15)
                evol.append(estado_de(b, stp.orden))
                log(f"   la stop {stp.orden}, +{15 * (i + 1)} s: {evol[-1]}")
            CONCLUSIONES["otra_pata"] = evol
            final = evol[-1]
            CONCLUSIONES["al_ejecutarse_una"] = (
                "viva" if final in vivas else
                "cancelada por XTB" if final == "CANCELED" else
                "rechazada" if final == "REJECTED" else final)
            log(f"==> AL EJECUTARSE UNA, LA OTRA: {CONCLUSIONES['al_ejecutarse_una']}")
            if final in vivas:
                r = b.cancelar_ordenes([stp.orden])
                time.sleep(4)
                CONCLUSIONES["cancelar_otra_pata"] = {"respuesta": r.get(stp.orden),
                                                      "estado": estado_de(b, stp.orden)}
                log(f"   cancelada a mano: {CONCLUSIONES['cancelar_otra_pata']}")
        else:
            log("la limitada no se ejecutó con el nivel pegado al mercado")
    return 0


def revisar(b) -> int:
    """Lo que hay ahora: posiciones y órdenes de contado (todas, con estado)."""
    for p in b.posiciones():
        log(f"posición {p['ticker']} x{p['acciones']} {p['lado']} ({p['orden']})")
    oc = b.ordenes_contado()
    log(f"ÓRDENES DE CONTADO: {len(oc['todas'])} en la lista, {len(oc['ordenes'])} vivas")
    for o in oc["todas"]:
        log(f"   {o['orden']} {o['ticker']} {o['tipo']}/{o['lado']} x{o['acciones']} "
            f"@ {o['precio']} -> {o['estado']} (creada {o['creada']}, vence {o['vence']})")
    for s, r in oc["reglas"].items():
        log(f"   reglas {s}: {r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operar", action="store_true", help="hacer la prueba (abre y cierra 1 acción)")
    ap.add_argument("--revisar", action="store_true", help="solo listar")
    ap.add_argument("--limpiar", action="store_true",
                    help="cancelar las órdenes vivas de F.US y cerrar su posición")
    args = ap.parse_args()
    try:
        cred = bx.credenciales_del_entorno_o_llavero()
        with bx.BrokerXTB(cred) as b:
            log("CONECTADO — candado de cuenta demo superado")
            revisar(b)
            if args.limpiar:
                ok = limpiar(b)
                log(f"LIMPIEZA: {'cuenta limpia' if ok else '¡QUEDAN RESTOS!'}")
                return 0 if ok else 1
            if not args.operar:
                return 0
            rc = 1
            try:
                rc = prueba(b)
            except Exception:
                log("FALLO en la prueba:")
                traceback.print_exc()
            finally:
                limpio = limpiar(b)
                log(f"LIMPIEZA: {'cuenta limpia' if limpio else '¡QUEDAN RESTOS!'}")
                log("CONCLUSIONES " + json.dumps(CONCLUSIONES, default=str, sort_keys=True))
                if not limpio:
                    rc = 1
            return rc
    except bx.CuentaNoDemo as exc:
        log(f"CANDADO ACTIVADO (no se envió nada): {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
