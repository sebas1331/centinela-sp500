"""Contabilidad de la CUENTA simulada: de suma de retornos a dinero.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
Hasta ahora el sistema publicaba la SUMA de los retornos de cada operación
(+3% +5% -12% ... = X%). Esa cifra responde "¿cuánto gana la estrategia por
operación?", pero NO responde "¿cuánto habría ganado mi dinero?", que es la
única pregunta que importa si algún día esto opera en real. Dos diferencias
grandes:

  1. COMPOSICIÓN. Una cuenta reinvierte: tras ganar, cada posición siguiente es
     mayor. La suma de retornos ignora eso.
  2. CAPITAL FINITO. Una cuenta tiene 20 slots y no puede abrir la 21ª posición
     aunque la señal sea buena. La suma de retornos suma todo lo que ocurrió.

Este módulo es SOLO LECTURA sobre el sistema de trading: lee la bitácora ya
escrita y la reinterpreta como una cuenta. No toca el modelo, ni el umbral, ni
las features, ni los objetivos, ni los stops, ni el simulador, ni decide nada.

MODELO DE CUENTA (decisiones interpretativas, explícitas a propósito)
---------------------------------------------------------------------
* CAPITAL CONTABLE Y SLOTS. `equity_contable = cash + Σ coste de las abiertas`.
  Cada entrada invierte `equity_contable / SLOTS`. Ese equity solo se mueve
  cuando una posición CIERRA y realiza su P&L: es exactamente "cada entrada usa
  un slot y compone al cerrar". Deliberadamente NO se dimensiona con el valor de
  mercado de las abiertas: subir el tamaño de las apuestas por ganancias todavía
  no realizadas es lo que convierte una buena racha en una ruina.

* ACCIONES FRACCIONARIAS. Con $10.000 y 20 slots, un slot son ~$500, y SNDK
  cotizaba a $1.510: con acciones enteras la operación sería de CERO acciones y
  la cuenta simulada no podría reproducir la bitácora. Se usan fracciones (las
  admiten IBKR, Schwab, Robinhood...), que además dejan la ponderación exacta y
  sin residuos de caja que ensucien el retorno. Las notificaciones dan el
  importe en dólares además del número de acciones, porque el importe es lo que
  se puede teclear en cualquier broker.

* ORDEN DENTRO DE UN DÍA: primero las ENTRADAS, después las SALIDAS. Es lo que
  hace el sistema real —la pre-apertura decide con el estado de ayer, cuando las
  posiciones que cierran hoy seguían vivas— y además es la hipótesis
  conservadora: el capital que libera un cierre de hoy no financia una compra de
  hoy.

* MARCA A MERCADO POR RATIO, no por precio absoluto. El valor diario de una
  posición abierta es `coste * (close_hoy / open_del_día_de_entrada)`, con los
  dos precios sacados de la MISMA descarga. Los precios de la bitácora se
  guardaron ajustados en su día y los de hoy están reajustados por los
  dividendos posteriores (discrepancia medida: 0,07% de media), así que mezclar
  las dos fuentes metería un error que no existe. El ratio es inmune.

FRICCIONES
----------
Bruto = tal cual lo registró la bitácora. Neto = con comisión+spread por lado y
slippage donde corresponde. Una salida por OBJETIVO no lleva slippage: es una
orden límite, que se ejecuta a su precio o mejor. Las salidas por STOP y por
TIEMPO sí: la primera es una orden a mercado disparada en movimiento y la
segunda un market-on-close.
"""
from __future__ import annotations

import math

import pandas as pd

from . import config


# --------------------------------------------------------------------------- #
# Fricciones
# --------------------------------------------------------------------------- #
#: Salidas que se ejecutan a mercado y por tanto pagan slippage. Una salida por
#: "objetivo" es una orden límite y no está aquí, por definición.
SALIDAS_A_MERCADO = ("stop", "tiempo")


def precio_entrada_neto(precio: float, fricciones: bool = True) -> float:
    """Precio realmente pagado en una compra market-on-open."""
    if not fricciones:
        return float(precio)
    return float(precio) * (1.0 + config.COMISION_SPREAD_POR_LADO + config.SLIPPAGE_MERCADO)


def precio_salida_neto(precio: float, motivo: str, fricciones: bool = True) -> float:
    """Precio realmente cobrado en una venta, según cómo se ejecute."""
    if not fricciones:
        return float(precio)
    coste = config.COMISION_SPREAD_POR_LADO
    if motivo in SALIDAS_A_MERCADO:
        coste += config.SLIPPAGE_MERCADO
    return float(precio) * (1.0 - coste)


# --------------------------------------------------------------------------- #
# Huella del bug de duplicados (2026-08-06)
# --------------------------------------------------------------------------- #
def marcar_duplicadas(bit: pd.DataFrame) -> pd.Series:
    """True en cada entrada que se abrió teniendo ya ese ticker vivo en su cartera.

    Es la cicatriz del bug corregido el 2026-08-06 (ver CHANGELOG): el filtro de
    tickers ocupados miraba siempre la cartera A, así que cuando el stop cerraba
    la posición de A pero la de B seguía abierta, el ticker volvía a entrar y B
    acababa con dos posiciones del mismo valor a la vez.

    Se marca la SEGUNDA y siguientes, nunca la primera: la primera entrada era
    legítima. Una posición que cierra el mismo día en que se abre la siguiente
    cuenta como solape, porque durante esa sesión las dos estuvieron vivas.

    Vivía en scripts/generar_dashboard.py; se mudó aquí cuando la contabilidad de
    la cuenta pasó a necesitar exactamente el mismo criterio. Una sola definición
    para las dos, que es lo que impide que el dashboard y la auditoría de
    fiabilidad cuenten universos distintos sin que nadie se entere.
    """
    dup = pd.Series(False, index=bit.index)
    entrada = pd.to_datetime(bit["fecha_entrada"])
    salida = pd.to_datetime(bit["fecha_salida"])
    # Una posición abierta ocupa hasta hoy; se usa el máximo del fichero como
    # "hoy" para que el cálculo no dependa de cuándo se ejecute este script.
    fin = salida.fillna(max(entrada.max(), salida.max()))

    for _, grupo in bit.groupby([bit["ticker"], bit["portafolio"]], sort=False):
        orden = grupo.sort_values(["fecha_entrada", "id"]).index
        for pos, idx in enumerate(orden):
            if any(fin[previo] >= entrada[idx] for previo in orden[:pos]):
                dup[idx] = True
    return dup


# --------------------------------------------------------------------------- #
# Simulación de la cuenta
# --------------------------------------------------------------------------- #
def simular(operaciones: pd.DataFrame,
            capital: float = None,
            slots: int = None,
            fricciones: bool = True) -> dict:
    """Reinterpreta las operaciones de UNA cartera como una cuenta con capital.

    `operaciones` son filas de bitacora.csv de una sola cartera (columnas
    ticker, fecha_entrada, precio_entrada, fecha_salida, precio_salida,
    motivo_salida, estado, id). Devuelve un dict con:

      capital_inicial, cash, equity_contable (cash + coste de las abiertas),
      realizado (P&L en dólares de lo ya cerrado), trades (una entrada por
      operación, con acciones/coste/ingreso/pnl_dinero), rechazadas (operaciones
      que la cuenta NO habría podido financiar), abiertas (las vivas al final).

    Una operación se rechaza cuando ya hay `slots` posiciones vivas o cuando no
    queda caja: la bitácora registra lo que el sistema decidió, y la cuenta
    registra lo que el dinero permitía. Cuando divergen, la diferencia es
    información, no un error que tapar.
    """
    capital = config.CAPITAL_INICIAL_CUENTA if capital is None else float(capital)
    slots = config.SLOTS_CUENTA if slots is None else int(slots)

    ops = operaciones.copy()
    ops["fecha_entrada"] = ops["fecha_entrada"].astype(str)

    # Calendario de eventos: por fecha, primero las entradas y luego las salidas.
    eventos: dict[str, dict[str, list]] = {}
    for _, r in ops.iterrows():
        eventos.setdefault(str(r["fecha_entrada"]), {"in": [], "out": []})["in"].append(r)
        if r["estado"] == "cerrada" and not pd.isna(r["fecha_salida"]):
            eventos.setdefault(str(r["fecha_salida"]), {"in": [], "out": []})["out"].append(r)

    cash = capital
    vivas: dict[int, dict] = {}        # id -> trade
    trades: dict[int, dict] = {}
    rechazadas: list[dict] = []

    for fecha in sorted(eventos):
        # --- ENTRADAS --------------------------------------------------------
        for r in eventos[fecha]["in"]:
            equity_contable = cash + sum(t["coste"] for t in vivas.values())
            if len(vivas) >= slots:
                rechazadas.append({"id": int(r["id"]), "ticker": r["ticker"],
                                   "fecha": fecha, "motivo": "sin slot libre"})
                continue
            importe = equity_contable / slots
            if importe > cash:
                # No debería ocurrir con <= slots posiciones, pero si el capital
                # se queda corto se invierte lo que haya y se deja constancia.
                importe = cash
            if importe <= 0:
                rechazadas.append({"id": int(r["id"]), "ticker": r["ticker"],
                                   "fecha": fecha, "motivo": "sin caja"})
                continue
            precio = precio_entrada_neto(float(r["precio_entrada"]), fricciones)
            acciones = importe / precio
            cash -= importe
            t = {
                "id": int(r["id"]), "ticker": r["ticker"],
                "sector": r.get("sector"),
                "fecha_entrada": fecha, "precio_entrada_bruto": float(r["precio_entrada"]),
                "precio_entrada_neto": precio, "acciones": acciones, "coste": importe,
                "fecha_salida": None, "precio_salida_bruto": None,
                "precio_salida_neto": None, "motivo_salida": None,
                "ingreso": None, "pnl_dinero": None, "pnl_pct": None,
            }
            vivas[t["id"]] = t
            trades[t["id"]] = t

        # --- SALIDAS ---------------------------------------------------------
        for r in eventos[fecha]["out"]:
            t = vivas.pop(int(r["id"]), None)
            if t is None:      # entrada rechazada: su salida tampoco existe
                continue
            motivo = r["motivo_salida"]
            precio = precio_salida_neto(float(r["precio_salida"]), motivo, fricciones)
            ingreso = t["acciones"] * precio
            cash += ingreso
            t.update({
                "fecha_salida": fecha, "precio_salida_bruto": float(r["precio_salida"]),
                "precio_salida_neto": precio, "motivo_salida": motivo,
                "ingreso": ingreso, "pnl_dinero": ingreso - t["coste"],
                "pnl_pct": precio / t["precio_entrada_neto"] - 1.0,
            })

    coste_vivas = sum(t["coste"] for t in vivas.values())
    cerrados = [t for t in trades.values() if t["fecha_salida"]]
    return {
        "capital_inicial": capital,
        "slots": slots,
        "fricciones": fricciones,
        "cash": cash,
        "equity_contable": cash + coste_vivas,
        "realizado": sum(t["pnl_dinero"] for t in cerrados),
        "trades": list(trades.values()),
        "cerrados": cerrados,
        "abiertas": list(vivas.values()),
        "rechazadas": rechazadas,
    }


# --------------------------------------------------------------------------- #
# Curva diaria y métricas de riesgo
# --------------------------------------------------------------------------- #
def curva_diaria(cta: dict, precios: dict, sesiones: list[str]) -> pd.DataFrame:
    """Equity marcado a mercado, sesión a sesión.

    `precios` es {ticker: DataFrame OHLC indexado por fecha} y `sesiones` la
    lista ordenada de fechas ISO a valorar. El equity de cada día es la caja más
    el valor de mercado de lo que está abierto:

        valor_i(t) = coste_i * (close_i(t) / open_i(fecha_entrada_i))

    Por ratio y no por precio absoluto, por la razón explicada en la cabecera
    del módulo. Una posición sin barra ese día conserva su última valoración.
    """
    capital = cta["capital_inicial"]
    trades = cta["trades"]

    # Precio de referencia de cada trade: el open de su día de entrada EN LA
    # MISMA SERIE con la que luego se marca a mercado.
    ref = {}
    for t in trades:
        df = precios.get(t["ticker"])
        ts = pd.Timestamp(t["fecha_entrada"])
        if df is not None and ts in df.index:
            ref[t["id"]] = float(df.loc[ts, "Open"])

    filas = []
    ultimo_valor: dict[int, float] = {}
    for fecha in sesiones:
        ts = pd.Timestamp(fecha)
        cash = capital
        valor_abierto = 0.0
        for t in trades:
            if t["fecha_entrada"] > fecha:
                continue                       # aún no existe
            cash -= t["coste"]
            if t["fecha_salida"] and t["fecha_salida"] <= fecha:
                cash += t["ingreso"]           # ya cerrada: su dinero está en caja
                continue
            base = ref.get(t["id"])
            df = precios.get(t["ticker"])
            if base and df is not None and ts in df.index:
                v = t["coste"] * float(df.loc[ts, "Close"]) / base
                ultimo_valor[t["id"]] = v
            else:
                v = ultimo_valor.get(t["id"], t["coste"])
            valor_abierto += v
        filas.append({"fecha": fecha, "cash": cash, "abierto": valor_abierto,
                      "equity": cash + valor_abierto})
    return pd.DataFrame(filas)


def metricas(curva: pd.DataFrame, capital: float, dias_por_anio: int = 252) -> dict:
    """Rentabilidad total, CAGR, drawdown máximo y Sharpe de una curva de equity.

    CAGR anualiza con las SESIONES transcurridas, no con días naturales: con
    apenas dos meses de historia la diferencia entre los dos criterios es ruido,
    pero el de sesiones es el que casa con el Sharpe de retornos diarios.

    El Sharpe se calcula con tasa libre de riesgo CERO y se marca como
    aproximado: con ~45 observaciones su error estándar es enorme (±0,3 aprox.
    solo por muestreo). La cifra está para ordenar magnitudes, no para creérsela.
    """
    if curva.empty:
        return {"rentabilidad_pct": None, "cagr_pct": None,
                "drawdown_max_pct": None, "sharpe": None, "sesiones": 0}

    eq = curva["equity"].astype(float)
    final = float(eq.iloc[-1])
    total = final / capital - 1.0
    n = len(eq)

    # Drawdown: caída máxima desde el máximo previo de la propia curva. El
    # máximo arranca en el capital inicial, así que una cuenta que solo baja
    # tiene drawdown desde el primer día, que es lo correcto.
    pico = eq.cummax().clip(lower=capital)
    dd = float((eq / pico - 1.0).min())

    rets = eq.pct_change().dropna()
    if len(rets) > 1 and float(rets.std()) > 0:
        sharpe = float(rets.mean() / rets.std() * math.sqrt(dias_por_anio))
    else:
        sharpe = None

    anios = n / dias_por_anio
    cagr = ((final / capital) ** (1.0 / anios) - 1.0) if anios > 0 and final > 0 else None

    return {
        "rentabilidad_pct": round(100.0 * total, 2),
        "cagr_pct": None if cagr is None else round(100.0 * cagr, 2),
        "drawdown_max_pct": round(100.0 * dd, 2),
        "sharpe": None if sharpe is None else round(sharpe, 2),
        "sesiones": n,
        "equity_final": round(final, 2),
    }
