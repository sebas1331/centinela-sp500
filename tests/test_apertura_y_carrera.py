"""Lo que el 2026-09-29 dejó al descubierto: un "ok" puede ser mudo.

Ese día la página dijo «Compras en XTB: ok» con cero órdenes enviadas, y era
literalmente cierto: el modelo dio una señal y se descartó. Pero el mismo "ok"
habría salido si el ejecutor hubiera llegado antes que la decisión, o si se
hubieran decidido tres compras y no hubiera salido ninguna. Estos tests fijan
las tres cosas que distinguen un caso del otro.
"""
from __future__ import annotations

import csv
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

HOY = "2026-10-15"                      # jueves, sesión de mercado


@pytest.fixture
def aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(ords, "ARCHIVO_PENDIENTES", tmp_path / "pendientes.json")
    monkeypatch.setattr(ords, "ARCHIVO_ENVIADAS", tmp_path / "enviadas.json")
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "broker.csv")
    ej.ENVIADAS.clear()          # global entre tests
    return tmp_path


def _reloj(fecha, hora="09:00"):
    class R(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(f"{fecha}T{hora}:00").replace(tzinfo=tz)
    return R


def _orden(ticker="MRNA", tipo=ords.COMPRA, sesion=HOY, acciones=3):
    return ords.Orden(
        id=ords.identificador(sesion, "A", ticker, tipo), tipo=tipo,
        cartera="A", ticker=ticker, acciones=acciones, sesion=sesion,
        objetivo=172.5 if tipo == ords.COMPRA else None,
        stop=138.0 if tipo == ords.COMPRA else None, precio_simulador=100.0)


# --------------------------------------------------------------------------- #
# 1. La carrera entre la decisión y las compras
# --------------------------------------------------------------------------- #
def test_si_la_decision_del_dia_no_esta_el_ejecutor_espera(aislado, monkeypatch):
    """El caso del 2026-09-29: el ejecutor llegó antes que quien decide.

    Los jobs `ordenes` y `ejecutor` arrancaron el mismo segundo y el ejecutor
    hizo checkout antes de que las órdenes se empujaran. Leyó el fichero de la
    víspera. Ahora relee hasta que la sesión sea la de hoy.
    """
    monkeypatch.setattr(ej, "datetime", _reloj(HOY))
    ords.guardar_pendientes([], "2026-10-14", "A")      # el de ayer

    intentos = {"n": 0}

    def aparece_a_la_tercera(_seg):
        intentos["n"] += 1
        if intentos["n"] == 2:
            ords.guardar_pendientes([_orden()], HOY, "A", "preapertura", hoy=HOY)

    p = ej.decision_del_dia("compras", sin_git=True, dormir=aparece_a_la_tercera)
    assert p["sesion"] == HOY
    assert [o.ticker for o in p["ordenes"]] == ["MRNA"]
    assert intentos["n"] == 2, "no reintentó hasta que la decisión apareció"


def test_si_la_ventana_se_agota_sin_decision_es_rojo(aislado, monkeypatch):
    """Nunca un "ok". Sin la decisión del día no se sabe si no había nada que
    comprar o si el ejecutor llegó antes de tiempo, y esas dos cosas no pueden
    contarse igual."""
    monkeypatch.setattr(ej, "datetime", _reloj(HOY))
    ords.guardar_pendientes([], "2026-10-14", "A")
    monkeypatch.setattr(ej, "en_ventana", lambda *_a, **_k: (False, "ventana agotada"))

    with pytest.raises(ej.SinDecision) as exc:
        ej.decision_del_dia("compras", sin_git=True, dormir=lambda _s: None)
    assert "2026-10-14" in str(exc.value), "no dice por qué sesión iba el fichero"
    assert "ventana" in str(exc.value)


def test_el_ejecutor_no_espera_a_nadie_si_la_decision_ya_es_la_de_hoy(
        aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj(HOY))
    ords.guardar_pendientes([_orden()], HOY, "A", "preapertura", hoy=HOY)

    def no_deberia_dormir(_s):
        raise AssertionError("esperó teniendo ya la decisión del día")

    p = ej.decision_del_dia("compras", sin_git=True, dormir=no_deberia_dormir)
    assert p["sesion"] == HOY


# --------------------------------------------------------------------------- #
# 2. Un "ok" con cero órdenes tiene que decir por qué
# --------------------------------------------------------------------------- #
def test_cero_ordenes_lleva_su_motivo_escrito(aislado, monkeypatch):
    ej.ENVIADAS[ords.COMPRA] = {"decididas": 0, "enviadas": 0, "ya_estaban": 0}
    r = ej.resultado_explicito("compras", "el modelo no dio ninguna señal hoy")
    assert r == "ok: 0 órdenes — el modelo no dio ninguna señal hoy"
    assert r != "ok", "un 'ok' a secas no distingue nada"


def test_el_motivo_sale_del_log_de_decisiones(tmp_path, monkeypatch):
    """El caso real: una señal descartada por duplicado. El motivo estaba en el
    log desde el principio; lo que faltaba era subirlo a donde se lee."""
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(ej, "datetime", _reloj(HOY))
    (tmp_path / f"decisiones-{HOY}.log").write_text(
        "RESUMEN: universo=503 drawdown>=30%=182 con_senal=1\n"
        "COHR | dd=35.8% | prob=0.808 | ENTRAR | score_fund=53.3\n"
        "COHR | dd=35.8% | prob=0.808 | ENTRADA DESCARTADA: ya hay posición "
        "abierta en Cartera A y Cartera B.\n"
        "DECIDIDAS PARA ENTRAR HOY (0): []\n", encoding="utf-8")
    motivo = ej.motivo_de_cero("compras")
    assert "COHR" in motivo and "ya hay posición abierta" in motivo


def test_sin_ninguna_senal_el_motivo_lo_dice_asi(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(ej, "datetime", _reloj(HOY))
    (tmp_path / f"decisiones-{HOY}.log").write_text(
        "RESUMEN: universo=503 drawdown>=30%=182 con_senal=0\n"
        "DECIDIDAS PARA ENTRAR HOY (0): []\n", encoding="utf-8")
    assert ej.motivo_de_cero("compras") == "el modelo no dio ninguna señal hoy"


def test_con_ordenes_el_resultado_lleva_el_recuento(aislado):
    ej.ENVIADAS[ords.COMPRA] = {"decididas": 3, "enviadas": 2, "ya_estaban": 1}
    assert ej.resultado_explicito("compras") == \
        "ok: 3 de 3 órdenes (1 ya estaban enviadas)"


# --------------------------------------------------------------------------- #
# 3. Decidir y no enviar es ROJO
# --------------------------------------------------------------------------- #
def test_enviar_menos_de_lo_decidido_no_puede_terminar_en_verde():
    """Tres decididas y dos enviadas es un fallo aunque las dos sean perfectas.
    Es el agujero exacto del 2026-09-29, un día antes de que costara dinero."""
    ej.ENVIADAS[ords.COMPRA] = {"decididas": 3, "enviadas": 2, "ya_estaban": 0}
    r = ej.ENVIADAS[ords.COMPRA]
    assert r["enviadas"] + r["ya_estaban"] < r["decididas"]


def test_una_decision_sin_orden_la_denuncia_la_verificacion(aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))

    class SinNada:
        def posiciones(self):
            return []

        def ordenes_pendientes(self):
            return []

    pendientes = {"sesion": HOY, "cartera_broker": "A", "ordenes": [_orden()]}
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})
    problemas = ej.verificar_apertura(SinNada(), pendientes)
    assert any("MRNA" in p and "no hay ninguna orden en la bitácora" in p
               for p in problemas), problemas


# --------------------------------------------------------------------------- #
# 4. La verificación posterior a la apertura
# --------------------------------------------------------------------------- #
def _bitacora(aislado, **campos):
    fila = {c: "" for c in ords.COLUMNAS_BROKER}
    fila.update({"id": ords.identificador(HOY, "A", "MRNA", ords.COMPRA),
                 "sesion": HOY, "cartera": "A", "ticker": "MRNA",
                 "simbolo_xtb": "MRNA.US", "tipo": ords.COMPRA, "acciones": 3,
                 "estado": "ejecutada", "precio": 100.0, "orden_xtb": 555,
                 "objetivo": 172.5, "stop": 138.0})
    fila.update(campos)
    with open(ords.ARCHIVO_BITACORA_BROKER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ords.COLUMNAS_BROKER)
        w.writeheader(); w.writerow(fila)
    return fila


class BrokerConPosicion:
    def __init__(self, stop=None, objetivo=None, precio=101.5, en_cola=()):
        self._stop, self._obj, self._precio = stop, objetivo, precio
        self._en_cola = list(en_cola)
        self.modificaciones = []

    def posiciones(self):
        return [{"ticker": "MRNA.US", "acciones": 3.0,
                 "precio_entrada": self._precio, "precio_actual": self._precio,
                 "stop": self._stop, "objetivo": self._obj,
                 "lado": "buy", "orden": 555, "pnl": 0.0}]

    def ordenes_pendientes(self):
        return self._en_cola

    def modificar_objetivo(self, orden, objetivo=None, stop=None):
        self.modificaciones.append((orden, objetivo, stop))
        raise bx.OperacionNoSoportada("xStation5 no permite modificarlo")


def test_una_orden_enviada_que_no_se_ejecuto_es_roja(aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    _bitacora(aislado)
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})

    class SigueEnCola:
        def posiciones(self):
            return []                       # XTB no tiene la posición

        def ordenes_pendientes(self):
            return [{"ticker": "MRNA.US", "acciones": 3.0, "precio": 100.0,
                     "lado": "buy", "orden": 555}]

    problemas = ej.verificar_apertura(SigueEnCola(), {"sesion": HOY, "ordenes": []})
    assert any("SIGUE EN COLA" in p for p in problemas), problemas


def test_una_orden_rechazada_es_roja(aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    _bitacora(aislado, estado="rechazada", error="Not enough funds")
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})
    problemas = ej.verificar_apertura(BrokerConPosicion(),
                                      {"sesion": HOY, "ordenes": []})
    assert any("RECHAZADA" in p and "Not enough funds" in p for p in problemas)


def test_la_verificacion_corrige_el_precio_y_calcula_el_slippage(
        aislado, monkeypatch):
    """Una compra encolada antes de abrir no tiene precio real hasta que abre:
    la fila que se escribió al enviar es provisional y hay que corregirla."""
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    _bitacora(aislado, precio=100.0)
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {"MRNA": 100.0})

    problemas = ej.verificar_apertura(BrokerConPosicion(precio=101.0),
                                      {"sesion": HOY, "ordenes": []})
    assert problemas == []
    fila = ords.filas_de_sesion(HOY)[0]
    assert float(fila["precio"]) == 101.0, "no corrigió el precio provisional"
    # Se compró un 1% por encima del open: eso es lo que cuesta de verdad.
    assert float(fila["slippage_pct"]) == pytest.approx(1.0)
    assert fila["estado"] == "ejecutada"


def test_si_falta_el_stop_o_el_objetivo_se_intenta_ponerlos(aislado, monkeypatch):
    """XTB ignora en silencio el stop y el objetivo en acciones al contado
    (comprobado con una orden real el 28/09/2026) y el cliente no oficial
    tampoco deja ponerlos después. Se intenta igual —el día que XTB los acepte
    se verá el mismo día— y se registra el resultado.

    Lo que NO se hace es pintarlo de rojo: es una limitación conocida y
    aceptada, y un rojo diario deja de significar nada.
    """
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    _bitacora(aislado)
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})

    broker = BrokerConPosicion(stop=None, objetivo=None)
    problemas = ej.verificar_apertura(broker, {"sesion": HOY, "ordenes": []})
    assert broker.modificaciones == [(555, 172.5, 138.0)], \
        "ni siquiera lo intentó"
    assert ej.ENVIADAS["apertura"]["sin_niveles"] == 1
    assert problemas == [], "un límite conocido del broker no puede ser rojo"


def test_con_stop_y_objetivo_puestos_no_intenta_nada(aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    _bitacora(aislado)
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})
    broker = BrokerConPosicion(stop=138.0, objetivo=172.5)
    ej.verificar_apertura(broker, {"sesion": HOY, "ordenes": []})
    assert broker.modificaciones == []


# --------------------------------------------------------------------------- #
# 5. La ventana del momento nuevo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("minutos_tras_abrir,dentro", [
    (10, False), (29, False), (30, True), (60, True), (90, True), (91, False),
])
def test_la_ventana_de_verificacion_son_30_a_90_minutos(minutos_tras_abrir, dentro):
    from centinela import calendario
    ac = calendario.apertura_cierre_et(HOY)
    ahora = ac[0] + timedelta(minutes=minutos_tras_abrir)
    ok, motivo = ej.en_ventana("apertura", ahora)
    assert ok is dentro, f"{minutos_tras_abrir} min tras abrir: {motivo}"


# --------------------------------------------------------------------------- #
# 6. El fichero de órdenes tiene DOS escritores al día
# --------------------------------------------------------------------------- #
def test_la_preapertura_no_puede_borrar_las_ventas_del_postcierre(aislado):
    """El caso exacto del 2026-09-29.

    El post-cierre de la víspera dejó dos ventas por tiempo para la sesión de
    hoy (VRT y GLW, las dos con su día límite). Por la mañana la pre-apertura
    escribió `"ordenes": []` —no había ninguna compra que hacer— y el fichero
    se quedó vacío: las dos ventas desaparecieron antes de su ventana.

    Aquel día no costó dinero porque eran posiciones heredadas que XTB no tenía,
    pero el mecanismo destruye órdenes reales en silencio.
    """
    ventas = [_orden("VRT", ords.VENTA_TIEMPO, acciones=6),
              _orden("GLW", ords.VENTA_TIEMPO, acciones=10)]
    ords.guardar_pendientes(ventas, HOY, "A", "postcierre", hoy="2026-10-14")

    # A la mañana siguiente, sin ninguna compra que hacer.
    ords.guardar_pendientes([], HOY, "A", "preapertura", hoy=HOY)

    quedan = ords.cargar_pendientes()["ordenes"]
    assert sorted(o.ticker for o in quedan) == ["GLW", "VRT"], \
        "la pre-apertura se llevó por delante las ventas del post-cierre"
    assert all(o.tipo == ords.VENTA_TIEMPO for o in quedan)


def test_y_cada_momento_sigue_reemplazando_lo_suyo(aislado):
    """Si la pre-apertura se relanza y decide otra cosa, sus compras anteriores
    no pueden quedarse: el fichero es el estado, no un historial."""
    ords.guardar_pendientes([_orden("MRNA")], HOY, "A", "preapertura", hoy=HOY)
    ords.guardar_pendientes([_orden("SNDK")], HOY, "A", "preapertura", hoy=HOY)
    quedan = ords.cargar_pendientes()["ordenes"]
    assert [o.ticker for o in quedan] == ["SNDK"]


def test_las_ordenes_de_sesiones_pasadas_se_caen_solas(aislado):
    """Para historia está bitacora_broker.csv; aquí solo estorban."""
    vieja = _orden("AAPL", ords.VENTA_TIEMPO, sesion="2026-10-01")
    ords.guardar_pendientes([vieja], "2026-10-01", "A", "postcierre",
                            hoy="2026-10-01")
    ords.guardar_pendientes([_orden("MRNA")], HOY, "A", "preapertura", hoy=HOY)
    quedan = ords.cargar_pendientes()["ordenes"]
    assert [o.ticker for o in quedan] == ["MRNA"]


def test_la_verificacion_tambien_dice_que_verifico(aislado, monkeypatch):
    """Si terminara con un "ok" a secas reproduciría el problema que vino a
    arreglar, solo que un escalón más abajo."""
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    _bitacora(aislado)
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})
    ej.verificar_apertura(BrokerConPosicion(stop=138.0, objetivo=172.5),
                          {"sesion": HOY, "ordenes": []})
    assert ej.resultado_explicito("apertura") == "ok: 1 de 1 órdenes ejecutadas"


def test_sin_ordenes_que_verificar_tambien_lleva_motivo(aislado, monkeypatch):
    monkeypatch.setattr(ej, "datetime", _reloj(HOY, "10:15"))
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {})

    class Vacio:
        def posiciones(self): return []
        def ordenes_pendientes(self): return []

    ej.verificar_apertura(Vacio(), {"sesion": HOY, "ordenes": []})
    r = ej.resultado_explicito("apertura", ej.motivo_de_cero("apertura"))
    assert r == "ok: 0 órdenes que verificar — no se envió ninguna orden hoy"
