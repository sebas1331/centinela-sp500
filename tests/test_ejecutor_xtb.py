"""Tests del ejecutor con un broker simulado. Sin red, sin git, sin XTB.

Lo que aquí se prueba es lo que decide si el dinero acaba donde debe:
idempotencia (no comprar dos veces), reconciliación (enterarse cuando el broker
y el simulador dejan de coincidir) y ventanas (no comprar cuando el mercado ya
lleva una hora abierto).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, ordenes as ords, broker_xtb as bx  # noqa: E402
import ejecutor_xtb as ej  # noqa: E402


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
class BrokerFalso:
    """Un broker que obedece y anota. Sustituye a BrokerXTB entero."""

    def __init__(self, posiciones=None, fallar_en=None):
        self._posiciones = posiciones or []
        self._fallar_en = fallar_en or set()
        self.enviadas = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def saldo(self):
        return {"saldo": 100000.0, "equity": 100000.0, "margen_libre": 90000.0,
                "divisa": "USD", "cuenta": 12345678}

    def posiciones(self):
        return self._posiciones

    def _ejecutar(self, ticker, lado, acciones):
        self.enviadas.append((lado, ticker, acciones))
        if ticker in self._fallar_en:
            return bx.Ejecucion(ticker=ticker, lado=lado, acciones=acciones,
                                estado="rechazada", error="sin fondos")
        return bx.Ejecucion(ticker=ticker, lado=lado, acciones=acciones,
                            estado="ejecutada", precio=101.0, orden=555)

    def comprar(self, ticker, acciones, objetivo=None, stop=None):
        return self._ejecutar(ticker, "compra", acciones)

    def vender(self, ticker, acciones):
        return self._ejecutar(ticker, "venta", acciones)


@pytest.fixture
def aislado(tmp_path, monkeypatch):
    """Ficheros del ejecutor redirigidos a un temporal."""
    monkeypatch.setattr(ords, "ARCHIVO_PENDIENTES", tmp_path / "pendientes.json")
    monkeypatch.setattr(ords, "ARCHIVO_ENVIADAS", tmp_path / "enviadas.json")
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "broker.csv")
    return tmp_path


def _orden(ticker="MRNA", tipo=ords.COMPRA, sesion="2026-09-25", acciones=3):
    return ords.Orden(
        id=ords.identificador(sesion, "A", ticker, tipo), tipo=tipo,
        cartera="A", ticker=ticker, acciones=acciones, sesion=sesion,
        objetivo=172.5 if tipo == ords.COMPRA else None,
        stop=138.0 if tipo == ords.COMPRA else None,
        precio_simulador=100.0)


def _pendientes(*ordenes, sesion="2026-09-25"):
    return {"sesion": sesion, "cartera_broker": "A", "ordenes": list(ordenes)}


# --------------------------------------------------------------------------- #
# 1. Idempotencia
# --------------------------------------------------------------------------- #
def test_una_orden_ya_enviada_no_se_repite(aislado, monkeypatch):
    """launchd dispara dos horarios y el Mac puede despertar dos veces.

    Sin esto, el segundo disparo compraría otra vez las mismas acciones.
    """
    monkeypatch.setattr(ej, "datetime", _reloj("2026-09-25"))
    broker = BrokerFalso()
    registro = {"enviadas": {}}
    p = _pendientes(_orden())

    ej.enviar(broker, p, "compras", registro)
    assert broker.enviadas == [("compra", "MRNA.US", 3)]

    # segundo disparo, mismo día, mismo registro
    ej.enviar(broker, p, "compras", registro)
    assert broker.enviadas == [("compra", "MRNA.US", 3)], "se envió dos veces"


def test_el_registro_sobrevive_al_disco(aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj("2026-09-25"))
    broker = BrokerFalso()
    registro = ords.cargar_enviadas()
    ej.enviar(broker, _pendientes(_orden()), "compras", registro)
    ords.guardar_enviadas(registro)

    otro_arranque = ords.cargar_enviadas()
    ej.enviar(BrokerFalso(), _pendientes(_orden()), "compras", otro_arranque)
    assert len(otro_arranque["enviadas"]) == 1


def test_una_orden_fallida_NO_se_marca_como_enviada(aislado, monkeypatch):
    """Si no llegó, el siguiente disparo tiene que poder reintentarla."""
    monkeypatch.setattr(ej, "datetime", _reloj("2026-09-25"))
    broker = BrokerFalso(fallar_en={"MRNA.US"})
    registro = {"enviadas": {}}
    with pytest.raises(RuntimeError, match="NO llegaron"):
        ej.enviar(broker, _pendientes(_orden()), "compras", registro)
    assert registro["enviadas"] == {}


def test_solo_se_envian_las_ordenes_del_tipo_y_del_dia(aislado, monkeypatch):
    """Una venta programada para mañana no se manda hoy."""
    monkeypatch.setattr(ej, "datetime", _reloj("2026-09-25"))
    broker = BrokerFalso()
    p = _pendientes(
        _orden("MRNA", ords.COMPRA, "2026-09-25"),
        _orden("SNDK", ords.VENTA_TIEMPO, "2026-09-25"),
        _orden("WDC", ords.COMPRA, "2026-09-28"),      # otro día
    )
    ej.enviar(broker, p, "compras", {"enviadas": {}})
    assert broker.enviadas == [("compra", "MRNA.US", 3)]

    broker2 = BrokerFalso()
    ej.enviar(broker2, p, "ventas", {"enviadas": {}})
    assert broker2.enviadas == [("venta", "SNDK.US", 3)]


def _reloj(fecha_iso):
    class _D(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(f"{fecha_iso}T10:00:00").replace(tzinfo=tz)
    return _D


# --------------------------------------------------------------------------- #
# 2. Reconciliación
# --------------------------------------------------------------------------- #
def _estado(tickers, tmp_path, monkeypatch):
    ruta = tmp_path / "estado.json"
    ruta.write_text(json.dumps({
        "posiciones": {"A": [{"id": i, "ticker": t, "fecha_entrada": "2026-09-01"}
                             for i, t in enumerate(tickers, 1)], "B": []},
        "entradas_pendientes": [],
    }), encoding="utf-8")
    monkeypatch.setattr(config, "ARCHIVO_ESTADO", ruta)


def _pos(ticker):
    return {"ticker": ticker, "acciones": 3.0, "precio_entrada": 100.0,
            "precio_actual": 101.0, "stop": 92.0, "objetivo": 115.0,
            "lado": "buy", "orden": "x", "pnl": 3.0}


def test_reconciliar_no_ve_diferencias_cuando_coinciden(tmp_path, monkeypatch):
    _estado(["MRNA", "SNDK"], tmp_path, monkeypatch)
    broker = BrokerFalso(posiciones=[_pos("MRNA.US"), _pos("SNDK.US")])
    assert ej.reconciliar(broker) == []


def test_reconciliar_detecta_una_posicion_que_falta_en_XTB(tmp_path, monkeypatch):
    """La compra no llegó, o el stop saltó y el simulador no se enteró."""
    _estado(["MRNA", "SNDK"], tmp_path, monkeypatch)
    broker = BrokerFalso(posiciones=[_pos("MRNA.US")])
    problemas = ej.reconciliar(broker)
    assert len(problemas) == 1
    assert "SNDK" in problemas[0] and "NO en XTB" in problemas[0]


def test_reconciliar_detecta_dinero_expuesto_que_nadie_sigue(tmp_path, monkeypatch):
    """Lo más grave: una posición en el broker que el sistema no conoce."""
    _estado(["MRNA"], tmp_path, monkeypatch)
    broker = BrokerFalso(posiciones=[_pos("MRNA.US"), _pos("TSLA.US")])
    problemas = ej.reconciliar(broker)
    assert len(problemas) == 1
    assert "TSLA" in problemas[0] and "NO en el simulador" in problemas[0]


def test_reconciliar_traduce_los_simbolos_de_XTB(tmp_path, monkeypatch):
    """'BRK.B' en la bitácora es 'BRK-B.US' en XTB: no puede dar diferencia."""
    _estado(["BRK.B"], tmp_path, monkeypatch)
    broker = BrokerFalso(posiciones=[_pos("BRK-B.US")])
    assert ej.reconciliar(broker) == []


# --------------------------------------------------------------------------- #
# 3. Ventanas
# --------------------------------------------------------------------------- #
def _ahora(fecha, hora):
    return datetime.fromisoformat(f"{fecha}T{hora}").replace(tzinfo=config.TZ_ET)


def test_las_compras_solo_se_mandan_ANTES_de_la_apertura():
    """Comprar con el mercado abierto ya no sería al precio de apertura.

    Es el mismo principio que hace fallar en rojo a la pre-apertura cuando
    llega tarde: la estrategia compra al open o no compra.
    """
    ok, _ = ej.en_ventana("compras", _ahora("2026-09-25", "09:00:00"))
    assert ok
    ok, motivo = ej.en_ventana("compras", _ahora("2026-09-25", "10:30:00"))
    assert not ok and "apertura" in motivo


def test_las_compras_no_se_mandan_demasiado_pronto():
    ok, motivo = ej.en_ventana("compras", _ahora("2026-09-25", "05:00:00"))
    assert not ok and "más de" in motivo


def test_las_ventas_por_tiempo_van_pegadas_al_cierre():
    ok, _ = ej.en_ventana("ventas", _ahora("2026-09-25", "15:45:00"))
    assert ok
    # Demasiado pronto: quedan cuatro horas de sesión y el objetivo aún puede tocar.
    ok, _ = ej.en_ventana("ventas", _ahora("2026-09-25", "12:00:00"))
    assert not ok
    # Demasiado tarde: el mercado ya cerró.
    ok, _ = ej.en_ventana("ventas", _ahora("2026-09-25", "16:30:00"))
    assert not ok


def test_la_reconciliacion_espera_a_que_cierre_el_mercado():
    ok, _ = ej.en_ventana("reconcilia", _ahora("2026-09-25", "15:00:00"))
    assert not ok
    ok, _ = ej.en_ventana("reconcilia", _ahora("2026-09-25", "18:00:00"))
    assert ok


def test_un_dia_sin_mercado_no_hace_nada():
    """26 de diciembre de 2026: sábado."""
    for momento in ej.MOMENTOS:
        ok, motivo = ej.en_ventana(momento, _ahora("2026-12-26", "09:00:00"))
        assert not ok and "mercado" in motivo


# --------------------------------------------------------------------------- #
# 4. La bitácora del broker
# --------------------------------------------------------------------------- #
def test_la_bitacora_del_broker_guarda_el_slippage(aislado):
    """La diferencia entre lo simulado y lo ejecutado es el dato que importa."""
    o = _orden()            # precio_simulador = 100.0
    e = bx.Ejecucion(ticker="MRNA.US", lado="compra", acciones=3,
                     estado="ejecutada", precio=101.0, orden=555)
    ords.registrar_ejecucion(o, e)

    import csv
    fila = list(csv.DictReader(open(ords.ARCHIVO_BITACORA_BROKER, encoding="utf-8")))[0]
    assert fila["ticker"] == "MRNA" and fila["simbolo_xtb"] == "MRNA.US"
    assert fila["estado"] == "ejecutada" and fila["orden_xtb"] == "555"
    # Comprar a 101 lo simulado a 100: un 1% PEOR.
    assert float(fila["slippage_pct"]) == pytest.approx(1.0)


def test_en_una_venta_el_slippage_cambia_de_signo(aislado):
    """Vender más barato de lo simulado también es peor, no mejor."""
    o = _orden(tipo=ords.VENTA_TIEMPO)
    e = bx.Ejecucion(ticker="MRNA.US", lado="venta", acciones=3,
                     estado="ejecutada", precio=99.0)
    ords.registrar_ejecucion(o, e)
    import csv
    fila = list(csv.DictReader(open(ords.ARCHIVO_BITACORA_BROKER, encoding="utf-8")))[0]
    assert float(fila["slippage_pct"]) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 5. El modelo de órdenes
# --------------------------------------------------------------------------- #
def test_no_se_puede_construir_una_orden_de_menos_de_una_accion():
    with pytest.raises(ValueError, match="fracciones"):
        ords.Orden(id="x", tipo=ords.COMPRA, cartera="A", ticker="SNDK",
                   acciones=0, sesion="2026-09-25")


def test_el_vocabulario_de_tipos_es_cerrado():
    with pytest.raises(ValueError, match="cerrado"):
        ords.Orden(id="x", tipo="venta_por_objetivo", cartera="A",
                   ticker="MRNA", acciones=1, sesion="2026-09-25")


@pytest.mark.parametrize("ticker,esperado", [
    ("MRNA", "MRNA.US"), ("BRK.B", "BRK-B.US"), ("aapl", "AAPL.US")])
def test_traduccion_de_simbolos_a_XTB(ticker, esperado):
    assert ords.simbolo_xtb(ticker) == esperado


def test_los_identificadores_son_deterministas():
    a = ords.identificador("2026-09-25", "A", "MRNA", ords.COMPRA)
    b = ords.identificador("2026-09-25", "A", "MRNA", ords.COMPRA)
    assert a == b == "2026-09-25|A|MRNA|compra"
    assert a != ords.identificador("2026-09-25", "A", "MRNA", ords.VENTA_TIEMPO)


# --------------------------------------------------------------------------- #
# 6. Vigilancia de niveles (XTB no acepta stop ni take profit al contado)
# --------------------------------------------------------------------------- #
def _estado_con_niveles(tmp_path, monkeypatch, posiciones):
    ruta = tmp_path / "estado.json"
    ruta.write_text(json.dumps({"posiciones": {"A": posiciones, "B": []},
                                "entradas_pendientes": []}), encoding="utf-8")
    monkeypatch.setattr(config, "ARCHIVO_ESTADO", ruta)


def _pos_broker(ticker, precio_actual, acciones=3.0):
    return {"ticker": ticker, "acciones": acciones, "precio_entrada": 100.0,
            "precio_actual": precio_actual, "stop": None, "objetivo": None,
            "lado": "buy", "orden": "x", "pnl": 0.0}


def test_cierra_la_posicion_que_cruzo_el_STOP(aislado, tmp_path, monkeypatch):
    """El stop lo vigila el ejecutor porque XTB lo ignora al contado."""
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 92.0, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=91.5)])
    hechas = ej.vigilar_niveles(broker, {"enviadas": {}})

    assert broker.enviadas == [("venta", "MRNA.US", 3)]
    assert hechas[0][0].tipo == ords.VENTA_STOP


def test_cierra_la_posicion_que_cruzo_el_OBJETIVO(aislado, tmp_path, monkeypatch):
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 92.0, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=121.0)])
    hechas = ej.vigilar_niveles(broker, {"enviadas": {}})

    assert broker.enviadas == [("venta", "MRNA.US", 3)]
    assert hechas[0][0].tipo == ords.VENTA_OBJETIVO


def test_no_toca_la_posicion_que_esta_entre_los_dos_niveles(aislado, tmp_path,
                                                            monkeypatch):
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 92.0, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=105.0)])
    assert ej.vigilar_niveles(broker, {"enviadas": {}}) == []
    assert broker.enviadas == []


def test_si_se_cruzan_los_dos_gana_el_STOP(aislado, tmp_path, monkeypatch):
    """La regla conservadora del simulador, también aquí."""
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 120.0, "objetivo": 100.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=110.0)])
    hechas = ej.vigilar_niveles(broker, {"enviadas": {}})
    assert hechas[0][0].tipo == ords.VENTA_STOP


def test_una_posicion_SIN_stop_solo_mira_el_objetivo(aislado, tmp_path, monkeypatch):
    """La Cartera B no tiene stop por diseño: no se le inventa uno."""
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": None, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=1.0)])
    assert ej.vigilar_niveles(broker, {"enviadas": {}}) == []


def test_sin_precio_actual_no_se_cierra_nada(aislado, tmp_path, monkeypatch):
    """Cerrar a ciegas sería peor que no cerrar: se avisa y se deja para la
    siguiente pasada."""
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 92.0, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=0.0)])
    assert ej.vigilar_niveles(broker, {"enviadas": {}}) == []
    assert broker.enviadas == []


def test_un_cierre_por_nivel_no_se_repite_en_la_misma_sesion(aislado, tmp_path,
                                                             monkeypatch):
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 92.0, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[_pos_broker("MRNA.US", precio_actual=91.5)])
    registro = {"enviadas": {}}
    ej.vigilar_niveles(broker, registro)
    ej.vigilar_niveles(broker, registro)
    assert broker.enviadas == [("venta", "MRNA.US", 3)]


def test_una_posicion_que_no_esta_en_XTB_no_se_vigila(aislado, tmp_path, monkeypatch):
    """Si falta en el broker, el problema es otro y lo denuncia la reconciliación."""
    _estado_con_niveles(tmp_path, monkeypatch, [
        {"id": 1, "ticker": "MRNA", "stop": 92.0, "objetivo": 120.0}])
    broker = BrokerFalso(posiciones=[])
    assert ej.vigilar_niveles(broker, {"enviadas": {}}) == []
