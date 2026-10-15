"""La reposición automática de compras rechazadas, y la regla que la gobierna:
NUNCA una compra doble, NUNCA una señal perdida en silencio.

XTB acepta la compra a mercado en los primeros minutos de sesión (devuelve
número de orden) y la descarta sin dejar posición; a los 20-30 min las mismas
entran. Hasta el 2026-10-12 reponerlas era una casilla manual, y cuando nadie
la marcaba la señal se perdía (STX el 09/10, FICO el 12/10). Ahora el ejecutor
reintenta solo, pero con una condición dura: antes de cada reintento confirma
contra XTB —por OrderService, la única lista con el estado real— que la orden
anterior está muerta y que no hay ni posición ni orden viva del símbolo. Estos
tests fijan ese contrato.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import ambiguas as amb, broker_xtb as bx, ordenes as ords  # noqa: E402
import ejecutor_xtb as ej  # noqa: E402

HOY = "2026-10-12"
APERTURA = __import__("datetime").datetime(2026, 10, 12, 9, 30)
CIERRE = __import__("datetime").datetime(2026, 10, 12, 16, 0)


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
def _pos(ticker, acciones):
    return {"ticker": ticker, "acciones": float(acciones), "lado": "buy",
            "precio_entrada": 10.0, "precio_actual": 10.0, "orden": 1,
            "stop": None, "objetivo": None, "pnl": 0.0}


class BrokerContado:
    """Un broker con posiciones y OrderService fijos (para confirmar/gate)."""

    def __init__(self, posiciones=None, contado=None):
        self._posiciones = posiciones or []
        # contado: lista de dicts {orden, ticker, estado, viva}
        self._contado = contado or []

    def posiciones(self):
        return list(self._posiciones)

    def ordenes_pendientes(self):
        return []

    def resumen_contado(self, simbolo, numero=None):
        simple = simbolo.replace(".US", "").replace("-", ".")
        mias = [o for o in self._contado
                if o["ticker"].replace(".US", "").replace("-", ".") == simple]
        estado = next((o["estado"] for o in mias
                       if numero is not None and str(o["orden"]) == str(numero)), None)
        return {"estado": estado, "vivas": [o for o in mias if o["viva"]],
                "todas": mias, "numero": None if numero is None else str(numero)}


# --------------------------------------------------------------------------- #
# confirmar(): OrderService es la autoridad sobre el estado
# --------------------------------------------------------------------------- #
def test_confirmar_lee_el_rechazo_real_de_OrderService():
    """Sin posición y sin orden en getAllOrders, el estado lo da OrderService:
    REJECTED es rechazada, y con su nombre, no un 'no tiene ni posición ni orden'
    genérico."""
    b = BrokerContado(contado=[{"orden": 919, "ticker": "FICO.US",
                                "estado": "REJECTED", "viva": False}])
    estado, porque, movidas = amb.confirmar(
        b, "FICO.US", acciones_antes=0, dormir=lambda *_: None,
        lado="compra", acciones=2, numero=919)
    assert estado == "rechazada"
    assert "REJECTED" in porque and "919" in porque
    assert movidas == 0


def test_confirmar_da_por_ejecutada_una_orden_FILLED_en_OrderService():
    b = BrokerContado(contado=[{"orden": 920, "ticker": "FICO.US",
                                "estado": "FILLED", "viva": False}])
    estado, porque, _ = amb.confirmar(
        b, "FICO.US", acciones_antes=0, dormir=lambda *_: None,
        lado="compra", acciones=2, numero=920)
    assert estado == "ejecutada"
    assert "FILLED" in porque


def test_confirmar_una_orden_viva_es_en_cola_no_rechazada():
    b = BrokerContado(contado=[{"orden": 921, "ticker": "FICO.US",
                                "estado": "ACCEPTED", "viva": True}])
    estado, _, _ = amb.confirmar(
        b, "FICO.US", acciones_antes=0, dormir=lambda *_: None,
        lado="compra", acciones=2, numero=921)
    assert estado == "en_cola"


# --------------------------------------------------------------------------- #
# listo_para_reponer(): la puerta que impide la compra doble
# --------------------------------------------------------------------------- #
def test_gate_si_ya_hay_posicion_no_se_repone():
    """Una compra que entró tarde deja posición: el gate lo ve y NO repone."""
    b = BrokerContado(posiciones=[_pos("FICO.US", 2)])
    verdicto, _ = amb.listo_para_reponer(b, "FICO.US", 919)
    assert verdicto == amb.YA_ENTRO


def test_gate_si_la_orden_sigue_viva_se_espera():
    b = BrokerContado(contado=[{"orden": 919, "ticker": "FICO.US",
                                "estado": "ACCEPTED", "viva": True}])
    verdicto, _ = amb.listo_para_reponer(b, "FICO.US", 919)
    assert verdicto == amb.ESPERAR_REPOSICION


def test_gate_orden_muerta_y_sin_posicion_se_repone():
    b = BrokerContado(contado=[{"orden": 919, "ticker": "FICO.US",
                                "estado": "REJECTED", "viva": False}])
    verdicto, _ = amb.listo_para_reponer(b, "FICO.US", 919)
    assert verdicto == amb.REPONER


def test_gate_orden_purgada_sin_posicion_ni_viva_se_repone():
    """OrderService purga las muertas: ausente + sin posición + sin orden viva
    es 'muerta', no 'quizá viva'."""
    b = BrokerContado(contado=[])          # la orden ya no está en la lista
    verdicto, _ = amb.listo_para_reponer(b, "FICO.US", 919)
    assert verdicto == amb.REPONER


def test_gate_si_no_se_puede_leer_OrderService_se_espera():
    class BrokerCiego(BrokerContado):
        def resumen_contado(self, *_a, **_k):
            raise RuntimeError("OrderService caído")
    verdicto, motivo = amb.listo_para_reponer(BrokerCiego(), "FICO.US", 919)
    assert verdicto == amb.ESPERAR_REPOSICION
    assert "OrderService" in motivo


def test_gate_un_broker_sin_OrderService_no_repone_a_ciegas():
    class BrokerViejo:
        def posiciones(self):
            return []
    verdicto, _ = amb.listo_para_reponer(BrokerViejo(), "FICO.US", 919)
    assert verdicto == amb.ESPERAR_REPOSICION


# --------------------------------------------------------------------------- #
# reponer_automatico(): el bucle completo
# --------------------------------------------------------------------------- #
class Reloj:
    """Un reloj que avanza `paso_min` cada vez que el bucle 'duerme'."""

    def __init__(self, inicio_min_tras_apertura=5, paso_min=5):
        self.t = APERTURA + __import__("datetime").timedelta(
            minutes=inicio_min_tras_apertura)
        self.paso = __import__("datetime").timedelta(minutes=paso_min)

    def ahora(self):
        return self.t

    def dormir(self, *_a, **_k):
        self.t += self.paso


class BrokerTimeline:
    """La cuenta a lo largo de 'pasos'. `avanzar()` se usa como el `dormir` del
    bucle y de confirmar, así que el mundo cambia justo cuando el bucle espera.

    `plan` es una lista de estados; cada estado = (posiciones_dict, contado_list).
    El paso se queda en el último estado cuando se acaban.
    """

    def __init__(self, plan, al_comprar=None):
        self.plan = plan
        self.paso = 0
        self.compras = []
        self._al_comprar = al_comprar or (lambda b: None)
        self._seq = 9000

    def _estado(self):
        pos, oc = self.plan[min(self.paso, len(self.plan) - 1)]
        return pos, oc

    def avanzar(self, *_a, **_k):
        self.paso += 1

    def posiciones(self):
        pos, _ = self._estado()
        return [_pos(t if t.endswith(".US") else t + ".US", a)
                for t, a in pos.items()]

    def ordenes_pendientes(self):
        return []

    def resumen_contado(self, simbolo, numero=None):
        _, oc = self._estado()
        simple = simbolo.replace(".US", "").replace("-", ".")
        mias = [o for o in oc
                if o["ticker"].replace(".US", "").replace("-", ".") == simple]
        estado = next((o["estado"] for o in mias
                       if numero is not None and str(o["orden"]) == str(numero)), None)
        return {"estado": estado, "vivas": [o for o in mias if o["viva"]],
                "todas": mias, "numero": None if numero is None else str(numero)}

    def comprar(self, simbolo, acciones, objetivo=None, stop=None):
        self.compras.append((simbolo, acciones))
        self._seq += 1
        self._al_comprar(self)
        return bx.Ejecucion(ticker=simbolo, lado="compra", acciones=acciones,
                            estado="en_cola", orden=self._seq)


@pytest.fixture
def entorno_reposicion(tmp_path, monkeypatch):
    """Bitácora, apertura y precios aislados; sin red, sin diario, sin fiabilidad."""
    from centinela import fiabilidad, diario, calendario
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "broker.csv")
    monkeypatch.setattr(ords, "ARCHIVO_ENVIADAS", tmp_path / "enviadas.json")
    monkeypatch.setattr(fiabilidad, "anotar", lambda *a, **k: None)
    monkeypatch.setattr(diario, "anotar", lambda *a, **k: None)
    monkeypatch.setattr(calendario, "apertura_cierre_et",
                        lambda _d: (APERTURA, CIERRE))
    monkeypatch.setattr(ej, "_apertura_del_dia", lambda _t: {"FICO": 660.0})
    # confirmar no debe dormir de verdad cuando una reposición sí envía.
    monkeypatch.setattr(amb, "ESPERA_CONFIRMACION", 0)
    monkeypatch.setattr(amb, "ESPERA_ENTRE_COMPROBACIONES", 0)
    # Por defecto, todo símbolo existe en XTB; un test lo cambia para FICO.
    monkeypatch.setattr(ej.disp, "disponible", lambda *a, **k: True)
    return tmp_path


def _pendientes_fico(acciones=2):
    o = ords.Orden(id=ords.identificador(HOY, "A", "FICO", ords.COMPRA),
                   tipo=ords.COMPRA, cartera="A", ticker="FICO",
                   acciones=acciones, sesion=HOY, objetivo=773.0, stop=587.0,
                   precio_simulador=667.0)
    return {"sesion": HOY, "cartera_broker": "A", "ordenes": [o]}


def _fila_rechazada(tmp_path, orden_xtb=919):
    """Deja en la bitácora la compra que XTB rechazó a primera hora."""
    o = ords.Orden(id=ords.identificador(HOY, "A", "FICO", ords.COMPRA),
                   tipo=ords.COMPRA, cartera="A", ticker="FICO", acciones=2,
                   sesion=HOY, precio_simulador=667.0)
    e = bx.Ejecucion(ticker="FICO", lado="compra", acciones=2,
                     estado="rechazada", orden=orden_xtb,
                     error="XTB marcó la orden REJECTED")
    ords.registrar_ejecucion(o, e)


def test_una_compra_que_entra_TARDE_no_provoca_una_segunda_compra(entorno_reposicion):
    """EL CASO QUE NO PUEDE PASAR. La orden original fue aceptada y sigue viva;
    el bucle de reposición NO debe reenviar mientras pueda ejecutarse, y cuando
    por fin aparece la posición, tampoco. Resultado: cero compras nuevas."""
    _fila_rechazada(entorno_reposicion, orden_xtb=919)
    reloj = Reloj(inicio_min_tras_apertura=5, paso_min=5)
    # Paso 0: la orden 919 sigue VIVA (ACCEPTED), sin posición -> esperar.
    # Paso 1: se ejecutó tarde -> hay posición de FICO.
    broker = BrokerTimeline(plan=[
        ({}, [{"orden": 919, "ticker": "FICO.US", "estado": "ACCEPTED", "viva": True}]),
        ({"FICO": 2}, [{"orden": 919, "ticker": "FICO.US", "estado": "FILLED", "viva": False}]),
    ])
    registro = {"enviadas": {}}
    problemas = ej.reponer_automatico(
        broker, _pendientes_fico(), registro,
        dormir=lambda s: (reloj.dormir(), broker.avanzar()),
        ahora_fn=reloj.ahora)
    assert broker.compras == []            # NUNCA una segunda compra
    assert problemas == []                  # la posición entró: no hay problema


def test_la_reposicion_reenvia_cuando_la_orden_esta_muerta(entorno_reposicion):
    """Orden previa muerta y sin posición: se repone, y cuando la reposición
    entra, el bucle termina sin reenviar de más."""
    _fila_rechazada(entorno_reposicion, orden_xtb=919)
    reloj = Reloj(inicio_min_tras_apertura=10, paso_min=5)

    def al_comprar(b):
        # La reposición entra: el siguiente estado tiene la posición.
        b.plan.append(({"FICO": 2}, []))
        b.paso = len(b.plan) - 1

    broker = BrokerTimeline(
        plan=[({}, [{"orden": 919, "ticker": "FICO.US",
                     "estado": "REJECTED", "viva": False}])],
        al_comprar=al_comprar)
    registro = {"enviadas": {}}
    problemas = ej.reponer_automatico(
        broker, _pendientes_fico(), registro,
        dormir=lambda s: (reloj.dormir(), broker.avanzar()),
        ahora_fn=reloj.ahora)
    assert len(broker.compras) == 1        # una sola reposición
    assert problemas == []
    filas = [f for f in ords.filas_de_sesion(HOY)
             if f["tipo"] == ords.ENTRADA_TARDIA]
    assert filas and filas[-1]["id"].endswith("|r1")   # id propio del reintento


def test_la_reposicion_se_agota_a_los_60_min_y_es_rojo_documentado(entorno_reposicion):
    """XTB sigue rechazando pasada la primera hora: la sesión se pierde, pero
    DOCUMENTADA (rojo), nunca en silencio."""
    _fila_rechazada(entorno_reposicion, orden_xtb=919)
    reloj = Reloj(inicio_min_tras_apertura=5, paso_min=20)   # pasa de 60 pronto
    # Siempre muerta, nunca posición: cada reposición vuelve a morir.
    broker = BrokerTimeline(
        plan=[({}, [{"orden": 919, "ticker": "FICO.US",
                     "estado": "REJECTED", "viva": False}])])
    registro = {"enviadas": {}}
    problemas = ej.reponer_automatico(
        broker, _pendientes_fico(), registro,
        dormir=lambda s: (reloj.dormir(), broker.avanzar()),
        ahora_fn=reloj.ahora)
    assert problemas                        # hay rojo
    assert any("FICO" in p and "reposición agotada" in p for p in problemas)


def test_un_simbolo_que_no_existe_en_XTB_no_se_reintenta(entorno_reposicion, monkeypatch):
    """COHR el 13/10: un error PERMANENTE (símbolo inexistente) no se reintenta.
    La reposición lo saca de las decididas y no manda ni una orden, ni lo deja
    como 'reposición agotada' (rojo): es un descarte, no un fallo del broker."""
    _fila_rechazada(entorno_reposicion, orden_xtb=919)
    monkeypatch.setattr(ej.disp, "disponible",
                        lambda broker, simbolo, *a, **k: simbolo != "FICO.US")
    reloj = Reloj(inicio_min_tras_apertura=5, paso_min=20)
    broker = BrokerTimeline(
        plan=[({}, [{"orden": 919, "ticker": "FICO.US",
                     "estado": "REJECTED", "viva": False}])])
    problemas = ej.reponer_automatico(
        broker, _pendientes_fico(), {"enviadas": {}},
        dormir=lambda s: (reloj.dormir(), broker.avanzar()),
        ahora_fn=reloj.ahora)
    assert broker.compras == [], "no debió reintentar un símbolo inexistente"
    assert problemas == [], "un descarte no es un rojo de reposición agotada"


# --------------------------------------------------------------------------- #
# Reconexión: la sesión se cae en el sueño y el bucle la recupera
# --------------------------------------------------------------------------- #
class BrokerConCaida(BrokerTimeline):
    """Como BrokerTimeline, pero la sesión está caída los primeros `caidas`
    ciclos (asegurar_conexion devuelve False) y luego se recupera."""

    def __init__(self, plan, caidas=1, al_comprar=None):
        super().__init__(plan, al_comprar=al_comprar)
        self.caidas = caidas
        self.intentos_conexion = 0

    def asegurar_conexion(self):
        self.intentos_conexion += 1
        return self.intentos_conexion > self.caidas


def test_una_caida_de_sesion_no_mata_la_reposicion(entorno_reposicion):
    """EL CASO DEL 15/10. La sesión se cayó en el primer sueño y la reposición
    se quedó 'esperando' hasta agotar la hora: 0 compras. Ahora reconecta: el
    ciclo sin conexión se salta, y cuando vuelve, repone."""
    _fila_rechazada(entorno_reposicion, orden_xtb=919)
    reloj = Reloj(inicio_min_tras_apertura=5, paso_min=5)

    def al_comprar(b):
        b.plan.append(({"FICO": 2}, []))     # la reposición entra
        b.paso = len(b.plan) - 1

    broker = BrokerConCaida(
        plan=[({}, [{"orden": 919, "ticker": "FICO.US",
                     "estado": "REJECTED", "viva": False}])],
        caidas=1, al_comprar=al_comprar)
    problemas = ej.reponer_automatico(
        broker, _pendientes_fico(), {"enviadas": {}},
        dormir=lambda s: (reloj.dormir(), broker.avanzar()),
        ahora_fn=reloj.ahora)
    assert broker.intentos_conexion >= 2, "debió reintentar la conexión"
    assert len(broker.compras) == 1, "tras reconectar, repuso una vez"
    assert problemas == []


def test_sin_conexion_toda_la_hora_es_rojo_no_bucle_infinito(entorno_reposicion):
    """Si la sesión no vuelve nunca, el tope de 60 min se respeta igual: rojo
    documentado, no un bucle infinito reintentando reconectar."""
    reloj = Reloj(inicio_min_tras_apertura=5, paso_min=20)   # pasa de 60 pronto
    broker = BrokerConCaida(
        plan=[({}, [{"orden": 919, "ticker": "FICO.US",
                     "estado": "REJECTED", "viva": False}])],
        caidas=999)                           # nunca reconecta
    problemas = ej.reponer_automatico(
        broker, _pendientes_fico(), {"enviadas": {}},
        dormir=lambda s: (reloj.dormir(), broker.avanzar()),
        ahora_fn=reloj.ahora)
    assert broker.compras == []
    assert problemas and any("agotada" in p for p in problemas)


# --------------------------------------------------------------------------- #
# El motivo literal del stream de OrderService
# --------------------------------------------------------------------------- #
class BrokerStream:
    def __init__(self, eventos):
        self._eventos = eventos

    def eventos_contado(self, segundos):
        return self._eventos


def test_motivo_contado_captura_la_secuencia_de_estados():
    """El motivo deja de ser deducido: es lo que XTB emite en el stream."""
    b = BrokerStream([
        {"event": "SNAPSHOT", "orders": [{"order_id": 919, "status": "ACCEPTED"}]},
        {"event": "UPDATE", "orders": [{"order_id": 919, "status": "REJECTED"}]},
    ])
    estado, motivo = ej._motivo_contado(b, "FICO.US", 919, segundos=0)
    assert estado == "REJECTED"
    assert "ACCEPTED -> REJECTED" in motivo


def test_motivo_contado_incluye_el_mensaje_grpc():
    b = BrokerStream([
        {"event": "SNAPSHOT", "orders": [{"order_id": 919, "status": "ACCEPTED"}]},
        {"event": "CIERRE", "orders": [], "grpc_message": "market is closed"},
    ])
    _, motivo = ej._motivo_contado(b, "FICO.US", 919, segundos=0)
    assert "grpc: market is closed" in motivo


def test_motivo_contado_dice_cuando_la_orden_ni_aparece():
    b = BrokerStream([{"event": "SNAPSHOT", "orders": []}])
    estado, motivo = ej._motivo_contado(b, "FICO.US", 919, segundos=0)
    assert estado is None and "purgada" in motivo


def test_enviar_resolviendo_registra_el_motivo_literal(entorno_reposicion):
    """El motivo que llega a la bitácora deja de ser deducido ('no tiene ni
    posición ni orden') y pasa a ser lo que XTB dijo en el stream."""
    b = BrokerContado(contado=[{"orden": 777, "ticker": "FICO.US",
                                "estado": "REJECTED", "viva": False}])
    escuchar = lambda broker, simbolo, numero: (
        "REJECTED", "OrderService: PENDING_NEW -> ACCEPTED -> REJECTED")
    e = amb.enviar_resolviendo(
        b, "FICO.US", ords.COMPRA,
        lambda: bx.Ejecucion(ticker="FICO.US", lado="compra", acciones=2,
                             estado="en_cola", orden=777),
        dormir=lambda *_: None, id_orden="2026-10-15|A|FICO|compra",
        escuchar_contado=escuchar)
    assert e.estado == "rechazada"
    assert "ACCEPTED -> REJECTED" in (e.error or "")
