"""El objetivo del día se evalúa contra el nivel que estaba PUESTO al abrirlo.

Corrección del 2026-09-25. La auditoría de fiabilidad encontró que las 13
salidas por objetivo del periodo se habían ejecutado en el máximo EXACTO de la
sesión, las 13. La causa estaba en el orden de dos pasos dentro de
`gestionar_posiciones`:

  1. recalcular el objetivo con `resistencia_reciente(df)`, donde `df` ya
     incluía la barra del día cerrado — en un día de máximos, esa resistencia
     ES el máximo de hoy, así que el objetivo se clavaba justo ahí;
  2. evaluar la salida contra ese objetivo recién puesto, con lo que
     `high >= objetivo` se cumplía con igualdad exacta.

Resultado: el simulador vendía en el máximo del día, un precio que ninguna
orden límite real habría capturado, porque esa orden se puso la víspera.

Estos tests fijan el orden corregido. El caso de MRNA está calcado del real
(operaciones 72 y 73 de la bitácora, 2026-08-13).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import simulador  # noqa: E402


def _barra(o, h, l, c):
    return pd.Series({"Open": o, "High": h, "Low": l, "Close": c})


# --------------------------------------------------------------------------- #
# 1. El caso real que lo destapó
# --------------------------------------------------------------------------- #
def test_mrna_2026_08_13_no_vende_en_el_maximo_del_dia():
    """MRNA/A, operación 72. Objetivo vigente 67.42; el día hizo máximo en 65.33.

    Con el bug, el recálculo bajaba el objetivo a 65.33 —la resistencia, que
    ese día era el propio máximo— y la salida se disparaba contra él: venta
    registrada a 65.33, el máximo exacto, con un +19.24%.

    Corregido, el objetivo del día es el de la víspera (67.42), el máximo no lo
    alcanza y la posición NO se cierra. Sigue viva, como habría seguido en un
    broker de verdad.
    """
    pos = {"objetivo": 67.42, "stop": None, "entrada": 54.79}
    bar = _barra(o=63.10, h=65.33, l=62.40, c=64.90)

    # Lo que hacía el bug: evaluar contra el objetivo recalculado con el high.
    con_bug = simulador.evaluar_salida_dia(pos, bar, False, objetivo=65.33)
    assert con_bug == ("objetivo", 65.33), "así se comportaba antes"

    # Lo que hace ahora: el objetivo del día es el que estaba puesto al abrir.
    corregido = simulador.evaluar_salida_dia(pos, bar, False, objetivo=67.42)
    assert corregido is None, "no debe cerrarse: el máximo no llegó al objetivo"


def test_el_objetivo_de_la_vispera_manda_aunque_el_nuevo_sea_menor():
    """Un objetivo recalculado A LA BAJA no puede cerrar la posición de hoy.

    Es la forma general del caso de MRNA: si bastara con que el objetivo nuevo
    quedara por debajo del máximo del día, cualquier recálculo a la baja
    fabricaría una venta a un precio que ya había pasado.
    """
    pos = {"objetivo": 120.0, "stop": None, "entrada": 100.0}
    bar = _barra(o=104.0, h=110.0, l=103.0, c=109.0)
    assert simulador.evaluar_salida_dia(pos, bar, False, objetivo=120.0) is None
    # El nivel recalculado (110, el máximo de hoy) habría vendido en el máximo.
    assert simulador.evaluar_salida_dia(pos, bar, False, objetivo=110.0) is not None


# --------------------------------------------------------------------------- #
# 2. El orden dentro de gestionar_posiciones
# --------------------------------------------------------------------------- #
def _estado_con(pos):
    return {"posiciones": {"A": [pos], "B": []}, "entradas_pendientes": []}


def _precios(fecha, o, h, l, c, historia=20):
    """Serie con `historia` sesiones planas y la barra de `fecha` al final."""
    fechas = pd.bdate_range(end=fecha, periods=historia)
    df = pd.DataFrame({"Open": 100.0, "High": 100.0, "Low": 100.0,
                       "Close": 100.0, "Volume": 1e6}, index=fechas)
    df.loc[pd.Timestamp(fecha)] = {"Open": o, "High": h, "Low": l,
                                   "Close": c, "Volume": 1e6}
    return df


def test_una_posicion_que_se_cierra_no_registra_cambio_de_objetivo(monkeypatch):
    """Mover la orden límite de algo ya vendido no significa nada.

    Antes se registraba igual, porque el recálculo iba primero. Ahora el
    recálculo solo ocurre si la posición sobrevive al día.
    """
    registradas = []
    monkeypatch.setattr(simulador.bitacora, "registrar_salida",
                        lambda *a, **k: registradas.append(a))
    monkeypatch.setattr(simulador.bitacora, "actualizar_objetivo",
                        lambda *a, **k: pytest.fail(
                            "no debe tocarse el objetivo de una posición que cierra hoy"))

    pos = {"id": 1, "ticker": "TEST", "portafolio": "A", "entrada": 100.0,
           "objetivo": 105.0, "stop": None, "fecha_entrada": "2026-09-01",
           "dia_limite": "2026-09-30", "historial_objetivos": []}
    # El día sube con fuerza y supera el objetivo vigente: cierra.
    precios = {"TEST": _precios("2026-09-15", o=104.0, h=112.0, l=103.0, c=111.0)}

    cerradas, cambios = simulador.gestionar_posiciones(
        _estado_con(pos), precios, "2026-09-15")

    assert len(cerradas) == 1
    assert cerradas[0]["motivo_salida"] == "objetivo"
    assert cerradas[0]["precio_salida"] == 105.0   # al objetivo, no al máximo
    assert cambios == []


def test_una_posicion_que_sobrevive_si_recalcula_el_objetivo(monkeypatch):
    """El recálculo no desaparece: se mueve después, para que rija mañana."""
    monkeypatch.setattr(simulador.bitacora, "actualizar_objetivo", lambda *a, **k: None)
    monkeypatch.setattr(simulador.bitacora, "registrar_salida",
                        lambda *a, **k: pytest.fail("no debía cerrarse"))

    pos = {"id": 1, "ticker": "TEST", "portafolio": "A", "entrada": 100.0,
           "objetivo": 200.0, "stop": None, "fecha_entrada": "2026-09-01",
           "dia_limite": "2026-09-30", "historial_objetivos": []}
    precios = {"TEST": _precios("2026-09-15", o=104.0, h=112.0, l=103.0, c=111.0)}
    estado = _estado_con(pos)

    cerradas, cambios = simulador.gestionar_posiciones(estado, precios, "2026-09-15")

    assert cerradas == []
    assert len(cambios) == 1, "la posición sigue viva: su objetivo se recalcula"
    assert estado["posiciones"]["A"][0]["objetivo"] != 200.0
    assert pos["historial_objetivos"][-1]["fecha"] == "2026-09-15"


def test_el_stop_sigue_ganando_al_objetivo(monkeypatch):
    """La corrección no toca el supuesto conservador: si se tocan los dos, stop."""
    monkeypatch.setattr(simulador.bitacora, "registrar_salida", lambda *a, **k: None)
    monkeypatch.setattr(simulador.bitacora, "actualizar_objetivo", lambda *a, **k: None)

    pos = {"id": 1, "ticker": "TEST", "portafolio": "A", "entrada": 100.0,
           "objetivo": 105.0, "stop": 95.0, "fecha_entrada": "2026-09-01",
           "dia_limite": "2026-09-30", "historial_objetivos": []}
    # El día toca el stop Y el objetivo.
    precios = {"TEST": _precios("2026-09-15", o=100.0, h=110.0, l=94.0, c=108.0)}

    cerradas, _ = simulador.gestionar_posiciones(_estado_con(pos), precios, "2026-09-15")
    assert cerradas[0]["motivo_salida"] == "stop"
    assert cerradas[0]["precio_salida"] == 95.0


def test_el_dia_de_entrada_usa_el_objetivo_de_la_preapertura(monkeypatch):
    """El objetivo inicial se calcula con datos de pre-apertura: está limpio.

    Antes, `gestionar_posiciones` lo recalculaba con la barra del propio día de
    entrada antes de evaluar nada, así que ni siquiera el primer día se
    respetaba el nivel que se había decidido sin ver el mercado.
    """
    monkeypatch.setattr(simulador.bitacora, "actualizar_objetivo", lambda *a, **k: None)
    monkeypatch.setattr(simulador.bitacora, "registrar_salida",
                        lambda *a, **k: pytest.fail("no debía cerrarse el día de entrada"))

    pos = {"id": 1, "ticker": "TEST", "portafolio": "A", "entrada": 100.0,
           "objetivo": 115.0, "stop": None, "fecha_entrada": "2026-09-15",
           "dia_limite": "2026-09-29", "historial_objetivos": [
               {"fecha": "2026-09-15", "objetivo": 115.0, "motivo": "objetivo inicial"}]}
    # El día de entrada hace máximo en 112: por debajo del objetivo decidido.
    precios = {"TEST": _precios("2026-09-15", o=100.0, h=112.0, l=99.0, c=111.0)}

    cerradas, _ = simulador.gestionar_posiciones(_estado_con(pos), precios, "2026-09-15")
    assert cerradas == []
