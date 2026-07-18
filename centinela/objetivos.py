"""Cálculo del objetivo de salida (variable) y del stop (Cartera A).

Objetivo inicial = MÁXIMO entre +5% y un objetivo técnico. El objetivo técnico
combina ATR, resistencia reciente y precio objetivo de analistas, con un tope
para no fijar metas absurdamente lejanas. El objetivo se RECALCULA en cada
escaneo con la información nueva (ver simulador.recalcular_objetivo).

Stop (solo Cartera A): basado en ATR. Justificación en README: un stop fijo
(p.ej. -7%) castiga por igual a una acción tranquila y a una muy volátil; el
stop por ATR se adapta a la volatilidad real de cada acción.
"""
from __future__ import annotations

import pandas as pd

from . import config


def resistencia_reciente(df: pd.DataFrame,
                         ventana: int = config.VENTANA_RESISTENCIA):
    """Máximo (high) de las últimas `ventana` sesiones hasta la última fila."""
    if df is None or len(df) == 0:
        return None
    return float(df["High"].tail(ventana).max())


def objetivo_inicial(entrada: float, atr: float,
                     resistencia: float | None = None,
                     target_analista: float | None = None) -> float:
    """Objetivo de salida = max(+5%, objetivo técnico), con tope por ATR."""
    if atr is None or atr <= 0:
        atr = entrada * 0.02   # fallback prudente si no hay ATR
    base_5pct = entrada * (1.0 + config.OBJETIVO_MINIMO)
    tope = entrada + config.ATR_OBJETIVO_TOPE_MULT * atr

    tec = [entrada + config.ATR_OBJETIVO_MULT * atr]
    if resistencia and entrada < resistencia <= tope:
        tec.append(resistencia)
    if target_analista and entrada < target_analista <= tope:
        tec.append(target_analista)
    objetivo_tecnico = min(max(tec), tope)
    return max(base_5pct, objetivo_tecnico)


def stop_inicial(entrada: float, atr: float) -> float:
    """Stop por ATR (solo Cartera A), acotado entre -3% y -12% del precio."""
    if atr is None or atr <= 0:
        atr = entrada * 0.02
    stop = entrada - config.ATR_STOP_MULT * atr
    piso = entrada * (1.0 - config.STOP_MAX_PORCENTAJE)   # no peor que -12%
    techo = entrada * (1.0 - config.STOP_MIN_PORCENTAJE)  # no más ajustado que -3%
    return min(max(stop, piso), techo)
