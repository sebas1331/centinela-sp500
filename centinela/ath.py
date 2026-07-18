"""Máximo histórico (ATH) por acción y su actualización incremental.

Idea (según especificación):
  - El ATH se calcula UNA vez con todo el historial disponible (period="max").
  - Luego se actualiza de forma incremental:
        ATH_nuevo = max(ATH_guardado, máximos recientes)
    para no volver a descargar el historial completo cada día.

Este ATH (ath.json) es el ATH EN VIVO, "a día de hoy", y lo usa el screener.
OJO: para el backtest histórico NO se usa este valor global (sería look-ahead);
allí el ATH se recalcula como el máximo expansivo hasta cada fecha (ver features).
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from . import config, datos


def cargar_ath() -> dict:
    if config.ARCHIVO_ATH.exists():
        with open(config.ARCHIVO_ATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_ath(d: dict) -> None:
    with open(config.ARCHIVO_ATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)


def calcular_ath_inicial(tickers, verbose=True) -> dict:
    """Descarga TODO el historial (period='max') y calcula el ATH inicial.

    Operación pesada: se corre una sola vez (localmente). Devuelve y guarda el
    diccionario {ticker: {ath, fecha_ath, actualizado}}.
    """
    ath = cargar_ath()
    if isinstance(tickers, str):
        tickers = [tickers]
    pendientes = [t for t in tickers if t not in ath]
    if verbose:
        print(f"[ath] calculando ATH inicial para {len(pendientes)} tickers "
              f"(ya había {len(ath)})")
    # descarga por lotes con period='max'
    for i in range(0, len(pendientes), config.LOTE_DESCARGA):
        grupo = pendientes[i:i + config.LOTE_DESCARGA]
        precios = datos.descargar(grupo, period="max")
        for t in grupo:
            df = precios.get(t)
            if df is None or df["High"].dropna().empty:
                continue
            idx_max = df["High"].idxmax()
            ath[t] = {
                "ath": float(df["High"].max()),
                "fecha_ath": pd.Timestamp(idx_max).date().isoformat(),
                "actualizado": pd.Timestamp(df.index.max()).date().isoformat(),
            }
        if verbose:
            print(f"[ath]   procesados {min(i+config.LOTE_DESCARGA, len(pendientes))}/{len(pendientes)}")
    guardar_ath(ath)
    return ath


def actualizar_ath(precios_por_ticker: dict) -> dict:
    """Actualiza incrementalmente el ATH con los máximos recientes en caché.

    precios_por_ticker: dict {ticker: DataFrame OHLCV reciente}.
    ATH_nuevo = max(ATH_guardado, max(High reciente)).
    """
    ath = cargar_ath()
    for t, df in precios_por_ticker.items():
        if df is None or "High" not in df or df["High"].dropna().empty:
            continue
        high_max = float(df["High"].max())
        fecha_high = pd.Timestamp(df["High"].idxmax()).date().isoformat()
        ult = pd.Timestamp(df.index.max()).date().isoformat()
        prev = ath.get(t)
        if prev is None:
            ath[t] = {"ath": high_max, "fecha_ath": fecha_high, "actualizado": ult}
        elif high_max > prev["ath"]:
            ath[t] = {"ath": high_max, "fecha_ath": fecha_high, "actualizado": ult}
        else:
            prev["actualizado"] = ult
            ath[t] = prev
    guardar_ath(ath)
    return ath


def drawdown(ticker: str, precio: float, ath: dict | None = None):
    """Drawdown actual (0..1) del precio respecto al ATH guardado.

    Devuelve None si no hay ATH para el ticker.
    """
    ath = ath if ath is not None else cargar_ath()
    info = ath.get(ticker)
    if not info or info["ath"] <= 0:
        return None
    return max(0.0, (info["ath"] - precio) / info["ath"])
