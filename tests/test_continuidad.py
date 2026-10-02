"""Una serie rota por una acción corporativa sin ajustar no se opera.

CTVA pasó de 77,65 a 12,57 en un día: una escisión que yfinance no reajustó. El
sistema vio "dd=86.2% | prob=1.000 | ENTRAR" y compró. El drawdown real era ~0%:
la acción bajó porque repartió el resto en otra compañía.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import continuidad  # noqa: E402


def _serie(cierres):
    idx = pd.bdate_range("2026-01-01", periods=len(cierres))
    return pd.DataFrame({"Close": cierres,
                         "High": [c * 1.01 for c in cierres],
                         "Low": [c * 0.99 for c in cierres],
                         "Open": cierres}, index=idx)


def test_una_serie_normal_no_esta_rota():
    assert continuidad.esta_rota(_serie([100, 101, 99, 103, 102])) is None


def test_el_caso_de_CTVA():
    """-84% en un día con el precio anterior intacto: eso no es el mercado."""
    salto = continuidad.esta_rota(_serie([80, 79, 78, 77.65, 12.57, 12.9]))
    assert salto is not None
    assert salto["antes"] == 77.65 and salto["despues"] == 12.57
    assert salto["ratio"] < 0.2


def test_el_caso_de_MRNA():
    """x2,77 hacia arriba: probable contrasplit sin ajustar."""
    salto = continuidad.esta_rota(_serie([60, 61, 62.96, 174.38, 170.0]))
    assert salto is not None and salto["ratio"] > 2.5


def test_un_desplome_brutal_pero_real_SI_pasa():
    """Un -35% en un día es brutal y ocurre. No se puede excluir el mercado."""
    assert continuidad.esta_rota(_serie([100, 100, 65, 64, 66])) is None


def test_y_un_subidon_tambien():
    assert continuidad.esta_rota(_serie([100, 100, 160, 158, 161])) is None


def test_un_salto_viejo_deja_de_contar():
    """Cuando sale de la ventana que el modelo mira, el ticker vuelve solo: ya
    no contamina ni las features ni el ATR."""
    cierres = [80, 12] + [12 + i * 0.01 for i in range(300)]
    assert continuidad.esta_rota(_serie(cierres), ventana=50) is None
    assert continuidad.esta_rota(_serie(cierres), ventana=400) is not None


def test_el_motivo_explica_lo_que_pasa_y_por_que_se_descarta():
    salto = continuidad.esta_rota(_serie([80, 79, 78, 77.65, 12.57]))
    texto = continuidad.motivo("CTVA", salto)
    assert "SERIE ROTA" in texto and "77.65" in texto and "12.57" in texto
    assert "no son reales" in texto and "hasta que el salto salga" in texto


def test_una_serie_corta_o_vacia_no_revienta():
    assert continuidad.esta_rota(None) is None
    assert continuidad.esta_rota(_serie([100])) is None


# --------------------------------------------------------------------------- #
# El screener la descarta antes de mirar nada
# --------------------------------------------------------------------------- #
def test_el_screener_descarta_la_serie_rota_antes_del_drawdown(monkeypatch):
    """Antes del filtro de drawdown, porque es justo el drawdown lo que la
    serie rota falsea."""
    from centinela import screener, ath as ath_mod

    class ModeloFalso:
        umbral = 0.79
        def predecir_proba(self, _f):
            raise AssertionError("no debería llegar a evaluar una serie rota")

    rota = _serie([80] * 230 + [12] * 5)
    monkeypatch.setattr(ath_mod, "cargar_ath", lambda: {"CTVA": 90.0})
    decisiones, lineas, resumen = screener.escanear(
        {"CTVA": rota}, ModeloFalso(), consultar_fundamentales=False)

    assert decisiones == []
    assert resumen["series_rotas"] == 1
    assert any("SERIE ROTA" in l for l in lineas)
