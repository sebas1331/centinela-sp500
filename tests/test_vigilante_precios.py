"""El vigilante de precios: disparos, relevo, latido y convivencia.

Lo que aquí se prueba es lo que decide si una posición sale por su nivel o se
queda hasta el vistazo de las 15:45 ET, que está medido como peor que no tener
stop. Todo con precios inventados: sin red, sin XTB, sin git.
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

from centinela import (config, latido as lat, niveles as niv,  # noqa: E402
                       ordenes as ords, broker_xtb as bx)
import vigilante_precios as vp  # noqa: E402
import hace_falta_vigilante as hfv  # noqa: E402

HOY = "2026-10-15"


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
class BrokerFalso:
    """Un broker con precios que uno decide, y memoria de lo que se le pidió."""

    def __init__(self, posiciones=None, fallar=()):
        self._pos = list(posiciones or [])
        self._fallar = set(fallar)
        self.vendidas = []
        self.suscritos = []
        self.bombeos = 0
        self._precios = None

    # --- lectura
    def posiciones(self):
        return list(self._pos)

    def saldo(self):
        return {"saldo": 30000.0, "equity": 30000.0, "divisa": "USD",
                "cuenta": config.CUENTA_DEMO}

    def cotizacion(self, simbolo):
        return {"ticker": simbolo, "bid": 100.0, "ask": 100.1, "spread": 0.1}

    @property
    def conectado(self):
        return True

    # --- streaming
    def al_recibir_tick(self, cb): self._tick = cb
    def al_perder_conexion(self, cb): self._caida = cb
    def al_recuperar_conexion(self, cb): self._vuelta = cb
    def suscribir_ticks(self, s): self.suscritos.append(s)
    def bombear(self, seg): self.bombeos += 1

    # --- escritura
    def vender(self, simbolo, acciones):
        self.vendidas.append((simbolo, acciones))
        if simbolo in self._fallar:
            return bx.Ejecucion(ticker=simbolo, lado="venta", acciones=acciones,
                                estado="rechazada", error="sin contrapartida")
        self._pos = [p for p in self._pos if p["ticker"] != simbolo]
        return bx.Ejecucion(ticker=simbolo, lado="venta", acciones=acciones,
                            estado="ejecutada", precio=99.5, orden=777)


def _posicion(ticker="MRNA.US", acciones=3):
    return {"ticker": ticker, "acciones": float(acciones), "lado": "buy",
            "precio_entrada": 100.0, "precio_actual": 100.0, "stop": None,
            "objetivo": None, "orden": 555, "pnl": 0.0}


def _vigilada(ticker="MRNA", objetivo=110.0, stop=90.0, acciones=3):
    return {"ticker": ticker, "simbolo": f"{ticker}.US", "acciones": acciones,
            "objetivo": objetivo, "stop": stop, "id_operacion": 1, "bid": None}


@pytest.fixture
def aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(ords, "ARCHIVO_ENVIADAS", tmp_path / "enviadas.json")
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "broker.csv")
    monkeypatch.setattr(vp, "publicar_venta", lambda *_a, **_k: None)

    class R(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(f"{HOY}T11:00:00").replace(tzinfo=tz)
    monkeypatch.setattr(vp, "datetime", R)
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. La regla de disparo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bid,esperado", [
    (100.0, None), (110.0, "objetivo"), (110.01, "objetivo"),
    (90.0, "stop"), (89.99, "stop"), (109.99, None),
])
def test_el_cruce_dispara_en_el_nivel_exacto(bid, esperado):
    assert niv.cruce(bid, stop=90.0, objetivo=110.0) is esperado or \
           niv.cruce(bid, stop=90.0, objetivo=110.0) == esperado


def test_si_el_bid_no_existe_no_se_vende_a_ciegas():
    for malo in (None, 0, -1, "", "nan"):
        assert niv.cruce(malo, 90.0, 110.0) is None


def test_gana_el_stop_cuando_los_dos_cruzan():
    """La regla conservadora del simulador. No puede pasar con un bid, pero sí
    con un hueco de apertura, y entonces el orden importa."""
    assert niv.cruce(50.0, stop=90.0, objetivo=40.0) == "stop"


def test_la_venta_en_vivo_tiene_su_propio_nombre():
    """Para poder medir si disparar en vivo acerca la ejecución al simulador,
    frente al vistazo único de las 15:45 que la alejaba."""
    assert niv.tipo_de_orden("stop", en_vivo=True) == ords.VENTA_STOP_INTRADIA
    assert niv.tipo_de_orden("stop", en_vivo=False) == ords.VENTA_STOP
    assert niv.tipo_de_orden("objetivo", True) == ords.VENTA_OBJETIVO_INTRADIA


def test_un_disparo_inventado_no_cuela():
    with pytest.raises(ValueError, match="Disparo desconocido"):
        niv.tipo_de_orden("corazonada", en_vivo=True)


# --------------------------------------------------------------------------- #
# 2. El disparo de verdad
# --------------------------------------------------------------------------- #
def test_dispara_por_objetivo_y_registra_el_nivel(aislado):
    broker = BrokerFalso([_posicion()])
    registro = {"enviadas": {}}
    v = _vigilada()
    assert vp.vender_por_nivel(broker, registro, v, "objetivo", 110.4) is True
    assert broker.vendidas == [("MRNA.US", 3)]

    fila = ords.filas_de_sesion(HOY)[0]
    assert fila["tipo"] == ords.VENTA_OBJETIVO_INTRADIA
    assert float(fila["precio"]) == 99.5          # lo que ejecutó XTB
    assert float(fila["precio_simulador"]) == 110.0   # el nivel exacto
    assert float(fila["precio_disparo"]) == 110.4  # el bid que lo disparó


def test_dispara_por_stop(aislado):
    broker = BrokerFalso([_posicion()])
    assert vp.vender_por_nivel(broker, {"enviadas": {}}, _vigilada(),
                               "stop", 89.5) is True
    assert ords.filas_de_sesion(HOY)[0]["tipo"] == ords.VENTA_STOP_INTRADIA


def test_no_vende_dos_veces_la_misma_posicion(aislado):
    """El identificador es determinista, así que un segundo disparo del mismo
    nivel el mismo día no manda nada."""
    broker = BrokerFalso([_posicion()])
    registro = {"enviadas": {}}
    assert vp.vender_por_nivel(broker, registro, _vigilada(), "stop", 89.0)
    broker._pos = [_posicion()]            # como si volviera a aparecer
    assert vp.vender_por_nivel(broker, registro, _vigilada(), "stop", 88.0) is False
    assert len(broker.vendidas) == 1


def test_si_otro_ya_la_vendio_no_se_vende(aislado):
    """El candado compartido con el ejecutor de ventas por tiempo: los dos
    corren en máquinas distintas y no se ven, pero los dos le preguntan a XTB
    justo antes de vender, y XTB es quien manda."""
    broker = BrokerFalso(posiciones=[])    # el otro ya cerró la posición
    assert vp.vender_por_nivel(broker, {"enviadas": {}}, _vigilada(),
                               "stop", 89.0) is False
    assert broker.vendidas == []


def test_una_venta_rechazada_rompe_en_rojo(aislado):
    """La posición sigue abierta con su nivel cruzado: callarse sería peor."""
    broker = BrokerFalso([_posicion()], fallar={"MRNA.US"})
    with pytest.raises(RuntimeError, match="NO llegó al broker"):
        vp.vender_por_nivel(broker, {"enviadas": {}}, _vigilada(), "stop", 89.0)


# --------------------------------------------------------------------------- #
# 3. El bucle con precios simulados
# --------------------------------------------------------------------------- #
def test_un_tick_que_cruza_el_objetivo_cierra_la_posicion(aislado):
    broker = BrokerFalso([_posicion()])
    precios = vp.Precios()
    precios.encajar({"symbol": "MRNA.US", "bid": 111.0, "ask": 111.1})
    quedan = vp.evaluar(broker, {"enviadas": {}}, [_vigilada()], precios)
    assert quedan == [] and broker.vendidas == [("MRNA.US", 3)]


def test_un_tick_que_no_cruza_deja_la_posicion_en_paz(aislado):
    broker = BrokerFalso([_posicion()])
    precios = vp.Precios()
    precios.encajar({"symbol": "MRNA.US", "bid": 100.0, "ask": 100.1})
    quedan = vp.evaluar(broker, {"enviadas": {}}, [_vigilada()], precios)
    assert len(quedan) == 1 and broker.vendidas == []
    assert quedan[0]["bid"] == 100.0


def test_un_tick_roto_no_tumba_el_vigilante():
    precios = vp.Precios()
    for basura in ({}, {"symbol": "X"}, {"bid": 1}, {"symbol": "X", "bid": "eso"}):
        precios.encajar(basura)
    assert precios.bid == {}


def test_solo_se_vigilan_las_posiciones_que_XTB_tiene_de_verdad(monkeypatch):
    """Las heredadas del paper trading nunca existieron en el broker, y una
    posición del broker sin nivel no tiene nada que vigilar."""
    from centinela import estado as est_mod
    monkeypatch.setattr(est_mod, "cargar", lambda: {"posiciones": {config.CARTERA_BROKER: [
        {"ticker": "MRNA", "fecha_entrada": "2026-10-01", "objetivo": 110.0, "stop": 90.0, "id": 1},
        {"ticker": "VIEJA", "fecha_entrada": "2026-09-01", "objetivo": 50.0, "stop": 40.0, "id": 2},
        {"ticker": "SINNIVEL", "fecha_entrada": "2026-10-02", "objetivo": None, "stop": None, "id": 3},
    ]}})
    broker = BrokerFalso([_posicion("MRNA.US"), _posicion("VIEJA.US"),
                          _posicion("SINNIVEL.US"), _posicion("AJENA.US")])
    vigiladas = vp.cargar_vigiladas(broker)
    assert [v["ticker"] for v in vigiladas] == ["MRNA"]


# --------------------------------------------------------------------------- #
# 4. El latido y el relevo
# --------------------------------------------------------------------------- #
def test_el_latido_no_lleva_ni_un_dato_de_sesion():
    """Se publica en una rama pública: cualquiera puede leerlo."""
    d = lat.construir("2026-10-15T09:30:00-04:00", [_vigilada()])
    texto = json.dumps(d, ensure_ascii=False)
    for prohibido in ("tgt", "TGT", "CASTGC", "password", "cookie", "token",
                      str(config.CUENTA_DEMO)):
        assert prohibido not in texto


def test_un_latido_reciente_evita_arrancar_otro_vigilante():
    ahora = datetime(2026, 10, 15, 11, 0, tzinfo=config.TZ_ET)
    reciente = {"cuando": (ahora - timedelta(minutes=2)).isoformat(), "run": "9"}
    arrancar, motivo = hfv.decidir(reciente, ahora, forzado=False)
    assert arrancar is False and "vivo" in motivo


def test_un_latido_vencido_hace_arrancar_otro():
    ahora = datetime(2026, 10, 15, 11, 0, tzinfo=config.TZ_ET)
    viejo = {"cuando": (ahora - timedelta(minutes=30)).isoformat(), "run": "9"}
    arrancar, motivo = hfv.decidir(viejo, ahora, forzado=False)
    assert arrancar is True and "muerto" in motivo


def test_sin_ningun_latido_arranca_uno():
    ahora = datetime(2026, 10, 15, 11, 0, tzinfo=config.TZ_ET)
    arrancar, motivo = hfv.decidir(None, ahora, forzado=False)
    assert arrancar is True and "nadie" in motivo


def test_fuera_de_sesion_no_se_arranca_nada():
    tarde = datetime(2026, 10, 15, 20, 0, tzinfo=config.TZ_ET)
    assert hfv.decidir(None, tarde, forzado=False)[0] is False
    finde = datetime(2026, 10, 17, 11, 0, tzinfo=config.TZ_ET)   # sábado
    assert hfv.decidir(None, finde, forzado=False)[0] is False


def test_el_saliente_no_se_va_hasta_ver_latir_al_sucesor(aislado, monkeypatch):
    """LA CLAVE DE QUE NO HAYA HUECO. Mientras espera al sucesor sigue mirando
    precios, así que aunque el relevo tarde cinco minutos, esos cinco minutos
    están cubiertos."""
    broker = BrokerFalso([_posicion()])
    precios = vp.Precios()
    broker._precios = precios
    vigiladas = [_vigilada()]
    vueltas = {"n": 0}

    def latido_del_sucesor(_trabajo):
        vueltas["n"] += 1
        # Las dos primeras vueltas el sucesor todavía no ha arrancado.
        if vueltas["n"] < 3:
            return {"cuando": datetime.now(config.TZ_ET).isoformat(), "run": "yo"}
        return {"cuando": datetime.now(config.TZ_ET).isoformat(), "run": "sucesor"}

    monkeypatch.setattr(lat, "leer", latido_del_sucesor)
    monkeypatch.setattr(lat, "publicar", lambda *_a, **_k: None)
    monkeypatch.setattr(vp.lat, "leer", latido_del_sucesor)
    monkeypatch.setattr(vp.lat, "publicar", lambda *_a, **_k: None)

    assert vp.esperar_sucesor(aislado, "yo", broker, {"enviadas": {}},
                              vigiladas, "2026-10-15T09:30:00-04:00") is True
    assert broker.bombeos >= 3, "dejó de vigilar mientras esperaba el relevo"


def test_si_el_sucesor_no_llega_el_relevo_se_da_por_fallido(aislado, monkeypatch):
    broker = BrokerFalso([_posicion()])
    broker._precios = vp.Precios()
    monkeypatch.setattr(vp.lat, "leer",
                        lambda _t: {"cuando": datetime.now(config.TZ_ET).isoformat(),
                                    "run": "yo"})
    monkeypatch.setattr(vp.lat, "publicar", lambda *_a, **_k: None)
    monkeypatch.setattr(vp, "ESPERA_SUCESOR_SEG", 0.01)
    assert vp.esperar_sucesor(aislado, "yo", broker, {"enviadas": {}},
                              [_vigilada()], "x") is False


# --------------------------------------------------------------------------- #
# 5. El respaldo cuando el WebSocket se cae
# --------------------------------------------------------------------------- #
def test_si_no_llegan_ticks_se_pregunta_por_peticion(aislado):
    broker = BrokerFalso([_posicion()])
    precios = vp.Precios()
    vigiladas = [_vigilada()]
    vp.respaldo_por_peticion(broker, vigiladas, precios)
    assert precios.bid["MRNA.US"] == 100.0


def test_un_simbolo_mudo_no_para_a_los_demas(aislado):
    class Medio(BrokerFalso):
        def cotizacion(self, simbolo):
            if simbolo == "MALA.US":
                raise RuntimeError("XTB no responde de este símbolo")
            return {"ticker": simbolo, "bid": 100.0, "ask": 100.1, "spread": .1}

    precios = vp.Precios()
    vp.respaldo_por_peticion(Medio(), [_vigilada("MALA"), _vigilada("BUENA")],
                             precios)
    assert precios.bid == {"BUENA.US": 100.0}


# --------------------------------------------------------------------------- #
# 6. Convivencia con el ejecutor de ventas por tiempo
# --------------------------------------------------------------------------- #
def test_la_pagina_sabe_nombrar_las_ventas_en_vivo():
    """El vocabulario de tipos es cerrado justamente para que añadir uno
    obligue a decidir también cómo se pinta."""
    import generar_operativa as go
    for tipo in (ords.VENTA_STOP_INTRADIA, ords.VENTA_OBJETIVO_INTRADIA):
        assert tipo in go.NOMBRE_TIPO and tipo in go.GRUPO_TIPO
        assert go.NOMBRE_TIPO[tipo] != tipo, "se quedó sin traducir"
    # Y no se confunden con las del vistazo diario, que es el punto de tenerlas
    # separadas.
    assert go.NOMBRE_TIPO[ords.VENTA_STOP_INTRADIA] != go.NOMBRE_TIPO[ords.VENTA_STOP]


def test_todos_los_tipos_tienen_nombre_y_grupo():
    import generar_operativa as go
    faltan = [t for t in ords.TIPOS if t not in go.NOMBRE_TIPO or t not in go.GRUPO_TIPO]
    assert not faltan, f"tipos sin pintar: {faltan}"


def test_el_ejecutor_de_ventas_no_vende_lo_que_el_vigilante_ya_cerro(
        aislado, monkeypatch):
    """Los dos corren a la vez al final de la sesión. El que llega segundo tiene
    que encontrarse la posición cerrada y callarse, no mandar una venta de unas
    acciones que ya no existen."""
    import ejecutor_xtb as ej
    broker = BrokerFalso(posiciones=[])        # el vigilante ya la cerró
    orden = ords.Orden(
        id=ords.identificador(HOY, "A", "MRNA", ords.VENTA_TIEMPO),
        tipo=ords.VENTA_TIEMPO, cartera="A", ticker="MRNA", acciones=3,
        sesion=HOY)

    class R(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(f"{HOY}T15:50:00").replace(tzinfo=tz)
    monkeypatch.setattr(ej, "datetime", R)

    pendientes = {"sesion": HOY, "cartera_broker": "A", "ordenes": [orden]}
    ej.enviar(broker, pendientes, "ventas", {"enviadas": {}})
    assert broker.vendidas == [], "mandó una venta de acciones que ya no existían"
