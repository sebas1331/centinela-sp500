"""Las órdenes de compra se dimensionan por capital por posición (floor) y una
acción más cara que el slot se emite con 0 acciones para que el ejecutor la
descarte en ámbar, no para que desaparezca en silencio."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

import generar_ordenes as go_ord  # noqa: E402

SESION = "2026-10-05"            # lunes de mercado


def _precios(mapa: dict[str, float]) -> dict:
    """Un df por ticker con un único cierre, anterior a la sesión."""
    idx = [pd.Timestamp("2026-10-02")]
    return {tk: pd.DataFrame({"Close": [p]}, index=idx) for tk, p in mapa.items()}


def _entradas(monkeypatch, tickers, slot_equity=30000.0, slots=20):
    monkeypatch.setattr(go_ord.est_mod, "cargar", lambda: {
        "entradas_pendientes": [
            {"fecha_decision": SESION, "carteras": ["A"], "ticker": tk,
             "atr": 1.0, "resistencia": None, "target_analista": None}
            for tk in tickers]})
    monkeypatch.setattr(go_ord, "_cuenta_de_la_cartera",
                        lambda *_a, **_k: {"equity_contable": slot_equity,
                                           "slots": slots})


def test_floor_por_capital_y_descarte_si_el_precio_supera_el_slot(monkeypatch):
    # slot = 30000/20 = 1500. FLEX a 92.6 -> floor(1500/92.6)=16; NVR a 5900 -> 0.
    _entradas(monkeypatch, ["FLEX", "NVR"])
    ordenes, descartes = go_ord.ordenes_de_compra(
        SESION, "A", None, _precios({"FLEX": 92.6, "NVR": 5900.0}))
    por_tk = {o.ticker: o for o in ordenes}

    # La barata se dimensiona por capital (floor), no por el recuento del simulador.
    assert por_tk["FLEX"].acciones == 16          # floor(1500/92.6)
    # La cara (precio > slot) NO es una orden: va a descartes (no desaparece en
    # silencio), con su motivo, para que el ejecutor la registre en ámbar.
    assert "NVR" not in por_tk
    assert len(descartes) == 1
    assert descartes[0]["ticker"] == "NVR"
    assert descartes[0]["motivo"] == "precio mayor que el capital por posición"
    assert descartes[0]["sesion"] == SESION


def test_una_accion_justo_bajo_el_slot_entra_con_una(monkeypatch):
    _entradas(monkeypatch, ["X"])
    ordenes, descartes = go_ord.ordenes_de_compra(
        SESION, "A", None, _precios({"X": 1499.0}))
    assert ordenes[0].acciones == 1 and not descartes   # floor(1500/1499)=1
