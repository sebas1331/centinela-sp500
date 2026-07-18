"""Capa de datos de precios.

Fuente principal: yfinance (gratuita, no oficial). Por eso implementamos:
  - reintentos con backoff exponencial ante fallos de red / rate-limit,
  - descargas por lotes,
  - caché agresiva en parquet (NO va a git; se reconstruye si se pierde),
  - fallback a stooq (best-effort; puede estar bloqueado según la red).

Todos los precios se descargan AJUSTADOS (auto_adjust=True): splits y dividendos
ya incorporados, para que ATH, drawdown y retornos sean consistentes.
"""
from __future__ import annotations

import time
import io
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from . import config

COLUMNAS = ["Open", "High", "Low", "Close", "Volume"]


# --------------------------------------------------------------------------- #
# Conversión de símbolos
# --------------------------------------------------------------------------- #
def a_simbolo_yf(ticker: str) -> str:
    """Convierte el símbolo canónico (Wikipedia) al formato yfinance.

    Ej.: 'BRK.B' -> 'BRK-B'. yfinance usa guion en clases de acciones.
    """
    return ticker.strip().upper().replace(".", "-")


def a_simbolo_stooq(ticker: str) -> str:
    """Símbolo para stooq (acciones USA llevan sufijo .us)."""
    return ticker.strip().lower().replace(".", "-") + ".us"


# --------------------------------------------------------------------------- #
# Normalización del resultado de yfinance
# --------------------------------------------------------------------------- #
def _normalizar_ohlcv(df: pd.DataFrame, ticker_yf: str) -> pd.DataFrame | None:
    """Extrae [Open,High,Low,Close,Volume] limpio para un ticker.

    yfinance devuelve columnas MultiIndex (nivel 'Price' y 'Ticker'); esta
    función aplana y limpia. Devuelve None si no hay datos usables.
    """
    if df is None or len(df) == 0:
        return None
    out = df
    if isinstance(out.columns, pd.MultiIndex):
        nombres = list(out.columns.names)
        try:
            if "Ticker" in nombres:
                out = out.xs(ticker_yf, axis=1, level="Ticker")
            else:
                # nivel del ticker suele ser el último
                out = out.xs(ticker_yf, axis=1, level=-1)
        except KeyError:
            # cuando hay un solo ticker, a veces el nivel Ticker trae otro valor
            nivel0 = out.columns.get_level_values(0)
            if set(COLUMNAS).issubset(set(nivel0)):
                out = out.droplevel(-1, axis=1)
            else:
                return None
    faltantes = [c for c in COLUMNAS if c not in out.columns]
    if faltantes:
        return None
    out = out[COLUMNAS].copy()
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index.name = "Date"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    # descartar filas totalmente vacías (ticker inexistente / deslistado)
    out = out.dropna(how="all")
    out = out[out["Close"].notna()]
    if len(out) == 0:
        return None
    return out


# --------------------------------------------------------------------------- #
# Descargas
# --------------------------------------------------------------------------- #
def _descargar_lote_yf(tickers_yf, start=None, end=None, period=None,
                       intervalo="1d") -> pd.DataFrame | None:
    """Un intento de descarga batch con yfinance."""
    kwargs = dict(interval=intervalo, auto_adjust=True, progress=False,
                  threads=True, group_by="column")
    if period is not None:
        kwargs["period"] = period
    else:
        kwargs["start"] = start
        kwargs["end"] = end
    return yf.download(tickers_yf, **kwargs)


def descargar(tickers, start=None, end=None, period=None, intervalo="1d"):
    """Descarga OHLCV para varios tickers con reintentos/backoff y por lotes.

    Devuelve dict {ticker_canonico: DataFrame OHLCV}. Los tickers sin datos
    quedan fuera del dict.
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    tickers = list(dict.fromkeys(t.strip().upper() for t in tickers))
    resultado: dict[str, pd.DataFrame] = {}

    for i in range(0, len(tickers), config.LOTE_DESCARGA):
        lote = tickers[i:i + config.LOTE_DESCARGA]
        mapa_yf = {a_simbolo_yf(t): t for t in lote}
        df = None
        for intento in range(config.REINTENTOS_MAX):
            try:
                df = _descargar_lote_yf(list(mapa_yf.keys()), start=start,
                                        end=end, period=period,
                                        intervalo=intervalo)
                if df is not None and len(df) > 0:
                    break
            except Exception as exc:  # noqa: BLE001 (queremos capturar todo)
                espera = config.BACKOFF_BASE_SEG * (2 ** intento)
                print(f"[datos] fallo lote {intento+1}/{config.REINTENTOS_MAX} "
                      f"({exc!r}); reintento en {espera:.0f}s")
                time.sleep(espera)
        if df is None or len(df) == 0:
            continue
        for sym_yf, canonico in mapa_yf.items():
            norm = _normalizar_ohlcv(df, sym_yf)
            if norm is not None:
                resultado[canonico] = norm
        # cortesía anti rate-limit entre lotes
        if i + config.LOTE_DESCARGA < len(tickers):
            time.sleep(1.0)

    # fallback stooq para los que faltaron
    faltantes = [t for t in tickers if t not in resultado]
    for t in faltantes:
        alt = _descargar_stooq(t, start=start, end=end)
        if alt is not None:
            resultado[t] = alt
    return resultado


def _descargar_stooq(ticker, start=None, end=None) -> pd.DataFrame | None:
    """Fallback best-effort vía stooq (CSV directo). Puede estar bloqueado."""
    sym = a_simbolo_stooq(ticker)
    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.text or "Date" not in r.text[:100]:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or "Close" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df.rename(columns=str.title)[COLUMNAS]
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df if len(df) else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Caché parquet (NO va a git)
# --------------------------------------------------------------------------- #
def ruta_cache(ticker: str):
    return config.CACHE_PRECIOS_DIR / f"{ticker.upper()}.parquet"


def cargar_cache(ticker: str) -> pd.DataFrame | None:
    ruta = ruta_cache(ticker)
    if not ruta.exists():
        return None
    try:
        df = pd.read_parquet(ruta)
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception:  # noqa: BLE001 (caché corrupta -> reconstruir)
        return None


def guardar_cache(ticker: str, df: pd.DataFrame) -> None:
    if df is None or len(df) == 0:
        return
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(ruta_cache(ticker))


def actualizar_precios(tickers, dias_historial=None):
    """Asegura que cada ticker tenga precios recientes en caché.

    Estrategia incremental: si ya hay caché, solo descarga desde la última
    fecha (con solape de seguridad); si no, descarga el historial completo
    solicitado. Devuelve dict {ticker: DataFrame}.
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    dias_historial = dias_historial or config.DIAS_HISTORIAL_CACHE
    hoy = pd.Timestamp(datetime.now(config.TZ_ET).date())

    nuevos_start: dict[str, pd.Timestamp] = {}
    cache_actual: dict[str, pd.DataFrame] = {}
    for t in tickers:
        c = cargar_cache(t)
        if c is not None and len(c) > 0:
            cache_actual[t] = c
            nuevos_start[t] = c.index.max() - pd.Timedelta(days=5)  # solape
        else:
            nuevos_start[t] = hoy - pd.Timedelta(days=dias_historial)

    # Agrupamos por fecha de inicio para descargar eficiente (la mayoría
    # comparte el mismo start incremental o el mismo start histórico).
    por_start: dict[pd.Timestamp, list[str]] = {}
    for t, s in nuevos_start.items():
        por_start.setdefault(s.normalize(), []).append(t)

    resultado: dict[str, pd.DataFrame] = {}
    fin = (hoy + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    for start, grupo in por_start.items():
        nuevos = descargar(grupo, start=start.strftime("%Y-%m-%d"), end=fin)
        for t in grupo:
            base = cache_actual.get(t)
            fresco = nuevos.get(t)
            if base is not None and fresco is not None:
                comb = pd.concat([base, fresco])
                comb = comb[~comb.index.duplicated(keep="last")].sort_index()
            elif fresco is not None:
                comb = fresco
            elif base is not None:
                comb = base
            else:
                continue
            # recortamos a la ventana de caché para no engordar
            limite = hoy - pd.Timedelta(days=dias_historial)
            comb = comb[comb.index >= limite]
            guardar_cache(t, comb)
            resultado[t] = comb
    return resultado
