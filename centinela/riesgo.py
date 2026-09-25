"""Riesgo y resultados POR OPERACIÓN, en dólares y en porcentaje.

POR QUÉ ESTE MÓDULO
-------------------
La cuenta simulada (`centinela/cuenta.py`) responde "cuánto vale mi dinero".
Esto responde la pregunta que va justo antes y que decide si uno puede dormir:
**cuánto puedo perder en una sola operación, y cuánto si se tuercen todas a la
vez**. Son dos preguntas distintas y la segunda es la que arruina cuentas.

Las cifras se dan siempre en las dos unidades. El porcentaje dice si el riesgo
es razonable; los dólares dicen si uno lo aguanta.

LAS DOS CARTERAS NO SE MIDEN IGUAL, Y NO ES UN DESCUIDO
-------------------------------------------------------
La Cartera A tiene stop, así que su riesgo por operación está ACOTADO por
diseño: la distancia de la entrada al stop, que se conoce antes de comprar.

La Cartera B no tiene stop. Su riesgo por operación no está acotado por nada:
en teoría es el 100% de la posición, y en la práctica lo que haya caído la peor
operación de la historia. Publicar ahí un "riesgo por trade" calculado sería
inventarse un límite que no existe, así que se publica la peor pérdida REAL
observada y se dice, con todas las letras, que no es un tope sino un
antecedente.

RIESGO AGREGADO
---------------
Si los 20 stops saltaran el mismo día. No es un escenario de laboratorio: el
85% del P&L de este sistema está en la cadena del silicio (ver
`reportes/auditoria_fiabilidad.md`), así que las 20 posiciones se mueven juntas
y un giro del sector es exactamente eso. El stop protege de que una posición se
hunda sola; no protege de que se hundan todas a la vez.
"""
from __future__ import annotations

import pandas as pd

from . import config, cuenta


def _pct(parte: float, total: float) -> float | None:
    return None if not total else 100.0 * parte / total


def perfil(cta: dict, operaciones: pd.DataFrame, abiertas_estado: list | None = None) -> dict:
    """Riesgo y resultados por operación de UNA cartera.

    `cta` es la salida de `cuenta.simular`; `operaciones` las filas de la
    bitácora de esa cartera (para leer los stops vigentes); `abiertas_estado`
    las posiciones vivas según estado.json, que son las que tienen un stop
    realmente puesto ahora mismo.
    """
    equity = cta["equity_contable"]
    slot = equity / cta["slots"]
    cerrados = cta["cerrados"]

    ganancias = [t["pnl_dinero"] for t in cerrados if t["pnl_dinero"] > 0]
    perdidas = [t["pnl_dinero"] for t in cerrados if t["pnl_dinero"] < 0]

    datos = {
        "equity": equity,
        "slots": cta["slots"],
        "capital_por_posicion": slot,
        "capital_por_posicion_pct": _pct(slot, equity),
        "operaciones_cerradas": len(cerrados),
        "ganancia_media": sum(ganancias) / len(ganancias) if ganancias else None,
        "perdida_media": sum(perdidas) / len(perdidas) if perdidas else None,
        "mayor_ganancia": max(ganancias) if ganancias else None,
        "mayor_perdida": min(perdidas) if perdidas else None,
        "n_ganadoras": len(ganancias),
        "n_perdedoras": len(perdidas),
    }
    datos["ganancia_media_pct"] = _pct(datos["ganancia_media"] or 0.0, equity) if ganancias else None
    datos["perdida_media_pct"] = _pct(datos["perdida_media"] or 0.0, equity) if perdidas else None
    datos["mayor_ganancia_pct"] = _pct(datos["mayor_ganancia"] or 0.0, equity) if ganancias else None
    datos["mayor_perdida_pct"] = _pct(datos["mayor_perdida"] or 0.0, equity) if perdidas else None

    datos.update(_riesgo_por_stop(cta, operaciones, abiertas_estado, slot, equity))
    return datos


def _riesgo_por_stop(cta: dict, operaciones: pd.DataFrame,
                     abiertas_estado, slot: float, equity: float) -> dict:
    """Cuánto se pierde si salta el stop: por operación y todos a la vez.

    Se mide sobre las posiciones ABIERTAS AHORA, con su stop y su coste reales,
    porque el riesgo es una propiedad de lo que está puesto en el mercado hoy,
    no un promedio histórico. Para la exposición futura se añade el riesgo de
    una entrada nueva al tamaño de slot actual, usando el tope del stop
    (STOP_MAX_PORCENTAJE) que es el peor caso admitido por el diseño.
    """
    abiertas = cta["abiertas"]
    if not abiertas:
        # Sin posiciones vivas, lo que se puede decir es cuánto arriesgaría la
        # próxima entrada, no cuánto se arriesga ahora.
        return {
            "tiene_stop": None,
            "riesgo_por_trade": None,
            "riesgo_por_trade_pct": None,
            "riesgo_agregado": None,
            "riesgo_agregado_pct": None,
            "riesgo_maximo_proxima_entrada": slot * config.STOP_MAX_PORCENTAJE,
            "peor_perdida_historica": None,
            "peor_perdida_historica_pct": None,
            "posiciones_con_stop": 0,
        }

    stops = {}
    if operaciones is not None and not operaciones.empty:
        for _, r in operaciones.iterrows():
            stops[int(r["id"])] = None if pd.isna(r.get("stop")) else float(r["stop"])

    riesgos = []
    con_stop = 0
    for t in abiertas:
        stop = stops.get(t["id"])
        if stop is None or stop <= 0:
            continue
        con_stop += 1
        # Pérdida si salta el stop: acciones * (entrada_neta - stop), más la
        # fricción de la venta a mercado que dispara el propio stop.
        salida = cuenta.precio_salida_neto(stop, "stop", cta["fricciones"])
        riesgos.append(t["acciones"] * (t["precio_entrada_neto"] - salida))

    cerrados = cta["cerrados"]
    peor = min((t["pnl_dinero"] for t in cerrados), default=None)

    if not riesgos:
        # Cartera sin stop (la B). El riesgo por operación no está acotado.
        return {
            "tiene_stop": False,
            "riesgo_por_trade": None,
            "riesgo_por_trade_pct": None,
            "riesgo_agregado": None,
            "riesgo_agregado_pct": None,
            "riesgo_maximo_proxima_entrada": None,
            "peor_perdida_historica": peor,
            "peor_perdida_historica_pct": _pct(peor, equity) if peor is not None else None,
            "posiciones_con_stop": 0,
        }

    medio = sum(riesgos) / len(riesgos)
    agregado = sum(riesgos)
    return {
        "tiene_stop": True,
        "riesgo_por_trade": medio,
        "riesgo_por_trade_pct": _pct(medio, equity),
        "riesgo_agregado": agregado,
        "riesgo_agregado_pct": _pct(agregado, equity),
        "riesgo_maximo_proxima_entrada": slot * config.STOP_MAX_PORCENTAJE,
        "peor_perdida_historica": peor,
        "peor_perdida_historica_pct": _pct(peor, equity) if peor is not None else None,
        "posiciones_con_stop": con_stop,
    }


def acciones_enteras(importe: float, precio: float) -> int:
    """Cuántas acciones ENTERAS caben en `importe` a `precio`.

    XTB vende acciones fraccionadas en su plataforma, pero el cliente de la API
    redondea el volumen a entero (`int(volume + 0.5)`) y rechaza cualquier orden
    que redondee a cero. Así que para el broker la unidad mínima es una acción,
    y la cuenta simulada tiene que poder contar igual cuando se compara con él.

    Trunca, nunca redondea al alza: comprar más de lo que cabe en el slot
    rompería el tamaño de posición, que es lo único que mantiene el riesgo
    acotado.
    """
    if precio <= 0:
        return 0
    return int(importe // precio)
