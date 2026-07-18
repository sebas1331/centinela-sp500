"""Tests de features y etiquetado (sin look-ahead)."""
import numpy as np
import pandas as pd
from centinela import features, etiquetado, config


def _serie(n=300, seed=0):
    rng = np.random.RandomState(seed)
    precio = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(precio, index=idx)
    df = pd.DataFrame({
        "Open": close.shift(1).fillna(close.iloc[0]),
        "High": close * 1.01,
        "Low": close * 0.99,
        "Close": close,
        "Volume": rng.randint(1e6, 5e6, n),
    }, index=idx)
    return df


def test_columnas_features_exactas():
    f = features.calcular_features(_serie())
    assert list(f.columns) == config.FEATURES_MODELO


def test_gap_overnight():
    df = _serie()
    f = features.calcular_features(df)
    esperado = df["Open"] / df["Close"].shift(1) - 1.0
    assert np.allclose(f["gap_overnight"].dropna(), esperado.loc[f["gap_overnight"].dropna().index])


def test_drawdown_no_negativo_y_acotado():
    f = features.calcular_features(_serie())
    dd = f["drawdown"].dropna()
    assert (dd >= -1e-9).all() and (dd <= 1.0).all()


def test_etiqueta_ultimas_filas_nan():
    df = _serie()
    y = etiquetado.etiquetar(df, horizonte=10, objetivo=0.05)
    # las últimas 10 filas no tienen horizonte completo
    assert y.tail(10).isna().all()


def test_etiqueta_positiva_cuando_sube():
    # construye un caso donde t+1..t+10 alcanza claramente +5% sobre open(t+1)
    idx = pd.bdate_range("2022-01-03", periods=12)
    close = pd.Series([100]*12, index=idx, dtype=float)
    df = pd.DataFrame({"Open": 100.0, "High": 100.0, "Low": 100.0,
                       "Close": close, "Volume": 1e6}, index=idx)
    df.iloc[5, df.columns.get_loc("High")] = 110.0  # un high alto dentro del horizonte
    y = etiquetado.etiquetar(df, horizonte=10, objetivo=0.05)
    assert y.iloc[0] == 1.0


def test_construir_dataset_solo_drawdown():
    df = _serie(seed=3)
    ds = etiquetado.construir_dataset({"XXX": df}, solo_drawdown=True)
    if len(ds):
        assert (ds["drawdown"] >= config.DRAWDOWN_MINIMO - 1e-9).all()
        assert set(ds["y"].unique()).issubset({0, 1})
