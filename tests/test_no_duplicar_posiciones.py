"""Tests de la regla que impide duplicar un ticker en la misma cartera.

EL BUG (corregido el 2026-08-06)
-------------------------------
`tickers_ocupados()` miraba SIEMPRE las posiciones de la cartera A, también para
decidir sobre la B. Como cada entrada abre posición en A y en B a la vez, y A
cierra pronto por stop (media 4.75 días hábiles) mientras B aguanta los 10 días
sin él (media 9.33), en cuanto un ticker volvía a dar señal el sistema veía el
hueco libre de A y abría una SEGUNDA posición de ese ticker en B, encima de la
primera, que seguía viva. Ocurrió 13 veces, las 13 en B y ninguna en A.

La regla es POR CARTERA: que COHR esté abierta en B no impide entrar en A.
"""
from __future__ import annotations

from centinela import simulador


def _estado(posiciones_a=(), posiciones_b=(), pendientes=()):
    return {
        "posiciones": {
            "A": [{"ticker": t} for t in posiciones_a],
            "B": [{"ticker": t} for t in posiciones_b],
        },
        "entradas_pendientes": list(pendientes),
    }


def _cand(ticker, proba=0.9, dd=0.55):
    return {"ticker": ticker, "proba": proba, "dd": dd, "sector": "Tech"}


def _registrar(estado, candidatas):
    return simulador.registrar_decisiones_entrada(
        estado, candidatas, "2026-08-06", "2026-08-06T08:45:00-04:00")


# --------------------------------------------------------------------------- #
# La regla, por cartera
# --------------------------------------------------------------------------- #
def test_no_se_abre_segunda_posicion_en_la_misma_cartera():
    """X abierta en A: si X vuelve a calificar hoy, no se abre otra X en A."""
    estado = _estado(posiciones_a=["X"], posiciones_b=["X"])
    nuevas, notas = _registrar(estado, [_cand("X")])

    assert nuevas == []
    assert estado["entradas_pendientes"] == []
    assert len(notas) == 1
    assert "ENTRADA DESCARTADA" in notas[0]
    assert "Cartera A y Cartera B" in notas[0]


def test_entra_en_la_cartera_libre_aunque_la_otra_este_ocupada():
    """El caso real: A cerró por stop, B sigue abierta. X entra SOLO en A."""
    estado = _estado(posiciones_a=[], posiciones_b=["COHR"])
    nuevas, notas = _registrar(estado, [_cand("COHR", proba=0.83, dd=0.61)])

    assert len(nuevas) == 1
    assert nuevas[0]["carteras"] == ["A"]          # B queda fuera
    assert len(notas) == 1
    assert "ENTRADA SOLO EN CARTERA A" in notas[0]
    assert "ya hay posición abierta en Cartera B" in notas[0]


def test_la_cartera_bloqueada_es_la_que_toca_y_no_la_otra():
    """Simétrico del anterior: con A ocupada y B libre, entra solo en B."""
    estado = _estado(posiciones_a=["GLW"], posiciones_b=[])
    nuevas, notas = _registrar(estado, [_cand("GLW")])

    assert len(nuevas) == 1
    assert nuevas[0]["carteras"] == ["B"]
    assert "ENTRADA SOLO EN CARTERA B" in notas[0]
    assert "ya hay posición abierta en Cartera A" in notas[0]


def test_ticker_sin_posicion_abierta_entra_en_las_dos():
    estado = _estado(posiciones_a=["OTRO"], posiciones_b=["OTRO"])
    nuevas, notas = _registrar(estado, [_cand("NUEVO")])

    assert len(nuevas) == 1
    assert nuevas[0]["carteras"] == ["A", "B"]
    assert notas == []                              # nada que explicar


def test_el_log_lleva_ticker_drawdown_y_probabilidad():
    """Formato pedido: TICKER | dd=X% | prob=Y | ENTRADA DESCARTADA: ..."""
    estado = _estado(posiciones_a=["COHR"], posiciones_b=["COHR"])
    _, notas = _registrar(estado, [_cand("COHR", proba=0.795, dd=0.613)])

    assert notas[0] == ("COHR | dd=61.3% | prob=0.795 | ENTRADA DESCARTADA: "
                        "ya hay posición abierta en Cartera A y Cartera B.")


def test_sin_drawdown_en_la_candidata_el_log_no_revienta():
    """El log es reporting: la falta de un campo informativo no puede tumbar el
    escaneo, que es lo que de verdad importa que termine."""
    estado = _estado(posiciones_a=["X"], posiciones_b=["X"])
    _, notas = _registrar(estado, [{"ticker": "X", "proba": 0.9}])
    assert "ENTRADA DESCARTADA" in notas[0] and "X |" in notas[0]


# --------------------------------------------------------------------------- #
# Las pendientes del día también ocupan sitio
# --------------------------------------------------------------------------- #
def test_una_pendiente_de_hoy_bloquea_un_segundo_registro_del_mismo_ticker():
    """Dos disparos del mismo día no pueden decidir el mismo ticker dos veces."""
    estado = _estado(pendientes=[{"ticker": "X", "carteras": ["A", "B"]}])
    nuevas, notas = _registrar(estado, [_cand("X")])

    assert nuevas == []
    assert "ENTRADA DESCARTADA" in notas[0]


def test_una_pendiente_solo_de_A_no_bloquea_la_B():
    estado = _estado(pendientes=[{"ticker": "X", "carteras": ["A"]}])
    nuevas, _ = _registrar(estado, [_cand("X")])
    assert nuevas[0]["carteras"] == ["B"]


def test_pendiente_antigua_sin_campo_carteras_bloquea_las_dos():
    """Compatibilidad: las pendientes escritas antes de la corrección no traen
    `carteras` y valen, como valían entonces, para las dos carteras."""
    estado = _estado(pendientes=[{"ticker": "X"}])
    nuevas, notas = _registrar(estado, [_cand("X")])

    assert nuevas == []
    assert "Cartera A y Cartera B" in notas[0]


def test_dos_candidatas_del_mismo_ticker_en_la_misma_tanda():
    """La segunda ya se encuentra ocupada por la primera de la misma tanda."""
    estado = _estado()
    nuevas, notas = _registrar(estado, [_cand("X", proba=0.95), _cand("X", proba=0.90)])

    assert len(nuevas) == 1
    assert len(notas) == 1 and "ENTRADA DESCARTADA" in notas[0]


# --------------------------------------------------------------------------- #
# tickers_ocupados, la función que tenía el fallo
# --------------------------------------------------------------------------- #
def test_tickers_ocupados_mira_la_cartera_que_se_le_pide():
    """Regresión directa del bug: antes devolvía siempre los tickers de A."""
    estado = _estado(posiciones_a=["SOLO_A"], posiciones_b=["SOLO_B"])

    assert simulador.tickers_ocupados(estado, "A") == {"SOLO_A"}
    assert simulador.tickers_ocupados(estado, "B") == {"SOLO_B"}


# --------------------------------------------------------------------------- #
# La ejecución respeta la decisión
# --------------------------------------------------------------------------- #
def test_la_ejecucion_solo_abre_en_las_carteras_decididas(monkeypatch, tmp_path):
    """Una entrada marcada solo para A no puede acabar creando posición en B."""
    import pandas as pd
    from centinela import bitacora

    idx = pd.to_datetime(["2026-08-05", "2026-08-06"])
    df = pd.DataFrame({"Open": [100.0, 101.0], "High": [102.0, 103.0],
                       "Low": [99.0, 100.0], "Close": [101.0, 102.0]}, index=idx)

    ids = iter(range(1000, 1100))
    monkeypatch.setattr(bitacora, "registrar_entrada", lambda datos: next(ids))

    estado = {
        "posiciones": {"A": [], "B": [{"ticker": "COHR"}]},
        "entradas_pendientes": [{
            "ticker": "COHR", "fecha_decision": "2026-08-06",
            "carteras": ["A"], "atr": 5.0, "resistencia": 130.0,
        }],
        "contador_grupo": 0,
    }
    abiertas = simulador.ejecutar_entradas_pendientes(
        estado, {"COHR": df}, "2026-08-06")

    assert [p["portafolio"] for p in abiertas] == ["A"]
    assert len(estado["posiciones"]["A"]) == 1
    assert len(estado["posiciones"]["B"]) == 1        # la que ya había, sin duplicar
    assert estado["entradas_pendientes"] == []


def test_pendiente_sin_carteras_sigue_abriendo_en_las_dos(monkeypatch):
    """Compatibilidad hacia atrás del formato de estado.json."""
    import pandas as pd
    from centinela import bitacora

    idx = pd.to_datetime(["2026-08-05", "2026-08-06"])
    df = pd.DataFrame({"Open": [100.0, 101.0], "High": [102.0, 103.0],
                       "Low": [99.0, 100.0], "Close": [101.0, 102.0]}, index=idx)
    ids = iter(range(2000, 2100))
    monkeypatch.setattr(bitacora, "registrar_entrada", lambda datos: next(ids))

    estado = {
        "posiciones": {"A": [], "B": []},
        "entradas_pendientes": [{"ticker": "NUEVO", "fecha_decision": "2026-08-06",
                                 "atr": 5.0, "resistencia": 130.0}],
        "contador_grupo": 0,
    }
    abiertas = simulador.ejecutar_entradas_pendientes(
        estado, {"NUEVO": df}, "2026-08-06")

    assert sorted(p["portafolio"] for p in abiertas) == ["A", "B"]
