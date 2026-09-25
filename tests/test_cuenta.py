"""Tests de la cuenta simulada: dinero, fricciones, drawdown y slots.

La cifra principal del sistema pasa a ser "cuánto vale la cuenta", así que esa
aritmética necesita quedar clavada contra números calculados a mano. Si alguien
cambia el modelo de asignación de capital, tiene que romper aquí y no
descubrirse tres meses después en una cifra del dashboard que nadie recalculó.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import config, cuenta  # noqa: E402


COLUMNAS = ["id", "ticker", "portafolio", "sector", "fecha_entrada",
            "precio_entrada", "fecha_salida", "precio_salida", "motivo_salida",
            "estado"]


def _ops(*filas) -> pd.DataFrame:
    return pd.DataFrame(list(filas), columns=COLUMNAS)


def _serie(precios_por_fecha: dict) -> pd.DataFrame:
    """OHLC donde las cuatro patas valen lo mismo: solo importa el Close."""
    idx = pd.to_datetime(sorted(precios_por_fecha))
    vals = [precios_por_fecha[d.date().isoformat()] for d in idx]
    return pd.DataFrame({"Open": vals, "High": vals, "Low": vals, "Close": vals},
                        index=idx)


# --------------------------------------------------------------------------- #
# 1. Fricciones
# --------------------------------------------------------------------------- #
def test_fricciones_por_lado_y_solo_a_mercado():
    """0,10% por lado siempre; 0,15% de slippage SOLO en órdenes a mercado.

    Una salida por objetivo es una orden LÍMITE: se ejecuta a su precio o
    mejor, nunca peor, así que no paga slippage. Meterle el mismo castigo que
    a un stop disparado sería pesimismo mal puesto, no prudencia.
    """
    assert cuenta.precio_entrada_neto(100.0) == pytest.approx(100.25)
    assert cuenta.precio_salida_neto(100.0, "objetivo") == pytest.approx(99.90)
    assert cuenta.precio_salida_neto(100.0, "stop") == pytest.approx(99.75)
    assert cuenta.precio_salida_neto(100.0, "tiempo") == pytest.approx(99.75)


def test_sin_fricciones_los_precios_son_los_de_la_bitacora():
    """La vista BRUTA tiene que ser exactamente lo que registró el sistema."""
    for motivo in ("objetivo", "stop", "tiempo"):
        assert cuenta.precio_salida_neto(123.45, motivo, fricciones=False) == 123.45
    assert cuenta.precio_entrada_neto(123.45, fricciones=False) == 123.45


def test_la_friccion_siempre_resta():
    """Nunca puede salir un precio de compra menor o de venta mayor que el bruto."""
    for p in (1.0, 50.0, 1500.0):
        assert cuenta.precio_entrada_neto(p) > p
        for motivo in ("objetivo", "stop", "tiempo"):
            assert cuenta.precio_salida_neto(p, motivo) < p


# --------------------------------------------------------------------------- #
# 2. La cuenta compone al cerrar
# --------------------------------------------------------------------------- #
def test_la_cuenta_compone_al_cerrar():
    """Números a mano, sin fricciones, para que el mecanismo quede desnudo.

    Capital 1.000 en 2 slots.
      Op 1: slot = 1000/2 = 500 a 100 -> 5 acciones. Cierra a 110 -> cobra 550.
            Caja 500 + 550 = 1050.
      Op 2: el slot YA es 1050/2 = 525, no 500: eso es componer. Entra a 100
            (5,25 acciones) y cierra a 90 -> cobra 472,50.
            Caja 1050 - 525 + 472,50 = 997,50.
    """
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, "2026-07-02", 110.0, "objetivo", "cerrada"),
        (2, "BBB", "A", "Tech", "2026-07-03", 100.0, "2026-07-06", 90.0, "stop", "cerrada"),
    )
    c = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)

    t1, t2 = c["trades"]
    assert t1["coste"] == pytest.approx(500.0)
    assert t1["acciones"] == pytest.approx(5.0)
    assert t1["ingreso"] == pytest.approx(550.0)
    assert t1["pnl_dinero"] == pytest.approx(50.0)
    # La segunda entrada es MAYOR que la primera: el capital compuso.
    assert t2["coste"] == pytest.approx(525.0)
    assert t2["pnl_dinero"] == pytest.approx(-52.5)

    assert c["cash"] == pytest.approx(997.5)
    assert c["equity_contable"] == pytest.approx(997.5)
    assert c["realizado"] == pytest.approx(-2.5)


def test_una_posicion_abierta_sigue_contando_como_capital():
    """Lo invertido no desaparece: está en el equity contable, no en la caja."""
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None, "abierta"),
    )
    c = cuenta.simular(ops, capital=1000.0, slots=4, fricciones=False)
    assert c["cash"] == pytest.approx(750.0)
    assert c["equity_contable"] == pytest.approx(1000.0)
    assert len(c["abiertas"]) == 1
    assert c["realizado"] == pytest.approx(0.0)


def test_el_tope_de_slots_rechaza_lo_que_no_cabe():
    """Una cuenta de 2 slots no puede tener 3 posiciones, diga lo que diga la bitácora.

    Este caso es REAL: el bug del 2026-08-06 llegó a dejar 30 posiciones vivas
    a la vez en la Cartera B. Una cuenta con 20 slots no habría podido abrir
    diez de ellas, y decirlo es el trabajo de esta función: la diferencia entre
    lo que el sistema decidió y lo que el dinero permitía es información.
    """
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None, "abierta"),
        (2, "BBB", "A", "Tech", "2026-07-01", 100.0, None, None, None, "abierta"),
        (3, "CCC", "A", "Tech", "2026-07-02", 100.0, None, None, None, "abierta"),
    )
    c = cuenta.simular(ops, capital=1000.0, slots=2, fricciones=False)
    assert len(c["abiertas"]) == 2
    assert [r["ticker"] for r in c["rechazadas"]] == ["CCC"]
    assert c["rechazadas"][0]["motivo"] == "sin slot libre"


def test_en_el_mismo_dia_la_entrada_va_antes_que_la_salida():
    """El capital que libera un cierre de hoy NO financia una compra de hoy.

    Es lo que hace el sistema real —la pre-apertura decide con el estado de
    ayer, cuando la posición que cierra hoy seguía viva— y es la hipótesis
    conservadora. Con un solo slot, la entrada del día choca con la posición que
    aún no ha cerrado y se rechaza.
    """
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, "2026-07-08", 200.0, "objetivo", "cerrada"),
        (2, "BBB", "A", "Tech", "2026-07-08", 100.0, None, None, None, "abierta"),
    )
    c = cuenta.simular(ops, capital=1000.0, slots=1, fricciones=False)
    assert [r["ticker"] for r in c["rechazadas"]] == ["BBB"]


# --------------------------------------------------------------------------- #
# 3. Curva diaria y métricas de riesgo
# --------------------------------------------------------------------------- #
def test_curva_diaria_marca_a_mercado_lo_abierto():
    """Una posición abierta se valora cada día, no se congela en su coste.

    250 invertidos en una acción que sube un 20% valen 300, y el equity de la
    cuenta tiene que reflejarlo aunque no se haya vendido nada.
    """
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, None, None, None, "abierta"),
    )
    c = cuenta.simular(ops, capital=1000.0, slots=4, fricciones=False)
    precios = {"AAA": _serie({"2026-07-01": 100.0, "2026-07-02": 110.0,
                              "2026-07-03": 120.0})}
    curva = cuenta.curva_diaria(c, precios, ["2026-07-01", "2026-07-02", "2026-07-03"])

    assert list(curva["equity"].round(2)) == [1000.0, 1025.0, 1050.0]
    assert list(curva["cash"].round(2)) == [750.0, 750.0, 750.0]


def test_drawdown_maximo_con_una_curva_conocida():
    """Caída del 20% desde el pico: -20%, no -20% desde el capital inicial.

    La cuenta sube a 1.250 y cae a 1.000. Medido contra el capital inicial el
    drawdown sería 0 —acabó donde empezó— y eso escondería exactamente el
    riesgo que esta métrica existe para enseñar. Se mide contra el PICO.
    """
    curva = pd.DataFrame({
        "fecha": ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"],
        "equity": [1000.0, 1250.0, 1000.0, 1100.0],
    })
    m = cuenta.metricas(curva, capital=1000.0)
    assert m["drawdown_max_pct"] == pytest.approx(-20.0)
    assert m["rentabilidad_pct"] == pytest.approx(10.0)
    assert m["equity_final"] == pytest.approx(1100.0)
    assert m["sesiones"] == 4


def test_drawdown_de_una_cuenta_que_solo_baja():
    """Sin ninguna subida, el pico es el capital inicial desde el primer día."""
    curva = pd.DataFrame({"fecha": ["a", "b", "c"], "equity": [950.0, 900.0, 920.0]})
    m = cuenta.metricas(curva, capital=1000.0)
    assert m["drawdown_max_pct"] == pytest.approx(-10.0)
    assert m["rentabilidad_pct"] == pytest.approx(-8.0)


def test_una_curva_plana_no_inventa_un_sharpe():
    """Sin volatilidad el Sharpe es una división por cero: se devuelve None.

    Pintar "∞" o un número grande sería inventarse una calidad de retorno que
    no existe. El dashboard prefiere un hueco honesto.
    """
    curva = pd.DataFrame({"fecha": ["a", "b", "c"], "equity": [1000.0] * 3})
    m = cuenta.metricas(curva, capital=1000.0)
    assert m["sharpe"] is None
    assert m["drawdown_max_pct"] == pytest.approx(0.0)


def test_curva_vacia_no_revienta():
    m = cuenta.metricas(pd.DataFrame(columns=["fecha", "equity"]), capital=1000.0)
    assert m["rentabilidad_pct"] is None and m["sesiones"] == 0


def test_cagr_anualiza_con_sesiones():
    """Un +10% en 252 sesiones es un CAGR del 10%, por construcción."""
    curva = pd.DataFrame({"fecha": range(252),
                          "equity": [1000.0] * 251 + [1100.0]})
    m = cuenta.metricas(curva, capital=1000.0)
    assert m["cagr_pct"] == pytest.approx(10.0, abs=0.1)


# --------------------------------------------------------------------------- #
# 4. Fricciones extremo a extremo
# --------------------------------------------------------------------------- #
def test_la_misma_operacion_rinde_menos_neta_que_bruta():
    ops = _ops(
        (1, "AAA", "A", "Tech", "2026-07-01", 100.0, "2026-07-08", 110.0, "objetivo", "cerrada"),
    )
    bruta = cuenta.simular(ops, capital=1000.0, slots=1, fricciones=False)
    neta = cuenta.simular(ops, capital=1000.0, slots=1, fricciones=True)
    assert neta["equity_contable"] < bruta["equity_contable"]
    # Compra a 100,25 y vende a 109,89: +9,62% en vez del +10% bruto.
    assert neta["trades"][0]["pnl_pct"] == pytest.approx(0.0962, abs=1e-4)
    assert bruta["trades"][0]["pnl_pct"] == pytest.approx(0.10)


# --------------------------------------------------------------------------- #
# 5. Duplicadas: una sola definición para todo el sistema
# --------------------------------------------------------------------------- #
def test_marcar_duplicadas_marca_la_segunda_no_la_primera():
    bit = pd.DataFrame([
        {"id": 1, "ticker": "COHR", "portafolio": "B",
         "fecha_entrada": "2026-07-01", "fecha_salida": "2026-07-10"},
        {"id": 2, "ticker": "COHR", "portafolio": "B",
         "fecha_entrada": "2026-07-03", "fecha_salida": "2026-07-08"},
        {"id": 3, "ticker": "COHR", "portafolio": "A",
         "fecha_entrada": "2026-07-03", "fecha_salida": "2026-07-08"},
    ])
    dup = cuenta.marcar_duplicadas(bit)
    assert list(dup) == [False, True, False]


def test_el_dashboard_usa_la_misma_definicion_de_duplicada():
    """Dos copias del criterio acabarían contando universos distintos."""
    sys.path.insert(0, str(RAIZ / "scripts"))
    import generar_dashboard as gd
    assert gd.marcar_duplicadas is cuenta.marcar_duplicadas


def test_los_parametros_de_cuenta_son_los_documentados():
    """Si alguien cambia el capital o las fricciones, que se entere por aquí."""
    assert config.CAPITAL_INICIAL_CUENTA == 10000.0
    assert config.SLOTS_CUENTA == config.MAX_POSICIONES_ABIERTAS == 20
    assert config.COMISION_SPREAD_POR_LADO == 0.0010
    assert config.SLIPPAGE_MERCADO == 0.0015
