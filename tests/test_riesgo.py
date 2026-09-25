"""Riesgo por operación: en dólares, en porcentaje y con las dos carteras
midiéndose de forma distinta a propósito.

La Cartera A tiene el riesgo ACOTADO por el stop y se puede calcular antes de
comprar. La B no tiene stop: su riesgo no está acotado por nada, y publicar un
número calculado ahí sería inventarse un límite que no existe.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import config, cuenta, riesgo  # noqa: E402

COLS = ["id", "ticker", "portafolio", "sector", "fecha_entrada",
        "precio_entrada", "fecha_salida", "precio_salida", "motivo_salida",
        "estado", "stop"]


def _ops(*filas):
    return pd.DataFrame(list(filas), columns=COLS)


# --------------------------------------------------------------------------- #
# Acciones enteras
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("importe,precio,esperado", [
    (500.0, 100.0, 5),
    (500.0, 1510.26, 0),     # SNDK con un slot de $500: no cabe ni una
    (2500.0, 1510.26, 1),    # con $50.000 de capital, sí
    (999.99, 100.0, 9),      # trunca, NUNCA redondea al alza
    (100.0, 0.0, 0),
])
def test_acciones_enteras_trunca(importe, precio, esperado):
    """Redondear al alza compraría más de lo que cabe en el slot, y el tamaño
    de posición es lo único que mantiene el riesgo acotado."""
    assert riesgo.acciones_enteras(importe, precio) == esperado


# --------------------------------------------------------------------------- #
# Cartera A: riesgo acotado por el stop
# --------------------------------------------------------------------------- #
def test_el_riesgo_por_operacion_es_la_distancia_al_stop():
    """Entrada a 100 con stop en 90: se arriesga el 10% de la posición.

    Sin fricciones para que el número salga redondo: 5 acciones × $10 = $50.
    """
    ops = _ops((1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None,
                "abierta", 90.0))
    cta = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)
    p = riesgo.perfil(cta, ops)

    assert p["tiene_stop"] is True
    assert p["capital_por_posicion"] == pytest.approx(500.0)
    assert p["riesgo_por_trade"] == pytest.approx(50.0)
    assert p["riesgo_por_trade_pct"] == pytest.approx(5.0)     # sobre $1.000
    assert p["posiciones_con_stop"] == 1


def test_el_riesgo_agregado_suma_todos_los_stops():
    """Si saltan todos el mismo día. Con el 85% del P&L en semiconductores no
    es un escenario de laboratorio: las 20 posiciones se mueven juntas."""
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None, "abierta", 90.0),
        (2, "BBB", "A", "Tech", "2026-07-01", 100.0, None, None, None, "abierta", 80.0),
    )
    cta = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)
    p = riesgo.perfil(cta, ops)
    # 500 al 10% + 500 al 20% = 50 + 100
    assert p["riesgo_agregado"] == pytest.approx(150.0)
    assert p["riesgo_agregado_pct"] == pytest.approx(15.0)
    assert p["riesgo_agregado"] > p["riesgo_por_trade"]


def test_las_fricciones_empeoran_el_riesgo_no_lo_mejoran():
    """El stop se ejecuta a mercado, así que además paga slippage."""
    ops = _ops((1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None,
                "abierta", 90.0))
    bruto = riesgo.perfil(cuenta.simular(ops, capital=1000.0, slots=2,
                                         fricciones=False), ops)
    neto = riesgo.perfil(cuenta.simular(ops, capital=1000.0, slots=2,
                                        fricciones=True), ops)
    assert neto["riesgo_por_trade"] > bruto["riesgo_por_trade"]


# --------------------------------------------------------------------------- #
# Cartera B: sin stop, sin límite
# --------------------------------------------------------------------------- #
def test_sin_stop_no_se_publica_un_riesgo_calculado():
    """Poner un número ahí sería inventarse un tope que no existe."""
    ops = _ops(
        (1, "AAA", "B", "Tech", "2026-07-01", 100.0, "2026-07-10", 70.0,
         "tiempo", "cerrada", None),
        (2, "BBB", "B", "Tech", "2026-07-11", 100.0, None, None, None,
         "abierta", None),
    )
    cta = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)
    p = riesgo.perfil(cta, ops)

    assert p["tiene_stop"] is False
    assert p["riesgo_por_trade"] is None
    assert p["riesgo_agregado"] is None
    # Lo que sí se puede decir: lo peor que ha pasado de verdad.
    assert p["peor_perdida_historica"] == pytest.approx(-150.0)


# --------------------------------------------------------------------------- #
# Resultados por operación
# --------------------------------------------------------------------------- #
def test_ganancias_y_perdidas_medias_y_extremas():
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, "2026-07-08", 120.0,
         "objetivo", "cerrada", 90.0),
        (2, "BBB", "A", "Tech", "2026-07-09", 100.0, "2026-07-16", 90.0,
         "stop", "cerrada", 90.0),
        (3, "CCC", "A", "Tech", "2026-07-17", 100.0, "2026-07-24", 110.0,
         "objetivo", "cerrada", 90.0),
    )
    cta = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)
    p = riesgo.perfil(cta, ops)

    assert p["n_ganadoras"] == 2 and p["n_perdedoras"] == 1
    assert p["mayor_ganancia"] > p["ganancia_media"] > 0
    assert p["mayor_perdida"] < 0 and p["perdida_media"] < 0
    assert p["operaciones_cerradas"] == 3


def test_una_cartera_sin_posiciones_abiertas_dice_lo_que_arriesgaria():
    """Sin nada vivo no hay riesgo actual, pero sí se puede decir cuánto
    arriesgaría la próxima entrada al tamaño de slot de hoy."""
    ops = _ops((1, "AAA", "A", "Tech", "2026-07-01", 100.0, "2026-07-08",
                120.0, "objetivo", "cerrada", 90.0))
    cta = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)
    p = riesgo.perfil(cta, ops)

    assert p["riesgo_por_trade"] is None
    assert p["riesgo_maximo_proxima_entrada"] == pytest.approx(
        p["capital_por_posicion"] * config.STOP_MAX_PORCENTAJE)


def test_el_porcentaje_del_capital_por_posicion_es_uno_entre_slots():
    ops = _ops((1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None,
                "abierta", 90.0))
    for slots in (2, 5, 20):
        cta = cuenta.simular(ops, capital=1000.0, slots=slots, fricciones=False)
        p = riesgo.perfil(cta, ops)
        assert p["capital_por_posicion_pct"] == pytest.approx(100.0 / slots)
