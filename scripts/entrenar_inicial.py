#!/usr/bin/env python
"""Entrenamiento inicial + backtest 8-10 años (CORRIDA PESADA, correr 1 vez).

Descarga el histórico del S&P 500, calcula el ATH inicial, construye el dataset,
valida walk-forward (gradient boosting vs logístico), corre el backtest de
trading de las dos carteras (con holdout del último año usado una sola vez),
elige el modelo ganador, entrena el modelo FINAL con todo el histórico y escribe
el reporte honesto en reportes/backtest_inicial.md.

Es RESUMIBLE: cachea el histórico por ticker en .cache/historico, así que si
falla la red se puede relanzar sin volver a descargar lo ya bajado.

Uso:
    python scripts/entrenar_inicial.py [--anios 11] [--limit N] [--tipos hgb,logreg]
"""
from __future__ import annotations

import sys
import argparse
import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (config, universo, datos, etiquetado, backtest as bt,
                       ath as ath_mod)
from centinela.modelo import entrenar_calibrado, elegir_umbral, Modelo

warnings.filterwarnings("ignore")

DIR_HIST = config.CACHE_DIR / "historico"
DIR_HIST.mkdir(parents=True, exist_ok=True)


def _log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def descargar_historico(tickers, refrescar=False):
    """Descarga (period='max') y cachea por ticker. Devuelve dict ticker->df."""
    hist = {}
    pendientes = []
    for t in tickers:
        ruta = DIR_HIST / f"{t}.parquet"
        if ruta.exists() and not refrescar:
            try:
                df = pd.read_parquet(ruta)
                df.index = pd.to_datetime(df.index)
                hist[t] = df.sort_index()
                continue
            except Exception:  # noqa: BLE001
                pass
        pendientes.append(t)

    _log(f"histórico: {len(hist)} en caché, {len(pendientes)} por descargar")
    for i in range(0, len(pendientes), config.LOTE_DESCARGA):
        grupo = pendientes[i:i + config.LOTE_DESCARGA]
        precios = datos.descargar(grupo, period="max")
        for t in grupo:
            df = precios.get(t)
            if df is not None and len(df) > 0:
                df.to_parquet(DIR_HIST / f"{t}.parquet")
                hist[t] = df
        _log(f"  descargados {min(i+config.LOTE_DESCARGA, len(pendientes))}/{len(pendientes)}")
    return hist


def calcular_ath_desde_hist(hist):
    """ATH inicial con TODO el historial disponible (period='max')."""
    ath = {}
    for t, df in hist.items():
        if df is None or df["High"].dropna().empty:
            continue
        ath[t] = {
            "ath": float(df["High"].max()),
            "fecha_ath": pd.Timestamp(df["High"].idxmax()).date().isoformat(),
            "actualizado": pd.Timestamp(df.index.max()).date().isoformat(),
        }
    ath_mod.guardar_ath(ath)
    _log(f"ATH inicial calculado para {len(ath)} tickers -> {config.ARCHIVO_ATH.name}")
    return ath


def evaluar_tipo(ds, hist, tipo):
    """Walk-forward + backtest de trading para un tipo de modelo."""
    _log(f"walk-forward tipo={tipo} ...")
    dsp, info = bt.walk_forward_predicciones(ds, tipo=tipo, verbose=True)
    wf = dsp[(~dsp["holdout"]) & dsp["proba"].notna()]
    ho = dsp[dsp["holdout"] & dsp["proba"].notna()]
    umbral, prec, rec, ns = elegir_umbral(wf["y"], wf["proba"])
    senal_wf = bt.metricas_senal(wf["y"], wf["proba"], umbral)
    senal_ho = (bt.metricas_senal(ho["y"], ho["proba"], umbral)
                if len(ho) else {"n": 0, "n_senales": 0})

    trades = bt.simular_trades(dsp, hist, umbral)
    t_wf = trades[~trades["holdout"]] if len(trades) else trades
    t_ho = trades[trades["holdout"]] if len(trades) else trades

    res = {"tipo": tipo, "umbral": umbral, "info_bloques": info,
           "senal_wf": senal_wf, "senal_ho": senal_ho, "n_trades": int(len(trades))}
    for periodo, sub in [("wf", t_wf), ("ho", t_ho)]:
        for cart in ["A", "B"]:
            res[f"trading_{periodo}_{cart}"] = bt.metricas_trading(
                sub, f"pnl_{cart}", f"fecha_salida_{cart}")
        res[f"motivos_{periodo}_A"] = bt.resumen_por_motivo(sub, "A")
        res[f"motivos_{periodo}_B"] = bt.resumen_por_motivo(sub, "B")
    return res, dsp, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anios", type=int, default=11,
                    help="años de historia para el dataset (>=10 recomendado)")
    ap.add_argument("--limit", type=int, default=0, help="limitar nº de tickers (test)")
    ap.add_argument("--tipos", default="hgb,logreg")
    ap.add_argument("--refrescar", action="store_true")
    args = ap.parse_args()

    t0 = datetime.now()
    tickers = universo.tickers_sp500()
    if args.limit:
        tickers = tickers[:args.limit]
    _log(f"universo: {len(tickers)} tickers")

    hist = descargar_historico(tickers, refrescar=args.refrescar)
    calcular_ath_desde_hist(hist)

    corte = pd.Timestamp(datetime.now(config.TZ_ET).date()) - pd.DateOffset(years=args.anios)
    hist_rec = {t: df[df.index >= corte] for t, df in hist.items()}
    _log("construyendo dataset (features + etiquetas, solo drawdown>=30%) ...")
    ds = etiquetado.construir_dataset(hist_rec, solo_drawdown=True)
    _log(f"dataset: {len(ds)} filas | positivos={ds['y'].mean():.1%} | "
         f"tickers={ds['ticker'].nunique()} | rango {ds['fecha'].min().date()}..{ds['fecha'].max().date()}")

    tipos = [x.strip() for x in args.tipos.split(",") if x.strip()]
    resultados = {}
    for tipo in tipos:
        resultados[tipo], _, _ = evaluar_tipo(ds, hist, tipo)

    # ganador: mayor precisión walk-forward con señales suficientes
    def score(t):
        s = resultados[t]["senal_wf"]
        p = s.get("precision")
        return p if (p == p and s.get("n_senales", 0) >= 25) else -1
    ganador = max(tipos, key=score)
    _log(f"modelo ganador: {ganador} (precisión WF={resultados[ganador]['senal_wf'].get('precision')})")

    # modelo FINAL: entrenado con TODO el dataset (incl. holdout) -> despliegue
    _log("entrenando modelo final con todo el histórico ...")
    est_final = entrenar_calibrado(ds[config.FEATURES_MODELO], ds["y"], tipo=ganador)
    umbral_final = resultados[ganador]["umbral"]
    modelo = Modelo(
        estimador=est_final, features=config.FEATURES_MODELO,
        umbral=umbral_final, tipo=ganador,
        metadatos={
            "entrenado": datetime.now(config.TZ_ET).isoformat(),
            "n_filas_train": int(len(ds)),
            "rango_datos": [ds["fecha"].min().date().isoformat(),
                            ds["fecha"].max().date().isoformat()],
            "anios_backtest": args.anios,
            "base_rate": float(ds["y"].mean()),
            "senal_wf": resultados[ganador]["senal_wf"],
            "senal_holdout": resultados[ganador]["senal_ho"],
            "umbral": umbral_final,
            "comparativa_tipos": {t: {"umbral": resultados[t]["umbral"],
                                       "senal_wf": resultados[t]["senal_wf"]}
                                   for t in tipos},
        },
    )
    modelo.guardar()
    _log(f"modelo guardado -> {config.ARCHIVO_MODELO}")

    # guardar resultados crudos + reporte
    with open(config.REPORTES_DIR / "backtest_inicial.json", "w", encoding="utf-8") as f:
        json.dump(_serializable(resultados), f, ensure_ascii=False, indent=2)
    escribir_reporte(resultados, ganador, ds, args)
    _log(f"LISTO en {(datetime.now()-t0)}")


def _serializable(o):
    if isinstance(o, dict):
        return {k: _serializable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_serializable(x) for x in o]
    if isinstance(o, (np.floating, np.integer)):
        return float(o)
    return o


def _fmt_trading(m):
    if not m or not m.get("n_operaciones"):
        return "| sin operaciones |"
    return (f"| {m['n_operaciones']} | {m['win_rate']:.1%} | {m['expectancy_pct']:.2%} "
            f"| {m['profit_factor']:.2f} | {m['drawdown_max_pct']:.1%} "
            f"| {m['pnl_total_usd']:.0f} |")


def escribir_reporte(resultados, ganador, ds, args):
    g = resultados[ganador]
    L = []
    L.append("# Backtest inicial — Centinela SP500\n")
    L.append(f"_Generado: {datetime.now(config.TZ_ET):%Y-%m-%d %H:%M ET}_\n")
    L.append("> ⚠️ **Experimento educativo con dinero simulado.** Las métricas se "
             "reportan tal cual, sin maquillar. Rentabilidad pasada simulada no "
             "garantiza nada.\n")
    L.append("## Datos y metodología\n")
    L.append(f"- Universo: S&P 500 ({ds['ticker'].nunique()} tickers con datos).")
    L.append(f"- Ventana: {args.anios} años ({ds['fecha'].min().date()} a {ds['fecha'].max().date()}).")
    L.append(f"- Eventos (filas en drawdown ≥30%): **{len(ds):,}** | tasa base de "
             f"+5% en 10 días: **{ds['y'].mean():.1%}**.")
    L.append(f"- Validación: walk-forward estricto + holdout del último año (usado 1 vez).")
    L.append(f"- Modelo ganador: **{ganador}**, umbral de probabilidad **{g['umbral']}**.\n")

    L.append("## Señal (ML) — fuera de muestra\n")
    L.append("| Conjunto | N | Señales | Precisión | Recall | Base rate | AUC |")
    L.append("|---|---|---|---|---|---|---|")
    for nombre, s in [("Walk-forward", g["senal_wf"]), ("Holdout (1 vez)", g["senal_ho"])]:
        if s.get("n"):
            L.append(f"| {nombre} | {s['n']:,} | {s.get('n_senales',0)} | "
                     f"{_p(s.get('precision'))} | {_p(s.get('recall'))} | "
                     f"{_p(s.get('base_rate'))} | {_f(s.get('auc'))} |")
    L.append("")

    L.append("## Trading — Cartera A (con stop ATR) vs B (sin stop)\n")
    L.append("| Periodo | Cartera | N ops | Win rate | Expectancy | Profit factor | Drawdown máx | P&L total (USD, $1k/op) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for periodo, etq in [("wf", "Walk-forward"), ("ho", "Holdout")]:
        for cart in ["A", "B"]:
            L.append(f"| {etq} | {cart} {_fmt_trading(g.get(f'trading_{periodo}_{cart}'))}")
    L.append("")
    L.append("### Motivos de salida (walk-forward)\n")
    L.append(f"- Cartera A: {g.get('motivos_wf_A')}")
    L.append(f"- Cartera B: {g.get('motivos_wf_B')}\n")

    L.append("## Comparativa de modelos (señal walk-forward)\n")
    L.append("| Modelo | Umbral | Señales | Precisión | Recall | AUC |")
    L.append("|---|---|---|---|---|---|")
    for t, r in resultados.items():
        s = r["senal_wf"]
        L.append(f"| {t} | {r['umbral']} | {s.get('n_senales',0)} | "
                 f"{_p(s.get('precision'))} | {_p(s.get('recall'))} | {_f(s.get('auc'))} |")
    L.append("")
    L.append("## Lectura honesta\n")
    L.append("- Predecir rebotes de corto plazo es genuinamente difícil; el valor "
             "está en la PRECISIÓN del umbral alto (pocas señales, mejores que la "
             "tasa base), no en un AUC alto.")
    L.append("- Sesgo de supervivencia: se usan los constituyentes ACTUALES del "
             "S&P 500 (no hay historial point-in-time gratuito). Esto infla algo "
             "los resultados históricos.")
    L.append("- El objetivo no se recalcula día a día en el backtest (el sistema en "
             "vivo sí lo hace); es una aproximación conservadora.")

    ruta = config.REPORTES_DIR / "backtest_inicial.md"
    ruta.write_text("\n".join(L), encoding="utf-8")
    _log(f"reporte -> {ruta}")


def _p(x):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x:.1%}"


def _f(x):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x:.3f}"


if __name__ == "__main__":
    main()
