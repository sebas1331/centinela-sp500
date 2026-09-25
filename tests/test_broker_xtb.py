"""Tests de la capa del broker con un cliente de XTB SIMULADO. Sin red.

Lo que se prueba aquí no es que la librería funcione —eso solo lo dice una
prueba real contra la demo— sino que NUESTRA capa se comporta cuando la
librería devuelve cada cosa que puede devolver. Sobre todo el candado: es la
única pieza cuyo fallo cuesta dinero de verdad.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import broker_xtb as bx  # noqa: E402


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
class _Resultado:
    def __init__(self, status, price=None, order_number=None, error=None):
        self.status = status
        self.price = price
        self.order_number = order_number
        self.error = error


class _WS:
    def __init__(self, url):
        self.url = url


class _ClienteFalso:
    """Imita lo justo de XTBClient: lo que nuestra capa toca, y nada más."""

    def __init__(self, *, url="wss://api5demoa.x-station.eu/v1/xstation",
                 cuenta=12345678, resultado=None):
        self.ws = _WS(url)
        self.account_number = cuenta
        self.conectado = False
        self.enviadas = []
        self._resultado = resultado or _Resultado("FILLED", price=101.5,
                                                  order_number=999)

    async def connect(self):
        self.conectado = True

    async def disconnect(self):
        self.conectado = False

    async def get_balance(self):
        class B:
            balance, equity, free_margin = 50000.0, 50250.0, 44000.0
            currency, account_number = "USD", 12345678
        return B()

    async def get_positions(self):
        class P:
            symbol, volume, open_price, current_price = "AAPL.US", 3.0, 100.0, 105.0
            stop_loss, take_profit, side = 92.0, 115.0, "buy"
            order_id, profit_net = "abc", 15.0
        return [P()]

    async def get_orders(self):
        return []

    async def buy(self, symbol, volume, stop_loss=None, take_profit=None):
        self.enviadas.append(("buy", symbol, volume, stop_loss, take_profit))
        return self._resultado

    async def sell(self, symbol, volume, stop_loss=None, take_profit=None):
        self.enviadas.append(("sell", symbol, volume, stop_loss, take_profit))
        return self._resultado

    async def cancel_order(self, numero):
        self.enviadas.append(("cancel", numero))
        return _Resultado("CANCELLED")


CRED = bx.Credenciales(email="x@y.z", cuenta=12345678, password="secreta")


def _broker(**kw):
    return bx.BrokerXTB(CRED, cliente=_ClienteFalso(**kw))


# --------------------------------------------------------------------------- #
# 1. EL CANDADO — lo único cuyo fallo cuesta dinero de verdad
# --------------------------------------------------------------------------- #
def test_conecta_contra_el_endpoint_de_demo():
    b = _broker()
    b.conectar()
    assert b.saldo()["cuenta"] == 12345678
    b.desconectar()


def test_un_endpoint_REAL_aborta_sin_enviar_nada():
    """El default de la librería es 'real'. Si algo lo resolviera así, aquí muere."""
    b = _broker(url="wss://api5reala.x-station.eu/v1/xstation")
    with pytest.raises(bx.CuentaNoDemo, match="no es de demo"):
        b.conectar()


def test_un_endpoint_ILEGIBLE_tambien_aborta():
    """"No se pudo determinar" cuenta como fallo, no como permiso."""
    b = _broker(url="")
    with pytest.raises(bx.CuentaNoDemo):
        b.conectar()


def test_si_la_cuenta_conectada_no_es_la_esperada_aborta():
    """Protege del caso en que el mismo login tenga varias cuentas."""
    b = _broker(cuenta=99999999)
    with pytest.raises(bx.CuentaNoDemo, match="99999999"):
        b.conectar()


def test_no_se_puede_pedir_una_cuenta_real_ni_a_proposito():
    b = bx.BrokerXTB(CRED, demo=False, cliente=_ClienteFalso())
    with pytest.raises(bx.CuentaNoDemo, match="solo opera en DEMO"):
        b.conectar()


def test_el_candado_corre_en_cada_conexion():
    """Una reconexión no puede heredar la verificación de la anterior."""
    b = _broker()
    b.conectar()
    b.desconectar()
    b._cliente.ws.url = "wss://api5reala.x-station.eu/v1/xstation"
    with pytest.raises(bx.CuentaNoDemo):
        b.conectar()


# --------------------------------------------------------------------------- #
# 2. Volumen entero
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("malo", [1.5, 0.4, "2", None, True])
def test_rechaza_cualquier_volumen_que_no_sea_un_entero(malo):
    """El cliente hace int(v+0.5) por su cuenta: 1.6 se convertiría en 2 acciones.

    Un redondeo AL ALZA silencioso rompe el tamaño de posición, que es lo único
    que mantiene el riesgo acotado. Aquí solo entran enteros ya calculados.
    """
    b = _broker(); b.conectar()
    with pytest.raises(bx.ErrorBroker, match="entero"):
        b.comprar("AAPL.US", malo)


def test_rechaza_cero_acciones_con_un_mensaje_util():
    b = _broker(); b.conectar()
    with pytest.raises(bx.ErrorBroker, match="una acción"):
        b.comprar("AAPL.US", 0)


def test_una_compra_valida_llega_con_su_stop_y_su_objetivo():
    b = _broker(); b.conectar()
    e = b.comprar("AAPL.US", 3, objetivo=120.0, stop=92.0)
    assert b._cliente.enviadas == [("buy", "AAPL.US", 3, 92.0, 120.0)]
    assert e.estado == "ejecutada" and e.ok
    assert e.precio == 101.5 and e.orden == 999


# --------------------------------------------------------------------------- #
# 3. Traducción de desenlaces
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status,esperado,ok", [
    ("FILLED", "ejecutada", True),
    ("QUEUED", "en_cola", True),
    ("REJECTED", "rechazada", False),
    ("AMBIGUOUS", "ambigua", False),
    ("INSUFFICIENT_VOLUME", "rechazada", False),
    ("TIMEOUT", "ambigua", False),
    ("LO_QUE_SEA", "ambigua", False),
])
def test_cada_desenlace_del_cliente_tiene_su_traduccion(status, esperado, ok):
    """Un estado desconocido se trata como AMBIGUO, nunca como rechazado.

    La diferencia importa: "rechazada" invita a reenviar la orden, y si en
    realidad se había enviado, se compraría dos veces.
    """
    b = bx.BrokerXTB(CRED, cliente=_ClienteFalso(resultado=_Resultado(status)))
    b.conectar()
    e = b.comprar("AAPL.US", 1)
    assert e.estado == esperado
    assert e.ok is ok


def test_una_orden_en_cola_cuenta_como_enviada():
    """Mercado cerrado: XTB la guarda y la ejecuta al abrir. Es lo que queremos.

    Si `en_cola` no contara como enviada, el siguiente peldaño de la escalera
    volvería a mandarla y se comprarían dos veces las mismas acciones.
    """
    b = bx.BrokerXTB(CRED, cliente=_ClienteFalso(
        resultado=_Resultado("QUEUED", order_number=4242)))
    b.conectar()
    e = b.comprar("AAPL.US", 2)
    assert e.estado == "en_cola" and e.ok
    assert e.orden == 4242


# --------------------------------------------------------------------------- #
# 4. Lo que NO se puede hacer se dice en voz alta
# --------------------------------------------------------------------------- #
def test_modificar_el_objetivo_falla_explicitamente():
    """Un hueco silencioso se notaría el día que alguien asuma que sí se hizo."""
    b = _broker(); b.conectar()
    with pytest.raises(bx.OperacionNoSoportada, match="take profit"):
        b.modificar_objetivo("AAPL.US", 130.0)


def test_cerrar_una_posicion_es_vender_el_mismo_volumen():
    b = _broker(); b.conectar()
    e = b.vender("AAPL.US", 3)
    assert b._cliente.enviadas == [("sell", "AAPL.US", 3, None, None)]
    assert e.lado == "venta"


# --------------------------------------------------------------------------- #
# 5. Lectura
# --------------------------------------------------------------------------- #
def test_saldo_y_posiciones_salen_en_el_vocabulario_de_este_repo():
    """Si mañana cambia la librería, esto es lo que NO puede cambiar."""
    b = _broker(); b.conectar()
    s = b.saldo()
    assert set(s) == {"saldo", "equity", "margen_libre", "divisa", "cuenta"}
    assert s["saldo"] == 50000.0 and s["divisa"] == "USD"

    p = b.posiciones()[0]
    assert set(p) == {"ticker", "acciones", "precio_entrada", "precio_actual",
                      "stop", "objetivo", "lado", "orden", "pnl"}
    assert p["ticker"] == "AAPL.US" and p["acciones"] == 3.0


def test_el_contexto_desconecta_siempre():
    cliente = _ClienteFalso()
    with bx.BrokerXTB(CRED, cliente=cliente):
        assert cliente.conectado
    assert not cliente.conectado


def test_las_credenciales_no_se_imprimen():
    """Un repr con la contraseña dentro acaba en un log cualquier día."""
    assert "secreta" not in repr(CRED)
