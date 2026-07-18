"""Validación anti-sobreajuste y backtest de trading.

Dos evaluaciones complementarias:

1) VALIDACIÓN DE SEÑAL (ML), walk-forward estricto:
   se entrena con datos hasta t y se evalúa en el bloque siguiente; jamás
   k-fold aleatorio (habría leakage temporal). Con las predicciones fuera de
   muestra (pre-holdout) se elige el umbral para precisión. El HOLDOUT (último
   año) se usa UNA sola vez para el reporte final; prohibido tunear contra él.

2) BACKTEST DE TRADING:
   con la señal, se simulan las operaciones REALES (entrada al open del día
   siguiente, salida por objetivo/tiempo/stop) para las dos carteras (A con stop
   ATR, B sin stop), y se calculan métricas honestas: win rate, P&L medio,
   expectancy, profit factor, drawdown máximo y nº de operaciones.

Simplificación documentada del backtest: el objetivo se fija al entrar y NO se
recalcula día a día (el sistema EN VIVO sí lo recalcula en cada escaneo). Es una
aproximación conservadora y estándar para el backtest.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import config, objetivos, ejecucion
from .modelo import entrenar_calibrado, elegir_umbral, construir_estimador, Modelo


# --------------------------------------------------------------------------- #
# 1) Predicciones walk-forward
# --------------------------------------------------------------------------- #
def walk_forward_predicciones(dataset: pd.DataFrame, tipo: str = "hgb",
                              meses_bloque: int = config.MESES_BLOQUE_WALKFORWARD,
                              anios_holdout: int = config.ANIOS_HOLDOUT,
                              min_train_anios: int = 3, verbose: bool = True):
    """Añade columna 'proba' (fuera de muestra) y 'holdout' al dataset.

    Devuelve (dataset_con_proba, info_bloques).
    """
    ds = dataset.sort_values("fecha").reset_index(drop=True).copy()
    ds["proba"] = np.nan
    ds["holdout"] = False
    feats = config.FEATURES_MODELO

    fecha_min = ds["fecha"].min()
    fecha_max = ds["fecha"].max()
    inicio_holdout = fecha_max - pd.DateOffset(years=anios_holdout)
    inicio_wf = fecha_min + pd.DateOffset(years=min_train_anios)

    bloques = []
    b_ini = inicio_wf
    while b_ini < inicio_holdout:
        b_fin = min(b_ini + pd.DateOffset(months=meses_bloque), inicio_holdout)
        train = ds[ds["fecha"] < b_ini]
        mask_bloque = (ds["fecha"] >= b_ini) & (ds["fecha"] < b_fin)
        if train["y"].nunique() == 2 and mask_bloque.any():
            modelo = entrenar_calibrado(train[feats], train["y"], tipo=tipo)
            ds.loc[mask_bloque, "proba"] = modelo.predict_proba(
                ds.loc[mask_bloque, feats])[:, 1]
            bloques.append((b_ini.date().isoformat(), b_fin.date().isoformat(),
                            int(train.shape[0]), int(mask_bloque.sum())))
        b_ini = b_fin
    if verbose:
        print(f"[backtest:{tipo}] {len(bloques)} bloques walk-forward; "
              f"holdout desde {inicio_holdout.date()}")

    # Holdout: entrenar con TODO lo previo al holdout, predecir el holdout (1 vez)
    train_h = ds[ds["fecha"] < inicio_holdout]
    mask_h = ds["fecha"] >= inicio_holdout
    ds.loc[mask_h, "holdout"] = True
    if train_h["y"].nunique() == 2 and mask_h.any():
        modelo_h = entrenar_calibrado(train_h[feats], train_h["y"], tipo=tipo)
        ds.loc[mask_h, "proba"] = modelo_h.predict_proba(ds.loc[mask_h, feats])[:, 1]

    info = {"tipo": tipo, "n_bloques": len(bloques),
            "inicio_holdout": inicio_holdout.date().isoformat(),
            "bloques": bloques}
    return ds, info


def metricas_senal(y_true, proba, umbral):
    """Precisión / recall / base rate / AUC de la señal a un umbral dado."""
    from sklearn.metrics import roc_auc_score
    y_true = np.asarray(y_true).astype(int)
    proba = np.asarray(proba, dtype=float)
    sel = proba >= umbral
    ns = int(sel.sum())
    tp = int((y_true[sel] == 1).sum()) if ns else 0
    prec = tp / ns if ns else float("nan")
    rec = tp / max(1, int((y_true == 1).sum()))
    try:
        auc = roc_auc_score(y_true, proba)
    except Exception:  # noqa: BLE001
        auc = float("nan")
    return {"n": len(y_true), "n_senales": ns, "precision": prec,
            "recall": rec, "base_rate": float(y_true.mean()), "auc": auc}


# --------------------------------------------------------------------------- #
# 2) Backtest de trading
# --------------------------------------------------------------------------- #
def simular_trades(ds_proba: pd.DataFrame, precios: dict, umbral: float,
                   horizonte: int = config.HORIZONTE_DIAS_HABILES) -> pd.DataFrame:
    """Simula operaciones para las filas con señal (proba >= umbral).

    Para cada señal: entrada al open del día siguiente; objetivo/stop por ATR;
    salida por objetivo/tiempo/stop. Devuelve un DataFrame con P&L de la Cartera
    A (con stop) y B (sin stop).
    """
    señales = ds_proba[(ds_proba["proba"] >= umbral)].copy()
    filas = []
    for _, r in señales.iterrows():
        ticker, fecha = r["ticker"], pd.Timestamp(r["fecha"])
        df = precios.get(ticker)
        if df is None or fecha not in df.index:
            continue
        pos = df.index.get_loc(fecha)
        if isinstance(pos, slice):
            continue
        futuro = df.iloc[pos + 1: pos + 1 + horizonte]
        if len(futuro) < horizonte:      # exigimos horizonte completo (justo)
            continue
        entrada = float(futuro["Open"].iloc[0])
        if entrada <= 0:
            continue
        cierre = float(df["Close"].iloc[pos])
        atr_abs = float(r["atr_pct"]) * cierre
        ini = max(0, pos - config.VENTANA_RESISTENCIA + 1)
        resistencia = float(df["High"].iloc[ini:pos + 1].max())

        objetivo = objetivos.objetivo_inicial(entrada, atr_abs, resistencia)
        stop = objetivos.stop_inicial(entrada, atr_abs)

        sal_a = ejecucion.simular_salida(futuro, entrada, objetivo, stop)
        sal_b = ejecucion.simular_salida(futuro, entrada, objetivo, None)
        filas.append({
            "ticker": ticker, "fecha_decision": fecha,
            "fecha_entrada": futuro.index[0], "entrada": round(entrada, 4),
            "proba": round(float(r["proba"]), 4), "y": int(r["y"]),
            "holdout": bool(r["holdout"]),
            "objetivo": round(objetivo, 4), "stop": round(stop, 4),
            "pnl_A": ejecucion.pnl_pct(entrada, sal_a["precio_salida"]),
            "motivo_A": sal_a["motivo"], "fecha_salida_A": sal_a["fecha_salida"],
            "dias_A": sal_a["dias"],
            "pnl_B": ejecucion.pnl_pct(entrada, sal_b["precio_salida"]),
            "motivo_B": sal_b["motivo"], "fecha_salida_B": sal_b["fecha_salida"],
            "dias_B": sal_b["dias"],
        })
    return pd.DataFrame(filas)


def metricas_trading(trades: pd.DataFrame, col_pnl: str, col_fecha_salida: str):
    """Métricas honestas de una cartera: win rate, P&L medio, expectancy,
    profit factor, drawdown máx y nº de operaciones."""
    if trades is None or len(trades) == 0:
        return {"n_operaciones": 0}
    pnl = trades[col_pnl].to_numpy(dtype=float)
    ganancias = pnl[pnl > 0]
    perdidas = pnl[pnl < 0]
    profit_factor = (ganancias.sum() / abs(perdidas.sum())
                     if perdidas.sum() != 0 else float("inf"))

    # Equity realizada ordenada por fecha de salida, anclada a un libro nocional
    # fijo = MAX_POSICIONES_ABIERTAS * CAPITAL_POR_OPERACION (tamaño fijo por
    # operación). El drawdown % se mide contra ese libro para que sea estable e
    # interpretable (no depende del arranque de la curva).
    capital_libro = config.MAX_POSICIONES_ABIERTAS * config.CAPITAL_POR_OPERACION
    orden = trades.sort_values(col_fecha_salida)
    realizado = (orden[col_pnl].to_numpy(dtype=float) * config.CAPITAL_POR_OPERACION).cumsum()
    equity = np.concatenate([[capital_libro], capital_libro + realizado])
    pico = np.maximum.accumulate(equity)
    dd = equity - pico
    i_min = int(np.argmin(dd))
    max_dd_abs = float(dd[i_min])
    max_dd_pct = max_dd_abs / pico[i_min] if pico[i_min] > 0 else 0.0

    return {
        "n_operaciones": int(len(pnl)),
        "win_rate": float((pnl > 0).mean()),
        "pnl_medio_pct": float(pnl.mean()),
        "expectancy_pct": float(pnl.mean()),
        "ganancia_media_pct": float(ganancias.mean()) if len(ganancias) else 0.0,
        "perdida_media_pct": float(perdidas.mean()) if len(perdidas) else 0.0,
        "profit_factor": float(profit_factor),
        "pnl_total_usd": float(pnl.sum() * config.CAPITAL_POR_OPERACION),
        "drawdown_max_usd": max_dd_abs,
        "drawdown_max_pct": max_dd_pct,
    }


def resumen_por_motivo(trades: pd.DataFrame, sufijo: str) -> dict:
    if trades is None or len(trades) == 0:
        return {}
    return trades[f"motivo_{sufijo}"].value_counts().to_dict()
