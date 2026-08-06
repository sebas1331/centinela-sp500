"""Tests del dashboard estático de docs/ (sin red y sin navegador).

Cubren las tres cosas que pueden romperse en silencio:

  1. El CONTRATO de docs/datos.json. El HTML no calcula nada, solo pinta lo que
     encuentra en ese fichero: si una clave cambia de nombre o de tipo, el
     dashboard se queda mudo sin que ningún workflow se ponga rojo.
  2. La ARITMÉTICA (win rate, expectancy, profit factor, P&L acumulado) contra
     una bitácora sintética cuyos resultados están calculados a mano aquí abajo.
  3. Que las posiciones ABIERTAS se marquen como tales y NO contaminen las
     estadísticas de cerradas. Su P&L es una marca a mercado, no un resultado.
"""
from __future__ import annotations

import html.parser
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

import generar_dashboard as gd  # noqa: E402


# --------------------------------------------------------------------------- #
# Bitácora sintética
# --------------------------------------------------------------------------- #
# Ocho operaciones con los números elegidos para que las cuentas salgan redondas
# y se puedan verificar mentalmente. `pnl_pct` va en FRACCIÓN, como en el CSV de
# verdad (-0.12 = -12%).
#
#   Cartera A cerradas: +20%, -10%, +5%, -15%   -> 2 de 4 ganadoras = 50%
#       expectancy = (20 - 10 + 5 - 15) / 4 = 0.0
#       profit factor = (20 + 5) / |−10 − 15| = 25 / 25 = 1.0
#   Cartera B cerradas: +30%, -10%              -> 1 de 2 ganadoras = 50%
#       expectancy = (30 - 10) / 2 = +10.0
#       profit factor = 30 / 10 = 3.0
#   Global: 6 cerradas, 3 ganadoras = 50%, P&L acumulado = 20-10+5-15+30-10 = +20
#   Y DOS ABIERTAS (una por cartera) que no deben entrar en nada de lo anterior.
CABECERA = ("id,grupo,ticker,portafolio,sector,fecha_entrada,hora_entrada_et,"
            "hora_entrada_utc,timestamp_escaneo,precio_entrada,probabilidad,"
            "score_fundamental,objetivo_inicial,objetivo_actual,historial_objetivos,"
            "stop,fecha_salida,hora_salida_et,precio_salida,motivo_salida,pnl_pct,"
            "dias_habiles,estado,notas")

FILAS = [
    # id, ticker, cartera, f_entrada, precio_ent, f_salida, precio_sal, motivo, pnl, estado
    (1, "AAA", "A", "2026-07-01", 100.0, "2026-07-08", 120.0, "objetivo", 0.20, "cerrada"),
    (2, "BBB", "A", "2026-07-02", 200.0, "2026-07-09", 180.0, "tiempo", -0.10, "cerrada"),
    (3, "CCC", "A", "2026-07-03", 50.0, "2026-07-10", 52.5, "tiempo", 0.05, "cerrada"),
    (4, "DDD", "A", "2026-07-06", 80.0, "2026-07-13", 68.0, "stop", -0.15, "cerrada"),
    (5, "AAA", "B", "2026-07-01", 100.0, "2026-07-14", 130.0, "objetivo", 0.30, "cerrada"),
    (6, "BBB", "B", "2026-07-02", 200.0, "2026-07-15", 180.0, "stop", -0.10, "cerrada"),
    (7, "EEE", "A", "2026-07-20", 10.0, None, None, None, None, "abierta"),
    (8, "EEE", "B", "2026-07-20", 10.0, None, None, None, None, "abierta"),
]

MFE_SINTETICO = """# Análisis MFE/MAE de posiciones

_Generado 2026-07-21 18:00 ET_

## Posiciones abiertas

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EEE | A | 2026-07-20 | 10.00 | +12.00% | 2026-07-21 | -3.00% | 2026-07-20 | +8.00% | ✅ sí | no | 14.00 |
| EEE | B | 2026-07-20 | 10.00 | +12.00% | 2026-07-21 | -3.00% | 2026-07-20 | +8.00% | ✅ sí | no | 14.00 |

## Posiciones cerradas en los últimos 30 días

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | P&L al cierre real | Motivo salida | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAA | A | 2026-07-01 | 100.00 | +25.00% | 2026-07-08 | -2.00% | 2026-07-02 | +20.00% | +20.00% | objetivo | ✅ sí | ✅ sí | 120.00 |

## Resumen

- **Posiciones abiertas:** 2 (2 con datos de precio).
"""

ESTADO_SINTETICO = {
    "actualizado": "2026-07-21T18:00:00-04:00",
    "ultima_preapertura": "2026-07-21",
    "ultima_postcierre": "2026-07-20",
}


def _csv_sintetico(filas=None) -> str:
    lineas = [CABECERA]
    for (oid, tk, cart, fe, pe, fs, ps, motivo, pnl, estado) in (FILAS if filas is None else filas):
        lineas.append(",".join([
            str(oid), "1", tk, cart, "Information Technology", fe,
            "", "", "", f"{pe}", "0.9", "60.0", "", "", '""', "",
            fs or "", "", "" if ps is None else f"{ps}", motivo or "",
            "" if pnl is None else f"{pnl}", "", estado, "",
        ]))
    return "\n".join(lineas) + "\n"


def _construir(tmp_path, monkeypatch, filas=None, mfe_md=None) -> dict:
    """Redirige las tres entradas del generador a ficheros temporales."""
    bit = tmp_path / "bitacora.csv"
    bit.write_text(_csv_sintetico(filas), encoding="utf-8")
    mfe = tmp_path / "mfe_actual.md"
    mfe.write_text(MFE_SINTETICO if mfe_md is None else mfe_md, encoding="utf-8")
    est = tmp_path / "estado.json"
    est.write_text(json.dumps(ESTADO_SINTETICO), encoding="utf-8")

    monkeypatch.setattr(gd, "RUTA_BITACORA", bit)
    monkeypatch.setattr(gd, "RUTA_MFE", mfe)
    monkeypatch.setattr(gd, "RUTA_ESTADO", est)
    return gd.construir_datos()


@pytest.fixture
def datos(tmp_path, monkeypatch) -> dict:
    """datos.json construido a partir de la bitácora sintética de arriba."""
    return _construir(tmp_path, monkeypatch)


# --------------------------------------------------------------------------- #
# 1. Contrato de datos.json
# --------------------------------------------------------------------------- #
def test_schema_datos_json(datos):
    assert set(datos) == {"resumen", "carteras", "curva_equity",
                          "resumen_limpio", "comparativa_ab_limpia",
                          "pnl_por_cartera_limpio", "curva_equity_limpia",
                          "operaciones", "mfe", "meta"}

    r = datos["resumen"]
    assert set(r) == {"cerradas", "abiertas", "win_rate", "pnl_acumulado", "ultima_cerrada"}
    assert isinstance(r["cerradas"], int) and isinstance(r["abiertas"], int)
    assert isinstance(r["win_rate"], float) and isinstance(r["pnl_acumulado"], float)
    assert isinstance(r["ultima_cerrada"], str)

    assert set(datos["carteras"]) == {"A", "B"}
    for c in datos["carteras"].values():
        assert set(c) == {"cerradas", "win_rate", "expectancy", "profit_factor",
                          "mejor", "peor", "abiertas",
                          "pnl_realizado", "pnl_total", "abiertas_sin_pnl"}
        assert isinstance(c["cerradas"], int) and isinstance(c["abiertas"], int)
        assert isinstance(c["pnl_realizado"], float)
        assert isinstance(c["pnl_total"], float)
        assert isinstance(c["abiertas_sin_pnl"], int)

    assert isinstance(datos["curva_equity"], list)
    for p in datos["curva_equity"]:
        assert set(p) == {"fecha", "pl_acumulado_a", "pl_acumulado_b",
                          "n_cerradas_a", "n_cerradas_b"}
        assert isinstance(p["fecha"], str) and len(p["fecha"]) == 10
        assert isinstance(p["pl_acumulado_a"], float)
        assert isinstance(p["pl_acumulado_b"], float)
        assert isinstance(p["n_cerradas_a"], int) and isinstance(p["n_cerradas_b"], int)
        for extremo in (c["mejor"], c["peor"]):
            assert set(extremo) == {"ticker", "pnl_pct", "fecha_salida"}
            assert isinstance(extremo["pnl_pct"], float)

    for o in datos["operaciones"]:
        assert set(o) == {"id", "ticker", "cartera", "estado", "fecha_entrada",
                          "precio_entrada", "fecha_salida", "precio_salida",
                          "pnl_pct", "no_realizado", "duplicada", "motivo"}
        assert isinstance(o["duplicada"], bool)
        assert isinstance(o["id"], int)
        assert o["estado"] in ("abierta", "cerrada")
        assert o["cartera"] in ("A", "B")
        assert o["motivo"] in ("Objetivo", "Stop", "Tiempo", "Abierta")
        assert isinstance(o["no_realizado"], bool)
        assert isinstance(o["precio_entrada"], float)

    for p in datos["mfe"]:
        assert set(p) == {"ticker", "cartera", "fecha_entrada", "precio_entrada",
                          "mfe_pct", "mae_pct", "pnl_actual_pct", "toco_5"}
        assert isinstance(p["toco_5"], bool)

    assert set(datos["meta"]) == {"actualizado", "mfe_generado", "ultima_preapertura",
                                  "ultima_postcierre", "repo",
                                  "duplicadas", "corregido_el"}
    assert isinstance(datos["meta"]["duplicadas"], int)


def test_schema_de_los_bloques_limpios(datos):
    """Los bloques _limpio tienen EXACTAMENTE la forma de sus equivalentes.

    El HTML pinta los dos juegos con el mismo código: en cuanto uno se desvíe del
    otro, la vista filtrada empieza a enseñar huecos en vez de números.
    """
    assert set(datos["resumen_limpio"]) == set(datos["resumen"])
    assert set(datos["comparativa_ab_limpia"]) == {"A", "B"}
    assert set(datos["pnl_por_cartera_limpio"]) == {"A", "B"}
    for c in ("A", "B"):
        # `carteras` es la fusión de los dos bloques limpios: juntas, las claves
        # de comparativa y pnl tienen que dar exactamente las de carteras.
        fusion = set(datos["comparativa_ab_limpia"][c]) | set(datos["pnl_por_cartera_limpio"][c])
        assert fusion == set(datos["carteras"][c])
    assert isinstance(datos["curva_equity_limpia"], list)
    for p in datos["curva_equity_limpia"]:
        assert set(p) == {"fecha", "pl_acumulado_a", "pl_acumulado_b",
                          "n_cerradas_a", "n_cerradas_b"}


def test_datos_json_es_serializable_y_sin_nan(datos):
    """json.dumps con allow_nan=False falla si se cuela un NaN de pandas.

    Importa porque JSON.parse() del navegador no entiende `NaN`: el dashboard
    entero se quedaría en blanco por un solo valor ausente mal convertido.
    """
    texto = json.dumps(datos, allow_nan=False, ensure_ascii=False)
    assert "NaN" not in texto and "Infinity" not in texto


# --------------------------------------------------------------------------- #
# 2. Aritmética
# --------------------------------------------------------------------------- #
def test_win_rate_expectancy_y_profit_factor_por_cartera(datos):
    a = datos["carteras"]["A"]
    assert a["cerradas"] == 4
    assert a["win_rate"] == pytest.approx(50.0)
    assert a["expectancy"] == pytest.approx(0.0)
    assert a["profit_factor"] == pytest.approx(1.0)      # (20+5) / |-10-15|
    assert a["mejor"]["ticker"] == "AAA" and a["mejor"]["pnl_pct"] == pytest.approx(20.0)
    assert a["peor"]["ticker"] == "DDD" and a["peor"]["pnl_pct"] == pytest.approx(-15.0)

    b = datos["carteras"]["B"]
    assert b["cerradas"] == 2
    assert b["win_rate"] == pytest.approx(50.0)
    assert b["expectancy"] == pytest.approx(10.0)
    assert b["profit_factor"] == pytest.approx(3.0)      # 30 / |-10|


def test_agregados_globales(datos):
    r = datos["resumen"]
    assert r["cerradas"] == 6
    assert r["abiertas"] == 2
    assert r["win_rate"] == pytest.approx(50.0)          # 3 de 6
    assert r["pnl_acumulado"] == pytest.approx(20.0)     # 20-10+5-15+30-10
    assert r["ultima_cerrada"] == "2026-07-15"


def test_pnl_va_en_puntos_porcentuales_no_en_fraccion(datos):
    """La bitácora guarda -0.12; el dashboard tiene que enseñar -12."""
    op = [o for o in datos["operaciones"] if o["id"] == 4][0]
    assert op["pnl_pct"] == pytest.approx(-15.0)
    assert op["motivo"] == "Stop"


def test_profit_factor_es_none_si_no_hubo_perdidas(tmp_path, monkeypatch):
    """Sin pérdidas el cociente no existe: null, no un número inventado."""
    import pandas as pd
    solo_ganadoras = pd.DataFrame({
        "ticker": ["AAA", "BBB"], "pnl_pct_pp": [10.0, 20.0],
        "fecha_salida": ["2026-07-08", "2026-07-09"],
    })
    m = gd.metricas_cartera(solo_ganadoras)
    assert m["profit_factor"] is None
    assert m["win_rate"] == pytest.approx(100.0)


def test_cartera_sin_operaciones_cerradas_no_revienta():
    import pandas as pd
    m = gd.metricas_cartera(pd.DataFrame(columns=["ticker", "pnl_pct_pp", "fecha_salida"]))
    assert m == {"cerradas": 0, "win_rate": None, "expectancy": None,
                 "profit_factor": None, "mejor": None, "peor": None}


# --------------------------------------------------------------------------- #
# 2b. P&L acumulado por cartera (realizado vs. total)
# --------------------------------------------------------------------------- #
def test_pnl_realizado_y_total_por_cartera(datos):
    """Realizado = solo cerradas. Total = realizado + marca a mercado de abiertas.

    A cerradas: +20 −10 +5 −15 = 0 ; su única abierta (EEE A) va +8  -> total +8
    B cerradas: +30 −10          = +20 ; su única abierta (EEE B) va +8 -> total +28
    """
    a, b = datos["carteras"]["A"], datos["carteras"]["B"]
    assert a["pnl_realizado"] == pytest.approx(0.0)
    assert a["pnl_total"] == pytest.approx(8.0)
    assert b["pnl_realizado"] == pytest.approx(20.0)
    assert b["pnl_total"] == pytest.approx(28.0)
    assert a["abiertas_sin_pnl"] == 0 and b["abiertas_sin_pnl"] == 0


def test_realizado_por_cartera_suma_el_acumulado_global(datos):
    """Las dos cifras vienen de sitios distintos y tienen que cuadrar."""
    suma = (datos["carteras"]["A"]["pnl_realizado"]
            + datos["carteras"]["B"]["pnl_realizado"])
    assert suma == pytest.approx(datos["resumen"]["pnl_acumulado"])


def test_una_abierta_sin_fila_en_el_informe_mfe_se_cuenta_y_no_se_inventa(
        tmp_path, monkeypatch):
    """El total no puede quedarse corto en silencio.

    Se quita del informe MFE la posición abierta de la cartera A: su P&L deja de
    conocerse, así que NO puede sumarse al total, y el hueco tiene que quedar
    contado para que el dashboard lo pueda decir.
    """
    sin_a = MFE_SINTETICO.replace(
        "| EEE | A | 2026-07-20 | 10.00 | +12.00% | 2026-07-21 | -3.00% | "
        "2026-07-20 | +8.00% | ✅ sí | no | 14.00 |\n", "")
    d = _construir(tmp_path, monkeypatch, mfe_md=sin_a)

    a = d["carteras"]["A"]
    assert a["abiertas_sin_pnl"] == 1
    assert a["pnl_total"] == pytest.approx(a["pnl_realizado"])   # sin sumar nada
    assert d["carteras"]["B"]["abiertas_sin_pnl"] == 0
    # Y la operación aparece igualmente en la tabla, con P&L desconocido.
    abierta_a = [o for o in d["operaciones"]
                 if o["estado"] == "abierta" and o["cartera"] == "A"][0]
    assert abierta_a["pnl_pct"] is None


# --------------------------------------------------------------------------- #
# 2c. Curva de equity
# --------------------------------------------------------------------------- #
def test_curva_equity_puntos_y_acumulados(datos):
    """Un punto por fecha de salida, ordenados, con el acumulado de AMBAS series.

    Salidas: A el 08 (+20), 09 (−10), 10 (+5) y 13 (−15); B el 14 (+30) y 15 (−10).
    B no cierra nada hasta el 14, así que hasta entonces su acumulado es 0 y su
    contador también: la serie va plana, no ausente.
    """
    curva = datos["curva_equity"]
    assert [p["fecha"] for p in curva] == [
        "2026-07-08", "2026-07-09", "2026-07-10",
        "2026-07-13", "2026-07-14", "2026-07-15"]

    assert [p["pl_acumulado_a"] for p in curva] == [
        pytest.approx(x) for x in (20.0, 10.0, 15.0, 0.0, 0.0, 0.0)]
    assert [p["pl_acumulado_b"] for p in curva] == [
        pytest.approx(x) for x in (0.0, 0.0, 0.0, 0.0, 30.0, 20.0)]
    assert [p["n_cerradas_a"] for p in curva] == [1, 2, 3, 4, 4, 4]
    assert [p["n_cerradas_b"] for p in curva] == [0, 0, 0, 0, 1, 2]


def test_curva_equity_cierra_donde_dice_el_realizado(datos):
    """El último punto de cada serie es, por definición, su P&L realizado."""
    ultimo = datos["curva_equity"][-1]
    assert ultimo["pl_acumulado_a"] == pytest.approx(datos["carteras"]["A"]["pnl_realizado"])
    assert ultimo["pl_acumulado_b"] == pytest.approx(datos["carteras"]["B"]["pnl_realizado"])
    assert (ultimo["n_cerradas_a"] + ultimo["n_cerradas_b"]
            == datos["resumen"]["cerradas"])


def test_curva_equity_agrega_los_cierres_del_mismo_dia(tmp_path, monkeypatch):
    """Tres operaciones que cierran el mismo día son UN punto, no tres."""
    filas = [
        (1, "AAA", "A", "2026-07-01", 100.0, "2026-07-08", 110.0, "objetivo", 0.10, "cerrada"),
        (2, "BBB", "A", "2026-07-01", 100.0, "2026-07-08", 95.0, "stop", -0.05, "cerrada"),
        (3, "CCC", "B", "2026-07-01", 100.0, "2026-07-08", 120.0, "objetivo", 0.20, "cerrada"),
        (4, "DDD", "A", "2026-07-02", 100.0, "2026-07-09", 90.0, "stop", -0.10, "cerrada"),
    ]
    curva = _construir(tmp_path, monkeypatch, filas=filas)["curva_equity"]
    assert len(curva) == 2
    # Día 1: A suma +10 y −5 en un solo punto; B suma +20.
    assert curva[0]["fecha"] == "2026-07-08"
    assert curva[0]["pl_acumulado_a"] == pytest.approx(5.0)
    assert curva[0]["pl_acumulado_b"] == pytest.approx(20.0)
    assert curva[0]["n_cerradas_a"] == 2 and curva[0]["n_cerradas_b"] == 1
    # Día 2: solo cierra A; B se queda plana en su último valor.
    assert curva[1]["pl_acumulado_a"] == pytest.approx(-5.0)
    assert curva[1]["pl_acumulado_b"] == pytest.approx(20.0)
    assert curva[1]["n_cerradas_b"] == 1


def test_curva_equity_ignora_las_abiertas(datos):
    """La curva es de resultado REALIZADO: lo no realizado no la toca.

    Las dos abiertas van +8% cada una. Si se colaran, el último punto no
    coincidiría con el realizado de su cartera.
    """
    assert datos["resumen"]["abiertas"] == 2
    assert len(datos["curva_equity"]) == 6           # 6 fechas de salida, ni una más
    assert datos["curva_equity"][-1]["pl_acumulado_a"] == pytest.approx(0.0)
    assert datos["curva_equity"][-1]["pl_acumulado_b"] == pytest.approx(20.0)


def test_pocas_operaciones_cerradas_no_rompe_nada(tmp_path, monkeypatch):
    """Menos de 3 cerradas: se generan datos válidos igualmente.

    El HTML es quien decide no dibujar (enseña el aviso), pero el JSON no puede
    salir a medias ni reventar: el dashboard tiene que abrir desde el día uno.
    """
    filas = [
        (1, "AAA", "A", "2026-07-01", 100.0, "2026-07-08", 110.0, "objetivo", 0.10, "cerrada"),
        (2, "EEE", "A", "2026-07-20", 10.0, None, None, None, None, "abierta"),
        (3, "EEE", "B", "2026-07-20", 10.0, None, None, None, None, "abierta"),
    ]
    d = _construir(tmp_path, monkeypatch, filas=filas)
    assert d["resumen"]["cerradas"] == 1
    assert len(d["curva_equity"]) == 1
    assert d["curva_equity"][0]["pl_acumulado_a"] == pytest.approx(10.0)
    assert d["curva_equity"][0]["pl_acumulado_b"] == pytest.approx(0.0)
    assert d["carteras"]["B"]["pnl_realizado"] == pytest.approx(0.0)
    assert d["carteras"]["B"]["pnl_total"] == pytest.approx(8.0)
    json.dumps(d, allow_nan=False)      # serializable pese a la cartera vacía


def test_sin_ninguna_operacion_cerrada(tmp_path, monkeypatch):
    """Caso extremo del día uno: solo posiciones abiertas."""
    filas = [
        (1, "EEE", "A", "2026-07-20", 10.0, None, None, None, None, "abierta"),
        (2, "EEE", "B", "2026-07-20", 10.0, None, None, None, None, "abierta"),
    ]
    d = _construir(tmp_path, monkeypatch, filas=filas)
    assert d["curva_equity"] == []
    assert d["resumen"]["cerradas"] == 0
    assert d["resumen"]["pnl_acumulado"] is None
    for c in d["carteras"].values():
        assert c["pnl_realizado"] == pytest.approx(0.0)
        assert c["pnl_total"] == pytest.approx(8.0)   # solo lo no realizado
    json.dumps(d, allow_nan=False)


# --------------------------------------------------------------------------- #
# 2d. Operaciones duplicadas y vista limpia
# --------------------------------------------------------------------------- #
# Reproduce el caso real de COHR/B: una posición abierta el día 1 que sigue viva
# cuando entra la segunda el día 3. La de la cartera A no solapa (cerró antes),
# así que A queda limpia entera, igual que en la bitácora de verdad.
FILAS_DUP = [
    # id, ticker, cartera, f_entrada, precio, f_salida, precio_sal, motivo, pnl, estado
    (1, "COHR", "A", "2026-07-01", 100.0, "2026-07-02", 88.0, "stop", -0.12, "cerrada"),
    (2, "COHR", "B", "2026-07-01", 100.0, "2026-07-10", 90.0, "tiempo", -0.10, "cerrada"),
    # Día 3: A está libre (cerró el 2) -> legítima. B sigue abierta -> DUPLICADA.
    (3, "COHR", "A", "2026-07-03", 90.0, "2026-07-08", 99.0, "objetivo", 0.10, "cerrada"),
    (4, "COHR", "B", "2026-07-03", 90.0, "2026-07-08", 126.0, "objetivo", 0.40, "cerrada"),
    (5, "OTRO", "A", "2026-07-06", 50.0, "2026-07-07", 55.0, "objetivo", 0.10, "cerrada"),
    (6, "OTRO", "B", "2026-07-06", 50.0, "2026-07-07", 55.0, "objetivo", 0.10, "cerrada"),
]

MFE_VACIO = """# Análisis MFE/MAE de posiciones

_Generado 2026-07-11 18:00 ET_

## Posiciones abiertas

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|

## Resumen
"""


@pytest.fixture
def datos_dup(tmp_path, monkeypatch) -> dict:
    return _construir(tmp_path, monkeypatch, filas=FILAS_DUP, mfe_md=MFE_VACIO)


def test_marca_solo_la_segunda_entrada_solapada(datos_dup):
    """La primera entrada es legítima; se marca la que se montó encima."""
    por_id = {o["id"]: o for o in datos_dup["operaciones"]}
    assert por_id[4]["duplicada"] is True     # COHR/B entró con la del día 1 viva
    assert por_id[2]["duplicada"] is False    # esa primera, no
    # COHR/A del día 3 NO es duplicada: la de A cerró por stop el día 2.
    assert por_id[3]["duplicada"] is False
    assert por_id[1]["duplicada"] is False
    assert datos_dup["meta"]["duplicadas"] == 1


def test_la_regla_es_por_cartera_no_por_ticker(datos_dup):
    """Que COHR estuviera abierta en B no convierte en duplicada la de A."""
    dups = [(o["ticker"], o["cartera"]) for o in datos_dup["operaciones"] if o["duplicada"]]
    assert dups == [("COHR", "B")]


def test_la_vista_limpia_excluye_las_duplicadas(datos_dup):
    """Los agregados limpios se calculan sin la entrada que creó el bug.

    B cerradas: −10% y +40% -> con el duplicado suma +30. Sin él, solo −10.
    A no cambia: no tiene ninguna duplicada.
    """
    assert datos_dup["resumen"]["cerradas"] == 6
    assert datos_dup["resumen_limpio"]["cerradas"] == 5

    b_todo = datos_dup["carteras"]["B"]
    b_limpio = {**datos_dup["comparativa_ab_limpia"]["B"],
                **datos_dup["pnl_por_cartera_limpio"]["B"]}
    assert b_todo["pnl_realizado"] == pytest.approx(40.0)      # −10 +40 +10
    assert b_limpio["pnl_realizado"] == pytest.approx(0.0)     # −10 +10
    assert b_todo["cerradas"] == 3 and b_limpio["cerradas"] == 2

    a_todo = datos_dup["carteras"]["A"]
    a_limpio = {**datos_dup["comparativa_ab_limpia"]["A"],
                **datos_dup["pnl_por_cartera_limpio"]["A"]}
    assert a_limpio["pnl_realizado"] == pytest.approx(a_todo["pnl_realizado"])
    assert a_limpio["cerradas"] == a_todo["cerradas"]


def test_la_curva_limpia_no_cuenta_la_duplicada(datos_dup):
    """El último punto de B en la curva limpia cuadra con su realizado limpio."""
    limpia = datos_dup["curva_equity_limpia"]
    b_limpio = datos_dup["pnl_por_cartera_limpio"]["B"]
    assert limpia[-1]["pl_acumulado_b"] == pytest.approx(b_limpio["pnl_realizado"])
    # Y una cerrada menos en el contador de B que en la curva completa.
    assert (limpia[-1]["n_cerradas_b"]
            == datos_dup["curva_equity"][-1]["n_cerradas_b"] - 1)
    # La serie A es idéntica en las dos curvas: A no tiene duplicadas.
    assert ([p["pl_acumulado_a"] for p in limpia]
            == [p["pl_acumulado_a"] for p in datos_dup["curva_equity"]])


def test_sin_duplicadas_las_dos_vistas_coinciden(datos):
    """La bitácora sintética base no tiene solapes: limpio == completo.

    Si algún día el sistema deja de generar duplicados, las dos vistas tienen que
    converger solas, sin tocar el dashboard.
    """
    assert datos["meta"]["duplicadas"] == 0
    assert all(not o["duplicada"] for o in datos["operaciones"])
    assert datos["resumen_limpio"] == datos["resumen"]
    assert datos["curva_equity_limpia"] == datos["curva_equity"]
    for c in ("A", "B"):
        fusion = {**datos["comparativa_ab_limpia"][c], **datos["pnl_por_cartera_limpio"][c]}
        assert fusion == datos["carteras"][c]


def test_marcar_duplicadas_cuenta_el_cierre_del_mismo_dia_como_solape():
    """Cerrar a las 16:00 y abrir otra al open del MISMO día es tener dos vivas."""
    import pandas as pd
    bit = pd.DataFrame([
        {"id": 1, "ticker": "X", "portafolio": "B",
         "fecha_entrada": "2026-07-01", "fecha_salida": "2026-07-03"},
        {"id": 2, "ticker": "X", "portafolio": "B",
         "fecha_entrada": "2026-07-03", "fecha_salida": None},
    ])
    assert list(gd.marcar_duplicadas(bit)) == [False, True]


def test_marcar_duplicadas_no_marca_lo_que_no_solapa():
    """Reentrar después de haber cerrado es el comportamiento correcto."""
    import pandas as pd
    bit = pd.DataFrame([
        {"id": 1, "ticker": "X", "portafolio": "A",
         "fecha_entrada": "2026-07-01", "fecha_salida": "2026-07-02"},
        {"id": 2, "ticker": "X", "portafolio": "A",
         "fecha_entrada": "2026-07-03", "fecha_salida": None},
    ])
    assert list(gd.marcar_duplicadas(bit)) == [False, False]


# --------------------------------------------------------------------------- #
# 3. Abiertas vs cerradas
# --------------------------------------------------------------------------- #
def test_abiertas_marcadas_y_fuera_de_las_estadisticas(datos):
    abiertas = [o for o in datos["operaciones"] if o["estado"] == "abierta"]
    assert len(abiertas) == 2
    for o in abiertas:
        assert o["no_realizado"] is True
        assert o["motivo"] == "Abierta"
        assert o["fecha_salida"] is None
        assert o["precio_salida"] is None
        # P&L a mercado tomado del informe MFE, no de la bitácora.
        assert o["pnl_pct"] == pytest.approx(8.0)

    # Las dos abiertas están en ganancia (+8%). Si contaran, el win rate global
    # subiría de 50% (3/6) a 62.5% (5/8) y el P&L acumulado de +20 a +36.
    assert datos["resumen"]["win_rate"] == pytest.approx(50.0)
    assert datos["resumen"]["pnl_acumulado"] == pytest.approx(20.0)
    assert datos["carteras"]["A"]["cerradas"] == 4     # no 5
    assert datos["carteras"]["B"]["cerradas"] == 2     # no 3
    assert datos["carteras"]["A"]["abiertas"] == 1
    assert datos["carteras"]["B"]["abiertas"] == 1


def test_cerradas_no_se_marcan_como_no_realizadas(datos):
    for o in datos["operaciones"]:
        if o["estado"] == "cerrada":
            assert o["no_realizado"] is False
            assert o["fecha_salida"] is not None
            assert o["pnl_pct"] is not None


def test_informe_mfe_parseado_por_nombre_de_columna(datos):
    """La tabla de abiertas se lee entera y con los signos correctos."""
    assert len(datos["mfe"]) == 2
    p = datos["mfe"][0]
    assert p["ticker"] == "EEE"
    assert p["mfe_pct"] == pytest.approx(12.0)
    assert p["mae_pct"] == pytest.approx(-3.0)
    assert p["pnl_actual_pct"] == pytest.approx(8.0)
    assert p["toco_5"] is True
    # La segunda tabla del informe (cerradas) NO debe colarse en esta lista.
    assert all(x["ticker"] == "EEE" for x in datos["mfe"])
    assert datos["meta"]["mfe_generado"] == "2026-07-21 18:00 ET"


# --------------------------------------------------------------------------- #
# 4. HTML generado
# --------------------------------------------------------------------------- #
class _Validador(html.parser.HTMLParser):
    """Comprueba que las etiquetas abren y cierran bien anidadas."""

    VACIAS = {"meta", "link", "br", "hr", "img", "input", "source", "col"}

    def __init__(self):
        super().__init__()
        self.pila: list[str] = []
        self.errores: list[str] = []
        self.vistas: set[str] = set()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.vistas.add(tag)
        for k, v in attrs:
            if k == "id":
                self.ids.add(v)
        if tag not in self.VACIAS:
            self.pila.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VACIAS:
            return
        if not self.pila:
            self.errores.append(f"</{tag}> sin apertura")
        elif self.pila[-1] != tag:
            self.errores.append(f"</{tag}> cierra un <{self.pila[-1]}> abierto")
        else:
            self.pila.pop()


@pytest.fixture(scope="module")
def html_generado() -> str:
    return gd.PLANTILLA.read_text(encoding="utf-8")


def test_html_bien_formado(html_generado):
    v = _Validador()
    v.feed(html_generado)
    assert v.errores == [], v.errores
    assert v.pila == [], f"etiquetas sin cerrar: {v.pila}"
    for tag in ("html", "head", "body", "header", "main", "footer", "table",
                "thead", "tbody", "details", "summary", "script", "style"):
        assert tag in v.vistas, f"falta <{tag}>"


def test_html_tiene_los_anclajes_que_el_script_rellena(html_generado):
    """Si alguien renombra un id en el HTML, el JS deja de encontrarlo y la
    sección se queda vacía sin ningún error visible. Aquí sale rojo."""
    v = _Validador()
    v.feed(html_generado)
    for ident in ("sello", "tema", "kpis", "carteras", "buscar", "chips", "cuenta",
                  "cabecera", "cuerpo", "vacio", "cuerpo-mfe", "mfe-sello",
                  "repo", "ult-cerrada", "det-mfe", "tabla",
                  "pnl-carteras", "nota-hueco", "curva", "lienzo", "globo"):
        assert ident in v.ids, f"falta id={ident}"


#: El namespace XML de SVG es un IDENTIFICADOR, no una descarga: createElementNS
#: nunca va a la red a buscarlo. Se descuenta antes de auditar, en vez de relajar
#: la prohibición de "http://", que es la que impide un CDN colado de verdad.
NS_SVG = "http://www.w3.org/2000/svg"


def test_html_es_autocontenido_y_responsive(html_generado):
    """Ni CDNs ni frameworks: el dashboard tiene que abrir sin red externa."""
    auditable = html_generado.lower().replace(NS_SVG, "")
    for prohibido in ("http://", "cdn.", "react", "tailwind", "jquery", "unpkg",
                      "jsdelivr", "googleapis"):
        assert prohibido not in auditable, f"referencia externa: {prohibido}"
    # El único origen que se contacta es el propio datos.json, mismo directorio.
    assert 'fetch("datos.json"' in html_generado
    assert html_generado.count("<script") == 1
    assert "viewport" in html_generado
    assert "@media (max-width:719px)" in html_generado       # layout de tarjetas
    assert "prefers-color-scheme" in html_generado
    assert "localStorage" in html_generado
    assert "tabular-nums" in html_generado


def test_html_contiene_las_secciones_del_diseno(html_generado):
    for texto in ("Centinela SP500", "P&amp;L acumulado por cartera",
                  "Comparativa A vs B", "Curva de equity", "Operaciones",
                  "Posiciones abiertas — MFE/MAE", "Paper trading — sin dinero real",
                  "Buscar ticker"):
        assert texto in html_generado, f"falta la sección/rótulo: {texto}"
    # Columnas de la tabla principal, en el orden exacto del diseño.
    columnas = ["Ticker", "Cartera", "Estado", "Fecha entrada", "Precio entrada",
                "Fecha salida", "Precio salida", "P&L %", "Motivo"]
    bloque = html_generado[html_generado.index("var COLS = ["):]
    posiciones = [bloque.index(f'"{c}"') for c in columnas]
    assert posiciones == sorted(posiciones), "las columnas no van en el orden pedido"
    # Chips de filtro combinables.
    for chip in ("Todas", "Abiertas", "Cerradas", "Cartera A", "Cartera B",
                 "Ganadoras", "Perdedoras"):
        assert f'txt:"{chip}"' in html_generado, f"falta el chip {chip}"
    # Paleta exacta del diseño, en sus dos temas.
    for color in ("#0a7d3b", "#4ade80", "#c2410c", "#f87171", "#0369a1", "#60a5fa"):
        assert color in html_generado, f"falta el color {color}"
    assert "details" in html_generado and "open" not in html_generado.split("<details")[1][:40]


def test_html_tiene_la_nota_de_honestidad_de_los_acumulados(html_generado):
    """La nota que explica QUÉ es esa suma no es decorativa: sin ella los cuatro
    números se leen como un retorno de cartera, que es justo lo que no son."""
    normalizado = " ".join(html_generado.split())
    assert ("Suma de retornos con posiciones equiponderadas (cada trade pesa igual). "
            "No es una curva de capital compuesta — este experimento no modela "
            "asignación de capital.") in normalizado
    # Pequeña y en color secundario, no un titular.
    assert ".nota{" in html_generado
    assert "color:var(--tenue)" in html_generado.split(".nota{")[1].split("}")[0]


def test_html_dibuja_la_curva_sin_librerias_externas(html_generado):
    """SVG a mano. Cualquier librería de gráficos aquí sería una regresión."""
    for prohibido in ("chart.js", "d3.", "recharts", "plotly", "highcharts",
                      "echarts", "apexcharts"):
        assert prohibido not in html_generado.lower(), f"librería externa: {prohibido}"
    assert "createElementNS" in html_generado          # SVG construido a mano
    assert 'svgEl("path"' in html_generado
    # Interacción con eventos de puntero, no con librerías de gestos.
    for ev in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
        assert ev in html_generado, f"falta el evento {ev}"
    assert "setPointerCapture" in html_generado        # tap sostenido en móvil
    # Alturas del diseño y aviso de datos insuficientes.
    assert "movil ? 240 : 320" in html_generado
    assert ("Aún no hay suficientes operaciones cerradas para dibujar la curva."
            in html_generado)
    assert "Se necesitan al menos 3." in html_generado
    assert "(R.cerradas || 0) < 3" in html_generado
    # Las dos series se distinguen también por trazo, no solo por color.
    assert "--serie-b" in html_generado
    assert "stroke-dasharray" in html_generado
    # Referencia del 0% y rejilla.
    assert ".cero-linea" in html_generado and ".rejilla" in html_generado


def test_html_tiene_el_aviso_de_duplicadas_y_el_filtro(html_generado):
    """El aviso y el interruptor son la forma de que los datos mixtos se lean
    como mixtos. Si desaparecen, el dashboard vuelve a dar por buenas unas
    estadísticas que llevan dentro las entradas de un bug."""
    # El mensaje se arma concatenando literales para no pasar de 90 columnas, así
    # que primero se sueldan los literales adyacentes (`" + "`). La fecha va en
    # medio como expresión, de ahí que se compruebe en dos mitades.
    unido = " ".join(html_generado.split()).replace('" + "', "")
    assert ("Las estadísticas incluyen operaciones duplicadas del mismo ticker "
            "por un bug corregido el ") in unido
    assert (". Ver CHANGELOG. La comparativa A vs B es interpretable solo desde "
            "esa fecha.") in unido
    # Descartable y recordado, para que no vuelva en cada visita.
    assert 'id="cerrar-aviso"' in html_generado
    assert 'localStorage.setItem(clave, "1")' in html_generado
    # Interruptor de vista limpia y los cuatro bloques que consume.
    assert "Solo operaciones limpias" in html_generado
    for bloque in ("resumen_limpio", "comparativa_ab_limpia",
                   "pnl_por_cartera_limpio", "curva_equity_limpia"):
        assert bloque in html_generado, f"el HTML no lee {bloque}"
    # Arranca en la vista completa: filtrar tiene que ser un acto explícito.
    assert "var limpio = false" in html_generado
    # Y las duplicadas se señalan una a una en la tabla.
    assert '"badge b-d","Duplicada"' in html_generado


def test_el_html_publicado_es_la_plantilla(tmp_path, monkeypatch, html_generado):
    """generar() copia la plantilla tal cual y deja el JSON al lado."""
    bit = tmp_path / "bitacora.csv"
    bit.write_text(_csv_sintetico(), encoding="utf-8")
    mfe = tmp_path / "mfe_actual.md"
    mfe.write_text(MFE_SINTETICO, encoding="utf-8")
    est = tmp_path / "estado.json"
    est.write_text(json.dumps(ESTADO_SINTETICO), encoding="utf-8")
    monkeypatch.setattr(gd, "RUTA_BITACORA", bit)
    monkeypatch.setattr(gd, "RUTA_MFE", mfe)
    monkeypatch.setattr(gd, "RUTA_ESTADO", est)

    destino = tmp_path / "docs"
    gd.generar(destino)
    assert (destino / "index.html").read_text(encoding="utf-8") == html_generado
    assert (destino / ".nojekyll").exists()
    publicado = json.loads((destino / "datos.json").read_text(encoding="utf-8"))
    assert publicado["resumen"]["cerradas"] == 6

    # Idempotencia: una segunda pasada no cambia un solo byte, así que el
    # workflow no genera un commit de ruido cuando no ha pasado nada.
    antes = {f.name: f.read_bytes() for f in destino.iterdir()}
    gd.generar(destino)
    assert {f.name: f.read_bytes() for f in destino.iterdir()} == antes


# --------------------------------------------------------------------------- #
# 5. Integración con el pipeline
# --------------------------------------------------------------------------- #
def test_el_dashboard_es_un_job_aparte_y_el_ultimo():
    """La publicación no puede poner en riesgo la persistencia de la bitácora.

    Si el dashboard fuera un paso más del job `postcierre`, un fallo suyo
    (yfinance caído, un KeyError en el markdown del informe) tumbaría el job que
    guarda la bitácora y el estado. Va aparte y detrás, como ya hace `mfe`.
    """
    import yaml
    wf = yaml.safe_load((RAIZ / ".github/workflows/postcierre.yml").read_text())
    job = wf["jobs"]["dashboard"]
    assert set(job["needs"]) == {"postcierre", "mfe"}
    # `mfe` puede quedar en skipped, y sin always() arrastraría al dashboard.
    assert "always()" in job["if"]
    assert "needs.postcierre.outputs.resultado == 'procesado'" in job["if"]
    assert isinstance(job.get("timeout-minutes"), int)

    pasos = " ".join(str(p.get("run", "")) for p in job["steps"])
    assert "scripts/generar_dashboard.py" in pasos
    # La persistencia se sigue exigiendo con el mismo script que el resto.
    assert "commit_y_push.sh" in pasos and "procesado" in pasos

    # El job del escaneo, que es el crítico, no depende del dashboard.
    assert "dashboard" not in wf["jobs"]["postcierre"].get("needs", [])
    assert "dashboard" not in wf["jobs"]["verificar"].get("needs", [])


def test_sin_silenciadores_de_errores_en_lo_nuevo():
    """Ni `|| true`, ni continue-on-error, ni 2>/dev/null, ni `set +e`.

    Un fallo tiene que verse rojo. Estos cuatro patrones son las cuatro formas
    habituales de convertir un fallo en un verde mentiroso.
    """
    ficheros = [RAIZ / "scripts/generar_dashboard.py",
                RAIZ / "scripts/plantilla_dashboard.html",
                RAIZ / "scripts/vigilante.py",
                RAIZ / ".github/workflows/vigilante.yml"]
    for f in ficheros:
        texto = f.read_text(encoding="utf-8")
        for patron in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
            assert patron not in texto, f"{f.name} contiene '{patron}'"

    # En postcierre.yml solo se mira el job nuevo: el resto del fichero es
    # anterior y tiene sus propias razones documentadas.
    texto = (RAIZ / ".github/workflows/postcierre.yml").read_text()
    bloque = texto[texto.index("  dashboard:"):]
    for patron in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
        assert patron not in bloque, f"el job dashboard contiene '{patron}'"


def test_el_vigilante_tiene_timeout_corto_y_cotas():
    """Regresión del 2026-08-06: un Vigilante que tarda minutos está roto."""
    import yaml
    wf = yaml.safe_load((RAIZ / ".github/workflows/vigilante.yml").read_text())
    assert wf["jobs"]["vigilar"]["timeout-minutes"] <= 5

    import vigilante
    assert vigilante.MAX_SESIONES > 0
    assert vigilante.MAX_COMMITS_LISTADOS > 0
    assert vigilante.TIMEOUT_GIT_S > 0
    # Una petición pedida a lo bruto no puede quedarse esperando para siempre.
    import socket
    assert socket.getdefaulttimeout() == vigilante.TIMEOUT_RED_S
    # El techo de sesiones se aplica de verdad, no es solo una constante.
    assert len(vigilante.sesiones_a_exigir(10 ** 6)) <= vigilante.MAX_SESIONES


def test_presupuestos_de_tamano():
    """Los techos del diseño, medidos sobre lo que hay publicado de verdad."""
    docs = gd.DOCS_DIR
    if not (docs / "index.html").exists():
        pytest.skip("docs/ aún no generado en este árbol")
    assert (docs / "index.html").stat().st_size < 50 * 1024
    assert (docs / "datos.json").stat().st_size < 500 * 1024
