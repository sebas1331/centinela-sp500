"""Etiquetado de eventos históricos para entrenar el modelo.

Regla (coherente con la ejecución real, sin leakage):
  Para una decisión tomada al cierre del día t (entrada simulada al OPEN de t+1),
  la etiqueta es POSITIVA (1) si en los HORIZONTE_DIAS_HABILES siguientes
  (t+1 .. t+H) el MÁXIMO (high) alcanza >= (1 + OBJETIVO_MINIMO) * Open(t+1).
  Si no, es 0.

Las últimas H filas no tienen horizonte completo -> etiqueta NaN (se descartan).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def etiquetar(df: pd.DataFrame,
              horizonte: int = config.HORIZONTE_DIAS_HABILES,
              objetivo: float = config.ETIQUETA_OBJETIVO) -> pd.Series:
    """Serie de etiquetas (0/1/NaN) alineada al índice de df (decisión en t)."""
    df = df.sort_index()
    alto = df["High"]
    apertura = df["Open"]

    open_siguiente = apertura.shift(-1)                 # Open(t+1)
    high_desde_siguiente = alto.shift(-1)               # High(t+1)
    # máximo de High(t+1 .. t+H): rolling hacia adelante con ventana H
    fwd_max = (high_desde_siguiente[::-1]
               .rolling(horizonte, min_periods=horizonte).max()[::-1])

    objetivo_precio = (1.0 + objetivo) * open_siguiente
    etiqueta = (fwd_max >= objetivo_precio).astype("float64")
    # invalidar filas sin horizonte completo o sin open siguiente
    etiqueta[fwd_max.isna() | open_siguiente.isna()] = np.nan
    etiqueta.name = "y"
    return etiqueta


def construir_dataset(precios_por_ticker: dict,
                      solo_drawdown: bool = True) -> pd.DataFrame:
    """Construye el dataset (features + etiqueta + metadatos) de varios tickers.

    Solo conserva, por defecto, las filas en drawdown >= DRAWDOWN_MINIMO (que es
    el contexto real de decisión del sistema). Devuelve un DataFrame con columnas
    de features, 'y', 'ticker' y 'fecha'.
    """
    from .features import calcular_features

    marcos = []
    for ticker, df in precios_por_ticker.items():
        if df is None or len(df) < 220:   # necesitamos historial para SMA200
            continue
        feats = calcular_features(df)
        y = etiquetar(df)
        bloque = feats.copy()
        bloque["y"] = y
        bloque["ticker"] = ticker
        bloque["fecha"] = bloque.index
        marcos.append(bloque)

    if not marcos:
        return pd.DataFrame(columns=config.FEATURES_MODELO + ["y", "ticker", "fecha"])

    data = pd.concat(marcos, ignore_index=True)
    # filas válidas: features completos y etiqueta definida
    data = data.dropna(subset=config.FEATURES_MODELO + ["y"])
    if solo_drawdown:
        data = data[data["drawdown"] >= config.DRAWDOWN_MINIMO]
    data["y"] = data["y"].astype(int)
    return data.reset_index(drop=True)
