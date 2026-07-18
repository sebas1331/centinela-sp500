"""Modelo de señal: predice P(+5% en <=10 días hábiles) para acciones en drawdown.

Diseño anti-sobreajuste:
  - Features FIJOS y acotados (12 técnicos), con regularización fuerte.
  - Gradient boosting histográfico (sklearn) poco profundo + hojas grandes, o
    regresión logística L2 como baseline; se elige el que valide mejor.
  - Probabilidades CALIBRADAS (isotónica/sigmoide) sobre una cola cronológica.
  - Umbral de decisión elegido para PRECISIÓN (pocas señales pero buenas).

El modelo NUNCA se elige ni ajusta contra el holdout (último año): eso solo se
usa una vez para el reporte final honesto (ver backtest.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV

from . import config


def construir_estimador(tipo: str = "hgb"):
    """Crea un estimador SIN entrenar. tipo: 'hgb' (gradient boosting) o 'logreg'."""
    if tipo == "hgb":
        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=300,
            max_leaf_nodes=31,
            max_depth=4,            # poco profundo -> regulariza
            min_samples_leaf=80,    # hojas grandes -> regulariza
            l2_regularization=1.0,
            early_stopping=True,
            validation_fraction=0.15,
            n_iter_no_change=20,
            random_state=42,
        )
    if tipo == "logreg":
        return Pipeline([
            ("escala", StandardScaler()),
            ("lr", LogisticRegression(C=0.5, penalty="l2", max_iter=1000,
                                      class_weight="balanced", random_state=42)),
        ])
    raise ValueError(f"tipo de modelo desconocido: {tipo}")


def _calibrar(base_ajustado, Xc, yc, metodo):
    """Calibra un estimador YA ajustado sobre (Xc, yc)."""
    try:  # sklearn >= 1.6 recomienda FrozenEstimator en lugar de cv='prefit'
        from sklearn.frozen import FrozenEstimator
        cal = CalibratedClassifierCV(FrozenEstimator(base_ajustado), method=metodo)
        cal.fit(Xc, yc)
    except Exception:  # noqa: BLE001
        cal = CalibratedClassifierCV(base_ajustado, method=metodo, cv="prefit")
        cal.fit(Xc, yc)
    return cal


def entrenar_calibrado(X: pd.DataFrame, y: pd.Series, tipo: str = "hgb"):
    """Entrena y calibra. X, y deben venir ORDENADOS por fecha.

    Reserva la última cola (~15%) para calibrar las probabilidades (isotónica si
    hay datos suficientes, si no sigmoide). Devuelve el estimador calibrado.
    """
    base = construir_estimador(tipo)
    n = len(X)
    k = max(1, int(n * 0.85))
    Xf, yf = X.iloc[:k], y.iloc[:k]
    Xc, yc = X.iloc[k:], y.iloc[k:]
    if len(Xc) < 50 or yc.nunique() < 2:
        # muy pocos datos para calibrar aparte: calibración interna por CV
        base.fit(Xf, yf)
        return base
    base.fit(Xf, yf)
    metodo = "isotonic" if (len(Xc) >= 500) else "sigmoid"
    return _calibrar(base, Xc, yc, metodo)


def elegir_umbral(y_true, proba, recall_min: float = 0.05):
    """Elige el umbral que MAXIMIZA la precisión con señales suficientes.

    Restricciones: recall >= recall_min y nº de señales >= max(25, 3% del total),
    para evitar umbrales degenerados (1 señal = 100% precisión no sirve).
    Devuelve (umbral, precision, recall, n_senales).
    """
    y_true = np.asarray(y_true).astype(int)
    proba = np.asarray(proba, dtype=float)
    n = len(y_true)
    min_senales = max(25, int(0.03 * n))

    mejor = (config.UMBRAL_PROB_DEFECTO, 0.0, 0.0, 0)
    mejor_prec = -1.0
    for u in np.arange(0.30, 0.901, 0.01):
        sel = proba >= u
        ns = int(sel.sum())
        if ns < min_senales:
            continue
        tp = int((y_true[sel] == 1).sum())
        prec = tp / ns
        rec = tp / max(1, int((y_true == 1).sum()))
        if rec < recall_min:
            continue
        # maximizar precisión; desempate por más recall
        if prec > mejor_prec or (abs(prec - mejor_prec) < 1e-9 and rec > mejor[2]):
            mejor_prec = prec
            mejor = (round(float(u), 3), round(prec, 4), round(rec, 4), ns)
    return mejor


@dataclass
class Modelo:
    """Contenedor serializable del modelo entrenado y su metadatos."""
    estimador: object
    features: list
    umbral: float
    tipo: str
    metadatos: dict = field(default_factory=dict)

    def predecir_proba(self, X) -> np.ndarray:
        if isinstance(X, dict):
            X = pd.DataFrame([X])[self.features]
        elif isinstance(X, pd.DataFrame):
            X = X[self.features]
        return self.estimador.predict_proba(X)[:, 1]

    def hay_senal(self, X) -> np.ndarray:
        return self.predecir_proba(X) >= self.umbral

    def guardar(self, ruta=None):
        ruta = ruta or config.ARCHIVO_MODELO
        joblib.dump(self, ruta)
        return ruta

    @staticmethod
    def cargar(ruta=None) -> "Modelo":
        ruta = ruta or config.ARCHIVO_MODELO
        return joblib.load(ruta)
