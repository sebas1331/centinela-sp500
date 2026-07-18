"""Primitivos PUROS de ejecución simulada (compartidos por backtest y motor vivo).

Modela la salida de UNA operación dada la entrada, el objetivo, el stop (opcional)
y las barras OHLC futuras (desde el día de entrada, t+1, en adelante).

Supuestos CONSERVADORES (sesgo pesimista, por especificación):
  - Entrada al OPEN del primer día (t+1).
  - Si en un mismo día se tocan stop y objetivo -> gana el STOP (peor caso).
  - Si hay gap de apertura MÁS ALLÁ del stop u objetivo -> se ejecuta al OPEN real
    (peor para el stop, mejor para el objetivo, pero siempre el precio real).
  - Límite de tiempo: si nada se dispara, salida al CIERRE del último día (t+H).
"""
from __future__ import annotations

import pandas as pd

from . import config


def simular_salida(bars: pd.DataFrame, entrada: float, objetivo: float,
                   stop: float | None = None) -> dict:
    """Simula la salida de una operación.

    bars: OHLC futuras desde el día de entrada (inclusive), ordenadas por fecha.
          bars.iloc[0]['Open'] debe ser el precio de entrada.
    Devuelve dict: fecha_salida, precio_salida, motivo ('objetivo'|'stop'|
    'tiempo'), dias (nº de sesiones mantenida).
    """
    n = len(bars)
    for i in range(n):
        o = float(bars["Open"].iloc[i])
        h = float(bars["High"].iloc[i])
        l = float(bars["Low"].iloc[i])
        fecha = bars.index[i]

        toca_stop = stop is not None and l <= stop
        toca_obj = h >= objetivo

        if toca_stop:
            # gap a la baja más allá del stop -> ejecutar al open real (peor)
            precio = o if o <= stop else stop
            # si además se tocó el objetivo el mismo día, gana el stop (peor caso)
            return _salida(fecha, precio, "stop", i + 1)
        if toca_obj:
            # gap al alza más allá del objetivo -> ejecutar al open real
            precio = o if o >= objetivo else objetivo
            return _salida(fecha, precio, "objetivo", i + 1)

    # límite de tiempo: cierre del último día disponible
    fecha_fin = bars.index[-1]
    precio_fin = float(bars["Close"].iloc[-1])
    return _salida(fecha_fin, precio_fin, "tiempo", n)


def _salida(fecha, precio, motivo, dias) -> dict:
    return {
        "fecha_salida": pd.Timestamp(fecha),
        "precio_salida": round(float(precio), 4),
        "motivo": motivo,
        "dias": int(dias),
    }


def pnl_pct(entrada: float, salida: float) -> float:
    """P&L porcentual sobre el precio de entrada."""
    if entrada <= 0:
        return 0.0
    return salida / entrada - 1.0
