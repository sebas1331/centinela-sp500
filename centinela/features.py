"""Cálculo de features técnicos, SIN look-ahead.

Convención temporal (clave para no hacer trampa):
  - La fila de features del día t usa SOLO información disponible al CIERRE de t.
  - La decisión se toma en la pre-apertura del día t+1 y la entrada se simula al
    OPEN de t+1. El etiquetado (ver etiquetado.py) es coherente con esto.

Todos los features son técnicos: tienen historial completo, así que se pueden
recalcular de forma idéntica para cualquier fecha pasada (reproducibilidad).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

_N_RSI = 14
_N_ATR = 14


def _rsi(close: pd.Series, n: int = _N_RSI) -> pd.Series:
    delta = close.diff()
    ganancia = delta.clip(lower=0.0)
    perdida = -delta.clip(upper=0.0)
    # suavizado de Wilder (EMA con alpha=1/n)
    avg_g = ganancia.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_p = perdida.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_g / avg_p.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(avg_p != 0.0, 100.0)   # sin pérdidas -> RSI 100
    return rsi


def _atr(df: pd.DataFrame, n: int = _N_ATR) -> pd.Series:
    alto, bajo, cierre = df["High"], df["Low"], df["Close"]
    cierre_prev = cierre.shift(1)
    tr = pd.concat([
        (alto - bajo),
        (alto - cierre_prev).abs(),
        (bajo - cierre_prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _dias_desde_ath(df: pd.DataFrame) -> pd.Series:
    """Días naturales desde el último máximo EXPANSIVO (running ATH hasta t)."""
    high = df["High"]
    running_max = high.cummax()
    es_nuevo_max = high >= running_max     # marca los días de nuevo máximo
    fechas = pd.Series(df.index, index=df.index)
    fecha_ultimo_max = fechas.where(es_nuevo_max).ffill()
    dias = (df.index - pd.DatetimeIndex(fecha_ultimo_max)).days
    return pd.Series(dias, index=df.index, dtype="float64")


def calcular_features(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve un DataFrame con las columnas de config.FEATURES_MODELO.

    'df' es OHLCV de un solo ticker, ordenado por fecha. Las filas iniciales sin
    suficiente historial (p.ej. para SMA200) quedan con NaN.
    """
    df = df.sort_index()
    cierre, alto, vol = df["Close"], df["High"], df["Volume"]
    apertura = df["Open"]

    running_max_high = alto.cummax()
    atr = _atr(df)

    feats = pd.DataFrame(index=df.index)
    feats["rsi_14"] = _rsi(cierre)
    feats["ret_5"] = cierre / cierre.shift(5) - 1.0
    feats["ret_20"] = cierre / cierre.shift(20) - 1.0
    feats["ret_60"] = cierre / cierre.shift(60) - 1.0
    feats["dist_sma20"] = cierre / cierre.rolling(20).mean() - 1.0
    feats["dist_sma50"] = cierre / cierre.rolling(50).mean() - 1.0
    feats["dist_sma200"] = cierre / cierre.rolling(200).mean() - 1.0
    feats["atr_pct"] = atr / cierre
    feats["vol_rel"] = vol / vol.rolling(20).mean()
    feats["drawdown"] = (running_max_high - cierre) / running_max_high
    feats["dias_desde_ath"] = _dias_desde_ath(df)
    feats["gap_overnight"] = apertura / cierre.shift(1) - 1.0

    # aseguramos el orden/columnas exactas del modelo
    return feats[config.FEATURES_MODELO]


def features_ultima_fila(df: pd.DataFrame) -> dict | None:
    """Features de la ÚLTIMA fila disponible (para el escaneo en vivo).

    Devuelve dict {feature: valor} o None si no hay suficiente historial (algún
    feature esencial NaN).
    """
    feats = calcular_features(df)
    if feats.empty:
        return None
    fila = feats.iloc[-1]
    if fila[["rsi_14", "dist_sma200", "drawdown"]].isna().any():
        return None
    return {k: (None if pd.isna(v) else float(v)) for k, v in fila.items()}
