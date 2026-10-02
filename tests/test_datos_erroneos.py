"""Operaciones decididas sobre una serie de precios que no era real.

Tres cosas que pueden romperse en silencio y por eso están aquí:

  1. QUIÉN queda marcado. La regla es por SOLAPAMIENTO con la ventana, no por
     "entró después": una operación cerrada antes del salto no está afectada
     aunque su ticker sí lo esté, y una que lo cruza es el caso más sucio.
  2. Que la exclusión no borre nada. Las filas siguen en la bitácora y en la
     tabla de la página, marcadas; lo único que no hacen es contar.
  3. Que el cierre por dato erróneo solo pueda cerrar lo DECLARADO, y que deje
     las dos patas (broker y simulador) iguales.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, cuenta, datos_erroneos as de  # noqa: E402

CABECERA = ("id,grupo,ticker,portafolio,sector,fecha_entrada,hora_entrada_et,"
            "hora_entrada_utc,timestamp_escaneo,precio_entrada,probabilidad,"
            "score_fundamental,objetivo_inicial,objetivo_actual,"
            "historial_objetivos,stop,fecha_salida,hora_salida_et,"
            "precio_salida,motivo_salida,pnl_pct,dias_habiles,estado,notas")

#: Una ventana que empieza el 15 de julio y sigue abierta.
VENTANA = {
    "ticker": "ROTA", "desde": "2026-07-15", "hasta": None,
    "causa": "contrasplit sin ajustar",
    "evidencia": "cierre del 15/07: 10 -> 30 (×3)",
    "detectado": "2026-07-20",
}


@pytest.fixture
def registro(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "ARCHIVO", tmp_path / "datos_erroneos.json")
    de.guardar([VENTANA])
    return de.ARCHIVO


def _precios(tickers, precio=100.0) -> dict:
    """OHLC plano: así marcar a mercado no mueve el equity y la cuenta cuadra.

    Hace falta de verdad —no vale `{}`— porque con el diccionario vacío la
    curva sale vacía y `cuenta.metricas` devuelve su rama degenerada: se
    probaría un camino que en producción no existe.
    """
    fechas = pd.date_range("2026-07-01", "2026-08-10", freq="B")
    return {tk: pd.DataFrame({"Open": precio, "High": precio, "Low": precio,
                              "Close": precio}, index=fechas)
            for tk in tickers}


def _bit(filas) -> pd.DataFrame:
    lineas = [CABECERA]
    for (oid, tk, cart, fe, fs, pnl, estado) in filas:
        lineas.append(",".join([
            str(oid), "1", tk, cart, "Materials", fe, "", "", "", "100.0",
            "0.9", "60.0", "", "", '""', "", fs or "", "", "110.0",
            "tiempo" if fs else "", "" if pnl is None else str(pnl), "",
            estado, "",
        ]))
    return pd.read_csv(pd.io.common.StringIO("\n".join(lineas) + "\n"))


# --------------------------------------------------------------------------- #
# 1. Quién queda marcado
# --------------------------------------------------------------------------- #
def test_la_ventana_se_cruza_por_solapamiento_no_por_fecha_de_entrada(registro):
    bit = _bit([
        # cerrada ANTES del salto: la serie que la midió seguía siendo buena
        (1, "ROTA", "A", "2026-07-01", "2026-07-10", 0.05, "cerrada"),
        # a caballo del salto: el P&L se calculó sobre la discontinuidad
        (2, "ROTA", "A", "2026-07-10", "2026-07-20", 0.90, "cerrada"),
        # entera después del salto: la decisión salió de la serie rota
        (3, "ROTA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
        # abierta: su salida se decidirá con la serie que haya, y está rota
        (4, "ROTA", "B", "2026-08-01", None, None, "abierta"),
        # otro ticker: no lo toca nadie
        (5, "SANA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
    ])
    marcadas = de.marcar(bit)
    assert list(marcadas) == [False, True, True, True, False]


def test_una_ventana_cerrada_deja_de_afectar_a_lo_posterior(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "ARCHIVO", tmp_path / "d.json")
    de.guardar([{**VENTANA, "hasta": "2026-07-31"}])
    bit = _bit([
        (1, "ROTA", "A", "2026-07-20", "2026-07-25", 0.05, "cerrada"),
        (2, "ROTA", "A", "2026-08-05", "2026-08-12", 0.05, "cerrada"),
    ])
    assert list(de.marcar(bit)) == [True, False]


def test_sin_registro_no_se_marca_nada(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "ARCHIVO", tmp_path / "no-existe.json")
    bit = _bit([(1, "ROTA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada")])
    assert not de.marcar(bit).any()


def test_una_ventana_sin_evidencia_no_se_puede_guardar(tmp_path, monkeypatch):
    """Una exclusión sin causa ni evidencia es un resultado que no gustó."""
    monkeypatch.setattr(de, "ARCHIVO", tmp_path / "d.json")
    with pytest.raises(ValueError, match="evidencia"):
        de.guardar([{k: v for k, v in VENTANA.items() if k != "evidencia"}])
    assert not de.ARCHIVO.exists()


def test_el_registro_real_declara_las_dos_series_del_2026_10_02():
    """El fichero del repositorio, no uno sintético: es el que publica la web."""
    ruta = config.ESTADO_DIR / "datos_erroneos.json"
    series = {s["ticker"]: s for s in de.cargar(ruta)}
    assert set(series) == {"CTVA", "MRNA"}
    assert series["CTVA"]["desde"] == "2026-10-01"
    assert series["MRNA"]["desde"] == "2026-08-19"
    for s in series.values():
        assert s["evidencia"] and s["causa"] and s["detectado"]


# --------------------------------------------------------------------------- #
# 2. La exclusión no borra nada
# --------------------------------------------------------------------------- #
def test_la_vista_limpia_quita_las_dos_clases_y_conserva_las_filas(registro):
    bit = _bit([
        (1, "ROTA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
        (2, "SANA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
        # duplicada: segunda entrada del mismo ticker y cartera con la primera viva
        (3, "SANA", "A", "2026-07-25", "2026-08-05", 0.05, "cerrada"),
    ])
    marcada = cuenta.marcar_excluidas(bit)
    assert list(marcada["dato_erroneo"]) == [True, False, False]
    assert list(marcada["duplicada"]) == [False, False, True]
    assert list(cuenta.vista_limpia(bit)["id"]) == [2]
    assert len(bit) == 3, "la exclusión no puede tocar la bitácora"


def test_el_resumen_cuenta_las_operaciones_de_cada_serie(registro):
    bit = _bit([
        (1, "ROTA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
        (2, "ROTA", "B", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
        (3, "SANA", "A", "2026-07-20", "2026-07-30", 0.05, "cerrada"),
    ])
    r = de.resumen(bit)
    assert r["operaciones"] == 2
    assert [s["ticker"] for s in r["series"]] == ["ROTA"]
    assert r["series"][0]["operaciones"] == 2


def test_la_salida_por_dato_erroneo_paga_slippage_de_mercado():
    """Es una venta a mercado, no una orden límite: tiene que pagar su coste."""
    assert de.MOTIVO_SALIDA in cuenta.SALIDAS_A_MERCADO
    bruto = 100.0
    assert cuenta.precio_salida_neto(bruto, de.MOTIVO_SALIDA) < \
        cuenta.precio_salida_neto(bruto, "objetivo")


# --------------------------------------------------------------------------- #
# 3. Lo que publica el panel
# --------------------------------------------------------------------------- #
def test_el_panel_las_pinta_marcadas_y_no_las_cuenta(tmp_path, monkeypatch,
                                                     registro):
    import generar_dashboard as gd

    bit = tmp_path / "bitacora.csv"
    bit.write_text("\n".join([
        CABECERA,
        # ROTA: +100% (si contara, dispararía el win rate y la expectancy)
        "1,1,ROTA,A,Materials,2026-07-20,,,,100.0,0.9,60.0,,,\"\",88.0,"
        "2026-07-27,,200.0,dato_erroneo,1.0,5,cerrada,",
        "2,1,SANA,A,Materials,2026-07-20,,,,100.0,0.9,60.0,,,\"\",88.0,"
        "2026-07-27,,110.0,tiempo,0.1,5,cerrada,",
    ]) + "\n", encoding="utf-8")
    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps({"actualizado": "2026-07-28T18:00:00-04:00",
                                  "posiciones": {"A": [], "B": []}}),
                      encoding="utf-8")
    monkeypatch.setattr(gd, "RUTA_BITACORA", bit)
    monkeypatch.setattr(gd, "RUTA_ESTADO", estado)

    d = gd.construir_datos(precios=_precios(["ROTA", "SANA"]))

    # Las dos filas siguen publicadas, y una va marcada.
    assert {o["ticker"]: o["es_dato_erroneo"] for o in d["operaciones"]} == {
        "ROTA": True, "SANA": False}
    # Pero las cifras son solo de la limpia: una cerrada, +10%.
    assert d["resumen"]["cerradas"] == 1
    assert d["carteras"]["A"]["cerradas"] == 1
    assert d["carteras"]["A"]["expectancy"] == pytest.approx(10.0)
    assert d["meta"]["datos_erroneos"]["operaciones"] == 1
    assert d["meta"]["datos_erroneos"]["series"][0]["ticker"] == "ROTA"
    # Y el motivo tiene etiqueta propia: no se disfraza de salida por tiempo.
    rota = next(o for o in d["operaciones"] if o["ticker"] == "ROTA")
    assert rota["motivo"] == "Dato erróneo"


def test_las_ordenes_activas_no_callan_una_posicion_viva(tmp_path, monkeypatch,
                                                         registro):
    """Excluida de la estadística, pero su stop y su límite siguen puestos."""
    import generar_dashboard as gd

    bit = tmp_path / "bitacora.csv"
    bit.write_text("\n".join([
        CABECERA,
        "1,1,ROTA,A,Materials,2026-07-20,,,,100.0,0.9,60.0,130.0,130.0,\"\","
        "88.0,,,,,,,abierta,",
        "2,2,SANA,A,Materials,2026-07-21,,,,100.0,0.9,60.0,130.0,130.0,\"\","
        "88.0,,,,,,,abierta,",
    ]) + "\n", encoding="utf-8")
    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps({
        "actualizado": "2026-07-28T18:00:00-04:00",
        "ultima_postcierre": "2026-07-27",
        "posiciones": {"A": [{"id": 1, "ticker": "ROTA",
                              "dia_limite": "2026-08-03"},
                             {"id": 2, "ticker": "SANA",
                              "dia_limite": "2026-08-04"}], "B": []},
    }), encoding="utf-8")
    monkeypatch.setattr(gd, "RUTA_BITACORA", bit)
    monkeypatch.setattr(gd, "RUTA_ESTADO", estado)

    d = gd.construir_datos(precios=_precios(["ROTA", "SANA"]))
    rota = next(o for o in d["ordenes"] if o["ticker"] == "ROTA")
    assert rota["stop"] == 88.0 and rota["objetivo"] == 130.0
    # Y no cuenta como abierta en la estadística: solo la limpia.
    assert d["resumen"]["abiertas"] == 1


def test_la_demo_no_promedia_el_slippage_de_una_serie_rota(tmp_path, monkeypatch,
                                                           registro):
    import generar_dashboard as gd
    from centinela import ordenes as ords

    ruta = tmp_path / "bitacora_broker.csv"
    ruta.write_text(
        ",".join(ords.COLUMNAS_BROKER) + "\n"
        "x|A|ROTA|compra,2026-07-20,2026-07-20T09:30:00-04:00,A,ROTA,ROTA.US,"
        "compra,10,ejecutada,12.0,1,,,,10.0,20.0,\n", encoding="utf-8")
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", ruta)

    assert gd.bloque_broker({}) is None, "la única ejecución era de serie rota"
    assert gd._ejecuciones_demo_excluidas() == 1


# --------------------------------------------------------------------------- #
# 4. El cierre por dato erróneo
# --------------------------------------------------------------------------- #
def test_solo_cierra_lo_declarado(registro):
    import cerrar_por_dato_erroneo as cdp
    assert cdp.exigir_declarado("ROTA")["desde"] == "2026-07-15"
    with pytest.raises(RuntimeError, match="no está declarado"):
        cdp.exigir_declarado("SANA")


def test_no_cierra_una_ventana_ya_resuelta(tmp_path, monkeypatch):
    import cerrar_por_dato_erroneo as cdp
    monkeypatch.setattr(de, "ARCHIVO", tmp_path / "d.json")
    de.guardar([{**VENTANA, "hasta": "2026-07-31"}])
    with pytest.raises(RuntimeError, match="no está declarado"):
        cdp.exigir_declarado("ROTA")


def test_el_cierre_en_el_simulador_deja_motivo_propio_y_pnl(registro, tmp_path):
    import cerrar_por_dato_erroneo as cdp
    from centinela import bitacora

    oid = bitacora.registrar_entrada({
        "grupo": 1, "ticker": "ROTA", "portafolio": "A", "sector": "Materials",
        "fecha_entrada": "2026-07-20", "precio_entrada": 100.0,
        "objetivo_inicial": 130.0, "stop": 88.0, "notas": "prob=1.000"})
    estado = {"posiciones": {"A": [{"id": oid, "ticker": "ROTA",
                                    "portafolio": "A", "entrada": 100.0,
                                    "fecha_entrada": "2026-07-20"}], "B": []}}

    cerradas = cdp.cerrar_en_simulador(estado, "ROTA", 90.0, "2026-07-27",
                                       "serie rota")

    assert len(cerradas) == 1
    assert estado["posiciones"]["A"] == [], "la posición tiene que desaparecer"
    fila = pd.read_csv(config.ARCHIVO_BITACORA_CSV).iloc[0]
    assert fila["motivo_salida"] == de.MOTIVO_SALIDA
    assert fila["estado"] == "cerrada"
    assert fila["precio_salida"] == 90.0
    assert fila["pnl_pct"] == pytest.approx(-0.10)
    assert "dato erróneo" in fila["notas"]
    # Y la bitácora conserva la entrada: no se borra, se cierra.
    assert fila["precio_entrada"] == 100.0
