"""Capa de aislamiento frente a XTB. TODO lo que toca el broker pasa por aquí.

POR QUÉ UNA CAPA PROPIA
-----------------------
XTB cerró su API oficial (xapi.xtb.com / ws.xtb.com) el 14 de marzo de 2025.
Lo único que queda es ingeniería inversa de xStation5, y eso se puede romper
cualquier martes sin aviso. Envolviéndolo aquí, el día que se rompa solo hay
que reescribir este fichero: el ejecutor, el simulador y el dashboard no saben
que `xtb_api` existe.

EL CANDADO DEMO
---------------
La librería, por defecto, se conecta a la cuenta REAL cuando no se le dice lo
contrario (`resolve_account_type` devuelve "real" si `XTB_ACCOUNT_TYPE` no está
puesto). Eso es una mina antipersona, así que aquí el tipo de cuenta se pasa
SIEMPRE explícito y además se verifica dos veces:

  1. antes de conectar, que el endpoint resuelto sea el de demo;
  2. después de conectar, que el número de cuenta sea el que se esperaba.

Si cualquiera de las dos falla —o no se puede determinar— no se envía nada y se
lanza `CuentaNoDemo`. Nunca "por si acaso"; nunca un aviso y seguir.

LO QUE EL CLIENTE NO PUEDE HACER (medido, no supuesto)
------------------------------------------------------
El protocolo reverse-engineered solo expone abrir órdenes y cancelar las que
están en cola. NO existe modificar una posición abierta ni cerrarla por id:

* **Poner take profit o stop loss: XTB LOS IGNORA en acciones al contado.**
  Comprobado con una orden real el 2026-09-28, con el mercado abierto: se
  compró 1 acción de F.US a 12,45 pasando `stop_loss=11.21` y
  `take_profit=13.70`, y la posición apareció con `STOP=None OBJETIVO=None`.
  La orden se aceptó y se ejecutó; los niveles simplemente no se aplicaron, sin
  ningún error. Coincide con lo que documenta XTB: en acciones reales los
  niveles no van sobre la posición, sino como órdenes pendientes
  independientes (sell stop / sell limit) que este cliente no sabe crear.

  Consecuencia directa: **la Cartera A no puede llevar su stop en el broker**
  por esta vía, que es justo lo que la distingue de la B.

* **Modificar el take profit**: imposible, y ya da igual, porque tampoco se
  puede poner al abrir.

* **Cerrar una posición**: se hace vendiendo el mismo volumen (`vender`). En
  acciones al contado eso netea la posición; si XTB la tratara como cobertura y
  abriera una corta, la reconciliación posterior lo detecta y el ejecutor
  termina en ROJO en lugar de dejar una posición espuria abierta.

VOLUMEN ENTERO
--------------
XTB vende acciones fraccionadas en su plataforma, pero el cliente redondea el
volumen (`int(volume + 0.5)`) y rechaza lo que quede por debajo de 1. Un
redondeo AL ALZA silencioso rompería el tamaño de posición, así que aquí solo
se aceptan enteros ya calculados por `riesgo.acciones_enteras`.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #
class ErrorBroker(RuntimeError):
    """Cualquier fallo hablando con el broker."""


class CuentaNoDemo(ErrorBroker):
    """La cuenta conectada no es demo, o no se pudo determinar que lo fuera."""


class OperacionNoSoportada(ErrorBroker):
    """El cliente de xStation5 no expone esta operación (ver cabecera)."""


# --------------------------------------------------------------------------- #
# Credenciales — Llavero de macOS
# --------------------------------------------------------------------------- #
#: Nombres de los servicios en el Llavero. Se leen con `security find-generic-
#: password`, que NO imprime nada por stdout salvo el valor pedido, y nunca se
#: escriben en disco, en logs ni en el repositorio.
LLAVERO = {
    "email": "centinela-xtb-email",
    "cuenta": "centinela-xtb-cuenta",
    "password": "centinela-xtb-password",
    # Secreto TOTP en base32. Opcional: solo hace falta si la cuenta tiene el
    # segundo factor activado, que es lo normal y lo recomendable. Sin él, el
    # login muere en "2FA is required but no totp_secret was provided".
    "totp": "centinela-xtb-totp",
}
LLAVERO_CUENTA = "centinela"

#: Dónde vive la sesión ya autenticada. FUERA del repositorio: un TGT es una
#: credencial viva, y las cookies de CAS son lo que hace que XTB reconozca el
#: navegador y no vuelva a pedir el segundo factor.
#:
#: Esto es lo que hace viable el ejecutor desatendido. XTB no ofrece TOTP —sus
#: métodos son SMS, push y correo, y los tres necesitan que alguien lea un
#: código— así que el 2FA se resuelve UNA vez a mano (scripts/login_xtb.py) y
#: a partir de ahí se reutiliza la sesión.
ARCHIVO_SESION = Path.home() / ".centinela_xtb_session"
ARCHIVO_COOKIES = Path.home() / ".centinela_xtb_cookies.json"


def _del_llavero(servicio: str, obligatorio: bool = True) -> str:
    """Lee un secreto del Llavero de macOS. Lanza si falta y es obligatorio."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", servicio,
             "-a", LLAVERO_CUENTA, "-w"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ErrorBroker(f"No se pudo consultar el Llavero: {exc!r}") from exc
    if r.returncode != 0:
        if not obligatorio:
            return ""
        raise ErrorBroker(
            f"Falta el secreto '{servicio}' en el Llavero. Guárdalo con:\n"
            f"  security add-generic-password -U -s \"{servicio}\" "
            f"-a {LLAVERO_CUENTA} -w")
    return r.stdout.strip()


@dataclass
class Credenciales:
    """Credenciales de XTB. `password` y `totp` nunca se imprimen."""

    email: str
    cuenta: int
    password: str = field(repr=False)
    #: Secreto TOTP en base32, si la cuenta tiene segundo factor. Cadena vacía
    #: si no lo tiene: el cliente solo lo usa cuando XTB lo pide.
    totp: str = field(default="", repr=False)

    @classmethod
    def del_llavero(cls) -> "Credenciales":
        cuenta = _del_llavero(LLAVERO["cuenta"])
        if not cuenta.isdigit():
            raise ErrorBroker(
                f"El número de cuenta del Llavero no es numérico. Debe ser solo "
                f"dígitos (lo ves en el selector de cuenta de xStation 5).")
        return cls(email=_del_llavero(LLAVERO["email"]),
                   cuenta=int(cuenta),
                   password=_del_llavero(LLAVERO["password"]),
                   totp=_del_llavero(LLAVERO["totp"], obligatorio=False))

    @classmethod
    def del_entorno(cls) -> "Credenciales":
        """Alternativa para entornos sin Llavero (CI). Mismo contrato."""
        faltan = [v for v in ("XTB_EMAIL", "XTB_CUENTA", "XTB_PASSWORD")
                  if not os.environ.get(v)]
        if faltan:
            raise ErrorBroker(f"Faltan variables de entorno: {', '.join(faltan)}")
        return cls(email=os.environ["XTB_EMAIL"],
                   cuenta=int(os.environ["XTB_CUENTA"]),
                   password=os.environ["XTB_PASSWORD"],
                   totp=os.environ.get("XTB_TOTP", ""))


def credenciales_del_entorno_o_llavero() -> "Credenciales":
    """Las credenciales, vengan de donde vengan.

    En GitHub Actions llegan por variables de entorno desde los secrets; en un
    Mac, del Llavero. Se prueba primero el entorno porque es lo que distingue a
    un runner, y así el mismo ejecutor sirve en los dos sitios sin ramas de
    código repartidas por ahí.
    """
    if os.environ.get("XTB_EMAIL"):
        return Credenciales.del_entorno()
    return Credenciales.del_llavero()


# --------------------------------------------------------------------------- #
# Resultado de una operación, en el vocabulario de ESTE repositorio
# --------------------------------------------------------------------------- #
@dataclass
class Ejecucion:
    """Lo que el broker hizo de verdad con una orden.

    Deliberadamente NO es el objeto del cliente: si mañana hay que cambiar de
    librería, lo que el resto del sistema consume sigue siendo esto.
    """

    ticker: str
    lado: str                    # "compra" | "venta"
    acciones: int
    estado: str                  # ejecutada | en_cola | rechazada | ambigua
    precio: float | None = None
    orden: int | None = None     # número de orden de XTB
    error: str | None = None
    cuando: str = ""

    @property
    def ok(self) -> bool:
        return self.estado in ("ejecutada", "en_cola")


#: Traducción del vocabulario del cliente al nuestro. AMBIGUOUS se traduce por
#: "ambigua" y NO por "rechazada": la orden puede haberse enviado, y tratarla
#: como fallida llevaría a mandarla dos veces.
_ESTADOS = {
    "FILLED": "ejecutada",
    "QUEUED": "en_cola",
    "REJECTED": "rechazada",
    "AMBIGUOUS": "ambigua",
    "INSUFFICIENT_VOLUME": "rechazada",
    "AUTH_EXPIRED": "rechazada",
    "RATE_LIMITED": "rechazada",
    "TIMEOUT": "ambigua",
}


# --------------------------------------------------------------------------- #
# El broker
# --------------------------------------------------------------------------- #
class BrokerXTB:
    """Fachada síncrona sobre el cliente async de xStation5.

    El resto del repositorio es síncrono; envolver aquí el `asyncio.run` evita
    contagiar async a los escaneos, al dashboard y a los tests.

    Se usa como contexto para que la desconexión esté garantizada:

        with BrokerXTB(credenciales) as b:
            saldo = b.saldo()
    """

    def __init__(self, credenciales: Credenciales, *, demo: bool | None = None,
                 cliente=None):
        self._cred = credenciales
        # El tipo sale de la configuración versionada, no de un argumento con
        # valor por defecto: así operar en real exige tocar el repositorio.
        self._demo = (config.TIPO_CUENTA_BROKER == "demo") if demo is None else demo
        # `cliente` inyectable: los tests pasan un doble y no tocan la red ni
        # necesitan tener instalada la librería.
        self._cliente = cliente
        self._conectado = False
        # UN SOLO event loop para toda la sesión. Ver `_ejecutar`.
        self._loop = None

    # ---------------------------------------------------------------- ciclo --
    def __enter__(self) -> "BrokerXTB":
        self.conectar()
        return self

    def __exit__(self, *_exc) -> None:
        self.desconectar()

    def conectar(self) -> None:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        if not self._demo:
            raise CuentaNoDemo(
                "Este ejecutor solo opera en DEMO. Conectarse a la cuenta real "
                "exigiría cambiar el código a propósito, no una variable de "
                "entorno.")
        if self._cliente is None:
            self._cliente = self._crear_cliente()
        self._ejecutar(self._cliente.connect())
        self._conectado = True
        self._verificar_demo()

    def desconectar(self) -> None:
        try:
            if self._cliente is not None and self._conectado:
                self._ejecutar(self._cliente.disconnect())
                self._conectado = False
        finally:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.close()
            self._loop = None

    def _crear_cliente(self):
        try:
            from xtb_api import XTBClient
        except ImportError as exc:  # pragma: no cover - entorno sin la librería
            raise ErrorBroker(
                "Falta la librería del broker. Instálala con:\n"
                "  pip install 'xtb-api-python==0.10.0'\n"
                "  playwright install chromium") from exc
        # El parche del formulario 2FA tiene que estar puesto ANTES de que el
        # cliente intente autenticarse: la librería busca el campo del código
        # por su nombre en polaco y no lo encuentra (ver parche_otp.py).
        from . import parche_otp
        parche_otp.aplicar()

        cas = None
        try:
            from xtb_api.auth.cas_client import CASClientConfig
            cas = CASClientConfig(cookies_file=ARCHIVO_COOKIES)
        except ImportError:
            pass

        return XTBClient(
            email=self._cred.email,
            password=self._cred.password,
            account_number=self._cred.cuenta,
            totp_secret=self._cred.totp,
            # Sesión y cookies persistidas: sin esto, cada arranque del ejecutor
            # sería un "dispositivo nuevo" para XTB y volvería a pedir el
            # segundo factor, que es justo lo que no se puede automatizar.
            session_file=ARCHIVO_SESION,
            cas_config=cas,
            # EXPLÍCITO y no por variable de entorno: el default de la librería
            # es "real" y no se puede depender de que el entorno esté bien.
            account_type="demo",
        )

    def _ejecutar(self, corutina):
        """Ejecuta una corrutina del cliente en el loop de ESTA sesión.

        No `asyncio.run`: crea un event loop nuevo y lo CIERRA al terminar, y
        el cliente mantiene un WebSocket vivo atado al loop donde se conectó.
        Con un loop por llamada, la primera lectura después de `connect()`
        revienta con "RuntimeError: Event loop is closed" — pasó contra la
        cuenta real el 2026-09-26. El doble de los tests no lo destapaba
        porque no tiene socket que sobreviva entre llamadas.
        """
        if not asyncio.iscoroutine(corutina):
            return corutina
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(corutina)

    # -------------------------------------------------------------- candado --
    def _verificar_demo(self) -> None:
        """El candado. Se ejecuta en CADA conexión, sin excepción.

        Comprueba que el endpoint al que se ha conectado el cliente es el de
        demo y que el número de cuenta es el que se pidió. Si algo no cuadra, o
        simplemente no se puede leer, se corta: "no se pudo determinar" cuenta
        como fallo, no como permiso.
        """
        url = self._url_del_socket()
        if not url:
            raise CuentaNoDemo(
                "No se pudo leer a qué servidor está conectado el cliente. Sin "
                "poder confirmar que es el de demo, no se envía nada.")
        if "demo" not in url.lower():
            raise CuentaNoDemo(
                f"El endpoint conectado NO es de demo (url={url!r}). No se "
                f"envía ninguna orden.")

        try:
            numero = int(self._cliente.account_number)
        except (TypeError, ValueError) as exc:
            raise CuentaNoDemo(
                "No se pudo leer el número de cuenta conectado; sin esa "
                "confirmación no se opera.") from exc
        # Contra la CONFIGURACIÓN, no contra las credenciales. Las credenciales
        # viven en secrets que se pueden cambiar desde una web sin dejar diff;
        # `config.CUENTA_DEMO` exige un commit. Si alguien apunta los secrets a
        # otra cuenta —aunque esté vacía, aunque sea suya— aquí se para.
        esperada = int(config.CUENTA_DEMO)
        if numero != esperada:
            raise CuentaNoDemo(
                f"Conectado a la cuenta {numero}, pero el sistema solo opera la "
                f"{esperada} (config.CUENTA_DEMO). No se envía ninguna orden.")
        if numero != self._cred.cuenta:
            raise CuentaNoDemo(
                f"Las credenciales dicen cuenta {self._cred.cuenta} y la sesión "
                f"conectó a la {numero}. No se envía ninguna orden.")

    def _url_del_socket(self) -> str:
        """La URL del WebSocket al que el cliente se ha conectado de verdad.

        Se busca por varios caminos porque la librería la guarda en un atributo
        PRIVADO (`ws._config.url`) y eso puede cambiar sin aviso en cualquier
        versión. Devolver cadena vacía no es "no pasa nada": el candado lo trata
        como fallo y se niega a operar, que es la respuesta correcta cuando no
        se puede comprobar dónde se está.
        """
        ws = getattr(self._cliente, "ws", None)
        candidatos = [
            getattr(ws, "url", None),
            getattr(getattr(ws, "_config", None), "url", None),
            getattr(getattr(ws, "config", None), "url", None),
        ]
        for c in candidatos:
            if c:
                return str(c)
        return ""

    # --------------------------------------------------------------- lectura --
    def saldo(self) -> dict:
        """Saldo, equity y divisa de la cuenta demo."""
        b = self._ejecutar(self._cliente.get_balance())
        return {"saldo": float(b.balance), "equity": float(b.equity),
                "margen_libre": float(getattr(b, "free_margin", 0.0)),
                "divisa": b.currency, "cuenta": int(b.account_number)}

    def posiciones(self) -> list[dict]:
        """Posiciones abiertas, en el vocabulario de este repositorio."""
        return [
            {"ticker": p.symbol, "acciones": float(p.volume),
             "precio_entrada": float(p.open_price),
             "precio_actual": float(getattr(p, "current_price", 0.0) or 0.0),
             "stop": p.stop_loss, "objetivo": p.take_profit,
             "lado": p.side, "orden": p.order_id,
             "pnl": float(getattr(p, "profit_net", 0.0) or 0.0)}
            for p in self._ejecutar(self._cliente.get_positions())
        ]

    def ordenes_pendientes(self) -> list[dict]:
        return [
            {"ticker": o.symbol, "acciones": float(o.volume),
             "precio": float(o.price), "lado": o.side, "orden": o.order_id}
            for o in self._ejecutar(self._cliente.get_orders())
        ]

    # -------------------------------------------------------------- escritura --
    def comprar(self, ticker: str, acciones: int, *, objetivo: float | None = None,
                stop: float | None = None) -> Ejecucion:
        """Compra a mercado. Con el mercado cerrado, XTB la deja EN COLA.

        Esa cola es justo lo que la estrategia necesita: la decisión se toma en
        la pre-apertura y la compra tiene que ejecutarse al precio de apertura,
        no al de la víspera.
        """
        self._exigir_entero(acciones)
        r = self._ejecutar(self._cliente.buy(
            ticker, volume=acciones, stop_loss=stop, take_profit=objetivo))
        return self._traducir(r, ticker, "compra", acciones)

    def vender(self, ticker: str, acciones: int) -> Ejecucion:
        """Vende a mercado. Es la ÚNICA forma de cerrar una posición.

        El cliente no expone cerrar por id (ver cabecera del módulo), así que se
        vende el mismo volumen y se comprueba después, en la reconciliación, que
        la posición desapareció de verdad.
        """
        self._exigir_entero(acciones)
        r = self._ejecutar(self._cliente.sell(ticker, volume=acciones))
        return self._traducir(r, ticker, "venta", acciones)

    def cancelar(self, numero_orden: int) -> str:
        """Cancela una orden que sigue en cola."""
        r = self._ejecutar(self._cliente.cancel_order(numero_orden))
        return str(getattr(r, "status", r))

    def modificar_objetivo(self, *_a, **_k):
        """No se puede: el cliente no expone modificar una posición abierta.

        Existe para que el ejecutor pueda pedirlo y recibir un "no" explícito en
        vez de que el hueco se note el día que alguien asuma que sí se hizo. El
        impacto está medido en la cabecera del módulo.
        """
        raise OperacionNoSoportada(
            "xStation5 (cliente no oficial) no permite modificar el take profit "
            "de una posición abierta. El TP se queda en el que se puso al "
            "comprar; la divergencia está medida y registrada.")

    # ---------------------------------------------------------------- interno --
    @staticmethod
    def _exigir_entero(acciones) -> None:
        if not isinstance(acciones, int) or isinstance(acciones, bool):
            raise ErrorBroker(
                f"El volumen tiene que ser un entero ya calculado, y llegó "
                f"{acciones!r}. El cliente redondea al alza por su cuenta "
                f"(int(v+0.5)) y eso rompería el tamaño de posición.")
        if acciones < 1:
            raise ErrorBroker(
                f"Volumen {acciones}: XTB no acepta menos de una acción por la "
                f"API. La entrada no cabe en el slot con este capital.")

    @staticmethod
    def _traducir(r, ticker: str, lado: str, acciones: int) -> Ejecucion:
        estado = _ESTADOS.get(str(getattr(r, "status", "")), "ambigua")
        precio = getattr(r, "price", None)
        return Ejecucion(
            ticker=ticker, lado=lado, acciones=acciones, estado=estado,
            precio=None if precio is None else float(precio),
            orden=getattr(r, "order_number", None),
            error=getattr(r, "error", None),
            cuando=datetime.now(config.TZ_ET).isoformat(),
        )
