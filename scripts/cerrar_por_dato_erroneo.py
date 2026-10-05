#!/usr/bin/env python
"""Cierra una posición cuya serie de precios no era real, en XTB y en el simulador.

POR QUÉ EXISTE
--------------
El 2026-10-02 el sistema compró 134 acciones de CTVA porque vio un drawdown del
86 %. Ese drawdown no existía: era una escisión que yfinance no reajustó (ver
`centinela/continuidad.py`). El screener ya no vuelve a mirar esa serie, pero la
posición se quedó abierta, con un objetivo en +103 % y un stop puesto a partir
de un ATR que valía el 51 % del precio. Dejarla correr es dejar que un artefacto
de datos decida cuándo y a cuánto se vende.

Esto la cierra. A mercado, en el broker y en el simulador, con motivo propio
—`dato_erroneo`— para que no se confunda nunca con una salida que la estrategia
sí decidió.

LAS DOS PATAS VAN JUNTAS, A PROPÓSITO
-------------------------------------
Cerrar solo en XTB dejaría al simulador con una posición que ya no existe y la
reconciliación del post-cierre gritaría cada mañana. Cerrar solo en el
simulador dejaría 134 acciones vivas en el broker sin nadie que las vigile. Así
que el script hace las dos o no hace ninguna: si XTB no tiene la posición que
dice el estado, se para en rojo sin tocar nada (salvo `--solo-simulador`, que es
para una posición que el broker nunca llegó a comprar).

SOLO LO DECLARADO
-----------------
Únicamente cierra tickers que estén en `estado/datos_erroneos.json` con una
ventana viva. Un cierre manual sin esa declaración sería una venta discrecional
con otro nombre, y aquí la discrecionalidad tiene que dejar rastro ANTES de
operar, no después.

NO ES UN COMPONENTE DEL SISTEMA
-------------------------------
No se anota en `estado/salud.json`: los componentes de esa página son
periódicos y se juzgan por si llegaron a tiempo. Esto es una acción manual de
una vez. Queda en `bitacora_broker.csv`, en la bitácora y en el commit.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (ambiguas as amb, bitacora, broker_xtb as bx,  # noqa: E402
                       calendario, config, datos, datos_erroneos,
                       ejecucion, estado as est_mod, estado_broker, niveles,
                       ordenes as ords, simulador)


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%H:%M:%S ET}] {msg}", flush=True)


def exigir_declarado(ticker: str) -> dict:
    """La ventana de serie rota que autoriza este cierre, o se para en rojo."""
    for v in datos_erroneos.cargar():
        if str(v["ticker"]).upper() != ticker.upper():
            continue
        if v.get("hasta"):
            continue                      # ventana ya cerrada: la serie volvió
        return v
    raise RuntimeError(
        f"{ticker} no está declarado en {datos_erroneos.ARCHIVO.name} con una "
        f"ventana viva. Este script solo cierra lo que ya está declarado como "
        f"dato erróneo, con su causa y su evidencia: sin eso, esto sería una "
        f"venta discrecional con otro nombre.")


def posicion_en_xtb(broker, ticker: str) -> dict | None:
    simbolo = ords.simbolo_xtb(ticker)
    for p in broker.posiciones():
        if p["lado"] == "buy" and p["ticker"] == simbolo:
            return p
    return None


def vender(broker, ticker: str, posicion: dict, sesion: str) -> float:
    """Vende a mercado todo el volumen. Devuelve el precio ejecutado."""
    simbolo = posicion["ticker"]
    acciones = int(posicion["acciones"])
    id_orden = ords.identificador(sesion, config.CARTERA_BROKER, ticker,
                                  ords.VENTA_DATO_ERRONEO)
    registro = ords.cargar_enviadas()
    if ords.ya_enviada(registro, id_orden):
        raise RuntimeError(
            f"La venta por dato erróneo de {ticker} ya se envió hoy "
            f"({id_orden}). No se repite: si la posición sigue abierta, hay que "
            f"mirar qué pasó con aquella orden antes de mandar otra.")
    if not niveles.sigue_abierta(broker, simbolo, acciones):
        raise RuntimeError(
            f"XTB ya no tiene {acciones} acciones de {simbolo}. Alguien movió "
            f"la posición entre que se leyó y se iba a vender; no se vende a "
            f"ciegas.")

    # Precio de referencia: el bid que se vio justo antes de mandar. NO se pone
    # `precio_simulador`, porque esta salida no tiene nivel en el simulador —el
    # simulador registra exactamente lo que ejecute el broker—, y un slippage
    # calculado contra el propio precio ejecutado sería un cero decorativo.
    try:
        bid = float(broker.cotizacion(simbolo)["bid"])
    except Exception as exc:  # noqa: BLE001
        log(f"sin cotización previa de {simbolo} ({exc!r}); se vende igual.")
        bid = None

    orden = ords.Orden(
        id=id_orden, tipo=ords.VENTA_DATO_ERRONEO, cartera=config.CARTERA_BROKER,
        ticker=ticker, acciones=acciones, sesion=sesion, referencia=bid)
    log(f"vendiendo {acciones} acciones de {simbolo} a mercado"
        + (f" (bid {bid})" if bid else "") + "...")
    e = amb.enviar_resolviendo(broker, simbolo, ords.VENTA_DATO_ERRONEO,
                               lambda: broker.vender(simbolo, acciones),
                               id_orden=id_orden)
    log(f"  -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
        + (f" (orden {e.orden})" if e.orden else "")
        + (f" ERROR: {e.error}" if e.error else ""))
    ords.registrar_ejecucion(orden, e)
    if not e.ok:
        raise RuntimeError(
            f"La venta por dato erróneo de {ticker} NO llegó al broker: "
            f"{e.estado} — {e.error}. La posición sigue abierta.")
    ords.marcar_enviada(registro, id_orden,
                        {"estado": e.estado, "orden": e.orden, "bid": bid})
    ords.guardar_enviadas(registro)
    if e.precio:
        return float(e.precio)
    # XTB no siempre devuelve precio en la confirmación. El bid de antes de
    # mandar es la mejor referencia que queda, y queda dicho que es eso.
    log("XTB no devolvió precio de ejecución; se usa el bid previo para el "
        "simulador y queda anotado en las notas de la bitácora.")
    if bid is None:
        raise RuntimeError(
            f"La venta de {ticker} se ejecutó pero no hay ni precio de "
            f"ejecución ni bid previo: el simulador no puede registrar la "
            f"salida sin un precio. Hay que mirarlo a mano en xStation 5.")
    return bid


def abrir_pendiente_si_hace_falta(estado: dict, ticker: str, sesion: str) -> None:
    """Convierte la entrada pendiente de hoy en posición, como haría el post-cierre.

    CTVA se compró en el broker por la mañana y en el simulador seguía en
    `entradas_pendientes`: el post-cierre la convierte en posición al cerrar la
    sesión. Si se cerrara el broker y se borrara la pendiente, el simulador se
    quedaría sin ninguna huella de una operación que SÍ ocurrió —el dinero de la
    demo se movió— y la bitácora dejaría de cuadrar con el broker.

    Así que se abre primero, con la MISMA función que usa el post-cierre, para
    que la entrada quede con el precio, el objetivo y el stop que el sistema
    habría registrado esta noche, y se cierra después. Lo que se publica no es
    una operación inventada: es la que hubo, con su salida por dato erróneo.
    """
    pendientes = [e for e in estado.get("entradas_pendientes", [])
                  if e["ticker"] == ticker]
    if not pendientes:
        return
    precios = datos.actualizar_precios([ticker])
    abiertas = simulador.ejecutar_entradas_pendientes(estado, precios, sesion)
    nuestras = [p for p in abiertas if p["ticker"] == ticker]
    if not nuestras:
        raise RuntimeError(
            f"{ticker} estaba pendiente de entrada y no se pudo abrir en el "
            f"simulador (¿falta la barra de su sesión de decisión?). Sin la "
            f"entrada no se puede registrar la salida.")
    for p in nuestras:
        log(f"  simulador: abierta {p['portafolio']} id={p['id']} a "
            f"{p['entrada']} (objetivo {p['objetivo']}, stop {p['stop']})")


def cerrar_en_simulador(estado: dict, ticker: str, precio: float, sesion: str,
                         nota: str) -> list[dict]:
    """Cierra en la bitácora y en el estado todas las posiciones del ticker."""
    ahora = datetime.now(config.TZ_ET)
    hora = ahora.strftime("%Y-%m-%d %H:%M:%S %Z")
    cerradas = []
    for cart in ("A", "B"):
        siguen = []
        for pos in estado["posiciones"].get(cart, []):
            if pos["ticker"] != ticker:
                siguen.append(pos)
                continue
            pnl = round(ejecucion.pnl_pct(pos["entrada"], precio), 6)
            dias = len(calendario.sesiones_en_rango(pos["fecha_entrada"], sesion))
            bitacora.registrar_salida(
                pos["id"], sesion, hora, round(float(precio), 4),
                datos_erroneos.MOTIVO_SALIDA, pnl, int(dias),
                notas_extra=f"{datos_erroneos.ETIQUETA}: {nota}")
            cerradas.append({**pos, "precio_salida": round(float(precio), 4),
                             "pnl_pct": pnl})
            log(f"  simulador: cerrada {cart} id={pos['id']} "
                f"{pos['entrada']} -> {precio} ({pnl:+.2%})")
        estado["posiciones"][cart] = siguen
    return cerradas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", required=True, help="ticker a cerrar")
    ap.add_argument("--enviar", action="store_true",
                    help="cerrar de verdad (sin esto solo informa)")
    ap.add_argument("--solo-simulador", action="store_true",
                    help="el broker nunca tuvo la posición: cerrar solo en el "
                         "simulador, con el último cierre de la serie")
    args = ap.parse_args()

    ticker = args.ticker.strip().upper()
    sesion = datetime.now(config.TZ_ET).date().isoformat()
    ventana = exigir_declarado(ticker)
    log(f"{ticker}: serie declarada rota desde {ventana['desde']} — "
        f"{ventana['causa'][:90]}...")

    estado = est_mod.cargar()
    pendiente = any(e["ticker"] == ticker
                    for e in estado.get("entradas_pendientes", []))
    abiertas = [(c, p) for c in ("A", "B")
                for p in estado["posiciones"].get(c, []) if p["ticker"] == ticker]
    log(f"simulador: {len(abiertas)} posición(es) abierta(s)"
        + (" + 1 entrada pendiente de hoy" if pendiente else ""))
    if not abiertas and not pendiente:
        log(f"el simulador no tiene {ticker} ni abierto ni pendiente: nada que "
            f"cerrar.")
        return 0

    if args.solo_simulador:
        if not args.enviar:
            log("(sin --enviar: no se toca nada)")
            return 0
        serie = datos.actualizar_precios([ticker]).get(ticker)
        if serie is None or serie.empty:
            raise RuntimeError(f"sin serie de {ticker}: no hay precio de salida.")
        precio = float(serie["Close"].iloc[-1])
        nota = (f"cerrada solo en el simulador al último cierre disponible "
                f"({precio}); el broker nunca tuvo esta posición")
        abrir_pendiente_si_hace_falta(estado, ticker, sesion)
        cerradas = cerrar_en_simulador(estado, ticker, precio, sesion, nota)
        est_mod.guardar(estado)
        log(f"✅ {len(cerradas)} posición(es) cerrada(s) en el simulador.")
        return 0

    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as broker:
        saldo = broker.saldo()
        log(f"cuenta DEMO verificada: equity {saldo['equity']:,.2f}")
        pos = posicion_en_xtb(broker, ticker)
        if pos is None:
            raise RuntimeError(
                f"XTB no tiene ninguna posición abierta de {ticker} y el "
                f"simulador cree que sí. No se cierra nada: primero hay que "
                f"saber por qué difieren. Si el broker nunca la compró, "
                f"--solo-simulador cierra solo esta otra pata y lo deja dicho.")
        log(f"XTB: {pos['acciones']:g} acciones a {pos['precio_entrada']} "
            f"(ahora {pos['precio_actual']}, P&L {pos['pnl']:+.2f})")

        if not args.enviar:
            log("(sin --enviar: no se manda nada y no se toca el simulador)")
            return 0

        precio = vender(broker, ticker, pos, sesion)
        nota = (f"serie rota desde {ventana['desde']}; cerrada a mercado en la "
                f"demo a {precio}")
        abrir_pendiente_si_hace_falta(estado, ticker, sesion)
        cerradas = cerrar_en_simulador(estado, ticker, precio, sesion, nota)
        est_mod.guardar(estado)
        if not cerradas:
            raise RuntimeError(
                f"Se vendió {ticker} en XTB y el simulador no cerró ninguna "
                f"posición. Las dos patas tienen que acabar iguales.")
        # Todo camino que cambie posiciones vuelca el estado del broker.
        estado_broker.volcar(broker, candado_ok=True, cotizar=True)

    pnls = ", ".join(f"{c['portafolio']}: {c['pnl_pct']:+.2%}" for c in cerradas)
    log(f"✅ {ticker} cerrado en XTB y en el simulador por dato erróneo "
        f"({pnls}). Queda fuera de las estadísticas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
