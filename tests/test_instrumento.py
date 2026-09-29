"""Cuando XTB ofrece la acción y su CFD con el mismo símbolo, hay que acertar.

El 2026-09-29 se perdieron siete de ocho compras por esto. XTB tiene dos
instrumentos llamados `F.US`: la acción (id 7813, clave `9_F.US_US_STC`) y su
CFD en close-only (id 335, clave `4_F.US_US_STC CFD`). El cliente coge «el
primero que coincida» y el orden de la lista cambia entre sesiones, así que unas
veces mandaba la acción y otras el CFD — y el servicio de contado rechaza el
CFD con «Could not find instrument for id: 335».
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import centinela  # noqa: F401,E402  — pone vendor/ en sys.path
from xtb_api.client import _is_cash_instrument  # noqa: E402
from xtb_api.exceptions import InstrumentNotFoundError  # noqa: E402


class Resultado:
    def __init__(self, symbol, instrument_id, symbol_key="", name="",
                 description=""):
        self.symbol = symbol
        self.instrument_id = instrument_id
        self.symbol_key = symbol_key
        self.name = name
        self.description = description
        self.asset_class = symbol_key[:1]


ACCION = Resultado("F.US", 7813, "9_F.US_US_STC", "Ford Motor Co")
CFD = Resultado("F.US", 335, "4_F.US_US_STC CFD", "CLOSE ONLY / Ford Motor Co CFD")
OTRO = Resultado("FF.US", 23201, "9_FF.US_US_STC", "FutureFuel Corp")


def _elegir(resultados, simbolo):
    """Lo que hace `_resolve_instrument_id` de la copia, sin red."""
    exactos = [r for r in resultados if r.symbol.upper() == simbolo.upper()]
    if not exactos:
        raise InstrumentNotFoundError(f"Symbol not found: {simbolo}")
    contado = [r for r in exactos if _is_cash_instrument(r)]
    if not contado:
        raise InstrumentNotFoundError(
            f"De los {len(exactos)} instrumentos que XTB llama {simbolo}, "
            f"ninguno es la acción al contado: "
            + ", ".join(f"{r.symbol_key} (id {r.instrument_id})" for r in exactos))
    return contado[0]


def test_entre_la_accion_y_su_CFD_se_elige_la_accion():
    assert _elegir([ACCION, CFD, OTRO], "F.US").instrument_id == 7813


def test_y_da_igual_en_que_orden_vengan():
    """Esta es LA prueba: el orden de la lista cambia entre sesiones, y de ahí
    salía la intermitencia de 1 de cada 8."""
    assert _elegir([CFD, ACCION, OTRO], "F.US").instrument_id == 7813
    assert _elegir([OTRO, CFD, ACCION], "F.US").instrument_id == 7813


def test_un_simbolo_parecido_no_vale():
    """El cliente, sin coincidencia exacta, se conforma con el primer resultado
    de la búsqueda. Eso es mandar una orden sobre algo que nadie pidió."""
    with pytest.raises(InstrumentNotFoundError, match="Symbol not found"):
        _elegir([OTRO], "F.US")


def test_si_solo_queda_el_CFD_se_falla_en_vez_de_operarlo():
    """Operar el instrumento equivocado es peor que no operar."""
    with pytest.raises(InstrumentNotFoundError, match="ninguno es la acción"):
        _elegir([CFD], "F.US")


def test_el_mensaje_dice_QUE_habia_para_poder_mirarlo():
    with pytest.raises(InstrumentNotFoundError) as exc:
        _elegir([CFD], "F.US")
    assert "4_F.US_US_STC CFD" in str(exc.value) and "335" in str(exc.value)


@pytest.mark.parametrize("clave,nombre,esperado", [
    ("9_AAPL.US_US_STC", "Apple Inc", True),
    ("4_AAPL.US_US_STC CFD", "Apple Inc CFD", False),
    ("", "Apple Inc", True),
    ("", "CLOSE ONLY / Apple Inc CFD", False),
])
def test_se_distingue_por_la_clave_y_de_reserva_por_el_nombre(clave, nombre, esperado):
    """La clave es estructura y el nombre es texto para humanos, que puede
    cambiar de un día para otro. Se mira la clave primero."""
    assert _is_cash_instrument(Resultado("AAPL.US", 1, clave, nombre)) is esperado


def test_la_accion_se_elige_aunque_el_CFD_no_diga_que_lo_es():
    """Si XTB dejara de escribir 'CFD' en el nombre, la clave lo seguiría
    diciendo."""
    cfd_callado = Resultado("F.US", 335, "4_F.US_US_STC", "Ford Motor Co")
    assert _elegir([cfd_callado, ACCION], "F.US").instrument_id == 7813
