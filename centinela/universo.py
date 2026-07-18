"""Universo de inversión: constituyentes del S&P 500.

Fuente: Wikipedia (se refresca semanalmente). Guardamos SIEMPRE una copia local
de respaldo (datos/sp500_respaldo.csv) para seguir operando si el scraping falla.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests

from . import config

_DIAS_REFRESCO = 7  # refrescar el universo como máximo cada 7 días


def _scrapear_wikipedia() -> pd.DataFrame:
    """Descarga la tabla de constituyentes del S&P 500 desde Wikipedia."""
    r = requests.get(config.WIKIPEDIA_SP500_URL, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 (Centinela SP500)"})
    r.raise_for_status()
    tablas = pd.read_html(StringIO(r.text))
    # La primera tabla con columna 'Symbol' es la de constituyentes.
    for t in tablas:
        cols = {str(c).strip().lower() for c in t.columns}
        if "symbol" in cols:
            df = t
            break
    else:
        raise ValueError("No se encontró la tabla de constituyentes en Wikipedia")

    df.columns = [str(c).strip() for c in df.columns]
    ren = {"Symbol": "ticker", "Security": "empresa",
           "GICS Sector": "sector", "GICS Sub-Industry": "subsector"}
    df = df.rename(columns=ren)
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    cols_finales = [c for c in ["ticker", "empresa", "sector", "subsector"]
                    if c in df.columns]
    df = df[cols_finales].dropna(subset=["ticker"]).drop_duplicates("ticker")
    df["fecha_actualizacion"] = datetime.now(config.TZ_ET).date().isoformat()
    return df.reset_index(drop=True)


def _respaldo_desactualizado() -> bool:
    if not config.ARCHIVO_UNIVERSO.exists():
        return True
    try:
        df = pd.read_csv(config.ARCHIVO_UNIVERSO)
        fecha = pd.to_datetime(df["fecha_actualizacion"].iloc[0]).date()
        return (datetime.now(config.TZ_ET).date() - fecha) > timedelta(days=_DIAS_REFRESCO)
    except Exception:  # noqa: BLE001
        return True


def obtener_sp500(forzar: bool = False) -> pd.DataFrame:
    """Devuelve el universo S&P 500 (DataFrame con ticker, empresa, sector...).

    Refresca desde Wikipedia si el respaldo está desactualizado (>7 días) o si
    se fuerza. Si el scraping falla, usa el respaldo local. Nunca revienta si
    hay respaldo disponible.
    """
    necesita = forzar or _respaldo_desactualizado()
    if necesita:
        try:
            df = _scrapear_wikipedia()
            df.to_csv(config.ARCHIVO_UNIVERSO, index=False)
            print(f"[universo] refrescado desde Wikipedia: {len(df)} tickers")
            return df
        except Exception as exc:  # noqa: BLE001
            print(f"[universo] scraping falló ({exc!r}); uso respaldo local")
    if config.ARCHIVO_UNIVERSO.exists():
        return pd.read_csv(config.ARCHIVO_UNIVERSO)
    # Sin respaldo y sin red: error explícito.
    raise RuntimeError("No hay universo disponible: falló el scraping y no hay "
                       "respaldo local en " + str(config.ARCHIVO_UNIVERSO))


def tickers_sp500(forzar: bool = False) -> list[str]:
    """Lista de tickers canónicos del S&P 500."""
    return obtener_sp500(forzar=forzar)["ticker"].tolist()


def mapa_sector(forzar: bool = False) -> dict[str, str]:
    """Diccionario ticker -> sector GICS (para el análisis post-trade)."""
    df = obtener_sp500(forzar=forzar)
    if "sector" not in df.columns:
        return {}
    return dict(zip(df["ticker"], df["sector"]))
