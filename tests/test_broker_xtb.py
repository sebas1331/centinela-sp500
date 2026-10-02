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

from centinela import broker_xtb as bx, config  # noqa: E402
from conftest import CUENTA_PRUEBA  # noqa: E402


#: La cuenta demo que el sistema tiene permitido operar. Los dobles usan ESTE
#: número, no uno inventado: el candado compara contra la configuración
#: versionada y un número cualquiera lo haría saltar (que es justo su trabajo).
CUENTA = CUENTA_PRUEBA


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
class _Resultado:
    def __init__(self, status, price=None, order_number=None, error=None):
        self.status = status
        self.price = price
        self.order_number = order_number
        self.error = error


class _ConfigWS:
    def __init__(self, url):
        self.url = url


class _WS:
    """Imita la estructura real: la url vive en un atributo PRIVADO `_config`.

    Si el doble expusiera un `.url` cómodo que la librería no tiene, el candado
    pasaría en los tests y fallaría contra la cuenta de verdad — que es
    exactamente lo que pasó el 2026-09-26.
    """

    def __init__(self, url):
        self._config = _ConfigWS(url)


class _ClienteFalso:
    """Imita lo justo de XTBClient: lo que nuestra capa toca, y nada más."""

    def __init__(self, *, url="wss://api5demoa.x-station.eu/v1/xstation",
                 cuenta=CUENTA, resultado=None):
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
            currency, account_number = "USD", CUENTA
        return B()

    posiciones_devueltas = None

    async def get_positions(self):
        if self.posiciones_devueltas is not None:
            return list(self.posiciones_devueltas)

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


CRED = bx.Credenciales(email="x@y.z", cuenta=CUENTA, password="secreta")


def _broker(**kw):
    return bx.BrokerXTB(CRED, cliente=_ClienteFalso(**kw))


# --------------------------------------------------------------------------- #
# 1. EL CANDADO — lo único cuyo fallo cuesta dinero de verdad
# --------------------------------------------------------------------------- #
def test_conecta_contra_el_endpoint_de_demo():
    b = _broker()
    b.conectar()
    assert b.saldo()["cuenta"] == CUENTA
    b.desconectar()


def test_un_endpoint_REAL_aborta_sin_enviar_nada():
    """El default de la librería es 'real'. Si algo lo resolviera así, aquí muere."""
    b = _broker(url="wss://api5reala.x-station.eu/v1/xstation")
    with pytest.raises(bx.CuentaNoDemo, match="NO es de demo"):
        b.conectar()


def test_un_endpoint_ILEGIBLE_tambien_aborta():
    """"No se pudo determinar" cuenta como fallo, no como permiso.

    Pasó de verdad el 2026-09-26: la url vive en un atributo privado de la
    librería, el candado no supo leerla y se negó a operar. Esa negativa es el
    comportamiento correcto — lo que estaba mal era la lectura.
    """
    b = _broker(url="")
    with pytest.raises(bx.CuentaNoDemo, match="No se pudo leer"):
        b.conectar()


def test_si_la_cuenta_conectada_no_es_la_esperada_aborta():
    """Protege del caso en que el mismo login tenga varias cuentas."""
    b = _broker(cuenta=99999999)
    with pytest.raises(bx.CuentaNoDemo, match="huella") as exc:
        b.conectar()
    # Se identifica por sus últimos dígitos, nunca entera: el log es público.
    assert "•••999" in str(exc.value) and "99999999" not in str(exc.value)


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


def _posicion_cruda(orden, acciones):
    class P:
        symbol, open_price, current_price = "AAPL.US", 100.0, 105.0
        stop_loss, take_profit, side = None, None, "buy"
        profit_net = 1.0
    P.order_id = orden
    P.volume = float(acciones)
    return P()


def test_una_compra_NO_lleva_los_niveles_a_XTB():
    """XTB no los acepta en acciones al contado. El 28/09 los ignoraba en
    silencio —una compra de F.US volvió con stop y objetivo a None— y el 02/10
    pasó a RECHAZAR la orden entera: CTVA x134 con niveles, rechazada; las
    mismas 134 sin niveles, ejecutada (orden 916937102).

    Se siguen aceptando como argumento y registrando en la bitácora —son la
    decisión del simulador y hay que saber cuáles eran— pero no se mandan.
    """
    b = _broker(); b.conectar()
    e = b.comprar("AAPL.US", 3, objetivo=120.0, stop=92.0)
    assert b._cliente.enviadas == [("buy", "AAPL.US", 3, None, None)]
    assert e.estado == "ejecutada" and e.ok
    assert e.precio == 101.5 and e.orden == 999


def test_una_posicion_repetida_por_XTB_se_cuenta_UNA_vez():
    """XTB devuelve la misma posición más de una vez: una compra de 50 acciones
    aparecía como "2 entradas, 100 acciones". No se ejecuta dos veces —el saldo
    bajó lo que cuestan 50— pero sumarlas hacía vender el doble."""
    b = _broker(); b.conectar()
    b._cliente.posiciones_devueltas = [
        _posicion_cruda(orden=1, acciones=50),
        _posicion_cruda(orden=1, acciones=50),      # la misma, repetida
        _posicion_cruda(orden=2, acciones=7),
    ]
    posiciones = b.posiciones()
    assert len(posiciones) == 2
    assert sum(p["acciones"] for p in posiciones) == 57


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


# --------------------------------------------------------------------------- #
# 6. Segundo factor (2FA)
# --------------------------------------------------------------------------- #
def test_el_secreto_totp_llega_al_cliente(monkeypatch):
    """Sin pasarlo, el login muere en "2FA is required" — pasó de verdad.

    La cuenta de pruebas tenía el segundo factor activado, así que este camino
    no es hipotético: es el que hace falta para entrar.
    """
    creado = {}

    class _Falso(_ClienteFalso):
        def __init__(self, **kw):
            creado.update(kw)
            super().__init__()

    monkeypatch.setitem(sys.modules, "xtb_api",
                        type(sys)("xtb_api"))
    sys.modules["xtb_api"].XTBClient = _Falso

    cred = bx.Credenciales(email="x@y.z", cuenta=CUENTA,
                           password="secreta", totp="BASE32SECRET")
    b = bx.BrokerXTB(cred, demo=True)
    b.conectar()

    assert creado["totp_secret"] == "BASE32SECRET"
    # Y el tipo de cuenta SIEMPRE explícito: el default de la librería es real.
    assert creado["account_type"] == "demo"


def test_sin_2fa_el_secreto_va_vacio_y_no_estorba(monkeypatch):
    creado = {}

    class _Falso(_ClienteFalso):
        def __init__(self, **kw):
            creado.update(kw)
            super().__init__()

    monkeypatch.setitem(sys.modules, "xtb_api", type(sys)("xtb_api"))
    sys.modules["xtb_api"].XTBClient = _Falso

    bx.BrokerXTB(bx.Credenciales(email="x@y.z", cuenta=CUENTA,
                                 password="p"), demo=True).conectar()
    assert creado["totp_secret"] == ""


def test_el_secreto_totp_tampoco_se_imprime():
    """Un repr con el secreto dentro vale tanto como la contraseña."""
    c = bx.Credenciales(email="x@y.z", cuenta=1, password="p", totp="SECRETO32")
    assert "SECRETO32" not in repr(c)


# --------------------------------------------------------------------------- #
# 7. Un solo event loop por sesión
# --------------------------------------------------------------------------- #
def test_todas_las_llamadas_comparten_el_MISMO_event_loop():
    """`asyncio.run` por llamada cierra el loop y deja el WebSocket huérfano.

    Contra la cuenta real dio "RuntimeError: Event loop is closed" en la
    primera lectura después de conectar. El cliente mantiene un socket vivo
    atado al loop donde se conectó, así que el loop tiene que durar lo que dure
    la sesión.
    """
    import asyncio

    loops = []

    class _Espia(_ClienteFalso):
        async def _anotar(self):
            loops.append(asyncio.get_running_loop())

        async def connect(self):
            await self._anotar()
            await super().connect()

        async def get_balance(self):
            await self._anotar()
            return await super().get_balance()

        async def get_positions(self):
            await self._anotar()
            return await super().get_positions()

    b = bx.BrokerXTB(CRED, cliente=_Espia())
    b.conectar()
    b.saldo()
    b.posiciones()
    b.desconectar()

    assert len(loops) == 3
    assert len(set(map(id, loops))) == 1, "cada llamada usó un loop distinto"


def test_desconectar_cierra_el_loop_y_se_puede_reconectar():
    b = _broker()
    b.conectar()
    primero = b._loop
    b.desconectar()
    assert primero.is_closed()

    b.conectar()          # una reconexión estrena loop, no reusa el cerrado
    assert not b._loop.is_closed() and b._loop is not primero
    b.desconectar()


# --------------------------------------------------------------------------- #
# 8. El candado se ancla a la CONFIGURACIÓN, no a las credenciales
# --------------------------------------------------------------------------- #
def test_una_cuenta_DISTINTA_se_rechaza_aunque_las_credenciales_cuadren():
    """El escenario que de verdad importa desde que las credenciales viven en
    secrets de GitHub.

    Un secret se cambia desde una página web, sin diff, sin revisión y sin
    dejar rastro en la historia del repositorio. Si el candado comparase la
    sesión solo contra lo que dicen las credenciales, apuntar el sistema a otra
    cuenta —la real, la de otra persona— sería editar un campo y nada más.

    Compara contra `CUENTA_PRUEBA`, que está versionada: cambiarla exige
    un commit.
    """
    otra = 99887766
    cred = bx.Credenciales(email="x@y.z", cuenta=otra, password="p")
    b = bx.BrokerXTB(cred, cliente=_ClienteFalso(cuenta=otra))

    with pytest.raises(bx.CuentaNoDemo) as exc:
        b.conectar()
    assert "CUENTA_DEMO_HUELLA" in str(exc.value)
    assert "solo opera" in str(exc.value)
    # Y el mensaje NO lleva el número entero: los logs de un repo público se
    # leen desde fuera.
    assert str(otra) not in str(exc.value)


def test_el_candado_tambien_ve_la_incoherencia_al_reves():
    """Credenciales de la cuenta buena, sesión conectada a otra."""
    cred = bx.Credenciales(email="x@y.z", cuenta=CUENTA, password="p")
    b = bx.BrokerXTB(cred, cliente=_ClienteFalso(cuenta=99887766))
    with pytest.raises(bx.CuentaNoDemo):
        b.conectar()


def test_pasar_a_real_exige_cambiar_DOS_cosas_versionadas():
    """Ni un despiste con una sola variable saca órdenes a una cuenta con dinero.

    `TIPO_CUENTA_BROKER` decide demo o real y `CUENTA_DEMO_HUELLA` fija cuál. Las dos
    están en el código, no en secrets, así que las dos exigen commit.
    """
    assert config.TIPO_CUENTA_BROKER == "demo"
    assert len(config.CUENTA_DEMO_HUELLA) == 64

    # Con el interruptor en "demo", pedir una conexión real se rechaza.
    b = bx.BrokerXTB(bx.Credenciales(email="x@y.z", cuenta=CUENTA, password="p"),
                     demo=False, cliente=_ClienteFalso())
    with pytest.raises(bx.CuentaNoDemo, match="solo opera en DEMO"):
        b.conectar()


def test_el_README_documenta_como_pasar_a_dinero_real():
    """El apartado no es decorativo: es la única red entre una demo y una
    cuenta con dinero, y tiene que nombrar las DOS constantes del candado.

    Si alguien renombra una y no actualiza el README, la próxima persona
    cambiará la que conoce, verá que el ejecutor se para y no sabrá por qué.
    """
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "Antes de pasar a dinero real" in readme
    assert "TIPO_CUENTA_BROKER" in readme and "CUENTA_DEMO_HUELLA" in readme
    for paso in ("TOTP", "credenciales", "auditoria_fiabilidad"):
        assert paso in readme, f"el apartado no menciona {paso}"


def test_el_numero_de_la_cuenta_no_esta_en_el_repositorio():
    """Solo su huella. El número real llega por el secret XTB_CUENTA.

    Se busca cualquier número de 8 cifras en config.py: es la forma de los
    números de cuenta de XTB, y ninguna constante legítima de config la tiene.
    """
    import re
    fuente = (RAIZ / "centinela" / "config.py").read_text(encoding="utf-8")
    assert not re.search(r"(?<![0-9a-f])\d{8}(?![0-9a-f])", fuente), \
        "hay un número de 8 cifras en config.py: ¿un número de cuenta?"
    assert re.search(r'CUENTA_DEMO_HUELLA = "[0-9a-f]{64}"', fuente)


def test_la_huella_distingue_cuentas():
    assert config.es_cuenta_demo(CUENTA_PRUEBA)
    assert not config.es_cuenta_demo(CUENTA_PRUEBA + 1)
    assert not config.es_cuenta_demo("no-es-un-numero")
