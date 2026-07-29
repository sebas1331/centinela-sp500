"""Tests del cálculo MFE/MAE (sin red: usa barras OHLC sintéticas)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from analizar_mfe import calcular_mfe_mae, generar_markdown, analizar  # noqa: E402


def _ventana(datos: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """datos = [(fecha, open, high, low, close), ...]"""
    idx = pd.to_datetime([d[0] for d in datos])
    return pd.DataFrame({
        "Open": [d[1] for d in datos], "High": [d[2] for d in datos],
        "Low": [d[3] for d in datos], "Close": [d[4] for d in datos],
    }, index=idx)


def test_mfe_mae_basico():
    v = _ventana([
        ("2026-07-21", 100, 102, 99, 101),
        ("2026-07-22", 101, 108, 100, 107),   # máximo favorable: High=108
        ("2026-07-23", 107, 107, 94, 96),      # máximo adverso: Low=94
        ("2026-07-24", 96, 99, 95, 98),
    ])
    r = calcular_mfe_mae(entrada=100.0, fecha_entrada="2026-07-21",
                         objetivo_inicial=105.0, ventana=v)
    assert r["mfe_pct"] == pytest.approx(8.0)
    assert r["fecha_mfe"] == "2026-07-22"
    assert r["mae_pct"] == pytest.approx(-6.0)
    assert r["fecha_mae"] == "2026-07-23"
    assert r["pnl_actual_pct"] == pytest.approx(-2.0)   # último close = 98
    assert r["toco_5pct"] is True
    assert r["toco_objetivo"] is True                   # 108 >= 105


def test_no_toco_objetivo_ni_5pct():
    v = _ventana([("2026-07-21", 100, 102, 99, 101),
                  ("2026-07-22", 101, 103, 98, 100)])
    r = calcular_mfe_mae(entrada=100.0, fecha_entrada="2026-07-21",
                         objetivo_inicial=120.0, ventana=v)
    assert r["mfe_pct"] == pytest.approx(3.0)
    assert r["toco_5pct"] is False
    assert r["toco_objetivo"] is False


def test_objetivo_desconocido_da_none():
    v = _ventana([("2026-07-21", 100, 110, 99, 105)])
    r = calcular_mfe_mae(entrada=100.0, fecha_entrada="2026-07-21",
                         objetivo_inicial=None, ventana=v)
    assert r["toco_objetivo"] is None
    assert r["toco_5pct"] is True  # +5% no depende del objetivo


def test_ventana_vacia_devuelve_none():
    assert calcular_mfe_mae(100.0, "2026-07-21", 105.0, pd.DataFrame()) is None
    assert calcular_mfe_mae(100.0, "2026-07-21", 105.0, None) is None


def test_entrada_invalida_devuelve_none():
    v = _ventana([("2026-07-21", 100, 102, 99, 101)])
    assert calcular_mfe_mae(0.0, "2026-07-21", 105.0, v) is None
    assert calcular_mfe_mae(None, "2026-07-21", 105.0, v) is None


def test_analizar_sin_posiciones_no_revienta():
    bit = pd.DataFrame(columns=["estado", "fecha_entrada", "fecha_salida",
                                "ticker", "portafolio", "precio_entrada",
                                "objetivo_inicial"])
    df = analizar(bit)
    assert len(df) == 0


def test_analizar_descarta_cerradas_viejas(monkeypatch):
    import analizar_mfe
    hoy = pd.Timestamp("2026-07-28")
    monkeypatch.setattr(analizar_mfe, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: hoy.to_pydatetime())}))

    bit = pd.DataFrame([
        {"estado": "abierta", "fecha_entrada": "2026-07-20", "fecha_salida": "",
         "ticker": "AAA", "portafolio": "A", "precio_entrada": 100.0,
         "objetivo_inicial": 110.0},
        {"estado": "cerrada", "fecha_entrada": "2026-05-01", "fecha_salida": "2026-05-05",
         "ticker": "BBB", "portafolio": "A", "precio_entrada": 50.0,
         "objetivo_inicial": 55.0, "pnl_pct": 0.03, "motivo_salida": "tiempo"},
        {"estado": "cerrada", "fecha_entrada": "2026-07-15", "fecha_salida": "2026-07-20",
         "ticker": "CCC", "portafolio": "B", "precio_entrada": 20.0,
         "objetivo_inicial": 22.0, "pnl_pct": 0.05, "motivo_salida": "objetivo"},
    ])

    def descargar_falso(tickers, start=None, end=None):
        return {t: _ventana([("2026-07-21", 10, 12, 9, 11)]) for t in tickers}

    monkeypatch.setattr(analizar_mfe.datos, "descargar", descargar_falso)
    sel = analizar_mfe.seleccionar_posiciones(bit, 30, hoy)
    assert "BBB" not in sel["ticker"].tolist()   # cerrada hace >30 días: fuera
    assert "CCC" in sel["ticker"].tolist()
    assert "AAA" in sel["ticker"].tolist()


def test_generar_markdown_resumen_cuenta_bien(monkeypatch):
    df = pd.DataFrame([
        {"estado": "abierta", "ticker": "AAA", "portafolio": "A",
         "fecha_entrada": "2026-07-20", "precio_entrada": 100.0,
         "objetivo_inicial": 110.0, "sin_datos": False,
         "mfe_pct": 8.0, "fecha_mfe": "2026-07-22", "mae_pct": -3.0,
         "fecha_mae": "2026-07-21", "pnl_actual_pct": 2.0,
         "toco_5pct": True, "toco_objetivo": False},
        {"estado": "abierta", "ticker": "BBB", "portafolio": "B",
         "fecha_entrada": "2026-07-20", "precio_entrada": 50.0,
         "objetivo_inicial": 55.0, "sin_datos": False,
         "mfe_pct": 2.0, "fecha_mfe": "2026-07-21", "mae_pct": -1.0,
         "fecha_mae": "2026-07-22", "pnl_actual_pct": 1.0,
         "toco_5pct": False, "toco_objetivo": False},
    ])
    md, resumen = generar_markdown(df, 30)
    assert resumen["n_abiertas"] == 2
    assert resumen["n_toco_5pct"] == 1
    assert "AAA" in md and "BBB" in md
    assert resumen["top_mfe"][0]["ticker"] == "AAA"
