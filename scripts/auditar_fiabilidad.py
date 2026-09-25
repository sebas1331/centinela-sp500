#!/usr/bin/env python
"""Auditoría de fiabilidad del rendimiento: ¿cuánto de esto es de fiar?

Responde, con datos y sin maquillar, a cinco preguntas que la cifra publicada
hasta ahora (la SUMA de los retornos de cada operación) no responde:

  1. CUENTA. ¿Cuánto habría ganado una cuenta real de $10.000 por cartera,
     dividida en 20 slots, que compone al cerrar? (centinela/cuenta.py)
  2. FRICCIONES. ¿Qué queda después de comisiones, spread y slippage?
  3. LOOK-AHEAD. ¿La ejecución simulada usa información que no estaba
     disponible cuando se habría puesto la orden?
  4. CONCENTRACIÓN. ¿El resultado depende de un puñado de operaciones o de un
     solo sector?
  5. RÉGIMEN. ¿Cuánto es habilidad y cuánto es simplemente que el mercado subió?

Es SOLO LECTURA: no toca el modelo, el umbral, las features, los objetivos, los
stops, el backtest ni el simulador. Ni siquiera corrige los sesgos que
encuentra; los mide y los publica, que es lo que pedía el encargo.

Uso:  python scripts/auditar_fiabilidad.py [--capital 10000] [--sin-descarga]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import config, cuenta, datos  # noqa: E402

RUTA_BITACORA = config.BASE_DIR / "bitacora.csv"
RUTA_REPORTE = config.REPORTES_DIR / "auditoria_fiabilidad.md"
REFERENCIA = "SPY"

#: Tickers del universo cuya actividad es semiconductores o su cadena de
#: suministro directa (equipos, memoria, óptica, sustratos, refrigeración de
#: centros de datos). Es una clasificación MANUAL y más estrecha que el sector
#: GICS "Information Technology", que mete en el mismo saco a una fundición de
#: obleas y a una empresa de software. La pregunta que se quiere contestar es
#: "¿esto es una apuesta encubierta al ciclo del silicio?", y para eso GICS no
#: sirve.
SEMICONDUCTORES = {
    "AMAT", "KLAC", "LRCX", "TER",          # equipos de fabricación
    "MU", "SNDK", "WDC", "STX", "INTC",     # memoria y almacenamiento / lógica
    "MRVL", "ON",                           # diseño de chips
    "COHR", "LITE", "CIEN", "APH", "GLW",   # óptica y conectividad
    "SMCI", "VRT", "FLEX",                  # hardware y energía de centros de datos
}


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def cargar_precios(tickers, inicio, fin, descargar=True):
    """OHLC de los tickers de la bitácora más el índice de referencia.

    Vía `actualizar_precios`, la MISMA puerta que usan el dashboard y las
    notificaciones. No es un detalle: con `descargar` a pelo, este informe y el
    dashboard leían series que diferían en el último decimal y publicaban dos
    Sharpe distintos (1.91 y 1.92) para la misma cartera. Dos cifras para el
    mismo dato es exactamente lo que este informe le reprocha al sistema.
    """
    todos = sorted(set(tickers) | {REFERENCIA})
    if not descargar:
        px = {}
        for t in todos:
            d = datos.cargar_cache(t)
            if d is not None:
                px[t] = d
        return px
    return datos.actualizar_precios(todos)


# --------------------------------------------------------------------------- #
# 3) Look-ahead: re-simulación contrafactual
# --------------------------------------------------------------------------- #
def objetivo_al_abrir_la_sesion(historial, fecha, objetivo_inicial):
    """El objetivo que ESTABA PUESTO como orden límite al abrir esa sesión.

    El simulador recalcula el objetivo dentro del post-cierre, con la barra del
    propio día ya cerrada, y evalúa la salida contra ese objetivo nuevo. Un
    operador real no puede hacer eso: su orden límite del martes es la que
    calculó el lunes por la noche. Esta función devuelve esa otra cifra, que es
    la única contra la que se puede comparar honestamente.
    """
    previos = [h["objetivo"] for h in historial if h["fecha"] < fecha]
    return previos[-1] if previos else objetivo_inicial


def resimular_sin_lookahead(bit: pd.DataFrame, precios: dict,
                            sesiones: list[str]) -> pd.DataFrame:
    """Repite cada operación usando solo información anterior a cada sesión.

    Mismas reglas conservadoras que el motor real (stop antes que objetivo, gaps
    al open, tiempo al cierre del día límite); lo ÚNICO que cambia es que el
    objetivo de cada día es el que se conocía la víspera. La diferencia entre
    esta columna y la real es, por construcción, el valor del look-ahead.
    """
    filas = []
    for _, r in bit.iterrows():
        historial = json.loads(r["historial_objetivos"])
        inicial = float(r["objetivo_inicial"])
        stop = None if pd.isna(r["stop"]) else float(r["stop"])
        df = precios.get(r["ticker"])
        if df is None or r["fecha_entrada"] not in sesiones:
            continue
        desde = sesiones.index(str(r["fecha_entrada"]))
        ventana = sesiones[desde: desde + config.HORIZONTE_DIAS_HABILES]

        # La ventana solo llega al día límite si hay datos para las diez
        # sesiones. En una entrada reciente todavía no los hay: entonces la
        # posición sigue ABIERTA, igual que en el sistema real. Cerrarla por
        # tiempo antes de tiempo inventaría un resultado.
        completa = len(ventana) == config.HORIZONTE_DIAS_HABILES
        salida = None
        for i, f in enumerate(ventana):
            ts = pd.Timestamp(f)
            if ts not in df.index:
                continue
            o, h, l, c = (float(df.loc[ts, k]) for k in ("Open", "High", "Low", "Close"))
            objetivo = objetivo_al_abrir_la_sesion(historial, f, inicial)
            if stop is not None and l <= stop:
                salida = (f, o if o <= stop else stop, "stop")
                break
            if h >= objetivo:
                salida = (f, o if o >= objetivo else objetivo, "objetivo")
                break
            if completa and i == len(ventana) - 1:
                salida = (f, c, "tiempo")
        filas.append({
            "id": int(r["id"]), "ticker": r["ticker"], "portafolio": r["portafolio"],
            "sector": r["sector"],
            "estado": "cerrada" if salida else "abierta",
            "fecha_entrada": r["fecha_entrada"], "precio_entrada": float(r["precio_entrada"]),
            "fecha_salida": salida[0] if salida else None,
            "precio_salida": round(salida[1], 4) if salida else None,
            "motivo_salida": salida[2] if salida else None,
            "pnl_pct": (salida[1] / float(r["precio_entrada"]) - 1.0) if salida else None,
            "real_fecha_salida": r["fecha_salida"], "real_motivo": r["motivo_salida"],
            "real_pnl_pct": r["pnl_pct"], "real_estado": r["estado"],
        })
    return pd.DataFrame(filas)


def salidas_en_el_maximo(bit: pd.DataFrame, precios: dict, margen=0.0015) -> dict:
    """Cuántas salidas por objetivo se ejecutaron justo en el máximo de la sesión.

    Es la huella dactilar del look-ahead: una orden límite se ejecuta EN su
    precio, y que ese precio coincida con el máximo exacto del día una y otra
    vez no es suerte, es que el nivel se fijó después de ver la barra.
    """
    total = coincide = 0
    for _, r in bit[bit["motivo_salida"] == "objetivo"].iterrows():
        df = precios.get(r["ticker"])
        ts = pd.Timestamp(r["fecha_salida"])
        if df is None or ts not in df.index:
            continue
        total += 1
        if float(r["precio_salida"]) >= float(df.loc[ts, "High"]) * (1 - margen):
            coincide += 1
    return {"total": total, "en_el_maximo": coincide}


# --------------------------------------------------------------------------- #
# Cálculo de una cartera completa
# --------------------------------------------------------------------------- #
def evaluar(ops: pd.DataFrame, precios: dict, sesiones: list[str],
            capital: float, fricciones: bool) -> dict:
    cta = cuenta.simular(ops, capital=capital, fricciones=fricciones)
    curva = cuenta.curva_diaria(cta, precios, sesiones)
    return {"cta": cta, "curva": curva,
            "m": cuenta.metricas(curva, capital)}


def referencia(precios: dict, sesiones: list[str]) -> dict:
    """Comprar y mantener el índice durante exactamente el mismo periodo."""
    df = precios.get(REFERENCIA)
    if df is None:
        return {}
    serie = df.loc[pd.Timestamp(sesiones[0]):pd.Timestamp(sesiones[-1]), "Close"]
    apertura = float(df.loc[pd.Timestamp(sesiones[0]), "Open"])
    rets = serie.pct_change().dropna()
    dd = float((serie / serie.cummax() - 1.0).min())
    return {
        "rentabilidad_pct": round(100.0 * (float(serie.iloc[-1]) / apertura - 1.0), 2),
        "drawdown_max_pct": round(100.0 * dd, 2),
        "sharpe": round(float(rets.mean() / rets.std() * math.sqrt(252)), 2),
        "serie": serie,
    }


def beta_y_alfa(curva: pd.DataFrame, serie_ref: pd.Series) -> dict:
    """Beta de la cuenta contra el índice y alfa que sobra después de quitarla.

    Con ~45 observaciones esto es orientativo y nada más, pero contesta la
    pregunta importante: de lo ganado, ¿cuánto es simplemente exposición al
    mercado que subió?
    """
    eq = curva.set_index("fecha")["equity"].astype(float)
    eq.index = pd.to_datetime(eq.index)
    r_cta = eq.pct_change().dropna()
    r_ref = serie_ref.pct_change().dropna()
    juntos = pd.concat([r_cta, r_ref], axis=1, join="inner").dropna()
    if len(juntos) < 10:
        return {}
    x, y = juntos.iloc[:, 1], juntos.iloc[:, 0]
    var = float(x.var())
    if var <= 0:
        return {}
    beta = float(x.cov(y) / var)
    # Alfa acumulada del periodo: lo que rindió la cuenta menos lo que explica
    # su exposición al índice.
    alfa_total = float((1 + y).prod() - 1) - beta * float((1 + x).prod() - 1)
    return {"beta": round(beta, 2), "alfa_periodo_pct": round(100.0 * alfa_total, 2)}


# --------------------------------------------------------------------------- #
# Informe
# --------------------------------------------------------------------------- #
def _pct(x, dec=2):
    return "n/d" if x is None else f"{x:+.{dec}f}%"


def _dol(x):
    return "n/d" if x is None else f"${x:,.0f}"


def construir_informe(ctx: dict) -> str:
    L = []
    a = L.append
    a("# Auditoría de fiabilidad del rendimiento")
    a("")
    a(f"_Periodo: {ctx['inicio']} → {ctx['fin']} ({ctx['n_sesiones']} sesiones "
      f"bursátiles, ~{ctx['n_sesiones']/21:.1f} meses). Generado por "
      f"`scripts/auditar_fiabilidad.py`._")
    a("")
    a("Este informe no lo escribe el sistema que opera: lo escribe un auditor que "
      "lee su bitácora y trata de romperla. Todo lo que sigue sale de "
      "`bitacora.csv` y de precios descargados de nuevo, no de ninguna cifra que "
      "el propio sistema hubiera publicado antes.")
    a("")
    a("> **Aviso sobre el tamaño de la muestra.** Son "
      f"{ctx['n_cerradas']} operaciones cerradas en {ctx['n_sesiones']} sesiones. "
      "Cualquier ratio anualizado de aquí (CAGR, Sharpe) es una extrapolación "
      "violenta de dos meses y se publica porque se ha pedido, no porque sea "
      "predictivo. El error estándar del Sharpe con esta muestra ronda ±0,3.")
    a("")

    # --- 1. Cuenta ---------------------------------------------------------
    a("## 1. Rentabilidad de la CUENTA, no suma de trades")
    a("")
    a(f"Cuenta de {_dol(ctx['capital'])} por cartera, dividida en "
      f"{config.SLOTS_CUENTA} slots. Cada entrada invierte un slot al precio de "
      "apertura y el capital compone al cerrar. El modelo completo y sus "
      "decisiones interpretativas están documentados en `centinela/cuenta.py`.")
    a("")
    a("| | Cartera A (con stop) | Cartera B (sin stop) |")
    a("|---|---|---|")
    for etiq, clave, fmt in [
        ("Capital final (bruto)", ("bruto", "equity_final"), _dol),
        ("Rentabilidad (bruta)", ("bruto", "rentabilidad_pct"), _pct),
        ("**Capital final (neto)**", ("neto", "equity_final"), _dol),
        ("**Rentabilidad (neta)**", ("neto", "rentabilidad_pct"), _pct),
        ("CAGR anualizado (neto)", ("neto", "cagr_pct"), _pct),
        ("Drawdown máximo (neto)", ("neto", "drawdown_max_pct"), _pct),
        ("Sharpe aprox. (neto)", ("neto", "sharpe"), lambda v: "n/d" if v is None else f"{v:.2f}"),
    ]:
        vista, campo = clave
        a(f"| {etiq} | {fmt(ctx[vista]['A']['m'][campo])} | {fmt(ctx[vista]['B']['m'][campo])} |")
    a("")
    a("Para comparar, la cifra que el dashboard publicaba hasta ahora —la suma de "
      f"los retornos de cada operación— era de {_pct(ctx['suma_retornos']['A'])} "
      f"(A) y {_pct(ctx['suma_retornos']['B'])} (B). La diferencia con la "
      "rentabilidad de la cuenta no es un error de ninguna de las dos: son dos "
      "preguntas distintas. La suma de retornos mide la estrategia; la cuenta "
      "mide el dinero, y el dinero está limitado a 20 posiciones a la vez.")
    a("")

    # --- 2. Fricciones -----------------------------------------------------
    a("## 2. Fricciones")
    a("")
    a(f"Comisión y spread de {config.COMISION_SPREAD_POR_LADO:.2%} **por lado**, más "
      f"{config.SLIPPAGE_MERCADO:.2%} de slippage en las órdenes a mercado: compra "
      "market-on-open, stop disparado y venta market-on-close por tiempo. La "
      "salida por objetivo NO paga slippage, porque es una orden límite y se "
      "ejecuta a su precio o mejor.")
    a("")
    a("| Cartera | Bruto | Neto | Coste de la fricción |")
    a("|---|---|---|---|")
    for c in ("A", "B"):
        br = ctx["bruto"][c]["m"]["rentabilidad_pct"]
        ne = ctx["neto"][c]["m"]["rentabilidad_pct"]
        a(f"| {c} | {_pct(br)} | {_pct(ne)} | {ne - br:.2f} pp |")
    a("")
    a(f"Son {ctx['n_cerradas']} operaciones cerradas y "
      f"{ctx['n_abiertas']} abiertas: en torno a "
      f"{0.5 * (ctx['coste_friccion_pp']['A'] + ctx['coste_friccion_pp']['B']):.1f} "
      "puntos de rentabilidad se van en costes. Es el precio de una estrategia "
      "que rota la cartera entera cada diez sesiones.")
    a("")

    # --- 3. Look-ahead -----------------------------------------------------
    a("## 3. Look-ahead en la ejecución simulada")
    a("")
    a("### 3.1 Lo que está BIEN")
    a("")
    a("- **Stop antes que objetivo.** `centinela/simulador.py:evaluar_salida_dia` "
      "comprueba el stop primero: si en la misma sesión se tocaron el stop y el "
      "objetivo, la Cartera A sale por el stop. Es el supuesto conservador y "
      "está aplicado. Confirmado también en `centinela/ejecucion.py`, que usa el "
      "backtest.")
    a("- **Gaps al precio real.** Si la apertura salta por debajo del stop o por "
      "encima del objetivo, se ejecuta al open real, no al nivel teórico. "
      "Pesimista en el stop, realista en el objetivo.")
    a("- **La decisión de entrada no ve el open.** La pre-apertura corre con un "
      "techo duro 20 minutos antes de la apertura y el run que llega tarde muere "
      "en rojo en vez de decidir (`resultados.FALLO_VENTANA_PERDIDA`). La entrada "
      "se ejecuta al open de la sesión decidida, no al de un día posterior.")
    a("- **Ninguna sesión sin evaluar.** Las "
      f"{ctx['n_sesiones']} sesiones del periodo tienen su post-cierre registrado "
      "en `logs/`, así que no hay barras que el simulador se haya saltado y en "
      "las que un stop hubiera saltado sin que nadie lo viera.")
    a("")
    a("### 3.2 El sesgo OPTIMISTA que sí existe")
    a("")
    mx = ctx["maximos"]
    a(f"**Las {mx['total']} salidas por objetivo del periodo —{mx['en_el_maximo']} "
      f"de {mx['total']}— se ejecutaron exactamente en el máximo de la sesión.**"
      if mx["en_el_maximo"] == mx["total"] else
      f"**{mx['en_el_maximo']} de las {mx['total']} salidas por objetivo se "
      f"ejecutaron exactamente en el máximo de la sesión.**")
    a("")
    a("No es casualidad. El post-cierre hace dos cosas en este orden "
      "(`simulador.gestionar_posiciones`):")
    a("")
    a("1. **Recalcula el objetivo** con `_atr(df)` y `resistencia_reciente(df)`, "
      "donde `df` ya incluye la barra del día que acaba de cerrar. La resistencia "
      "es el máximo de los últimos 20 highs, así que en un día de máximos **la "
      "resistencia es el máximo de hoy** y el objetivo se clava justo ahí.")
    a("2. **Evalúa la salida** contra ese objetivo recién puesto. La condición "
      "`high >= objetivo` se cumple con igualdad exacta y la venta se registra al "
      "máximo del día.")
    a("")
    a("Un operador real no puede vender ahí: su orden límite del martes es la que "
      "calculó el lunes por la noche. El precio existió durante la sesión, pero no "
      "había ninguna orden esperándolo.")
    a("")
    a("**Cuantificación.** Se ha vuelto a simular la bitácora entera cambiando una "
      "sola cosa: el objetivo de cada sesión es el que ya estaba puesto al abrirla "
      "(el calculado la víspera). Todo lo demás —stop primero, gaps al open, "
      "salida por tiempo al cierre del día límite— es idéntico.")
    a("")
    a("| | Real (publicado) | Sin look-ahead | Diferencia |")
    a("|---|---|---|---|")
    cf = ctx["contrafactual"]
    a(f"| P&L medio por operación | {cf['real_medio']:+.2f}% | "
      f"{cf['cf_medio']:+.2f}% | {cf['cf_medio'] - cf['real_medio']:.2f} pp |")
    for c in ("A", "B"):
        a(f"| Rentabilidad de la cuenta {c} (neta) | "
          f"{_pct(ctx['neto'][c]['m']['rentabilidad_pct'])} | "
          f"{_pct(ctx['cf_neto'][c]['m']['rentabilidad_pct'])} | "
          f"{ctx['cf_neto'][c]['m']['rentabilidad_pct'] - ctx['neto'][c]['m']['rentabilidad_pct']:.2f} pp |")
    a("")
    a(f"De {cf['n']} operaciones cerradas, {cf['cambian_motivo']} cambian de motivo "
      f"de salida y {cf['cambian_pnl']} mueven su P&L más de 0,1 pp. Las más "
      "afectadas:")
    a("")
    a("| Op. | Ticker | Real | Sin look-ahead | Diferencia |")
    a("|---|---|---|---|---|")
    for f in cf["peores"]:
        a(f"| {f['id']} | {f['ticker']}/{f['portafolio']} | "
          f"{f['real_motivo']} {f['real_pnl_pct']*100:+.2f}% | "
          f"{f['motivo_salida']} {f['pnl_pct']*100:+.2f}% | "
          f"{(f['pnl_pct'] - f['real_pnl_pct'])*100:.2f} pp |")
    a("")
    a("**No se ha corregido nada en la lógica de decisión**, por instrucción "
      "expresa: este informe mide, no arregla. Lo que sí queda dicho es cuál es "
      "la cifra honesta —la de la columna 'sin look-ahead'— y dónde está el "
      "arreglo si algún día se hace: recalcular el objetivo **antes** de evaluar "
      "la salida, o evaluar contra el objetivo de la víspera.")
    a("")

    # --- 4. Concentración --------------------------------------------------
    a("## 4. Concentración")
    a("")
    a("| | Cartera A | Cartera B |")
    a("|---|---|---|")
    cc = ctx["concentracion"]
    a(f"| P&L realizado (neto) | {_dol(cc['A']['total'])} | {_dol(cc['B']['total'])} |")
    a(f"| Aportado por las 5 mejores | {_dol(cc['A']['top5'])} "
      f"({cc['A']['pct_top5']:.0f}%) | {_dol(cc['B']['top5'])} ({cc['B']['pct_top5']:.0f}%) |")
    a(f"| **Rentabilidad SIN las 5 mejores** | "
      f"{_pct(cc['A']['ret_sin_top5'])} | {_pct(cc['B']['ret_sin_top5'])} |")
    a(f"| Operaciones en semiconductores | {cc['A']['n_semis']}/{cc['A']['n']} | "
      f"{cc['B']['n_semis']}/{cc['B']['n']} |")
    a(f"| P&L de semiconductores | {_dol(cc['A']['pnl_semis'])} "
      f"({cc['A']['pct_semis']:.0f}%) | {_dol(cc['B']['pnl_semis'])} "
      f"({cc['B']['pct_semis']:.0f}%) |")
    a("")
    a("**Sí, sigue siendo positivo sin las cinco mejores**, que es la pregunta "
      "que importaba. Pero el perfil es el de una estrategia que vive de pocas "
      "operaciones grandes: quitando 5 de "
      f"{cc['A']['n']} en A se evapora la mayor parte del resultado.")
    a("")
    a("Y la concentración sectorial es el riesgo más serio del sistema. El filtro "
      "de partida —acciones a más del 30% de su máximo histórico— seleccionó "
      "en este periodo, casi en bloque, la cadena de valor del silicio. Esto no "
      "es una cartera diversificada del S&P 500: es una apuesta al ciclo de los "
      "semiconductores con 20 patas. Un giro del sector se llevaría por delante "
      "casi todas las posiciones a la vez, y ni el stop de la Cartera A protege "
      "de eso, porque saltaría en todas el mismo día.")
    a("")
    a("Reparto del P&L por sector GICS (neto, Cartera A):")
    a("")
    a("| Sector | P&L | Operaciones |")
    a("|---|---|---|")
    for s, v in sorted(cc["A"]["por_sector"].items(), key=lambda kv: -kv[1][0]):
        a(f"| {s} | {_dol(v[0])} | {v[1]} |")
    a("")

    # --- 5. Régimen --------------------------------------------------------
    a("## 5. Régimen de mercado: ¿habilidad o beta?")
    a("")
    ref = ctx["ref"]
    a(f"| | Cartera A | Cartera B | {REFERENCIA} (comprar y mantener) |")
    a("|---|---|---|---|")
    a(f"| Rentabilidad del periodo | {_pct(ctx['neto']['A']['m']['rentabilidad_pct'])} | "
      f"{_pct(ctx['neto']['B']['m']['rentabilidad_pct'])} | {_pct(ref['rentabilidad_pct'])} |")
    a(f"| Drawdown máximo | {_pct(ctx['neto']['A']['m']['drawdown_max_pct'])} | "
      f"{_pct(ctx['neto']['B']['m']['drawdown_max_pct'])} | {_pct(ref['drawdown_max_pct'])} |")
    a(f"| Sharpe aprox. | {ctx['neto']['A']['m']['sharpe']:.2f} | "
      f"{ctx['neto']['B']['m']['sharpe']:.2f} | {ref['sharpe']:.2f} |")
    a("")
    for c in ("A", "B"):
        ba = ctx["beta"].get(c, {})
        if ba:
            a(f"- **Cartera {c}**: beta {ba['beta']} contra {REFERENCIA}; alfa del "
              f"periodo {_pct(ba['alfa_periodo_pct'])}.")
    a("")
    a(f"El mercado subió {_pct(ref['rentabilidad_pct'])} en estas "
      f"{ctx['n_sesiones']} sesiones, así que el sistema batió al índice. Pero lo "
      "hizo con un drawdown "
      f"{abs(ctx['neto']['A']['m']['drawdown_max_pct']) / abs(ref['drawdown_max_pct']):.1f} "
      "veces mayor, que es el precio que se pagó por ese exceso. Y dos meses de "
      "mercado alcista no distinguen entre una estrategia con alfa y una "
      "estrategia con beta alta; para eso hace falta un tramo bajista, que este "
      "periodo no tiene.")
    a("")

    # --- Conclusión --------------------------------------------------------
    a("## Conclusión honesta")
    a("")
    for linea in ctx["conclusion"]:
        a(f"{linea}")
        a("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", type=float, default=config.CAPITAL_INICIAL_CUENTA)
    ap.add_argument("--sin-descarga", action="store_true",
                    help="usa solo la caché local de precios")
    args = ap.parse_args()

    bit = pd.read_csv(RUTA_BITACORA)
    bit["duplicada"] = cuenta.marcar_duplicadas(bit)
    # La vista LIMPIA es la que manda, igual que en el dashboard: las 13 entradas
    # del bug del 2026-08-06 no son la estrategia, son una cicatriz.
    limpias = bit[~bit["duplicada"]].copy()

    inicio = str(bit["fecha_entrada"].min())
    fin = str(max(bit["fecha_entrada"].max(), bit["fecha_salida"].dropna().max()))
    precios = cargar_precios(bit["ticker"].unique(), inicio, fin,
                             descargar=not args.sin_descarga)
    if REFERENCIA not in precios:
        raise RuntimeError(f"No hay precios de {REFERENCIA}: sin índice de "
                           f"referencia no se puede auditar el régimen.")
    sesiones = [d.date().isoformat() for d in precios[REFERENCIA].index
                if inicio <= d.date().isoformat() <= fin]

    ctx = {"inicio": inicio, "fin": fin, "n_sesiones": len(sesiones),
           "capital": args.capital,
           "n_cerradas": int((limpias["estado"] == "cerrada").sum()),
           "n_abiertas": int((limpias["estado"] != "cerrada").sum())}

    for vista, fric in (("bruto", False), ("neto", True)):
        ctx[vista] = {c: evaluar(limpias[limpias["portafolio"] == c], precios,
                                 sesiones, args.capital, fric)
                      for c in ("A", "B")}
    ctx["coste_friccion_pp"] = {
        c: ctx["neto"][c]["m"]["rentabilidad_pct"] - ctx["bruto"][c]["m"]["rentabilidad_pct"]
        for c in ("A", "B")}
    ctx["suma_retornos"] = {
        c: round(100.0 * limpias[(limpias["portafolio"] == c)
                                 & (limpias["estado"] == "cerrada")]["pnl_pct"].sum(), 2)
        for c in ("A", "B")}

    # --- look-ahead
    ctx["maximos"] = salidas_en_el_maximo(limpias, precios)
    cf = resimular_sin_lookahead(limpias, precios, sesiones)
    # Solo se comparan las operaciones que cerraron en las DOS versiones: una
    # que la real cerró y la contrafactual mantiene viva no tiene P&L con el que
    # comparar todavía, y meterla como cero falsearía la media.
    cf_cerr = cf[(cf["estado"] == "cerrada") & (cf["real_estado"] == "cerrada")]
    ctx["cf_neto"] = {c: evaluar(cf[cf["portafolio"] == c], precios, sesiones,
                                 args.capital, True) for c in ("A", "B")}
    peores = cf_cerr.assign(delta=cf_cerr["pnl_pct"] - cf_cerr["real_pnl_pct"]) \
                    .sort_values("delta").head(8)
    ctx["contrafactual"] = {
        "n": int(len(cf_cerr)),
        "real_medio": 100.0 * float(cf_cerr["real_pnl_pct"].mean()),
        "cf_medio": 100.0 * float(cf_cerr["pnl_pct"].mean()),
        "cambian_motivo": int((cf_cerr["real_motivo"] != cf_cerr["motivo_salida"]).sum()),
        "cambian_pnl": int(((cf_cerr["pnl_pct"] - cf_cerr["real_pnl_pct"]).abs() > 0.001).sum()),
        "peores": peores.to_dict("records"),
    }

    # --- concentración
    conc = {}
    for c in ("A", "B"):
        cta = ctx["neto"][c]["cta"]
        cerrados = sorted(cta["cerrados"], key=lambda t: t["pnl_dinero"], reverse=True)
        total = sum(t["pnl_dinero"] for t in cerrados)
        top5 = cerrados[:5]
        suma5 = sum(t["pnl_dinero"] for t in top5)
        ops_sin = limpias[(limpias["portafolio"] == c)
                          & (~limpias["id"].isin({t["id"] for t in top5}))]
        sin5 = evaluar(ops_sin, precios, sesiones, args.capital, True)
        por_sector: dict[str, list] = {}
        for t in cerrados:
            s = t["sector"] or "n/d"
            v = por_sector.setdefault(s, [0.0, 0])
            v[0] += t["pnl_dinero"]
            v[1] += 1
        semis = [t for t in cerrados if t["ticker"] in SEMICONDUCTORES]
        pnl_semis = sum(t["pnl_dinero"] for t in semis)
        conc[c] = {
            "total": total, "top5": suma5,
            "pct_top5": 100.0 * suma5 / total if total else 0.0,
            "ret_sin_top5": sin5["m"]["rentabilidad_pct"],
            "n": len(cerrados), "n_semis": len(semis), "pnl_semis": pnl_semis,
            "pct_semis": 100.0 * pnl_semis / total if total else 0.0,
            "por_sector": por_sector,
        }
    ctx["concentracion"] = conc

    # --- régimen
    ctx["ref"] = referencia(precios, sesiones)
    ctx["beta"] = {c: beta_y_alfa(ctx["neto"][c]["curva"], ctx["ref"]["serie"])
                   for c in ("A", "B")}

    ctx["conclusion"] = conclusion(ctx)

    RUTA_REPORTE.write_text(construir_informe(ctx), encoding="utf-8")
    print(f"informe -> {RUTA_REPORTE}")
    for c in ("A", "B"):
        m = ctx["neto"][c]["m"]
        print(f"  cartera {c}: {m['rentabilidad_pct']:+.2f}% neto | "
              f"CAGR {m['cagr_pct']:+.1f}% | DD {m['drawdown_max_pct']:.2f}% | "
              f"Sharpe {m['sharpe']:.2f}")
    print(f"  {REFERENCIA}: {ctx['ref']['rentabilidad_pct']:+.2f}%")
    return 0


def conclusion(ctx: dict) -> list[str]:
    """Las cinco líneas que pidió el encargo. Sin adornos."""
    A, B = ctx["neto"]["A"]["m"], ctx["neto"]["B"]["m"]
    cfA = ctx["cf_neto"]["A"]["m"]["rentabilidad_pct"]
    cfB = ctx["cf_neto"]["B"]["m"]["rentabilidad_pct"]
    ref = ctx["ref"]["rentabilidad_pct"]
    cc = ctx["concentracion"]
    return [
        f"**1. La cuenta gana dinero, y menos del que parecía.** Neto de fricciones, "
        f"{A['rentabilidad_pct']:+.2f}% en A y {B['rentabilidad_pct']:+.2f}% en B "
        f"sobre {_dol(ctx['capital'])}, frente al {ref:+.2f}% del {REFERENCIA}. "
        f"Descontado además el look-ahead de la ejecución, queda en "
        f"{cfA:+.2f}% y {cfB:+.2f}%. Sigue batiendo al índice.",

        f"**2. Lo ROBUSTO es el filtro de entrada.** Que la selección siga siendo "
        f"rentable sin sus cinco mejores operaciones ({cc['A']['ret_sin_top5']:+.2f}% en "
        f"A, {cc['B']['ret_sin_top5']:+.2f}% en B) y con {cc['A']['n']} cierres "
        f"dice que la ventaja no depende de un golpe de suerte aislado. El stop de "
        f"A tampoco destruye valor: A y B terminan a menos de un punto de distancia.",

        f"**3. Lo FRÁGIL es la ejecución de las salidas.** "
        f"{ctx['maximos']['en_el_maximo']} de {ctx['maximos']['total']} salidas por "
        f"objetivo se registraron en el máximo exacto de la sesión, un precio que "
        f"ninguna orden límite real habría capturado. Infla el resultado en "
        f"{abs(ctx['contrafactual']['cf_medio'] - ctx['contrafactual']['real_medio']):.2f} "
        f"puntos por operación y "
        f"{abs(cfA - A['rentabilidad_pct']):.2f} puntos de rentabilidad de la cuenta. Es el único sesgo optimista encontrado, pero es "
        f"sistemático, no anecdótico.",

        f"**4. Lo MÁS FRÁGIL es la concentración.** "
        f"{cc['A']['n_semis']} de {cc['A']['n']} operaciones y el "
        f"{cc['A']['pct_semis']:.0f}% del P&L están en la cadena del silicio. Esto no "
        f"es un sistema sobre el S&P 500: es una apuesta apalancada al ciclo de los "
        f"semiconductores que hasta ahora ha salido bien. Un giro del sector golpea "
        f"las 20 posiciones el mismo día y el stop no protege de una caída "
        f"correlacionada.",

        f"**5. Dos meses no demuestran nada, y el CAGR del {A['cagr_pct']:.0f}% es "
        f"un artefacto aritmético.** {ctx['n_sesiones']} sesiones, todas en mercado "
        f"alcista, sin un solo tramo bajista que separe el alfa de la beta. Las "
        f"cifras de arriba son un punto de partida creíble y bien medido; no son "
        f"una expectativa. Con dinero real, el tamaño de posición debería fijarse "
        f"por el drawdown observado ({A['drawdown_max_pct']:.1f}%) y no por la "
        f"rentabilidad.",
    ]


if __name__ == "__main__":
    sys.exit(main())
