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

LO QUE EL CLIENTE PUEDE Y NO PUEDE HACER (medido, no supuesto)
------------------------------------------------------------------
* **Stop y objetivo como órdenes pendientes** (desde el 2026-10-05, parche 3
  del cliente): `poner_orden_venta`, `modificar_orden`, `cancelar_ordenes` y
  `ordenes_contado`. XTB solo admite UNA orden pendiente de venta por acción,
  así que la stop va en XTB y el objetivo lo ejecuta el vigilante
  (centinela/proteccion.py). `vender` cancela antes las órdenes pendientes del
  símbolo para liberar las acciones.

* **Niveles al comprar: no se mandan.** El 28/09 XTB los "ignoró" y el 02/10
  rechazó la orden entera. El parche 3 encontró por qué: el cliente original
  los metía en un campo que el esquema no tiene (ver vendor/xtb_api/CAMBIOS.md).
  No se ha probado la forma correcta; el sistema usa la orden pendiente.

* **Modificar el objetivo de una posición** (`modificar_objetivo`): no se usa;
  el objetivo lo lleva el vigilante.

* **Cerrar una posición**: se hace vendiendo el mismo volumen (`vender`). En
  acciones al contado eso netea la posición.

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


#: Marca que el ejecutor imprime y el Vigilante busca. Se escribe así, literal y
#: en una sola línea, para que se pueda encontrar de un vistazo en el log de un
#: run desde el móvil.
MARCA_SESION_CADUCADA = "XTB_REQUIERE_CODIGO"


class SesionCaducada(ErrorBroker):
    """XTB pide un código de verificación: la sesión guardada ya no sirve.

    Pasa cada vez que el TGT caduca (8 h) o que la caché de Actions se pierde.
    Como XTB no ofrece TOTP —sus métodos son SMS, push y correo— no hay forma
    de resolverlo sin una persona leyendo un código, así que el run tiene que
    morir en rojo y decir exactamente qué hacer.

    OJO: cuando esto se lanza, XTB YA ha enviado el correo con el código. Ese
    es el que hay que pegar en el workflow "Renovar sesión XTB".
    """

    def __init__(self, detalle: str = ""):
        super().__init__(
            f"{MARCA_SESION_CADUCADA}: la sesión caducó. XTB acaba de enviarte "
            f"un código de verificación por correo. Lánzalo desde la pestaña "
            f"Actions -> 'Renovar sesión XTB' -> Run workflow, pegando ese "
            f"código. No hace falta terminal."
            + (f"\n  (detalle del cliente: {detalle})" if detalle else ""))


#: Señales de que XTB está pidiendo el segundo factor. Se mira el texto porque
#: el cliente no expone un tipo propio para esto: lanza `CASError` con un código
#: dentro del mensaje.
_SENALES_2FA = ("2FA is required", "AUTH_MANAGER_2FA_NO_SECRET",
                "requires_2fa", "two_factor", "TWO_FACTOR")


def _pide_codigo(exc: BaseException) -> bool:
    texto = f"{type(exc).__name__}: {exc}"
    return any(s.lower() in texto.lower() for s in _SENALES_2FA)


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
    #: Lo que XTB movió DE VERDAD, visto en sus posiciones al confirmar. Puede
    #: ser menos que `acciones` (ejecución parcial). None = no se pudo mirar.
    acciones_hechas: int | None = None
    #: De dónde sale `precio`: xtb | posicion | saldo (ver ordenes.py).
    precio_fuente: str | None = None

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


#: Vocabulario del repo -> vocabulario del cliente.
_TIPO_CLIENTE = {"limitada": "limit", "stop": "stop"}
_TIPO_REPO = {"limit": "limitada", "stop": "stop", "market": "mercado"}


@dataclass
class OrdenPendiente:
    """Resultado de poner o modificar una orden pendiente de venta."""

    ticker: str
    tipo: str                    # limitada | stop
    acciones: int
    precio: float
    estado: str                  # aceptada | rechazada | ambigua
    orden: int | None = None
    error: str | None = None
    cuando: str = ""

    @property
    def ok(self) -> bool:
        return self.estado == "aceptada"

    @classmethod
    def de_resultado(cls, r, ticker, tipo, acciones, precio) -> "OrdenPendiente":
        if getattr(r, "success", False):
            estado = "aceptada"
        elif getattr(r, "ambiguous", False):
            estado = "ambigua"
        else:
            estado = "rechazada"
        return cls(ticker=ticker, tipo=tipo, acciones=acciones, precio=precio,
                   estado=estado, orden=getattr(r, "order_number", None),
                   error=getattr(r, "error", None),
                   cuando=datetime.now(config.TZ_ET).isoformat())


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
        try:
            self._ejecutar(self._cliente.connect())
        except Exception as exc:
            # El cliente lanza un CASError genérico cuando XTB pide el segundo
            # factor. Traducirlo aquí es lo que convierte un traceback ilegible
            # en una instrucción que se puede seguir desde el móvil.
            if _pide_codigo(exc):
                raise SesionCaducada(str(exc)) from exc
            raise
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
        # `config.CUENTA_DEMO_HUELLA` exige un commit. Si alguien apunta los
        # secrets a otra cuenta —aunque esté vacía, aunque sea suya— aquí se
        # para. Los mensajes NO llevan el número: los logs de un repositorio
        # público se leen desde fuera.
        if not config.es_cuenta_demo(numero):
            raise CuentaNoDemo(
                f"Conectado a una cuenta (•••{str(numero)[-3:]}) cuya huella no "
                f"es la de config.CUENTA_DEMO_HUELLA: el sistema solo opera la "
                f"cuenta demo permitida. No se envía ninguna orden.")
        if numero != self._cred.cuenta:
            raise CuentaNoDemo(
                "Las credenciales (secret XTB_CUENTA) y la sesión conectada son "
                "cuentas distintas. No se envía ninguna orden.")

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
        """Posiciones abiertas, en el vocabulario de este repositorio.

        DEDUPLICADAS POR NÚMERO DE ORDEN (fallo del 2026-10-02). XTB devuelve
        la misma posición más de una vez: una compra de 50 acciones aparecía
        como "2 entradas, 100 acciones". No es que se ejecute dos veces —el
        saldo bajó lo que cuestan 50, no 100— es que la lista viene repetida.
        Sumarlas hacía vender el doble de lo que había.
        """
        vistas: dict = {}
        for p in self._posiciones_crudas():
            vistas.setdefault(p["orden"], p)
        return list(vistas.values())

    def _posiciones_crudas(self) -> list[dict]:
        return [
            {"ticker": p.symbol, "acciones": float(p.volume),
             "precio_entrada": float(p.open_price),
             "precio_actual": float(getattr(p, "current_price", 0.0) or 0.0),
             "stop": p.stop_loss, "objetivo": p.take_profit,
             "lado": p.side, "orden": p.order_id,
             "pnl": float(getattr(p, "profit_net", 0.0) or 0.0)}
            for p in self._ejecutar(self._cliente.get_positions())
        ]

    def cotizacion(self, ticker: str) -> dict:
        """El bid/ask de ahora mismo, por petición.

        Es el respaldo del vigilante de precios cuando el WebSocket se cae: más
        caro que un tick empujado, pero no depende de que la suscripción siga
        viva. Lo que se mira para vender es el BID, que es el precio al que uno
        vende de verdad; usar el último precio o el ask daría disparos que el
        mercado no habría pagado.
        """
        q = self._ejecutar(self._cliente.get_quote(ticker))
        if q is None:
            raise ErrorBroker(f"XTB no devolvió cotización de {ticker}.")
        return {"ticker": ticker, "bid": float(q.bid), "ask": float(q.ask),
                "spread": float(getattr(q, "spread", 0.0) or 0.0),
                "cuando": getattr(q, "time", None)}

    # ------------------------------------------------------------ streaming --
    def al_recibir_tick(self, callback) -> None:
        """Registra quién atiende cada tick. El callback recibe un dict crudo
        del cliente; normalizarlo es cosa de quien lo use, porque el formato
        viene de ingeniería inversa y puede cambiar sin aviso."""
        self._cliente.on("tick", callback)

    def al_perder_conexion(self, callback) -> None:
        self._cliente.on("disconnected", callback)

    def al_recuperar_conexion(self, callback) -> None:
        self._cliente.on("connected", callback)

    def suscribir_ticks(self, ticker: str) -> None:
        """Pide a XTB que empuje los ticks de un símbolo por el WebSocket."""
        self._ejecutar(self._cliente.subscribe_ticks(ticker))

    def cancelar_ticks(self, ticker: str) -> None:
        self._ejecutar(self._cliente.unsubscribe_ticks(ticker))

    def bombear(self, segundos: float) -> None:
        """Deja correr el event loop para que lleguen los ticks empujados.

        El resto de la capa es síncrona a propósito —el ejecutor manda una
        orden y espera—, pero un vigilante que escucha necesita ceder el hilo
        para que el WebSocket entregue. Esto es ese hueco, y el único sitio del
        repositorio donde el tiempo pasa esperando a XTB.
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(asyncio.sleep(max(0.0, segundos)))

    @property
    def conectado(self) -> bool:
        """Si el WebSocket sigue vivo según el cliente."""
        return bool(getattr(self._cliente, "is_connected", False))

    def ordenes_pendientes(self) -> list[dict]:
        """Todo lo que XTB tiene en cola: compras a mercado esperando la
        apertura Y, desde el parche 3, las ventas limitadas/stop que protegen
        las posiciones. `lado` y `tipo` son los que permiten distinguirlas."""
        return [
            {"ticker": o.symbol, "acciones": float(o.volume),
             "precio": float(o.price), "lado": o.side, "orden": o.order_id,
             "tipo": getattr(o, "order_type", None)}
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
        # LOS NIVELES NO SE MANDAN (fallo del 2026-10-02). XTB no los acepta en
        # acciones al contado: el 28/09 los ignoraba en silencio —una compra de
        # F.US volvió con stop y objetivo a None— y el 02/10 pasó a RECHAZAR la
        # orden entera. CTVA x134 con niveles: rechazada; las mismas 134 sin
        # niveles: ejecutada, orden 916937102.
        #
        # Se siguen aceptando como argumento y se siguen registrando en la
        # bitácora, porque son la decisión del simulador y hay que saber cuáles
        # eran. Lo que no se hace es mandárselos a un broker que los rechaza.
        # De vigilarlos se encarga el vigilante de precios, que para eso está.
        if stop is not None or objetivo is not None:
            print(f"[niveles] {ticker}: XTB no acepta stop ni objetivo en "
                  f"acciones al contado; la orden va a mercado y los niveles "
                  f"los vigila el vigilante de precios.", flush=True)
        r = self._ejecutar(self._cliente.buy(ticker, volume=acciones))
        return self._traducir(r, ticker, "compra", acciones)

    def vender(self, ticker: str, acciones: int) -> Ejecucion:
        """Vende a mercado. Es la ÚNICA forma de cerrar una posición.

        El cliente no expone cerrar por id (ver cabecera del módulo), así que se
        vende el mismo volumen y se comprueba después, en la reconciliación, que
        la posición desapareció de verdad.
        """
        self._exigir_entero(acciones)
        self.liberar_acciones(ticker)
        r = self._ejecutar(self._cliente.sell(ticker, volume=acciones))
        return self._traducir(r, ticker, "venta", acciones)

    def cancelar(self, numero_orden: int) -> str:
        """Cancela una orden que sigue en cola."""
        r = self._ejecutar(self._cliente.cancel_order(numero_orden))
        return str(getattr(r, "status", r))

    # ------------------------------------------------ órdenes pendientes --
    def ordenes_contado(self) -> dict:
        """Las órdenes de acciones al contado que XTB tiene, con su ESTADO.

        `ordenes_pendientes()` (getAllOrders del WebSocket) no las ve: medido el
        2026-10-05 con una limitada y una stop aceptadas, devolvió cero. Esta
        es la lista que usa la web (OrderService). Devuelve
        {"ordenes": [...], "reglas": {simbolo: {"limitada"|"stop": {"cancelar", "modificar"}}}}
        con solo las órdenes VIVAS en "ordenes" y todas en "todas".
        """
        from xtb_api.grpc.proto import ORDER_STATUS_VIVA
        foto = self._ejecutar(self._cliente.get_cash_orders())
        todas = [{
            "orden": o["order_id"], "ticker": o["symbol"],
            "tipo": _TIPO_REPO.get(o["type"], o["type"]), "lado": o["side"],
            "acciones": o["volume"], "precio": o["price"], "estado": o["status"],
            "viva": o["status"] in ORDER_STATUS_VIVA, "creada": o["create_time"],
            "vence": o["expiration"],
        } for o in foto["orders"]]
        reglas = {s: {_TIPO_REPO[k]: {"cancelar": v["delete"], "modificar": v["modify"]}
                      for k, v in r.items()} for s, r in foto["rules"].items()}
        return {"ordenes": [o for o in todas if o["viva"]], "todas": todas,
                "reglas": reglas}

    def poner_orden_venta(self, ticker: str, acciones: int, tipo: str,
                          precio: float) -> "OrdenPendiente":
        """Venta pendiente en el servidor de XTB, sin vencimiento.

        tipo "limitada" = objetivo (Orden Limitada de venta, por encima del
        mercado); tipo "stop" = stop (Orden Stop de venta, por debajo). La
        cantidad tiene que ser EXACTAMENTE la de la posición: con más, XTB no
        la acepta porque sería ir en corto (comprobado a mano en xStation 5).
        """
        self._exigir_entero(acciones)
        r = self._ejecutar(self._cliente.place_pending_order(
            ticker, acciones, _TIPO_CLIENTE[tipo], float(precio)))
        return OrdenPendiente.de_resultado(r, ticker, tipo, acciones, float(precio))

    def modificar_orden(self, orden: int, tipo: str, precio: float) -> "OrdenPendiente":
        """Cambia el precio de una orden pendiente que YA existe. No crea otra."""
        r = self._ejecutar(self._cliente.modify_pending_order(
            int(orden), _TIPO_CLIENTE[tipo], float(precio)))
        o = OrdenPendiente.de_resultado(r, "", tipo, 0, float(precio))
        if o.orden is None:
            o.orden = int(orden)
        return o

    def liberar_acciones(self, ticker: str) -> list[int]:
        """Cancela las órdenes pendientes de VENTA del símbolo antes de vender.

        Una stop puesta en XTB (centinela/proteccion.py) reserva las acciones:
        con ella viva, la venta a mercado no tiene acciones libres. Va aquí, en
        el único sitio por el que vende todo el sistema (objetivo, tiempo,
        diferida, limpieza), para que ningún camino se olvide de hacerlo. Si
        la venta luego falla, la próxima sincronización repone la stop.

        Un cliente sin la lista de contado (los dobles de los tests antiguos)
        no tiene nada que liberar.
        """
        if not hasattr(self._cliente, "get_cash_orders"):
            return []
        vivas = [o["orden"] for o in self.ordenes_contado()["ordenes"]
                 if o["ticker"] == ticker and o["lado"] == "sell"]
        if not vivas:
            return []
        r = self.cancelar_ordenes(vivas)
        malas = {n: e for n, (ok, e) in r.items() if not ok}
        print(f"[liberar] {ticker}: canceladas {sorted(set(vivas) - set(malas))} "
              f"antes de vender" + (f"; NO se pudo con {malas}" if malas else ""),
              flush=True)
        return [n for n in vivas if n not in malas]

    def cancelar_ordenes(self, ordenes: list[int]) -> dict[int, tuple[bool, str | None]]:
        """{orden: (cancelada_de_verdad, error)}. Lee la rama de error de CADA
        orden: "no la encuentro" NO es "cancelada"."""
        if not ordenes:
            return {}
        r = self._ejecutar(self._cliente.cancel_pending_orders([int(n) for n in ordenes]))
        return {int(n): (bool(v.success), v.error) for n, v in r.items()}

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
        error = getattr(r, "error", None)

        # Si el fallo vino con cuerpo vacío, el motivo de verdad está en las
        # cabeceras gRPC y lo ha recogido la copia del cliente (ver
        # vendor/xtb_api/CAMBIOS.md, parche 2). Se antepone al mensaje genérico
        # del cliente, que solo dice que no había cuerpo.
        from xtb_api.grpc.client import CENTINELA_ULTIMO_ERROR
        motivo = CENTINELA_ULTIMO_ERROR.get("motivo")
        if motivo and estado == "ambigua":
            error = f"{motivo}" + (f" [{error}]" if error else "")

        return Ejecucion(
            ticker=ticker, lado=lado, acciones=acciones, estado=estado,
            precio=None if precio is None else float(precio),
            orden=getattr(r, "order_number", None),
            error=error,
            cuando=datetime.now(config.TZ_ET).isoformat(),
        )
