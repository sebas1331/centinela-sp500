"""Screener: convierte precios + modelo + fundamentales en DECISIONES de entrada.

Pipeline de decisión (y todo se registra en decisiones.log para auditar):
  1. Filtro base: solo acciones en drawdown >= 30% respecto al ATH.
  2. Modelo: probabilidad de +5% en <=10 días; se exige prob >= umbral.
  3. Veto fundamental: si la empresa está en deterioro grave se DESCARTA, salvo
     que la señal sea EXCEPCIONAL (prob >= UMBRAL_PROB_EXCEPCIONAL).
  4. Sentimiento de titulares (VADER): señal secundaria, se registra como matiz.

Los fundamentales y el sentimiento solo se consultan para los candidatos con
señal del modelo (pocas llamadas de red por escaneo).
"""
from __future__ import annotations

import pandas as pd

from . import config, ath as ath_mod, fundamentales as fu, sentimiento as se
from .features import features_ultima_fila, _atr
from .objetivos import resistencia_reciente


def escanear(precios: dict, modelo, ath_dict: dict | None = None,
             sectores: dict | None = None, consultar_fundamentales: bool = True):
    """Escanea el universo y devuelve (decisiones, lineas_log, resumen).

    decisiones: lista de dicts listos para registrar como entradas, ordenada por
    probabilidad descendente.
    """
    ath_dict = ath_dict if ath_dict is not None else ath_mod.cargar_ath()
    sectores = sectores or {}
    decisiones = []
    lineas = []
    n_universo = len(precios)
    n_drawdown = 0

    for ticker, df in precios.items():
        if df is None or len(df) < 220:
            continue
        cierre = float(df["Close"].iloc[-1])
        dd = ath_mod.drawdown(ticker, cierre, ath_dict)
        if dd is None or dd < config.DRAWDOWN_MINIMO:
            continue
        n_drawdown += 1

        feats = features_ultima_fila(df)
        if feats is None:
            lineas.append(f"{ticker} | dd={dd:.1%} | features insuficientes -> descartado")
            continue
        prob = float(modelo.predecir_proba(feats)[0])

        if prob < modelo.umbral:
            lineas.append(f"{ticker} | dd={dd:.1%} | prob={prob:.3f} < umbral -> SIN SEÑAL")
            continue

        # Señal del modelo -> consultar fundamentales + sentimiento
        excepcional = prob >= config.UMBRAL_PROB_EXCEPCIONAL
        analisis, sent = None, {"score": 0.0, "n": 0}
        if consultar_fundamentales:
            try:
                analisis = fu.analizar(ticker)
            except Exception as exc:  # noqa: BLE001
                lineas.append(f"{ticker} | prob={prob:.3f} | fallo fundamentales ({exc!r})")
            try:
                sent = se.sentimiento_titulares(ticker)
            except Exception:  # noqa: BLE001
                pass

        if analisis and analisis["deterioro_grave"] and not excepcional:
            lineas.append(
                f"{ticker} | dd={dd:.1%} | prob={prob:.3f} | SEÑAL pero VETO por "
                f"deterioro (score={analisis['score']}, vetos={analisis['vetos']}) -> NO ENTRA")
            continue

        score_f = analisis["score"] if analisis else None
        target_analista = (analisis["analistas"]["precio_objetivo_medio"]
                           if analisis else None)
        atr = float(_atr(df).iloc[-1])
        resistencia = resistencia_reciente(df)
        etiqueta_exc = " (EXCEPCIONAL: entra pese a deterioro)" if (
            analisis and analisis["deterioro_grave"] and excepcional) else ""
        nota = (f"prob={prob:.3f} score_fund={score_f} sent={sent['score']} "
                f"(n={sent['n']}){etiqueta_exc}")
        decisiones.append({
            "ticker": ticker, "proba": round(prob, 4),
            "score_fundamental": score_f, "atr": round(atr, 4),
            "resistencia": round(resistencia, 4) if resistencia else None,
            "target_analista": target_analista, "sector": sectores.get(ticker),
            "sentimiento": sent["score"], "notas": nota,
        })
        lineas.append(
            f"{ticker} | dd={dd:.1%} | prob={prob:.3f} | ENTRAR{etiqueta_exc} | "
            f"score_fund={score_f} | sent={sent['score']} | obj_analista={target_analista}")

    decisiones.sort(key=lambda d: d["proba"], reverse=True)
    resumen = {"universo": n_universo, "en_drawdown": n_drawdown,
               "con_senal": len(decisiones)}
    lineas.insert(0, f"RESUMEN: universo={n_universo} drawdown>=30%={n_drawdown} "
                     f"con_senal={len(decisiones)}")
    return decisiones, lineas, resumen
