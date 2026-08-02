"""Tests del reentrenamiento mensual (workflow largo programado).

Cubren la regresión del 2026-08-01 (primera ejecución en vivo, workflow rojo):
con la caché de Actions ".cache/historico" fría (nunca guardada por ningún
workflow hasta entonces), `actualizar_historico_incremental` escribía un
parquet de solo ~30 días como si fuera el histórico COMPLETO, dejando el
dataset de entrenamiento en 0 filas y el pipeline moría con un TypeError
crudo tres funciones más abajo (fecha_max NaN - DateOffset).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from centinela import config  # noqa: E402
from centinela.modelo import Modelo, construir_estimador  # noqa: E402

import entrenar_inicial as ei  # noqa: E402
import reentrenar_mensual as rm  # noqa: E402


def _serie(n=1600, seed=0, inicio="2018-01-02"):
    """Camino aleatorio con volatilidad suficiente para generar drawdowns >=30%
    y rebotes reales (ver test_features_etiquetado._serie): sirve para
    ejercitar el pipeline completo sin depender de datos reales de yfinance."""
    rng = np.random.RandomState(seed)
    precio = 100 * np.cumprod(1 + rng.normal(0.0002, 0.02, n))
    idx = pd.bdate_range(inicio, periods=n)
    close = pd.Series(precio, index=idx)
    return pd.DataFrame({
        "Open": close.shift(1).fillna(close.iloc[0]),
        "High": close * 1.01,
        "Low": close * 0.99,
        "Close": close,
        "Volume": rng.randint(1_000_000, 5_000_000, n),
    }, index=idx)


# --------------------------------------------------------------------------- #
# Regresión puntual: bootstrap de caché fría en actualizar_historico_incremental
# --------------------------------------------------------------------------- #
def test_cache_fria_descarga_historico_completo_no_stub_de_30_dias(monkeypatch, tmp_path):
    """Con .cache/historico VACÍO (como en el primer run real de este workflow),
    el histórico persistido debe quedar COMPLETO, no truncado a ~30 días."""
    monkeypatch.setattr(ei, "DIR_HIST", tmp_path / "historico")

    tickers = ["AAA", "BBB"]
    completos = {t: _serie(n=1600, seed=i) for i, t in enumerate(tickers)}

    def fake_descargar_historico(pendientes, refrescar=False):
        out = {}
        for t in pendientes:
            df = completos[t]
            ei.DIR_HIST.mkdir(parents=True, exist_ok=True)
            df.to_parquet(ei.DIR_HIST / f"{t}.parquet")
            out[t] = df
        return out

    monkeypatch.setattr(ei, "descargar_historico", fake_descargar_historico)

    def fail_si_se_llama(*a, **k):
        raise AssertionError("no debería pedirse un incremental de 30 días sin base cacheada")
    monkeypatch.setattr(rm.datos, "descargar", fail_si_se_llama)

    hist = rm.actualizar_historico_incremental(tickers)

    for t in tickers:
        assert len(hist[t]) == 1600, f"{t}: histórico en memoria truncado ({len(hist[t])} filas)"
        en_disco = pd.read_parquet(ei.DIR_HIST / f"{t}.parquet")
        assert len(en_disco) == 1600, (
            f"{t}: se persistió un histórico de solo {len(en_disco)} filas — "
            "este es exactamente el bug del 2026-08-01 (stub de 30 días guardado "
            "como si fuera el histórico completo)."
        )


def test_cache_caliente_solo_anexa_lo_reciente(monkeypatch, tmp_path):
    """Con base ya cacheada, se anexa el incremental reciente (comportamiento
    normal, rápido) sin redescargar el histórico completo."""
    monkeypatch.setattr(ei, "DIR_HIST", tmp_path / "historico")
    ei.DIR_HIST.mkdir(parents=True, exist_ok=True)

    base = _serie(n=1600, seed=0)
    base.to_parquet(ei.DIR_HIST / "AAA.parquet")

    hoy = pd.Timestamp(pd.Timestamp.now(config.TZ_ET).date())
    nuevo_dia = pd.bdate_range(base.index.max() + pd.Timedelta(days=1), periods=1)
    fresco = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                          "Close": [1.0], "Volume": [1.0]}, index=nuevo_dia)

    monkeypatch.setattr(rm.datos, "descargar", lambda grupo, start, end: {"AAA": fresco})

    def fail_si_se_llama(*a, **k):
        raise AssertionError("no debería redescargar el histórico completo con base ya cacheada")
    monkeypatch.setattr(ei, "descargar_historico", fail_si_se_llama)

    hist = rm.actualizar_historico_incremental(["AAA"])
    assert len(hist["AAA"]) == 1601
    assert hist["AAA"].index.max() == nuevo_dia[0]


# --------------------------------------------------------------------------- #
# Simulación rápida de una ejecución completa (subconjunto de tickers)
# --------------------------------------------------------------------------- #
@pytest.fixture
def universo_sintetico():
    n_tickers = 8
    return {f"T{i}": _serie(n=1600, seed=i) for i in range(n_tickers)}


def test_reentrenamiento_mensual_end_to_end_caché_fría(monkeypatch, tmp_path, universo_sintetico):
    """Simula una ejecución real de scripts/reentrenar_mensual.py de punta a
    punta con un universo pequeño y la caché de histórico completamente fría
    (el escenario exacto que tumbó el workflow el 2026-08-01). Debe: terminar
    sin excepción, producir un dataset no-trivial, guardar un modelo nuevo y
    respetar la regla dura de umbral (pocos cierres nuevos => no cambia)."""
    tickers = list(universo_sintetico.keys())

    # Rutas: todo a tmp_path, nada toca el repo real.
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "ARCHIVO_MODELO", tmp_path / "modelo_centinela.pkl")
    monkeypatch.setattr(config, "ARCHIVO_ATH", tmp_path / "ath.json")
    monkeypatch.setattr(ei, "DIR_HIST", tmp_path / "historico")

    # Universo pequeño en vez de los 503 reales.
    monkeypatch.setattr(rm.universo, "tickers_sp500", lambda: tickers)

    # Caché fría: cada ticker se "descarga" completo (simulando yfinance).
    def fake_descargar_historico(pendientes, refrescar=False):
        out = {}
        ei.DIR_HIST.mkdir(parents=True, exist_ok=True)
        for t in pendientes:
            df = universo_sintetico[t]
            df.to_parquet(ei.DIR_HIST / f"{t}.parquet")
            out[t] = df
        return out
    monkeypatch.setattr(ei, "descargar_historico", fake_descargar_historico)

    def sin_red(*a, **k):
        raise AssertionError("no debería llamarse a datos.descargar en este escenario 100% frío")
    monkeypatch.setattr(rm.datos, "descargar", sin_red)

    # Pocos cierres nuevos (<30): la regla dura debe dejar el umbral intacto.
    monkeypatch.setattr(rm.bitacora, "n_cerradas_desde", lambda ref: 5)

    # Modelo "actual" preexistente (como el vigente en producción).
    modelo_previo = Modelo(
        estimador=construir_estimador("logreg"),
        features=config.FEATURES_MODELO, umbral=0.79, tipo="logreg",
        metadatos={"entrenado": "2026-01-15", "ultimo_reentreno": "2026-06-01"},
    )
    # Necesita estar "ajustado" para poder guardarse/cargarse igual que el real;
    # basta con un fit trivial sobre datos sintéticos con las mismas columnas.
    X_dummy = pd.DataFrame(np.random.RandomState(0).normal(size=(60, len(config.FEATURES_MODELO))),
                           columns=config.FEATURES_MODELO)
    y_dummy = pd.Series([0, 1] * 30)
    modelo_previo.estimador.fit(X_dummy, y_dummy)
    modelo_previo.guardar()

    monkeypatch.setattr(sys, "argv", ["reentrenar_mensual.py", "--anios", "6"])

    rm.main()

    # El modelo se reajustó y se guardó de nuevo.
    modelo_nuevo = Modelo.cargar()
    assert modelo_nuevo.tipo == "logreg"
    assert modelo_nuevo.umbral == 0.79, "regla dura violada: el umbral cambió con <30 cierres nuevos"
    assert modelo_nuevo.metadatos["n_filas_train"] > 1000, (
        "dataset de reentrenamiento sospechosamente pequeño: si esto falla, "
        "es probable que haya vuelto el bug de caché fría del 2026-08-01"
    )
    assert modelo_nuevo.metadatos["ultimo_reentreno"] == pd.Timestamp.now(config.TZ_ET).date().isoformat()

    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Reentrenamiento mensual" in changelog
    assert "Umbral SIN cambios" in changelog


def test_reentrenamiento_mensual_falla_alto_y_claro_si_dataset_queda_vacio(monkeypatch, tmp_path):
    """Si por cualquier motivo el histórico llega truncado/vacío, el script debe
    abortar con un mensaje diagnóstico claro en vez de reventar varias llamadas
    más abajo con un TypeError críptico (lo que pasó el 2026-08-01)."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "ARCHIVO_MODELO", tmp_path / "modelo_centinela.pkl")
    monkeypatch.setattr(config, "ARCHIVO_ATH", tmp_path / "ath.json")
    monkeypatch.setattr(ei, "DIR_HIST", tmp_path / "historico")

    tickers = ["AAA", "BBB"]
    monkeypatch.setattr(rm.universo, "tickers_sp500", lambda: tickers)

    # Simula el bug original: solo hay ~20 filas por ticker (un mes), muy por
    # debajo de las 220 necesarias -> construir_dataset descarta todo.
    def fake_descargar_historico(pendientes, refrescar=False):
        out = {}
        ei.DIR_HIST.mkdir(parents=True, exist_ok=True)
        for t in pendientes:
            df = _serie(n=20, seed=hash(t) % 1000)
            df.to_parquet(ei.DIR_HIST / f"{t}.parquet")
            out[t] = df
        return out
    monkeypatch.setattr(ei, "descargar_historico", fake_descargar_historico)
    monkeypatch.setattr(rm.datos, "descargar", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no debería llegar aquí con caché 100% fría")))
    monkeypatch.setattr(sys, "argv", ["reentrenar_mensual.py", "--anios", "6"])

    with pytest.raises(RuntimeError, match="sospechosamente pequeño"):
        rm.main()
