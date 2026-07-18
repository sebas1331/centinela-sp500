"""Reportes automáticos en español (semanal y mensual).

- Semanal: operaciones de la semana, P&L acumulado por cartera, comparación
  A vs B y observaciones.
- Mensual: análisis post-trade (patrones por sector, motivo de salida, día de
  entrada) y qué aprendió el sistema (o por qué no cambió nada).

Todas las métricas salen de la bitácora real. Nada se maquilla.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import config, bitacora


def _metricas(df: pd.DataFrame) -> dict:
    """Métricas de una cartera a partir de sus operaciones CERRADAS."""
    if df is None or len(df) == 0:
        return {"n": 0}
    pnl = df["pnl_pct"].to_numpy(dtype=float)
    gan, per = pnl[pnl > 0], pnl[pnl < 0]
    pf = gan.sum() / abs(per.sum()) if per.sum() != 0 else float("inf")
    capital = config.MAX_POSICIONES_ABIERTAS * config.CAPITAL_POR_OPERACION
    orden = df.sort_values("fecha_salida")
    eq = np.concatenate([[capital], capital + (orden["pnl_pct"].to_numpy(float)
                         * config.CAPITAL_POR_OPERACION).cumsum()])
    pico = np.maximum.accumulate(eq)
    dd = eq - pico
    i = int(np.argmin(dd))
    return {
        "n": int(len(pnl)),
        "win_rate": float((pnl > 0).mean()),
        "pnl_medio": float(pnl.mean()),
        "expectancy": float(pnl.mean()),
        "profit_factor": float(pf),
        "pnl_total_usd": float(pnl.sum() * config.CAPITAL_POR_OPERACION),
        "drawdown_max_pct": float(dd[i] / pico[i]) if pico[i] > 0 else 0.0,
    }


def _fmt(m: dict) -> str:
    if not m or not m.get("n"):
        return "sin operaciones cerradas"
    return (f"n={m['n']} · win={m['win_rate']:.1%} · P&L medio={m['pnl_medio']:.2%} "
            f"· PF={m['profit_factor']:.2f} · DDmáx={m['drawdown_max_pct']:.1%} "
            f"· P&L total=${m['pnl_total_usd']:.0f}")


def _tabla_comparativa(cerr: pd.DataFrame) -> list[str]:
    L = ["| Métrica | Cartera A (con stop) | Cartera B (sin stop) |",
         "|---|---|---|"]
    ma = _metricas(cerr[cerr["portafolio"] == "A"])
    mb = _metricas(cerr[cerr["portafolio"] == "B"])
    def g(m, k, f):
        return f(m[k]) if m.get("n") else "—"
    L.append(f"| Operaciones | {ma.get('n',0)} | {mb.get('n',0)} |")
    L.append(f"| Win rate | {g(ma,'win_rate',lambda x:f'{x:.1%}')} | {g(mb,'win_rate',lambda x:f'{x:.1%}')} |")
    L.append(f"| P&L medio | {g(ma,'pnl_medio',lambda x:f'{x:.2%}')} | {g(mb,'pnl_medio',lambda x:f'{x:.2%}')} |")
    L.append(f"| Expectancy | {g(ma,'expectancy',lambda x:f'{x:.2%}')} | {g(mb,'expectancy',lambda x:f'{x:.2%}')} |")
    L.append(f"| Profit factor | {g(ma,'profit_factor',lambda x:f'{x:.2f}')} | {g(mb,'profit_factor',lambda x:f'{x:.2f}')} |")
    L.append(f"| Drawdown máx | {g(ma,'drawdown_max_pct',lambda x:f'{x:.1%}')} | {g(mb,'drawdown_max_pct',lambda x:f'{x:.1%}')} |")
    L.append(f"| P&L total (USD) | {g(ma,'pnl_total_usd',lambda x:f'${x:.0f}')} | {g(mb,'pnl_total_usd',lambda x:f'${x:.0f}')} |")
    return L


def generar_semanal(fecha=None) -> str:
    fecha = pd.Timestamp(fecha or datetime.now(config.TZ_ET).date())
    anio, semana, _ = fecha.isocalendar()
    ini = fecha - pd.Timedelta(days=fecha.weekday())
    fin = ini + pd.Timedelta(days=6)
    df = bitacora.cargar_df()
    cerr = df[df["estado"] == "cerrada"].copy()
    cerr["fs"] = pd.to_datetime(cerr["fecha_salida"], errors="coerce")
    semana_df = cerr[(cerr["fs"] >= ini) & (cerr["fs"] <= fin)]
    abiertas = df[df["estado"] == "abierta"]

    L = [f"# Reporte semanal — semana {anio}-W{semana:02d}\n",
         f"_Del {ini.date()} al {fin.date()} · generado {datetime.now(config.TZ_ET):%Y-%m-%d %H:%M ET}_\n",
         "> Experimento educativo, dinero simulado. Métricas reales sin maquillar.\n",
         "## Operaciones cerradas esta semana\n"]
    if len(semana_df):
        L.append("| ID | Ticker | Cartera | Entrada | Salida | Motivo | P&L% |")
        L.append("|---|---|---|---|---|---|---|")
        for _, r in semana_df.sort_values(["fecha_salida", "grupo"]).iterrows():
            L.append(f"| {r['id']} | {r['ticker']} | {r['portafolio']} | "
                     f"{r['fecha_entrada']} | {r['fecha_salida']} | "
                     f"{r['motivo_salida']} | {float(r['pnl_pct']):.2%} |")
    else:
        L.append("_Sin operaciones cerradas esta semana._")
    L.append(f"\n**Posiciones abiertas al cierre:** {len(abiertas)//2} (en cada cartera).\n")

    L.append("## Acumulado histórico por cartera (todas las operaciones cerradas)\n")
    L += _tabla_comparativa(cerr)
    L.append("\n## Observaciones\n")
    L.append(_observacion_ab(cerr))

    ruta = config.REPORTES_DIR / f"semana-{anio}-W{semana:02d}.md"
    ruta.write_text("\n".join(L), encoding="utf-8")
    return str(ruta)


def generar_mensual(fecha=None) -> str:
    fecha = pd.Timestamp(fecha or datetime.now(config.TZ_ET).date())
    # mes anterior (el reporte mensual se genera al inicio del mes siguiente)
    primero = fecha.replace(day=1)
    fin_mes_ant = primero - pd.Timedelta(days=1)
    ini_mes_ant = fin_mes_ant.replace(day=1)
    df = bitacora.cargar_df()
    cerr = df[df["estado"] == "cerrada"].copy()
    cerr["fs"] = pd.to_datetime(cerr["fecha_salida"], errors="coerce")
    mes_df = cerr[(cerr["fs"] >= ini_mes_ant) & (cerr["fs"] <= fin_mes_ant)]

    etq = f"{ini_mes_ant.year}-{ini_mes_ant.month:02d}"
    L = [f"# Reporte mensual — {etq}\n",
         f"_Generado {datetime.now(config.TZ_ET):%Y-%m-%d %H:%M ET}_\n",
         "> Experimento educativo, dinero simulado. Métricas reales sin maquillar.\n",
         f"## Resumen del mes ({len(mes_df)} operaciones cerradas)\n"]
    L += _tabla_comparativa(mes_df if len(mes_df) else cerr.iloc[0:0])

    L.append("\n## Análisis post-trade (patrones)\n")
    L += _patrones(mes_df)

    L.append("\n## Autoaprendizaje\n")
    n_nuevas = len(mes_df)
    if n_nuevas < config.MIN_OPERACIONES_PARA_CAMBIO:
        L.append(f"- Operaciones cerradas nuevas este mes: **{n_nuevas}** "
                 f"(< {config.MIN_OPERACIONES_PARA_CAMBIO}). **Regla dura:** no se "
                 f"cambia umbral/features/stop hasta acumular ≥"
                 f"{config.MIN_OPERACIONES_PARA_CAMBIO} cierres nuevos. Sin cambios.")
    else:
        L.append(f"- Operaciones cerradas nuevas: **{n_nuevas}** "
                 f"(≥ {config.MIN_OPERACIONES_PARA_CAMBIO}). Hay base estadística para "
                 f"evaluar cambios; cualquier ajuste se registrará en CHANGELOG.md "
                 f"con evidencia. El holdout nunca se reutiliza para tunear.")

    ruta = config.REPORTES_DIR / f"mes-{etq}.md"
    ruta.write_text("\n".join(L), encoding="utf-8")
    return str(ruta)


def _observacion_ab(cerr: pd.DataFrame) -> str:
    ma = _metricas(cerr[cerr["portafolio"] == "A"])
    mb = _metricas(cerr[cerr["portafolio"] == "B"])
    if not ma.get("n") or not mb.get("n"):
        return "_Aún no hay suficientes operaciones cerradas para comparar A vs B._"
    mejor = "B (sin stop)" if mb["expectancy"] > ma["expectancy"] else "A (con stop)"
    return (f"- Hasta ahora, por expectancy conviene la **Cartera {mejor}** "
            f"(A={ma['expectancy']:.2%} vs B={mb['expectancy']:.2%}). "
            f"Es una lectura provisional; se consolidará con más operaciones.")


def _patrones(df: pd.DataFrame) -> list[str]:
    if df is None or len(df) == 0:
        return ["_Sin operaciones cerradas en el periodo para analizar patrones._"]
    L = []
    for col, etq in [("sector", "Sector"), ("motivo_salida", "Motivo de salida")]:
        if col in df.columns and df[col].notna().any():
            g = df.groupby(col)["pnl_pct"].agg(["count", "mean"]).sort_values("mean", ascending=False)
            L.append(f"**{etq}:**\n")
            L.append("| " + etq + " | Ops | P&L medio |")
            L.append("|---|---|---|")
            for idx, row in g.iterrows():
                L.append(f"| {idx} | {int(row['count'])} | {row['mean']:.2%} |")
            L.append("")
    return L or ["_Sin dimensiones suficientes para el análisis._"]
