"""Tests de la bitácora (SQLite + espejo CSV)."""
import importlib
import pandas as pd
import pytest

from centinela import config


@pytest.fixture()
def bit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_SQLITE", tmp_path / "b.sqlite")
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_CSV", tmp_path / "b.csv")
    from centinela import bitacora
    importlib.reload(bitacora)
    return bitacora


def _entrada(**kw):
    base = dict(grupo=1, ticker="AAA", portafolio="A", sector="Tech",
                fecha_entrada="2026-01-05", hora_entrada_et="09:30",
                hora_entrada_utc="14:30", timestamp_escaneo="2026-01-05T08:45",
                precio_entrada=100.0, probabilidad=0.8, score_fundamental=55.0,
                objetivo_inicial=105.0, objetivo_actual=105.0,
                historial_objetivos=[{"fecha": "2026-01-05", "objetivo": 105.0,
                                      "motivo": "inicial"}], stop=93.0, notas="x")
    base.update(kw)
    return base


def test_entrada_salida_y_pnl(bit):
    oid = bit.registrar_entrada(_entrada())
    assert oid == 1
    abiertas = bit.operaciones_abiertas()
    assert len(abiertas) == 1 and abiertas.iloc[0]["estado"] == "abierta"

    bit.registrar_salida(oid, "2026-01-09", "16:00", 105.0, "objetivo", 0.05, 4)
    cerr = bit.operaciones_cerradas()
    assert len(cerr) == 1
    fila = cerr.iloc[0]
    assert fila["motivo_salida"] == "objetivo"
    assert abs(fila["pnl_pct"] - 0.05) < 1e-9
    # P&L% coherente con precios
    assert abs((fila["precio_salida"] / fila["precio_entrada"] - 1) - fila["pnl_pct"]) < 1e-9


def test_actualizar_objetivo_e_historial(bit):
    oid = bit.registrar_entrada(_entrada())
    hist = [{"fecha": "2026-01-05", "objetivo": 105.0, "motivo": "inicial"},
            {"fecha": "2026-01-06", "objetivo": 107.0, "motivo": "recálculo"}]
    bit.actualizar_objetivo(oid, 107.0, hist)
    df = bit.cargar_df()
    assert df.iloc[0]["objetivo_actual"] == 107.0
    assert "recálculo" in df.iloc[0]["historial_objetivos"]


def test_csv_espejo_existe_y_ordenado(bit):
    bit.registrar_entrada(_entrada(grupo=2, portafolio="B", fecha_entrada="2026-02-01"))
    bit.registrar_entrada(_entrada(grupo=1, portafolio="A", fecha_entrada="2026-01-05"))
    assert config.ARCHIVO_BITACORA_CSV.exists()
    csv = pd.read_csv(config.ARCHIVO_BITACORA_CSV)
    # ordenado por fecha_entrada ascendente
    fechas = pd.to_datetime(csv["fecha_entrada"]).tolist()
    assert fechas == sorted(fechas)
