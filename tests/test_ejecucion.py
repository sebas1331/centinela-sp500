"""Tests del primitivo de ejecución de salidas (reglas conservadoras)."""
import pandas as pd
from centinela import ejecucion as ej


def _bars(rows):
    idx = pd.date_range("2024-01-02", periods=len(rows), freq="B")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


ENTRADA, OBJ, STOP = 100.0, 105.0, 93.0


def test_objetivo_limpio():
    b = _bars([[100, 102, 99, 101], [101, 106, 100, 105]])
    r = ej.simular_salida(b, ENTRADA, OBJ, STOP)
    assert r["motivo"] == "objetivo" and r["precio_salida"] == 105.0


def test_stop_limpio():
    b = _bars([[100, 102, 99, 101], [101, 101, 92, 94]])
    r = ej.simular_salida(b, ENTRADA, OBJ, STOP)
    assert r["motivo"] == "stop" and r["precio_salida"] == 93.0


def test_ambos_mismo_dia_gana_stop():
    b = _bars([[100, 106, 92, 100]])
    r = ej.simular_salida(b, ENTRADA, OBJ, STOP)
    assert r["motivo"] == "stop"


def test_gap_alza_ejecuta_al_open():
    b = _bars([[100, 101, 99, 100], [107, 108, 106, 107]])
    r = ej.simular_salida(b, ENTRADA, OBJ, STOP)
    assert r["motivo"] == "objetivo" and r["precio_salida"] == 107.0


def test_gap_baja_ejecuta_al_open():
    b = _bars([[100, 101, 99, 100], [90, 92, 88, 89]])
    r = ej.simular_salida(b, ENTRADA, OBJ, STOP)
    assert r["motivo"] == "stop" and r["precio_salida"] == 90.0


def test_limite_de_tiempo():
    b = _bars([[100, 101, 99, 100], [100, 102, 99, 101], [101, 103, 100, 102]])
    r = ej.simular_salida(b, ENTRADA, OBJ, STOP)
    assert r["motivo"] == "tiempo" and r["precio_salida"] == 102.0


def test_cartera_b_sin_stop_no_para():
    b = _bars([[100, 101, 80, 82], [82, 106, 81, 105]])
    r = ej.simular_salida(b, ENTRADA, OBJ, None)
    assert r["motivo"] == "objetivo"


def test_pnl_pct():
    assert abs(ej.pnl_pct(100, 105) - 0.05) < 1e-9
