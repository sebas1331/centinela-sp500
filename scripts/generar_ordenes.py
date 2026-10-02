#!/usr/bin/env python
"""Traduce las decisiones del sistema a órdenes concretas para el broker.

Corre en GitHub Actions, después de cada escaneo, y NO habla con XTB: solo
escribe `ordenes/pendientes.json`. El ejecutor del Mac lo lee más tarde. Esa
frontera es deliberada — ver la cabecera de `centinela/ordenes.py`.

Dos momentos, dos tipos de orden:

  pre-apertura  Las COMPRAS decididas hoy, dimensionadas con el capital real de
                la cuenta, para que XTB las deje en cola y las ejecute al open.
  post-cierre   Las VENTAS POR TIEMPO de la próxima sesión: las posiciones que
                mañana cumplen su décima sesión y hay que cerrar al cierre.

Solo se generan órdenes de la cartera que se opera en el broker
(`config.CARTERA_BROKER`). La otra se sigue simulando igual, y la comparación
entre las dos sigue siendo el experimento.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (config, calendario, cuenta, objetivos,  # noqa: E402
                       ordenes as ords, riesgo, estado as est_mod)

RUTA_BITACORA = config.BASE_DIR / "bitacora.csv"


def log(msg: str) -> None:
    print(f"[ordenes] {msg}", flush=True)


def _cuenta_de_la_cartera(cartera: str, capital: float | None = None) -> dict:
    # LA MISMA VISTA LIMPIA QUE EL PANEL (centinela/cuenta.py). De aquí sale el
    # tamaño del slot, o sea cuántas acciones se compran: si el equity que se
    # publica y el que dimensiona las órdenes no fueran el mismo número, la
    # página diría una cosa y el broker haría otra. Las operaciones de una serie
    # rota no cuentan para ninguno de los dos.
    limpias = cuenta.vista_limpia(pd.read_csv(RUTA_BITACORA))
    return cuenta.simular(limpias[limpias["portafolio"] == cartera],
                          capital=capital, fricciones=True)


def _dia_limite(fecha_entrada_iso: str) -> str:
    s = calendario.sesion_n_despues(fecha_entrada_iso,
                                    config.HORIZONTE_DIAS_HABILES - 1)
    return pd.Timestamp(s).date().isoformat()


# --------------------------------------------------------------------------- #
# Pre-apertura: compras
# --------------------------------------------------------------------------- #
def ordenes_de_compra(sesion: str, cartera: str, capital: float | None,
                      precios: dict) -> list[ords.Orden]:
    """Una orden de compra por cada entrada decidida hoy para esta cartera.

    El tamaño sale del slot de la cuenta simulada, truncado a acciones ENTERAS
    (XTB no admite fracciones por la API). Una entrada que no llega ni a una
    acción no se manda y se dice en voz alta: es capital insuficiente, no un
    error, pero tampoco algo que deba pasar en silencio.
    """
    estado = est_mod.cargar()
    pendientes = [e for e in estado.get("entradas_pendientes", [])
                  if e.get("fecha_decision") == sesion
                  and cartera in e.get("carteras", ("A", "B"))]
    if not pendientes:
        return []

    cta = _cuenta_de_la_cartera(cartera, capital)
    slot = cta["equity_contable"] / cta["slots"]
    limite = _dia_limite(sesion)

    salida = []
    for e in pendientes:
        ref = _ultimo_cierre(precios, e["ticker"], antes_de=sesion)
        if ref is None:
            raise RuntimeError(
                f"Sin precio de referencia para {e['ticker']}: no se puede "
                f"dimensionar la orden sin saber a cuánto cotiza.")
        acciones = riesgo.acciones_enteras(slot, ref)
        if acciones < 1:
            log(f"  {e['ticker']}: NO cabe en el slot (${slot:,.0f} a "
                f"${ref:,.2f}/acción). Se queda sin ejecutar en el broker; "
                f"el simulador sí la contará.")
            continue
        atr = e.get("atr")
        objetivo = objetivos.objetivo_inicial(ref, atr, e.get("resistencia"),
                                              e.get("target_analista"))
        salida.append(ords.Orden(
            id=ords.identificador(sesion, cartera, e["ticker"], ords.COMPRA),
            tipo=ords.COMPRA, cartera=cartera, ticker=e["ticker"],
            acciones=acciones, sesion=sesion,
            objetivo=round(objetivo, 2),
            stop=round(objetivos.stop_inicial(ref, atr), 2) if cartera == "A" else None,
            referencia=round(ref, 2), fecha_limite=limite,
        ))
    return salida


def _ultimo_cierre(precios: dict, ticker: str, antes_de: str | None = None):
    df = precios.get(ticker)
    if df is None or df.empty:
        return None
    if antes_de:
        df = df[df.index < pd.Timestamp(antes_de)]
        if df.empty:
            return None
    return float(df["Close"].iloc[-1])


# --------------------------------------------------------------------------- #
# Post-cierre: ventas por tiempo de la próxima sesión
# --------------------------------------------------------------------------- #
def ordenes_de_venta_por_tiempo(sesion: str, cartera: str,
                                capital: float | None) -> list[ords.Orden]:
    """Posiciones que cumplen su décima sesión en la SIGUIENTE sesión.

    Se emiten la víspera para que el ejecutor de las 15:50 ET del día siguiente
    las encuentre ya escritas y no dependa de que GitHub haya corrido a tiempo.
    """
    estado = est_mod.cargar()
    manana = calendario.sesion_n_despues(sesion, 1)
    manana_iso = pd.Timestamp(manana).date().isoformat()

    cta = _cuenta_de_la_cartera(cartera, capital)
    por_id = {t["id"]: t for t in cta["abiertas"]}

    salida = []
    for pos in estado.get("posiciones", {}).get(cartera, []):
        limite = pos.get("dia_limite") or _dia_limite(str(pos["fecha_entrada"]))
        if limite != manana_iso:
            continue
        t = por_id.get(int(pos["id"]))
        if t is None:
            log(f"  {pos['ticker']}: abierta en el estado pero no en la cuenta "
                f"simulada; no se genera su venta. Lo verá la reconciliación.")
            continue
        acciones = riesgo.acciones_enteras(t["coste"], t["precio_entrada_neto"])
        if acciones < 1:
            continue
        salida.append(ords.Orden(
            id=ords.identificador(manana_iso, cartera, pos["ticker"],
                                  ords.VENTA_TIEMPO),
            tipo=ords.VENTA_TIEMPO, cartera=cartera, ticker=pos["ticker"],
            acciones=acciones, sesion=manana_iso,
            fecha_limite=manana_iso, id_operacion=int(pos["id"]),
        ))
    return salida


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evento", choices=["preapertura", "postcierre"])
    ap.add_argument("--fecha", default=None)
    ap.add_argument("--capital", type=float, default=None,
                    help="capital de la cuenta; por defecto, el configurado")
    args = ap.parse_args()

    if not config.EJECUCION_BROKER:
        log("ejecución en broker DESACTIVADA (config.EJECUCION_BROKER). "
            "No se generan órdenes.")
        return 0

    estado = est_mod.cargar()
    clave = {"preapertura": "ultima_preapertura",
             "postcierre": "ultima_postcierre"}[args.evento]
    sesion = args.fecha or estado.get(clave)
    if not sesion:
        log(f"el escaneo de {args.evento} no ha marcado ninguna sesión; nada "
            f"que generar.")
        return 0

    cartera = config.CARTERA_BROKER
    if args.evento == "preapertura":
        from centinela import datos
        tickers = sorted({e["ticker"] for e in estado.get("entradas_pendientes", [])})
        precios = datos.actualizar_precios(tickers) if tickers else {}
        ordenes = ordenes_de_compra(sesion, cartera, args.capital, precios)
    else:
        ordenes = ordenes_de_venta_por_tiempo(sesion, cartera, args.capital)

    ruta = ords.guardar_pendientes(ordenes, sesion, cartera, args.evento)
    log(f"{len(ordenes)} orden(es) de {args.evento} para la cartera {cartera} "
        f"-> {ruta.relative_to(config.BASE_DIR)}")
    for o in ordenes:
        log(f"  {o.tipo:12s} {o.ticker:6s} {o.acciones:4d} acc"
            + (f"  objetivo {o.objetivo}" if o.objetivo else "")
            + (f"  stop {o.stop}" if o.stop else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
