"""Análisis fundamental con datos gratuitos de yfinance (.info y .calendar).

Construye un SCORE DE SALUD FINANCIERA (0..100) a partir de rentabilidad,
apalancamiento, crecimiento y valoración, y expone las métricas de analistas
(precio objetivo, recomendación media) y la fecha del próximo reporte.

Criterio de DETERIORO GRAVE (documentado): una empresa se descarta si su score
< SCORE_FUNDAMENTAL_MINIMO o si dispara cualquier veto duro (margen neto muy
negativo, deuda/EBITDA excesiva, ingresos desplomándose). El escaneo puede
saltarse el descarte SOLO si la señal del modelo es excepcional.

Nota honesta: .info es una FOTO actual (no hay historial gratuito point-in-time),
por eso estos fundamentales alimentan la decisión EN VIVO, no el modelo histórico.
"""
from __future__ import annotations

from datetime import date

import yfinance as yf

from . import config


def _num(x):
    """Convierte a float si es posible; None si no."""
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _map_lineal(x, malo, bueno):
    """Mapea x a 0..1 linealmente (clamp). malo->0, bueno->1.

    Sirve tanto para métricas "más alto mejor" (bueno>malo) como "más bajo
    mejor" (bueno<malo). Devuelve 0.5 si x es None (neutral).
    """
    if x is None:
        return 0.5
    if bueno == malo:
        return 0.5
    t = (x - malo) / (bueno - malo)
    return max(0.0, min(1.0, t))


def analizar(ticker: str, info: dict | None = None,
             calendario: dict | None = None) -> dict:
    """Devuelve el análisis fundamental de un ticker.

    Estructura del resultado:
      score (0..100), subscores, métricas crudas, analistas, próxima fecha de
      resultados, vetos disparados y bandera 'deterioro_grave'.
    """
    tk = yf.Ticker(config_ticker(ticker))
    if info is None:
        try:
            info = tk.info or {}
        except Exception:  # noqa: BLE001
            info = {}
    if calendario is None:
        try:
            calendario = tk.calendar or {}
        except Exception:  # noqa: BLE001
            calendario = {}

    margen_neto = _num(info.get("profitMargins"))
    margen_op = _num(info.get("operatingMargins"))
    roe = _num(info.get("returnOnEquity"))
    total_deuda = _num(info.get("totalDebt"))
    ebitda = _num(info.get("ebitda"))
    crec_ingresos = _num(info.get("revenueGrowth"))
    crec_bpa = _num(info.get("earningsGrowth"))
    if crec_bpa is None:
        crec_bpa = _num(info.get("earningsQuarterlyGrowth"))
    pe = _num(info.get("trailingPE")) or _num(info.get("forwardPE"))
    market_cap = _num(info.get("marketCap"))
    fcf = _num(info.get("freeCashflow"))
    precio = _num(info.get("currentPrice"))

    deuda_ebitda = None
    if total_deuda is not None and ebitda is not None and ebitda > 0:
        deuda_ebitda = total_deuda / ebitda
    p_fcf = None
    if market_cap is not None and fcf is not None and fcf > 0:
        p_fcf = market_cap / fcf

    # --- subscores 0..1 ---
    s_margen = _map_lineal(margen_neto, -0.05, 0.20)
    s_margen_op = _map_lineal(margen_op, 0.0, 0.25)
    s_roe = _map_lineal(roe, 0.0, 0.25)
    s_apalanc = _map_lineal(deuda_ebitda, 6.0, 1.0) if deuda_ebitda is not None else (
        0.15 if (ebitda is not None and ebitda <= 0) else 0.5)
    s_crec_ing = _map_lineal(crec_ingresos, -0.10, 0.20)
    s_crec_bpa = _map_lineal(crec_bpa, -0.20, 0.25)
    s_pfcf = _map_lineal(p_fcf, 60.0, 10.0) if p_fcf is not None else 0.35
    s_pe = _map_lineal(pe, 50.0, 10.0) if (pe is not None and pe > 0) else 0.30

    pesos = {
        "margen_neto": (s_margen, 0.15),
        "margen_operativo": (s_margen_op, 0.10),
        "roe": (s_roe, 0.15),
        "apalancamiento": (s_apalanc, 0.15),
        "crecimiento_ingresos": (s_crec_ing, 0.15),
        "crecimiento_bpa": (s_crec_bpa, 0.10),
        "p_fcf": (s_pfcf, 0.10),
        "p_e": (s_pe, 0.10),
    }
    score = 100.0 * sum(v * w for v, w in pesos.values())

    # --- analistas ---
    target_medio = _num(info.get("targetMeanPrice"))
    target_mediano = _num(info.get("targetMedianPrice"))
    reco_media = _num(info.get("recommendationMean"))   # 1=strong buy .. 5=sell
    n_analistas = _num(info.get("numberOfAnalystOpinions"))
    upside_analistas = None
    if target_medio is not None and precio is not None and precio > 0:
        upside_analistas = target_medio / precio - 1.0

    # --- próxima fecha de resultados ---
    prox_resultados = None
    ed = calendario.get("Earnings Date") if calendario else None
    if isinstance(ed, (list, tuple)) and ed:
        prox_resultados = _fecha_iso(ed[0])
    elif ed is not None:
        prox_resultados = _fecha_iso(ed)

    # --- vetos de deterioro grave ---
    vetos = []
    if margen_neto is not None and margen_neto < config.VETO_MARGEN_NETO_MENOR:
        vetos.append(f"margen_neto={margen_neto:.1%}")
    if deuda_ebitda is not None and deuda_ebitda > config.VETO_DEUDA_EBITDA_MAYOR:
        vetos.append(f"deuda/EBITDA={deuda_ebitda:.1f}")
    if crec_ingresos is not None and crec_ingresos < config.VETO_CRECIMIENTO_INGRESOS_MENOR:
        vetos.append(f"crec_ingresos={crec_ingresos:.1%}")
    deterioro_grave = (score < config.SCORE_FUNDAMENTAL_MINIMO) or bool(vetos)

    return {
        "ticker": ticker,
        "score": round(score, 1),
        "subscores": {k: round(v, 3) for k, (v, _w) in pesos.items()},
        "metricas": {
            "margen_neto": margen_neto,
            "margen_operativo": margen_op,
            "roe": roe,
            "deuda_ebitda": deuda_ebitda,
            "crecimiento_ingresos": crec_ingresos,
            "crecimiento_bpa": crec_bpa,
            "p_e": pe,
            "p_fcf": p_fcf,
        },
        "analistas": {
            "precio_objetivo_medio": target_medio,
            "precio_objetivo_mediano": target_mediano,
            "recomendacion_media": reco_media,
            "n_analistas": n_analistas,
            "upside": upside_analistas,
        },
        "proximos_resultados": prox_resultados,
        "vetos": vetos,
        "deterioro_grave": deterioro_grave,
    }


def config_ticker(ticker: str) -> str:
    """Símbolo yfinance (importa localmente para evitar dependencia circular)."""
    from .datos import a_simbolo_yf
    return a_simbolo_yf(ticker)


def _fecha_iso(x):
    try:
        if isinstance(x, date):
            return x.isoformat()
        return str(x)
    except Exception:  # noqa: BLE001
        return None
