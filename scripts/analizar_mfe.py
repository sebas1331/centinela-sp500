#!/usr/bin/env python
"""MFE/MAE (Maximum Favorable/Adverse Excursion) por posición.

Solo lectura y reporting: NO toca objetivos, stops, ni el modelo. Mide, para
cada posición abierta y para las cerradas en los últimos 30 días, cuánto se
movió el precio A FAVOR (máximo High) y EN CONTRA (mínimo Low) desde su
entrada hasta hoy, usando barras diarias reales de yfinance.

Sirve para ver si el sistema deja ganancias sobre la mesa: la gestión diaria
(centinela/simulador.py::gestionar_posiciones) SÍ evalúa el High/Low de cada
día contra el objetivo/stop vigentes, así que si una posición tocó su
objetivo INICIAL sin haberse cerrado, hay tres explicaciones legítimas (no
necesariamente un bug) y este script las señala en el resumen:

  1. El objetivo es variable y se recalcula a diario (ATR/resistencia): puede
     haber SUBIDO desde la entrada, así que tocar el objetivo_inicial (más
     bajo) no dispara la salida contra el objetivo vigente (más alto).
  2. En la Cartera A, si el mismo día se tocan stop Y objetivo, gana el stop
     por diseño (regla conservadora, ver centinela/ejecucion.py).
  3. Falta de datos ese día en el pipeline (yfinance sin barra, o el post-
     cierre no corrió) — en ese caso ese día nunca se evaluó.

Uso:
    python scripts/analizar_mfe.py [--dias-cerradas 30]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import config, datos  # noqa: E402


# --------------------------------------------------------------------------- #
# Cálculo puro (testable sin red)
# --------------------------------------------------------------------------- #
def calcular_mfe_mae(entrada: float, fecha_entrada, objetivo_inicial,
                     ventana: pd.DataFrame) -> dict | None:
    """Calcula MFE/MAE/P&L para una posición dada su ventana de precios OHLC.

    `ventana` ya debe venir recortada a [fecha_entrada, hoy]. Devuelve None si
    la ventana no tiene datos utilizables.
    """
    if ventana is None or len(ventana) == 0 or entrada is None or entrada <= 0:
        return None

    max_high = float(ventana["High"].max())
    fecha_max = ventana["High"].idxmax()
    min_low = float(ventana["Low"].min())
    fecha_min = ventana["Low"].idxmin()
    ultimo_close = float(ventana["Close"].iloc[-1])
    fecha_ultimo = ventana.index[-1]

    mfe_pct = (max_high - entrada) / entrada * 100.0
    mae_pct = (min_low - entrada) / entrada * 100.0
    pnl_actual_pct = (ultimo_close - entrada) / entrada * 100.0

    toco_5pct = mfe_pct >= 5.0
    toco_objetivo = (bool(max_high >= objetivo_inicial)
                     if objetivo_inicial is not None and pd.notna(objetivo_inicial)
                     else None)

    return {
        "mfe_pct": mfe_pct,
        "fecha_mfe": pd.Timestamp(fecha_max).date().isoformat(),
        "mae_pct": mae_pct,
        "fecha_mae": pd.Timestamp(fecha_min).date().isoformat(),
        "pnl_actual_pct": pnl_actual_pct,
        "fecha_ultimo_precio": pd.Timestamp(fecha_ultimo).date().isoformat(),
        "toco_5pct": toco_5pct,
        "toco_objetivo": toco_objetivo,
        "dias_en_ventana": int(len(ventana)),
    }


# --------------------------------------------------------------------------- #
# Selección de posiciones y orquestación de datos
# --------------------------------------------------------------------------- #
def seleccionar_posiciones(bit: pd.DataFrame, dias_cerradas: int,
                           hoy: pd.Timestamp) -> pd.DataFrame:
    abiertas = bit[bit["estado"] == "abierta"].copy()
    cerr = bit[bit["estado"] == "cerrada"].copy()
    if len(cerr):
        fs = pd.to_datetime(cerr["fecha_salida"], errors="coerce")
        cerr = cerr[fs >= (hoy - pd.Timedelta(days=dias_cerradas))]
    return pd.concat([abiertas, cerr], ignore_index=True)


def analizar(bit: pd.DataFrame, dias_cerradas: int = 30) -> pd.DataFrame:
    """Devuelve un DataFrame con una fila por posición y sus métricas MFE/MAE."""
    hoy = pd.Timestamp(datetime.now(config.TZ_ET).date())
    sel = seleccionar_posiciones(bit, dias_cerradas, hoy)

    if len(sel) == 0:
        return sel.assign(**{k: pd.Series(dtype="object") for k in (
            "mfe_pct", "fecha_mfe", "mae_pct", "fecha_mae", "pnl_actual_pct",
            "fecha_ultimo_precio", "toco_5pct", "toco_objetivo",
            "dias_en_ventana", "sin_datos")})

    inicio_global = pd.to_datetime(sel["fecha_entrada"]).min()
    fin = (hoy + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    tickers = sorted(sel["ticker"].unique().tolist())
    print(f"[mfe] descargando histórico de {len(tickers)} tickers desde "
         f"{inicio_global.date()} hasta hoy...", flush=True)
    precios = datos.descargar(tickers, start=inicio_global.strftime("%Y-%m-%d"),
                              end=fin)
    print(f"[mfe] datos obtenidos para {len(precios)}/{len(tickers)} tickers.",
         flush=True)

    filas = []
    for _, row in sel.iterrows():
        entrada_fecha = pd.Timestamp(row["fecha_entrada"])
        df = precios.get(row["ticker"])
        ventana = None
        if df is not None:
            ventana = df[(df.index >= entrada_fecha) & (df.index <= hoy)]

        metricas = calcular_mfe_mae(row.get("precio_entrada"), entrada_fecha,
                                    row.get("objetivo_inicial"), ventana)
        base = row.to_dict()
        if metricas is None:
            base["sin_datos"] = True
            for k in ("mfe_pct", "fecha_mfe", "mae_pct", "fecha_mae",
                     "pnl_actual_pct", "fecha_ultimo_precio", "toco_5pct",
                     "toco_objetivo", "dias_en_ventana"):
                base[k] = None
        else:
            base["sin_datos"] = False
            base.update(metricas)
        filas.append(base)

    return pd.DataFrame(filas)


# --------------------------------------------------------------------------- #
# Reporte markdown
# --------------------------------------------------------------------------- #
def _fmt_pct(x) -> str:
    return f"{x:+.2f}%" if x is not None and pd.notna(x) else "—"


def _fmt_bool(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return "✅ sí" if x else "no"


def _tabla(df: pd.DataFrame, con_pnl_cierre: bool) -> list[str]:
    if len(df) == 0:
        return ["_Sin posiciones en esta categoría._"]
    orden = df.sort_values("mfe_pct", ascending=False, na_position="last")
    cab = ["Ticker", "Cartera", "Entrada", "Precio entrada", "MFE %",
          "Fecha MFE", "MAE %", "Fecha MAE", "P&L actual %"]
    if con_pnl_cierre:
        cab += ["P&L al cierre real", "Motivo salida"]
    cab += ["¿Tocó +5%?", "¿Tocó objetivo?", "Objetivo inicial"]
    L = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    for _, r in orden.iterrows():
        if r.get("sin_datos"):
            fila = [r["ticker"], r["portafolio"], r["fecha_entrada"],
                   f"{r['precio_entrada']:.2f}", "sin datos de yfinance", "—",
                   "—", "—", "—"]
            if con_pnl_cierre:
                fila += [_fmt_pct(r.get("pnl_pct") and r["pnl_pct"] * 100),
                        r.get("motivo_salida") or "—"]
            fila += ["—", "—", f"{r['objetivo_inicial']:.2f}"]
        else:
            fila = [r["ticker"], r["portafolio"], r["fecha_entrada"],
                   f"{r['precio_entrada']:.2f}", _fmt_pct(r["mfe_pct"]),
                   r["fecha_mfe"], _fmt_pct(r["mae_pct"]), r["fecha_mae"],
                   _fmt_pct(r["pnl_actual_pct"])]
            if con_pnl_cierre:
                pnl_cierre = r.get("pnl_pct")
                fila += [_fmt_pct(pnl_cierre * 100 if pd.notna(pnl_cierre) else None),
                        r.get("motivo_salida") or "—"]
            fila += [_fmt_bool(r["toco_5pct"]), _fmt_bool(r["toco_objetivo"]),
                    f"{r['objetivo_inicial']:.2f}"]
        L.append("| " + " | ".join(str(c) for c in fila) + " |")
    return L


def generar_markdown(df: pd.DataFrame, dias_cerradas: int) -> tuple[str, dict]:
    abiertas = df[df["estado"] == "abierta"]
    cerradas = df[df["estado"] == "cerrada"]
    con_datos = df[~df["sin_datos"].fillna(True)]
    abiertas_con_datos = con_datos[con_datos["estado"] == "abierta"]

    n_abiertas = len(abiertas)
    n_toco_5 = int(abiertas_con_datos["toco_5pct"].fillna(False).sum())
    con_objetivo_conocido = abiertas_con_datos[abiertas_con_datos["toco_objetivo"].notna()]
    n_toco_obj_no_ejecutada = int((con_objetivo_conocido["toco_objetivo"] == True).sum())  # noqa: E712
    mfe_prom = float(abiertas_con_datos["mfe_pct"].mean()) if len(abiertas_con_datos) else None

    ahora = datetime.now(config.TZ_ET)
    L = [
        "# Análisis MFE/MAE de posiciones\n",
        f"_Generado {ahora:%Y-%m-%d %H:%M ET}_\n",
        "> Experimento educativo, dinero simulado. Solo lectura: no cambia "
        "objetivos, stops ni el modelo.\n",
        "**MFE** (Maximum Favorable Excursion) = mejor precio intradía a "
        "favor desde la entrada. **MAE** (Maximum Adverse Excursion) = peor "
        "precio intradía en contra. Ambos usan High/Low diarios reales, no "
        "el cierre.\n",
        "## Posiciones abiertas\n",
    ]
    L += _tabla(abiertas, con_pnl_cierre=False)
    L.append(f"\n## Posiciones cerradas en los últimos {dias_cerradas} días\n")
    L.append("_El MFE/MAE de las cerradas cubre TODO el período desde la "
             "entrada hasta hoy (no se corta en la fecha de salida), para ver "
             "qué pasó con el precio después de cerrar. Compara `P&L actual %` "
             "(hoy) contra `P&L al cierre real` (el que quedó registrado).\n")
    L += _tabla(cerradas, con_pnl_cierre=True)

    L.append("\n## Resumen\n")
    L.append(f"- **Posiciones abiertas:** {n_abiertas} "
            f"({len(abiertas_con_datos)} con datos de precio).")
    L.append(f"- **Ya tocaron +5% en algún momento (sin haberse cerrado):** "
            f"{n_toco_5} de {len(abiertas_con_datos)}.")
    L.append(f"- **Tocaron su objetivo inicial pero siguen abiertas:** "
            f"{n_toco_obj_no_ejecutada} de {len(con_objetivo_conocido)} con "
            f"objetivo conocido. **No es necesariamente un bug** — el "
            f"objetivo se recalcula a diario y puede haber subido desde la "
            f"entrada, o (en Cartera A) el mismo día se tocó también el stop "
            f"y por diseño gana el stop (regla conservadora). Revisar caso a "
            f"caso en `logs/decisiones-*.log` antes de asumir un fallo.")
    L.append(f"- **MFE promedio (abiertas):** "
            f"{_fmt_pct(mfe_prom) if mfe_prom is not None else '—'}.")
    sin_datos_n = int(df["sin_datos"].fillna(True).sum())
    if sin_datos_n:
        L.append(f"- ⚠️ **{sin_datos_n} posición(es) sin datos de yfinance** "
                f"para este análisis (no afecta a la bitácora real).")

    resumen = {
        "n_abiertas": n_abiertas,
        "n_toco_5pct": n_toco_5,
        "n_toco_objetivo_sin_cerrar": n_toco_obj_no_ejecutada,
        "mfe_promedio": mfe_prom,
        "top_mfe": (abiertas_con_datos.sort_values("mfe_pct", ascending=False)
                   [["ticker", "portafolio", "mfe_pct"]].head(3).to_dict("records")),
        "peor_mae": (abiertas_con_datos.sort_values("mae_pct", ascending=True)
                    [["ticker", "portafolio", "mae_pct"]].head(3).to_dict("records")),
    }
    return "\n".join(L), resumen


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias-cerradas", type=int, default=30,
                    help="ventana de días hacia atrás para posiciones cerradas")
    args = ap.parse_args()

    if not config.ARCHIVO_BITACORA_CSV.exists():
        print("::error::No existe bitacora.csv; nada que analizar.", flush=True)
        return 1

    bit = pd.read_csv(config.ARCHIVO_BITACORA_CSV)
    df = analizar(bit, dias_cerradas=args.dias_cerradas)

    if len(df) == 0:
        print("[mfe] no hay posiciones abiertas ni cerradas recientes; "
             "nada que reportar.", flush=True)
        ruta = config.REPORTES_DIR / "mfe_actual.md"
        ruta.write_text(
            f"# Análisis MFE/MAE de posiciones\n\n"
            f"_Generado {datetime.now(config.TZ_ET):%Y-%m-%d %H:%M ET}_\n\n"
            f"Sin posiciones abiertas ni cerradas en los últimos "
            f"{args.dias_cerradas} días.\n", encoding="utf-8")
        return 0

    if df["sin_datos"].fillna(True).all():
        print("::error::yfinance no devolvió datos para NINGÚN ticker; "
             "no se puede generar el análisis. Probable fallo de red o "
             "rate-limit, no de la bitácora.", flush=True)
        return 1

    md, resumen = generar_markdown(df, args.dias_cerradas)
    ruta = config.REPORTES_DIR / "mfe_actual.md"
    ruta.write_text(md, encoding="utf-8")

    print(f"[mfe] reporte escrito en {ruta}", flush=True)
    print(f"[mfe] abiertas={resumen['n_abiertas']} "
         f"tocaron_+5%={resumen['n_toco_5pct']} "
         f"tocaron_objetivo_sin_cerrar={resumen['n_toco_objetivo_sin_cerrar']} "
         f"mfe_promedio={_fmt_pct(resumen['mfe_promedio'])}", flush=True)
    print("[mfe] top 3 MFE: " + ", ".join(
        f"{r['ticker']}/{r['portafolio']} {_fmt_pct(r['mfe_pct'])}"
        for r in resumen["top_mfe"]), flush=True)
    print("[mfe] top 3 peor MAE: " + ", ".join(
        f"{r['ticker']}/{r['portafolio']} {_fmt_pct(r['mae_pct'])}"
        for r in resumen["peor_mae"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
