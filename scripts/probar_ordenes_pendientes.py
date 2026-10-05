#!/usr/bin/env python
"""Prueba REAL de las órdenes pendientes de venta en la demo de XTB.

Responde con evidencia, sobre una posición de prueba de 1 acción barata:

  1. ¿XTB acepta a la vez una Limitada de venta y una Stop de venta por la
     MISMA cantidad, o la primera reserva las acciones y rechaza la segunda?
  2. ¿Se pueden modificar de precio y cancelar?
  3. Cuando una se ejecuta (se fuerza con un nivel artificial pegado al
     mercado), ¿qué pasa con la otra: XTB la cancela, la rechaza o la deja viva?

Y, con `--revisar`, la parte del día siguiente: lista lo que sigue en cola.

Todo va con el dato crudo de XTB al lado (lo que devuelve `getAllOrders`), para
que la conclusión no dependa de cómo lo interprete el cliente. Al terminar deja
la cuenta como estaba: sin la posición de prueba y sin órdenes suyas.

Solo se lanza a mano (workflow "Probar órdenes pendientes"), y operar exige
`--operar`: una prueba que opera por defecto acaba operando cuando alguien solo
quería mirar.
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
#: Todo número de orden que XTB nos haya dado, por si la lista usa otro id.
PUESTAS: set[int] = set()


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:6.1f}s] {msg}", flush=True)


def _mismo(ticker: str) -> bool:
    return ticker.upper().replace(".US", "") == TICKER.upper().replace(".US", "")


def crudo(b) -> list[dict]:
    """Los elementos de `getAllOrders` tal cual llegan, solo los del ticker."""
    from xtb_api.types.enums import SubscriptionEid
    ws = b._cliente.ws
    res = b._ejecutar(ws.send("getAllOrders",
                              {"getAndSubscribeElement": {"eid": SubscriptionEid.ORDERS}}))
    out = []
    for el in ws._extract_elements(res):
        t = (el or {}).get("value", {}).get("xcfdtrade") or {}
        if _mismo(str(t.get("symbol", ""))):
            out.append(t)
    return out


def foto(b, etiqueta: str) -> tuple[list[dict], list[dict]]:
    pos = [p for p in b.posiciones() if _mismo(p["ticker"])]
    ords = [o for o in b.ordenes_pendientes() if _mismo(o["ticker"])]
    log(f"--- {etiqueta}: posiciones {TICKER}={[(p['acciones'], p['lado'], p['orden']) for p in pos]}")
    for o in ords:
        log(f"      orden {o['orden']} lado={o['lado']} tipo={o['tipo']} "
            f"acciones={o['acciones']} precio={o['precio']}")
    try:
        for t in crudo(b):
            log("      crudo: " + json.dumps(t, default=str, sort_keys=True))
    except Exception as exc:  # el crudo es evidencia extra, no condición
        log(f"      (no se pudo leer el crudo: {exc!r})")
    return pos, ords


def limpiar(b) -> bool:
    """Cancela las órdenes del ticker y vende lo que quede. True si queda limpio."""
    if PUESTAS:
        log(f"   cancelando las que pusimos {sorted(PUESTAS)}: "
            f"{b.cancelar_ordenes(sorted(PUESTAS))}")
        PUESTAS.clear()
        time.sleep(2)
    for intento in range(3):
        pos, ords = foto(b, f"limpieza {intento + 1}")
        if ords:
            log(f"   cancelando {[o['orden'] for o in ords]}: "
                f"{b.cancelar_ordenes([int(o['orden']) for o in ords])}")
            time.sleep(3)
            pos, ords = foto(b, "tras cancelar")
        for p in pos:
            if p["lado"] == "buy":
                c = b.vender(TICKER, int(p["acciones"]))
                log(f"   venta de limpieza: {c.estado} precio={c.precio} error={c.error}")
            else:
                c = b.comprar(TICKER, int(p["acciones"]))
                log(f"   ¡había una CORTA! recompra: {c.estado} error={c.error}")
        time.sleep(4)
        pos, ords = foto(b, "comprobación final")
        if not pos and not ords:
            return True
    return False


def _anotar(o):
    if o is not None and getattr(o, "orden", None):
        PUESTAS.add(int(o.orden))
    return o


def esperar(cond, segundos: int, cada: float = 5.0) -> bool:
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(cada)
    return cond()


def prueba(b) -> int:
    pos, ords = foto(b, "antes de empezar")
    if pos or ords:
        log("Hay restos de una prueba anterior; se limpian primero.")
        if not limpiar(b):
            log("NO se pudo limpiar; no se empieza.")
            return 1

    q = b.cotizacion(TICKER)
    bid = q["bid"]
    log(f"cotización {TICKER}: bid={bid} ask={q['ask']}")

    # 1. posición de prueba
    e = b.comprar(TICKER, 1)
    log(f"COMPRA 1 x {TICKER}: {e.estado} precio={e.precio} orden={e.orden} error={e.error}")
    if not e.ok:
        return 1
    if not esperar(lambda: any(p["lado"] == "buy" for p in b.posiciones() if _mismo(p["ticker"])), 30):
        log("la compra no aparece como posición; se limpia y se para")
        limpiar(b)
        return 1
    foto(b, "tras la compra")

    # 2. las dos órdenes sobre la MISMA cantidad (niveles lejanos: no se tocan)
    lim_lejos, stop_lejos = round(bid * 1.30, 2), round(bid * 0.70, 2)
    lim = _anotar(b.poner_orden_venta(TICKER, 1, "limitada", lim_lejos))
    log(f"LIMITADA venta 1 @ {lim_lejos}: {lim.estado} orden={lim.orden} error={lim.error}")
    stp = _anotar(b.poner_orden_venta(TICKER, 1, "stop", stop_lejos))
    log(f"STOP venta 1 @ {stop_lejos}: {stp.estado} orden={stp.orden} error={stp.error}")
    time.sleep(3)
    _, ords = foto(b, "tras poner las dos")
    vivas = {int(o["orden"]) for o in ords if o["orden"] is not None}
    CONCLUSIONES["limitada_aceptada"] = lim.ok
    CONCLUSIONES["stop_aceptada"] = stp.ok
    CONCLUSIONES["numeros"] = {"limitada": lim.orden, "stop": stp.orden}
    CONCLUSIONES["en_lista"] = sorted(vivas)
    CONCLUSIONES["dos_a_la_vez"] = (
        "aceptadas" if lim.ok and stp.ok else "solo una" if lim.ok or stp.ok else "ninguna")
    log(f"==> DOS ÓRDENES SOBRE LA MISMA CANTIDAD: {CONCLUSIONES['dos_a_la_vez']}")

    # Si la stop no entró con la limitada puesta, ¿entra sola? (orden inverso)
    if lim.ok and not stp.ok:
        r = b.cancelar_ordenes([lim.orden])
        log(f"   cancelo la limitada para probar la stop sola: {r}")
        time.sleep(2)
        stp = _anotar(b.poner_orden_venta(TICKER, 1, "stop", stop_lejos))
        log(f"   STOP sola: {stp.estado} orden={stp.orden} error={stp.error}")
        lim2 = _anotar(b.poner_orden_venta(TICKER, 1, "limitada", lim_lejos))
        log(f"   y la LIMITADA después de la stop: {lim2.estado} error={lim2.error}")
        CONCLUSIONES["orden_inverso"] = {"stop_sola": stp.ok, "limitada_despues": lim2.ok}
        lim = lim2 if lim2.ok else lim
        if not lim2.ok:
            lim.orden = None

    # 3. modificar precio
    if lim.ok and lim.orden:
        m = _anotar(b.modificar_orden(lim.orden, "limitada", round(bid * 1.25, 2)))
        log(f"MODIFICAR limitada -> {round(bid * 1.25, 2)}: {m.estado} orden={m.orden} error={m.error}")
        CONCLUSIONES["modificar_limitada"] = m.ok
        CONCLUSIONES["modificar_mantiene_numero"] = (m.orden == lim.orden)
        if m.orden and m.orden != lim.orden:
            log(f"   OJO: modificar devolvió OTRO número ({m.orden}); se sigue con él")
            lim.orden = m.orden
    if stp.ok and stp.orden:
        m = _anotar(b.modificar_orden(stp.orden, "stop", round(bid * 0.75, 2)))
        log(f"MODIFICAR stop -> {round(bid * 0.75, 2)}: {m.estado} orden={m.orden} error={m.error}")
        CONCLUSIONES["modificar_stop"] = m.ok
        if m.orden and m.orden != stp.orden:
            stp.orden = m.orden
    time.sleep(3)
    _, ords = foto(b, "tras modificar")
    precios = {int(o["orden"]): o["precio"] for o in ords if o["orden"] is not None}
    CONCLUSIONES["precios_en_lista_tras_modificar"] = precios

    # 4. cancelar (la stop) y reponerla
    if stp.ok and stp.orden:
        r = b.cancelar_ordenes([stp.orden])
        log(f"CANCELAR stop {stp.orden}: {r}")
        time.sleep(3)
        _, ords = foto(b, "tras cancelar la stop")
        CONCLUSIONES["cancelar"] = (r.get(stp.orden, (False,))[0]
                                    and all(int(o["orden"]) != stp.orden for o in ords if o["orden"]))
        # cancelar dos veces: ¿dice que no la encuentra? (para no confundir
        # "cancelada" con "ya no existía")
        r2 = b.cancelar_ordenes([stp.orden])
        log(f"CANCELAR otra vez la misma: {r2}")
        CONCLUSIONES["cancelar_inexistente"] = r2.get(stp.orden)
        stp = _anotar(b.poner_orden_venta(TICKER, 1, "stop", stop_lejos))
        log(f"REPONER stop @ {stop_lejos}: {stp.estado} orden={stp.orden} error={stp.error}")

    # 5. OCO: forzar que la limitada se ejecute y mirar la stop
    if lim.ok and lim.orden:
        q = b.cotizacion(TICKER)
        ejecutada = False
        for nivel in (round(q["bid"] * 0.99, 2), round(q["bid"], 2)):
            m = _anotar(b.modificar_orden(lim.orden, "limitada", nivel))
            log(f"FORZAR limitada a {nivel} (bid {q['bid']}): {m.estado} error={m.error}")
            if m.orden and m.orden != lim.orden:
                lim.orden = m.orden
            if m.ok and esperar(lambda: not any(
                    p["lado"] == "buy" for p in b.posiciones() if _mismo(p["ticker"])), 120):
                ejecutada = True
                break
        CONCLUSIONES["limitada_ejecutada"] = ejecutada
        if ejecutada:
            log("==> la limitada se ejecutó: la posición desapareció")
            estados = []
            for i in range(4):
                time.sleep(20)
                pos, ords = foto(b, f"la otra pata, +{20 * (i + 1)} s")
                viva = any(o["orden"] is not None and int(o["orden"]) == stp.orden for o in ords)
                estados.append({"viva": viva, "posiciones": [(p['acciones'], p['lado']) for p in pos]})
            CONCLUSIONES["otra_pata"] = estados
            ultima = estados[-1]
            if ultima["viva"]:
                CONCLUSIONES["al_ejecutarse_una"] = "viva"
            elif stp.ok:
                CONCLUSIONES["al_ejecutarse_una"] = "cancelada por XTB"
            log(f"==> AL EJECUTARSE UNA, LA OTRA: {CONCLUSIONES.get('al_ejecutarse_una')}")
            if stp.ok and stp.orden:
                r = b.cancelar_ordenes([stp.orden])
                log(f"cancelar la otra pata tras la ejecución: {r}")
                CONCLUSIONES["cancelar_otra_pata"] = r.get(stp.orden)
        else:
            log("la limitada NO llegó a ejecutarse con niveles pegados al mercado")
    return 0


def revisar(b) -> int:
    """Día siguiente: qué sigue en cola, con el crudo."""
    ords = b.ordenes_pendientes()
    log(f"ÓRDENES PENDIENTES: {len(ords)}")
    for o in ords:
        log(f"   {o}")
    for p in b.posiciones():
        log(f"   posición {p['ticker']} x{p['acciones']} {p['lado']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operar", action="store_true", help="hacer la prueba (abre y cierra 1 acción)")
    ap.add_argument("--revisar", action="store_true", help="solo listar órdenes pendientes")
    args = ap.parse_args()
    try:
        cred = bx.credenciales_del_entorno_o_llavero()
        with bx.BrokerXTB(cred) as b:
            log("CONECTADO — candado de cuenta demo superado")
            if args.revisar or not args.operar:
                return revisar(b)
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
