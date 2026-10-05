"""El stop de cada posición, puesto como orden pendiente EN EL SERVIDOR de XTB.

POR QUÉ
-------
Hasta el 2026-10-05 el stop de la Cartera A solo existía en el vigilante de
precios: si el vigilante o GitHub fallaban, la posición quedaba sin protección.
Una "Orden Stop de venta" en XTB salta aunque no haya nadie mirando.

LO QUE XTB PERMITE (medido en la demo el 2026-10-05, no supuesto)
------------------------------------------------------------------
Runs 37338861588 y 37359224699 del workflow "Probar órdenes pendientes XTB",
sobre 1 acción de F.US con el mercado abierto:

* **Solo UNA orden pendiente de venta por acción.** La segunda se ACEPTA al
  enviarla (devuelve número) y XTB la descarta en silencio a los pocos segundos:
  no aparece en la lista y modificarla o cancelarla responde "Order
  modification not allowed" (código 2029). Pasa en los dos órdenes: limitada y
  luego stop, y stop y luego limitada. La primera reserva las acciones.
* Modificar el precio funciona y conserva el número de orden. Cancelar funciona.
* La lista buena es `OrderService` (`BrokerXTB.ordenes_contado`): solo trae
  las órdenes vivas. `getAllOrders` del WebSocket no ve las de contado.

EL DISEÑO QUE SALE DE ESO
-------------------------
* La STOP va en XTB (es la protección crítica: la que importa cuando nadie
  mira). Solo Cartera A: la B no tiene stop.
* El OBJETIVO lo sigue ejecutando el vigilante a mercado. Al vender, el broker
  cancela antes la stop para liberar las acciones (`BrokerXTB.vender`).
* Como "aceptada" no significa "viva", toda colocación se confirma releyendo la
  lista de XTB. Lo que no aparece, no está puesto.
* Si el stop cambia, se MODIFICA la orden existente; nunca se pone otra (XTB
  descartaría la segunda y la primera seguiría con el precio viejo).
* Si falta, se repone. Si la cantidad no es la de la posición, se cancela y se
  pone de nuevo con la exacta. Si sobra (dos stops, o una stop sin posición), se
  cancela.

QUIÉN LO HACE
-------------
El vigilante de precios en cada relectura (cada pocos minutos), y la
reconciliación del ejecutor, que además lo VERIFICA: posición sin su stop en
XTB con la cantidad y el precio correctos = rojo.
"""
from __future__ import annotations

import time
from datetime import datetime

from . import config, estado as est_mod, ordenes as ords

#: Diferencia de precio a partir de la cual se modifica la orden. Medio
#: céntimo: los precios se mandan con 2 decimales.
TOLERANCIA = 0.005
#: Cuánto se espera a que una orden recién puesta aparezca en la lista.
CONFIRMAR_SEG = 12
#: Cada cuánto se relee la lista mientras se espera.
CONFIRMAR_CADA = 2.0


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def stops_deseados(broker_posiciones: list[dict], hoy: str | None = None,
                   estado: dict | None = None) -> dict[str, dict]:
    """{simbolo: {"ticker", "acciones", "stop"}} de lo que XTB tiene abierto.

    Las acciones salen de XTB (lo que hay que proteger es lo que hay); el stop,
    del simulador o, si la compra es de hoy, de su fila en la bitácora del
    broker — igual que el vigilante. Sin stop (Cartera B, o una posición que el
    sistema no conoce), no se pide nada.
    """
    if config.CARTERA_BROKER != "A" or not config.STOP_EN_XTB:
        return {}
    hoy = hoy or datetime.now(config.TZ_ET).date().isoformat()
    estado = estado if estado is not None else est_mod.cargar()
    stops: dict[str, float] = {}
    for p in estado.get("posiciones", {}).get(config.CARTERA_BROKER, []):
        if str(p.get("fecha_entrada", "")) >= config.EJECUCION_DESDE and _f(p.get("stop")):
            stops[p["ticker"]] = _f(p["stop"])
    for f in ords.filas_de_sesion(hoy):
        if (f.get("tipo") in ords.TIPOS_COMPRA and f.get("estado") == "ejecutada"
                and f.get("ticker") not in stops and _f(f.get("stop"))):
            stops[f["ticker"]] = _f(f["stop"])
    acciones: dict[str, float] = {}
    for p in broker_posiciones:
        if p.get("lado") == "buy":
            acciones[p["ticker"]] = acciones.get(p["ticker"], 0.0) + float(p["acciones"])
    fuera = {}
    for simbolo, n in acciones.items():
        ticker = simbolo.replace(".US", "").replace("-", ".")
        if ticker in stops and int(n) >= 1:
            fuera[simbolo] = {"ticker": ticker, "acciones": int(n),
                              "stop": round(stops[ticker], 2)}
    return fuera


def _vivas_de_venta(broker) -> dict[str, list[dict]]:
    por: dict[str, list[dict]] = {}
    for o in broker.ordenes_contado()["ordenes"]:
        if o.get("lado") == "sell":
            por.setdefault(o["ticker"], []).append(o)
    return por


def _esperar_viva(broker, numero: int, dormir=time.sleep) -> dict | None:
    """La orden tal como la ve XTB, o None si no aparece (descartada)."""
    fin = time.monotonic() + CONFIRMAR_SEG
    while True:
        for o in broker.ordenes_contado()["ordenes"]:
            if o["orden"] == numero:
                return o
        if time.monotonic() >= fin:
            return None
        dormir(CONFIRMAR_CADA)


def _cancelar(broker, numeros: list[int]) -> list[str]:
    if not numeros:
        return []
    r = broker.cancelar_ordenes(numeros)
    return [f"{n}: {err}" for n, (ok, err) in r.items() if not ok]


def sincronizar(broker, deseados: dict[str, dict], log=print,
                dormir=time.sleep) -> list[dict]:
    """Deja en XTB exactamente una stop por posición, con su cantidad y precio.

    Devuelve un informe por símbolo: {"simbolo", "ticker", "stop", "acciones",
    "orden", "accion", "ok", "error"}. `accion`: ya_estaba | modificada |
    colocada | repuesta | fallo. No lanza por un símbolo: un fallo en uno no
    deja sin stop a los demás.
    """
    vivas = _vivas_de_venta(broker)
    informe: list[dict] = []

    # Stops de símbolos sin posición: ya no protegen nada y, si saltaran,
    # intentarían vender lo que no hay. Fuera.
    for simbolo, ords_sim in vivas.items():
        if simbolo in deseados:
            continue
        sobran = [o["orden"] for o in ords_sim if o.get("tipo") == "stop"]
        if sobran:
            log(f"[proteccion] {simbolo}: stop sin posición {sobran}; se cancela")
            for e in _cancelar(broker, sobran):
                log(f"[proteccion]   no se pudo cancelar {e}")

    for simbolo, d in sorted(deseados.items()):
        r = {"simbolo": simbolo, "ticker": d["ticker"], "stop": d["stop"],
             "acciones": d["acciones"], "orden": None, "accion": None,
             "ok": False, "error": None}
        try:
            mias = vivas.get(simbolo, [])
            stops = [o for o in mias if o.get("tipo") == "stop"]
            otras = [o for o in mias if o.get("tipo") != "stop"]
            # Una limitada de venta reservaría las acciones y XTB descartaría la
            # stop. El objetivo no va en XTB: si hay una, es un resto; fuera.
            quitar = [o["orden"] for o in otras]
            # Más de una stop, o una con otra cantidad: se queda la buena.
            buena = next((o for o in stops if int(o["acciones"] or 0) == d["acciones"]), None)
            quitar += [o["orden"] for o in stops if o is not buena]
            if quitar:
                log(f"[proteccion] {simbolo}: cancelo {quitar} (sobran o cantidad distinta)")
                errores = _cancelar(broker, quitar)
                if errores:
                    raise RuntimeError("no se pudo cancelar " + "; ".join(errores))
            if buena is not None:
                r["orden"] = buena["orden"]
                if abs(float(buena["precio"]) - d["stop"]) <= TOLERANCIA:
                    r["accion"], r["ok"] = "ya_estaba", True
                else:
                    m = broker.modificar_orden(buena["orden"], "stop", d["stop"])
                    if not m.ok:
                        raise RuntimeError(f"modificar {buena['orden']}: {m.estado} {m.error}")
                    dormir(1.0)
                    vista = _esperar_viva(broker, buena["orden"], dormir)
                    if vista is None or abs(float(vista["precio"]) - d["stop"]) > TOLERANCIA:
                        raise RuntimeError(
                            f"modificada {buena['orden']} y XTB enseña "
                            f"{None if vista is None else vista['precio']}")
                    r["accion"], r["ok"] = "modificada", True
                    log(f"[proteccion] {simbolo}: stop {buena['orden']} "
                        f"{buena['precio']} -> {d['stop']}")
            else:
                o = broker.poner_orden_venta(simbolo, d["acciones"], "stop", d["stop"])
                if not o.ok or not o.orden:
                    raise RuntimeError(f"poner stop: {o.estado} {o.error}")
                dormir(1.0)
                if _esperar_viva(broker, o.orden, dormir) is None:
                    raise RuntimeError(
                        f"XTB aceptó la stop {o.orden} y no aparece en su lista: la "
                        f"descartó (¿hay otra orden reservando las acciones?)")
                r["orden"], r["ok"] = o.orden, True
                r["accion"] = "colocada"
                log(f"[proteccion] {simbolo}: stop colocada en XTB, orden {o.orden}, "
                    f"{d['acciones']} acciones @ {d['stop']}")
        except Exception as exc:  # noqa: BLE001 — un símbolo no tumba a los demás
            r["accion"], r["error"] = "fallo", str(exc)[:300]
            log(f"[proteccion] {simbolo}: FALLO — {r['error']}")
        informe.append(r)
    return informe


def verificar(broker, deseados: dict[str, dict]) -> list[str]:
    """Problemas (rojo): posición sin su stop en XTB con cantidad y precio exactos."""
    vivas = _vivas_de_venta(broker)
    problemas = []
    for simbolo, d in sorted(deseados.items()):
        stops = [o for o in vivas.get(simbolo, []) if o.get("tipo") == "stop"]
        ok = [o for o in stops if int(o["acciones"] or 0) == d["acciones"]
              and abs(float(o["precio"]) - d["stop"]) <= TOLERANCIA]
        if len(stops) == 1 and ok:
            continue
        visto = ", ".join(f"#{o['orden']} x{o['acciones']} @ {o['precio']}" for o in stops) or "ninguna"
        problemas.append(
            f"{simbolo}: debería tener UNA stop de venta en XTB de {d['acciones']} "
            f"acciones a {d['stop']} y tiene: {visto}. La posición no está "
            f"protegida en el servidor.")
    return problemas


def stops_por_simbolo(broker) -> dict[str, dict]:
    """{simbolo: {"orden", "precio", "acciones"}} de las stops vivas (para la página)."""
    out = {}
    for simbolo, lista in _vivas_de_venta(broker).items():
        for o in lista:
            if o.get("tipo") == "stop":
                out[simbolo] = {"orden": o["orden"], "precio": o["precio"],
                                "acciones": o["acciones"]}
    return out


def stops_ejecutadas(broker_posiciones: list[dict], antes: dict[str, dict],
                     vivas_ahora: dict[str, dict], libro: dict[str, int]) -> list[dict]:
    """Stops que XTB ejecutó: la había, ya no está, y faltan acciones en XTB
    respecto al libro (bitacora_broker.csv). Devuelve {simbolo, acciones, precio, orden}.

    Las tres condiciones a la vez: una stop que desaparece con la posición
    intacta es una cancelación, no una venta; y unas acciones que faltan sin que
    hubiera stop las denuncia `cuadrar_libro` como venta no registrada.
    """
    reales: dict[str, int] = {}
    for p in broker_posiciones:
        if p.get("lado") == "buy":
            reales[p["ticker"]] = reales.get(p["ticker"], 0) + int(float(p["acciones"]))
    fuera = []
    for simbolo, s in antes.items():
        if simbolo in vivas_ahora:
            continue
        falta = libro.get(simbolo, 0) - reales.get(simbolo, 0)
        if falta >= 1:
            fuera.append({"simbolo": simbolo, "acciones": min(falta, int(s["acciones"] or falta)),
                          "precio": s["precio"], "orden": s["orden"]})
    return fuera


def registrar_stop_ejecutada(e: dict, hoy: str | None = None) -> ords.Orden:
    """Anota en bitacora_broker.csv la venta que hizo XTB al saltar la stop.

    El precio es el NIVEL de la stop (`precio_fuente = nivel`): la orden sale a
    mercado al tocarlo y el precio exacto de ejecución no llega por esta vía.
    Queda marcado para que nadie lo confunda con un precio de XTB.
    """
    from .broker_xtb import Ejecucion
    hoy = hoy or datetime.now(config.TZ_ET).date().isoformat()
    ticker = e["simbolo"].replace(".US", "").replace("-", ".")
    orden = ords.Orden(
        id=ords.identificador(hoy, config.CARTERA_BROKER, ticker, ords.VENTA_STOP_XTB),
        tipo=ords.VENTA_STOP_XTB, cartera=config.CARTERA_BROKER, ticker=ticker,
        acciones=int(e["acciones"]), sesion=hoy, stop=e["precio"],
        precio_simulador=e["precio"])
    ej = Ejecucion(ticker=e["simbolo"], lado="venta", acciones=int(e["acciones"]),
                   estado="ejecutada", precio=e["precio"], orden=e["orden"],
                   cuando=datetime.now(config.TZ_ET).isoformat(),
                   acciones_hechas=int(e["acciones"]), precio_fuente="nivel")
    ords.registrar_ejecucion(orden, ej)
    return orden
