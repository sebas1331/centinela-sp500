"""Motor de simulación EN VIVO (paper trading) de las dos carteras.

Flujo diario:
  Pre-apertura : se DECIDEN las entradas (registrar_decisiones_entrada) y quedan
                 pendientes de ejecutar al open oficial.
  Post-cierre  : (1) se EJECUTAN las entradas pendientes al open oficial de hoy,
                 (2) se GESTIONAN las posiciones abiertas: se recalcula el
                     objetivo con la info nueva y se evalúan salidas
                     (objetivo/tiempo/stop) con la barra de HOY.

Diferencia con el backtest: aquí se procesa UNA barra (la de hoy) por escaneo y
el objetivo se RECALCULA en cada escaneo, registrando cada cambio con su motivo.

Reglas conservadoras (iguales al backtest): si se tocan stop y objetivo el mismo
día gana el stop; si hay gap más allá del nivel, se ejecuta al open real.
Cartera A tiene stop (ATR); Cartera B no tiene stop.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import config, objetivos, ejecucion, bitacora, calendario
from .features import _atr


# --------------------------------------------------------------------------- #
# Decisión de entradas (pre-apertura)
# --------------------------------------------------------------------------- #
def capacidad_entradas(estado: dict) -> int:
    """Cuántas entradas nuevas caben hoy (tope de posiciones y de entradas/día)."""
    abiertas = len(estado["posiciones"].get("A", []))
    pendientes = len(estado.get("entradas_pendientes", []))
    por_tope = config.MAX_POSICIONES_ABIERTAS - abiertas - pendientes
    return max(0, min(config.MAX_ENTRADAS_POR_DIA, por_tope))


def registrar_decisiones_entrada(estado: dict, decisiones: list[dict],
                                 fecha_iso: str, timestamp_escaneo: str) -> list:
    """Registra en el estado las entradas decididas (pendientes de ejecutar).

    Evita duplicar tickers ya en cartera o ya pendientes. Respeta la capacidad.
    'decisiones' viene ordenada por prioridad (mayor probabilidad primero).
    """
    ya = tickers_ocupados(estado)
    cupo = capacidad_entradas(estado)
    nuevas = []
    for d in decisiones:
        if cupo <= 0:
            break
        if d["ticker"] in ya:
            continue
        entrada = {
            "ticker": d["ticker"],
            "fecha_decision": fecha_iso,
            "timestamp_escaneo": timestamp_escaneo,
            "proba": d.get("proba"),
            "score_fundamental": d.get("score_fundamental"),
            "atr": d.get("atr"),
            "resistencia": d.get("resistencia"),
            "target_analista": d.get("target_analista"),
            "sector": d.get("sector"),
            "notas": d.get("notas", ""),
        }
        estado["entradas_pendientes"].append(entrada)
        nuevas.append(entrada)
        ya.add(d["ticker"])
        cupo -= 1
    return nuevas


def tickers_ocupados(estado: dict) -> set:
    ocup = {p["ticker"] for p in estado["posiciones"].get("A", [])}
    ocup |= {e["ticker"] for e in estado.get("entradas_pendientes", [])}
    return ocup


# --------------------------------------------------------------------------- #
# Ejecución de entradas al open oficial (post-cierre)
# --------------------------------------------------------------------------- #
def ejecutar_entradas_pendientes(estado: dict, precios: dict, fecha_iso: str) -> list:
    """Ejecuta las entradas pendientes al OPEN oficial de su SESIÓN de decisión.

    La entrada se simula al open de la sesión decidida en la pre-apertura
    (e['fecha_decision']), NO del día de proceso: así, si el post-cierre llega
    tarde, la entrada sigue siendo el open correcto. Crea posiciones en A y B y
    registra las entradas en la bitácora. Devuelve las posiciones abiertas.
    """
    abiertas = []
    quedan = []
    for e in estado.get("entradas_pendientes", []):
        df = precios.get(e["ticker"])
        sesion_iso = e.get("fecha_decision", fecha_iso)
        fecha_ts = pd.Timestamp(sesion_iso)
        if df is None or fecha_ts not in df.index:
            # aún no hay datos de la sesión de entrada: dejar pendiente
            quedan.append(e)
            continue
        ac = calendario.apertura_cierre_et(sesion_iso)
        if ac is None:
            continue
        apertura_et, _ = ac
        hora_entrada_et = apertura_et.strftime("%Y-%m-%d %H:%M:%S %Z")
        hora_entrada_utc = apertura_et.astimezone(config.TZ_UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        dia_limite = _dia_limite(sesion_iso)
        entrada = float(df.loc[fecha_ts, "Open"])
        if entrada <= 0:
            continue
        atr = e.get("atr")
        if atr is None or atr <= 0:
            atr = float(_atr(df).iloc[-1])
        resistencia = e.get("resistencia") or objetivos.resistencia_reciente(df)
        objetivo = objetivos.objetivo_inicial(entrada, atr, resistencia,
                                              e.get("target_analista"))
        stop = objetivos.stop_inicial(entrada, atr)
        grupo = _siguiente_grupo(estado)
        hist_ini = [{"fecha": sesion_iso, "objetivo": round(objetivo, 4),
                     "motivo": "objetivo inicial"}]

        for cart, usa_stop in [("A", True), ("B", False)]:
            datos_bit = {
                "grupo": grupo, "ticker": e["ticker"], "portafolio": cart,
                "sector": e.get("sector"), "fecha_entrada": sesion_iso,
                "hora_entrada_et": hora_entrada_et, "hora_entrada_utc": hora_entrada_utc,
                "timestamp_escaneo": e.get("timestamp_escaneo"),
                "precio_entrada": round(entrada, 4), "probabilidad": e.get("proba"),
                "score_fundamental": e.get("score_fundamental"),
                "objetivo_inicial": round(objetivo, 4),
                "objetivo_actual": round(objetivo, 4),
                "historial_objetivos": hist_ini,
                "stop": round(stop, 4) if usa_stop else None,
                "notas": e.get("notas", ""),
            }
            oid = bitacora.registrar_entrada(datos_bit)
            pos = {
                "id": oid, "grupo": grupo, "ticker": e["ticker"], "portafolio": cart,
                "fecha_entrada": sesion_iso, "entrada": round(entrada, 4),
                "objetivo": round(objetivo, 4), "objetivo_inicial": round(objetivo, 4),
                "historial_objetivos": list(hist_ini),
                "stop": round(stop, 4) if usa_stop else None,
                "atr_entrada": round(atr, 4), "dia_limite": dia_limite,
                "proba": e.get("proba"), "score_fundamental": e.get("score_fundamental"),
                "sector": e.get("sector"),
            }
            estado["posiciones"][cart].append(pos)
            abiertas.append(pos)
    estado["entradas_pendientes"] = quedan
    return abiertas


# --------------------------------------------------------------------------- #
# Gestión de posiciones abiertas (post-cierre)
# --------------------------------------------------------------------------- #
def recalcular_objetivo(pos: dict, df: pd.DataFrame, target_analista=None):
    """Recalcula el objetivo con la info nueva (ATR/resistencia/analistas).

    Devuelve (nuevo_objetivo, cambio_bool, motivo). El objetivo nunca baja del
    piso de +5% sobre la entrada.
    """
    entrada = pos["entrada"]
    atr = float(_atr(df).iloc[-1])
    resistencia = objetivos.resistencia_reciente(df)
    nuevo = objetivos.objetivo_inicial(entrada, atr, resistencia, target_analista)
    nuevo = round(nuevo, 4)
    actual = round(pos["objetivo"], 4)
    if abs(nuevo - actual) / max(actual, 1e-9) > 0.001:   # cambio material (>0.1%)
        motivo = f"recálculo: ATR={atr:.2f}, resistencia={resistencia:.2f}"
        return nuevo, True, motivo
    return actual, False, ""


def evaluar_salida_dia(pos: dict, bar, es_dia_limite: bool):
    """Evalúa la salida de HOY para una posición. Devuelve (motivo, precio) o None.

    Reglas conservadoras: stop antes que objetivo; gaps al open real.
    """
    o = float(bar["Open"]); h = float(bar["High"]); l = float(bar["Low"]); c = float(bar["Close"])
    stop = pos.get("stop")
    objetivo = pos["objetivo"]
    if stop is not None and l <= stop:
        return "stop", (o if o <= stop else stop)
    if h >= objetivo:
        return "objetivo", (o if o >= objetivo else objetivo)
    if es_dia_limite:
        return "tiempo", c
    return None


def gestionar_posiciones(estado: dict, precios: dict, fecha_iso: str,
                         targets_analista: dict | None = None):
    """Recalcula objetivos y evalúa salidas de todas las posiciones con la barra
    de hoy. Devuelve (cerradas, cambios_objetivo)."""
    targets_analista = targets_analista or {}
    ac = calendario.apertura_cierre_et(fecha_iso)
    hora_salida_et = (ac[1].strftime("%Y-%m-%d %H:%M:%S %Z") if ac else fecha_iso)
    fecha_ts = pd.Timestamp(fecha_iso)

    cerradas, cambios = [], []
    for cart in ("A", "B"):
        siguen = []
        for pos in estado["posiciones"].get(cart, []):
            df = precios.get(pos["ticker"])
            if df is None or fecha_ts not in df.index:
                siguen.append(pos)          # sin datos de hoy: se mantiene
                continue
            # 1) recalcular objetivo
            nuevo, cambio, motivo = recalcular_objetivo(
                pos, df, targets_analista.get(pos["ticker"]))
            if cambio:
                pos["objetivo"] = nuevo
                pos["historial_objetivos"].append(
                    {"fecha": fecha_iso, "objetivo": nuevo, "motivo": motivo})
                bitacora.actualizar_objetivo(pos["id"], nuevo, pos["historial_objetivos"])
                cambios.append({"ticker": pos["ticker"], "portafolio": cart,
                                "objetivo": nuevo, "motivo": motivo})
            # 2) evaluar salida
            bar = df.loc[fecha_ts]
            es_limite = fecha_iso >= pos["dia_limite"]
            salida = evaluar_salida_dia(pos, bar, es_limite)
            if salida is None:
                siguen.append(pos)
                continue
            mot, precio = salida
            pnl = round(ejecucion.pnl_pct(pos["entrada"], precio), 6)
            dias = len(calendario.sesiones_en_rango(pos["fecha_entrada"], fecha_iso))
            bitacora.registrar_salida(
                pos["id"], fecha_iso, hora_salida_et, round(float(precio), 4),
                mot, pnl, int(dias), notas_extra=f"salida {mot}")
            cerradas.append({**pos, "motivo_salida": mot,
                             "precio_salida": round(float(precio), 4), "pnl_pct": pnl})
        estado["posiciones"][cart] = siguen
    return cerradas, cambios


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _dia_limite(fecha_entrada_iso: str) -> str:
    """Fecha de la sesión límite (10ª sesión hábil contando la de entrada)."""
    s = calendario.sesion_n_despues(fecha_entrada_iso, config.HORIZONTE_DIAS_HABILES - 1)
    return pd.Timestamp(s).date().isoformat()


def _siguiente_grupo(estado: dict) -> int:
    estado["contador_grupo"] = int(estado.get("contador_grupo", 0)) + 1
    return estado["contador_grupo"]
