"""Las órdenes del día: qué hay que mandar al broker, y qué se mandó de verdad.

SEPARAR DECISIÓN DE EJECUCIÓN
-----------------------------
Los escaneos deciden y no saben que existe un broker. Escriben lo que hay que
hacer en `ordenes/pendientes.json`; el ejecutor lo lee, lo manda a XTB y anota
en `bitacora_broker.csv` lo que pasó REALMENTE. Dos ficheros, dos
responsabilidades, y un sistema que sigue funcionando entero si el broker se
cae: se seguiría decidiendo y simulando igual, solo que nadie ejecutaría.

Esa separación es también la que permite comparar. La bitácora del simulador
dice lo que "debería" haber pasado; `bitacora_broker.csv` dice lo que pasó. La
diferencia entre las dos es el slippage real, y es la única forma honesta de
saber cuánto vale la estrategia fuera del papel.

IDENTIFICADOR DETERMINISTA
--------------------------
`fecha|cartera|ticker|tipo`. Determinista a propósito: si el ejecutor se
reejecuta —el Mac se despertó dos veces, launchd disparó sus dos horarios de
verano e invierno, alguien lo lanzó a mano— el mismo id ya está en
`estado/ordenes_enviadas.json` y no se manda nada. Sin esto, un despertar de
más compra dos veces las mismas acciones.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from . import config

#: Tipos de orden que el ejecutor sabe mandar. Vocabulario CERRADO, como el de
#: `resultados.py`: uno nuevo obliga a decidir a mano qué hace el ejecutor con
#: él, en vez de colarse por un `else` silencioso.
COMPRA = "compra"
VENTA_TIEMPO = "venta_tiempo"
#: XTB IGNORA el stop loss y el take profit en acciones al contado —comprobado
#: con una orden real el 2026-09-28— así que los vigila el ejecutor y cierra a
#: mercado cuando el precio los cruza. Estas dos no se escriben en
#: pendientes.json: nacen en el momento, de comparar el precio con el nivel.
VENTA_STOP = "venta_stop"
VENTA_OBJETIVO = "venta_objetivo"
#: Plan B de la salida por tiempo. La ventana de ventas son 25 minutos justo
#: antes del cierre y el cron de Actions se ha retrasado horas en este
#: repositorio: cuando esa ventana se pierde, la posición debía haber salido y
#: sigue viva. En vez de dejarla ahí, se cierra a la apertura siguiente con
#: orden de mercado. Se registra con SU PROPIO NOMBRE para poder medir después
#: cuánto cuesta el plan B frente a haber cerrado a tiempo.
VENTA_TIEMPO_DIFERIDO = "tiempo_diferido"
#: Las mismas dos salidas, pero disparadas EN VIVO por el vigilante de precios
#: mientras la sesión está abierta, no en el único vistazo de las 15:45 ET.
#:
#: Tienen nombre propio y no reutilizan VENTA_STOP/VENTA_OBJETIVO porque la
#: diferencia entre las dos cosas es justo lo que hay que poder medir: el
#: vistazo diario llevaba la Cartera A de +12,07% / -10,44% a +9,32% / -12,50%
#: porque 7 de 21 stops se dispararon por un precio que el día cerró por
#: encima. Si el vigilante en vivo acerca la ejecución al simulador, se verá
#: comparando estas filas con aquellas; si las mezcláramos bajo el mismo
#: nombre, no habría forma de saberlo.
VENTA_OBJETIVO_INTRADIA = "objetivo_intradia"
VENTA_STOP_INTRADIA = "stop_intradia"
#: Una compra que se manda DESPUÉS de la apertura porque la de la apertura no
#: entró. Tiene nombre propio para poder medir lo que cuesta llegar tarde: su
#: `precio_simulador` es el precio de APERTURA del día, que es el que la
#: estrategia supone que se pagó, así que la diferencia contra el ejecutado es
#: exactamente la factura del retraso.
ENTRADA_TARDIA = "entrada_tardia"
#: Cierre de una posición porque la serie con la que se decidió entrar no era
#: real (ver centinela/datos_erroneos.py). No la decide la estrategia: la
#: decide el descubrimiento de que el dato estaba roto, así que no puede
#: contarse como una salida por stop ni por tiempo. Se manda a mano y queda
#: fuera de las estadísticas de la demo, como la operación que cierra.
VENTA_DATO_ERRONEO = "venta_dato_erroneo"
#: La stop que vive en el SERVIDOR de XTB como orden pendiente
#: (centinela/proteccion.py) y que saltó sola. Nombre propio porque no la
#: disparó nadie de este sistema: su precio registrado es el nivel de la orden
#: (`precio_fuente = nivel`), no uno devuelto por XTB.
VENTA_STOP_XTB = "stop_xtb"
TIPOS = (COMPRA, VENTA_TIEMPO, VENTA_STOP, VENTA_OBJETIVO,
         VENTA_TIEMPO_DIFERIDO, VENTA_OBJETIVO_INTRADIA, VENTA_STOP_INTRADIA,
         ENTRADA_TARDIA, VENTA_DATO_ERRONEO, VENTA_STOP_XTB)

#: Las que COMPRAN. Todo lo demás vende. Hace falta saberlo para confirmar una
#: orden contra XTB: una compra se confirma porque la posición CRECE y una venta
#: porque BAJA o desaparece. Confundirlas anotó como "rechazada" la venta de
#: CTVA del 2026-10-02, que XTB sí ejecutó.
TIPOS_COMPRA = (COMPRA, ENTRADA_TARDIA)


def lado_de(tipo: str) -> str:
    """'compra' o 'venta', según el tipo de orden.

    Acepta también "compra"/"venta" a secas, que es como llegan los tests y los
    diagnósticos. Un tipo que no está en el vocabulario es un error: deducir el
    lado "por defecto" es exactamente cómo una venta acabó juzgada como compra.
    """
    if tipo in TIPOS_COMPRA or tipo == "compra":
        return "compra"
    if tipo in TIPOS or tipo == "venta":
        return "venta"
    raise ValueError(f"Tipo de orden desconocido: {tipo!r}; no se sabe si "
                     f"compra o vende.")

#: Las órdenes del día. El fichero —y su directorio— existen en el repositorio
#: desde el principio, aunque estén vacíos: `git add` de una ruta inexistente
#: aborta el commit, y `commit_y_push.sh` no silencia esos fallos a propósito.
#: Pasó dos veces en los primeros runs del ejecutor, con este fichero y con
#: bitacora_broker.csv.
ARCHIVO_PENDIENTES = config.BASE_DIR / "ordenes" / "pendientes.json"
ARCHIVO_ENVIADAS = config.ESTADO_DIR / "ordenes_enviadas.json"
ARCHIVO_BITACORA_BROKER = config.BASE_DIR / "bitacora_broker.csv"

COLUMNAS_BROKER = [
    "id", "sesion", "cuando_et", "cartera", "ticker", "simbolo_xtb", "tipo",
    "acciones", "estado", "precio", "orden_xtb", "objetivo", "stop",
    # `precio_disparo`: el bid exacto que vio el vigilante de precios en el
    # momento de decidir. Con él, `precio_simulador` (el nivel) y `precio` (lo
    # que XTB ejecutó) se pueden separar dos cosas que no son la misma:
    # cuánto se pasó el precio del nivel antes de que nadie lo viera, y cuánto
    # se movió entre que se vio y la orden llegó. La primera es el coste de
    # vigilar; la segunda, el de ejecutar.
    "precio_disparo",
    "precio_simulador", "slippage_pct", "error",
    # De dónde sale `precio`. XTB no siempre lo devuelve al ejecutar, y un
    # precio deducido no vale lo mismo que uno que dio el broker:
    #   xtb       lo devolvió XTB en la respuesta de la orden
    #   posicion  precio de apertura de la posición nueva (compras)
    #   saldo     deducido del cambio de saldo de la cuenta (ventas)
    #   manual    corregido a mano, con la evidencia en `error`
    "precio_fuente",
]


def simbolo_xtb(ticker: str) -> str:
    """Ticker canónico -> símbolo de XTB para acciones de EE.UU.

    XTB nombra las acciones estadounidenses con sufijo `.US` y guion en las
    clases de acciones, igual que yfinance: 'BRK.B' -> 'BRK-B.US'.
    """
    base = ticker.strip().upper().replace(".", "-")
    return f"{base}.US"


@dataclass
class Orden:
    """Una orden a mandar. Lo que el ejecutor necesita y nada más."""

    id: str
    tipo: str
    cartera: str
    ticker: str
    acciones: int
    sesion: str
    objetivo: float | None = None
    stop: float | None = None
    referencia: float | None = None
    precio_simulador: float | None = None
    fecha_limite: str | None = None
    id_operacion: int | None = None

    @property
    def simbolo(self) -> str:
        return simbolo_xtb(self.ticker)

    def __post_init__(self):
        if self.tipo not in TIPOS:
            raise ValueError(
                f"Tipo de orden desconocido: {self.tipo!r}. El vocabulario es "
                f"cerrado ({', '.join(TIPOS)}) para que añadir uno obligue a "
                f"decidir qué hace el ejecutor con él.")
        if not isinstance(self.acciones, int) or self.acciones < 1:
            raise ValueError(
                f"{self.ticker}: {self.acciones} acciones. XTB no acepta "
                f"fracciones por la API, así que una orden con menos de una "
                f"acción entera no se puede mandar.")


def identificador(sesion: str, cartera: str, ticker: str, tipo: str) -> str:
    return f"{sesion}|{cartera}|{ticker}|{tipo}"


# --------------------------------------------------------------------------- #
# pendientes.json
# --------------------------------------------------------------------------- #
#: Qué tipos de orden produce cada momento. Al guardar, un momento reemplaza
#: LOS SUYOS y no toca los del otro: es lo que impide que la pre-apertura de la
#: mañana borre las ventas que el post-cierre de la víspera dejó encoladas.
TIPOS_POR_EVENTO = {
    "preapertura": {COMPRA},
    "postcierre": {VENTA_TIEMPO},
}


def guardar_pendientes(ordenes: list[Orden], sesion: str, cartera: str,
                       evento: str | None = None, hoy: str | None = None) -> Path:
    """Escribe las órdenes pendientes CONSERVANDO las del otro momento.

    POR QUÉ NO SE REESCRIBE EL FICHERO ENTERO (fallo del 2026-09-29)
    ----------------------------------------------------------------
    Antes sí se reescribía, y eso convertía el fichero en "gana el último que
    escribe". Cada día hay DOS escritores para la misma sesión: el post-cierre
    de la víspera deja las ventas por tiempo del día siguiente, y la
    pre-apertura de la mañana deja las compras. El 2026-09-29 la pre-apertura
    escribió `"ordenes": []` —no había ninguna compra que hacer— y se llevó por
    delante las dos ventas por tiempo que el post-cierre había encolado para ese
    mismo día (VRT y GLW, las dos con `dia_limite` de ese día).

    Aquel día no costó dinero porque las dos eran posiciones heredadas del paper
    trading que XTB no tenía, pero el mecanismo destruye órdenes reales en
    cuanto el broker tenga posiciones propias, y lo hace en silencio: el fichero
    resultante es perfectamente válido, solo que le faltan órdenes.

    Ahora cada momento reemplaza únicamente los tipos que él genera. Se
    descartan además las órdenes de sesiones ya pasadas, que no son historia
    —para eso está `bitacora_broker.csv`— sino basura que solo puede confundir.
    """
    ARCHIVO_PENDIENTES.parent.mkdir(parents=True, exist_ok=True)
    hoy = hoy or datetime.now(config.TZ_ET).date().isoformat()
    mios = TIPOS_POR_EVENTO.get(evento or "", set())

    conservadas: list[dict] = []
    if evento and ARCHIVO_PENDIENTES.exists():
        try:
            previas = json.loads(ARCHIVO_PENDIENTES.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previas = {"ordenes": []}
        nuevos_ids = {o.id for o in ordenes}
        for o in previas.get("ordenes", []):
            if o.get("tipo") in mios:
                continue                       # lo regenera este momento
            if str(o.get("sesion", "")) < hoy:
                continue                       # de una sesión ya pasada
            if o.get("id") in nuevos_ids:
                continue                       # duplicado exacto
            conservadas.append(o)

    datos = {
        "sesion": sesion,
        "cartera_broker": cartera,
        "generado": datetime.now(config.TZ_ET).isoformat(),
        "ordenes": conservadas + [asdict(o) for o in ordenes],
    }
    ARCHIVO_PENDIENTES.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return ARCHIVO_PENDIENTES


def cargar_pendientes(ruta: Path | None = None) -> dict:
    ruta = ruta or ARCHIVO_PENDIENTES
    if not ruta.exists():
        return {"sesion": None, "cartera_broker": None, "ordenes": []}
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["ordenes"] = [Orden(**o) for o in datos.get("ordenes", [])]
    return datos


# --------------------------------------------------------------------------- #
# Idempotencia
# --------------------------------------------------------------------------- #
def cargar_enviadas(ruta: Path | None = None) -> dict:
    ruta = ruta or ARCHIVO_ENVIADAS
    if not ruta.exists():
        return {"enviadas": {}}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Un registro ilegible no puede tumbar el ejecutor, pero tampoco puede
        # pasar desapercibido: si se reinicia en blanco, el peor caso es una
        # orden repetida, así que se grita y se sigue.
        print("[ordenes] registro de enviadas ILEGIBLE; se reinicia. "
              "Revisa las posiciones en XTB antes del siguiente disparo.",
              flush=True)
        return {"enviadas": {}}
    datos.setdefault("enviadas", {})
    return datos


def guardar_enviadas(registro: dict, ruta: Path | None = None) -> None:
    ruta = ruta or ARCHIVO_ENVIADAS
    ruta.parent.mkdir(parents=True, exist_ok=True)
    registro["actualizado"] = datetime.now(config.TZ_ET).isoformat()
    ruta.write_text(
        json.dumps(registro, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def ya_enviada(registro: dict, id_orden: str) -> bool:
    return id_orden in registro.get("enviadas", {})


def marcar_enviada(registro: dict, id_orden: str, detalle: dict) -> None:
    """Se marca DESPUÉS de que el broker responda, nunca antes.

    Marcar antes perdería la orden para siempre si el envío fallara; marcar
    después, como mucho, la repite — y para eso está la reconciliación.
    """
    entrada = {"cuando": datetime.now(config.TZ_ET).isoformat(), **detalle}
    registro.setdefault("enviadas", {})[id_orden] = entrada
    # El registro de idempotencia es lo que impide repetir una orden. Si se
    # perdiera en un push fallido, el siguiente run volvería a mandarla.
    from . import diario
    diario.anotar("enviada", id=id_orden, detalle=entrada)


# --------------------------------------------------------------------------- #
# bitacora_broker.csv
# --------------------------------------------------------------------------- #
def registrar_ejecucion(orden: Orden, ejecucion, ruta: Path | None = None) -> None:
    """Anota en `bitacora_broker.csv` lo que el broker hizo DE VERDAD.

    Incluye el precio del simulador para la misma operación cuando se conoce,
    porque la diferencia entre los dos es el dato que justifica todo esto: el
    slippage real de operar la estrategia fuera del papel.
    """
    ruta = ruta or ARCHIVO_BITACORA_BROKER
    nuevo = not ruta.exists()
    slippage = None
    if (orden.precio_simulador and ejecucion.precio
            and orden.precio_simulador > 0):
        # Positivo = el broker lo hizo PEOR que el simulador, en la dirección
        # que corresponde: comprar más caro o vender más barato.
        #
        # En una salida `tiempo_diferido`, `precio_simulador` es el CIERRE del
        # día en que debía haber salido, así que esta columna mide exactamente
        # lo que cuesta el plan B: cuánto se pierde (o se gana) por cerrar a la
        # apertura siguiente en vez de a tiempo.
        #
        # El lado sale de `lado_de`, no de `tipo == COMPRA` (fallo del
        # 2026-10-05): una ENTRADA TARDÍA también compra, y con la comparación
        # vieja se le daba el signo de una venta — WDC pagó 439,71 frente a
        # una apertura de 429,93 y la página enseñaba −2,27 % en verde.
        bruto = ejecucion.precio / orden.precio_simulador - 1.0
        slippage = round(100.0 * (bruto if lado_de(orden.tipo) == "compra"
                                  else -bruto), 4)

    fila = {
        # La hora SIEMPRE: con (id, hora) el diario distingue dos intentos de
        # la misma orden, y una fila sin hora no se podría distinguir de nada.
        "id": orden.id, "sesion": orden.sesion,
        "cuando_et": ejecucion.cuando or datetime.now(config.TZ_ET).isoformat(),
        "cartera": orden.cartera, "ticker": orden.ticker,
        "simbolo_xtb": orden.simbolo, "tipo": orden.tipo,
        "acciones": orden.acciones, "estado": ejecucion.estado,
        "precio": ejecucion.precio, "orden_xtb": ejecucion.orden,
        "objetivo": orden.objetivo, "stop": orden.stop,
        "precio_disparo": orden.referencia,
        "precio_simulador": orden.precio_simulador,
        "slippage_pct": slippage, "error": ejecucion.error,
        "precio_fuente": getattr(ejecucion, "precio_fuente", None)
                         or ("xtb" if ejecucion.precio else None),
    }
    # Una venta parcial deja escrito lo que se vendió DE VERDAD, no lo pedido:
    # la reconciliación cuadra acciones contra XTB con esta columna.
    hechas = getattr(ejecucion, "acciones_hechas", None)
    if hechas is not None and ejecucion.estado == "ejecutada":
        fila["acciones"] = hechas
    if not nuevo:
        _migrar_columnas(ruta)
    with open(ruta, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BROKER)
        if nuevo:
            w.writeheader()
        w.writerow(fila)
    # PRIMERO en el CSV del árbol, que es lo que lee el resto de este proceso,
    # y en el diario local, que es lo que sobrevive a un push que choca.
    if ruta == ARCHIVO_BITACORA_BROKER:
        from . import diario
        diario.anotar("orden", fila=fila)


def _migrar_columnas(ruta: Path) -> None:
    """Reescribe la cabecera si al fichero le faltan columnas nuevas.

    Añadir una columna al final y seguir escribiendo filas con más campos que
    la cabecera produce un CSV que pandas lee desplazado. Se reescribe una vez,
    rellenando en blanco, y a partir de ahí todas las filas tienen la misma
    forma.
    """
    with open(ruta, encoding="utf-8", newline="") as f:
        lector = csv.DictReader(f, restval="")
        if list(lector.fieldnames or []) == COLUMNAS_BROKER:
            return
        filas = [{c: fila.get(c, "") for c in COLUMNAS_BROKER} for fila in lector]
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BROKER)
        w.writeheader()
        w.writerows(filas)


def actualizar_ejecucion(id_orden: str, ruta: Path | None = None, **campos) -> bool:
    """Corrige una fila ya escrita de `bitacora_broker.csv`. True si la tocó.

    POR QUÉ HACE FALTA
    ------------------
    Una compra a mercado enviada antes de la apertura se queda EN COLA, y XTB
    devuelve entonces el precio que tenía en ese momento, no el de apertura al
    que realmente se ejecutará. La fila que se escribe al enviar es, por tanto,
    provisional: el precio de verdad solo se conoce con el mercado abierto.

    La verificación posterior a la apertura vuelve sobre esa fila y la corrige.
    Se corrige, no se añade otra: dos filas para la misma orden convertirían la
    bitácora en algo que hay que interpretar, y la bitácora existe justamente
    para no tener que interpretar nada.
    """
    ruta = ruta or ARCHIVO_BITACORA_BROKER
    if not ruta.exists():
        return False
    with open(ruta, encoding="utf-8", newline="") as f:
        # `restval`: las filas escritas antes de que existiera una columna no
        # tienen esa clave, y reescribirlas sin esto reventaría.
        filas = [{c: fila.get(c, "") for c in COLUMNAS_BROKER}
                 for fila in csv.DictReader(f, restval="")]

    tocada = False
    for fila in filas:
        if fila.get("id") != id_orden:
            continue
        for k, v in campos.items():
            if k not in COLUMNAS_BROKER:
                raise ValueError(
                    f"Columna desconocida {k!r} en bitacora_broker.csv. Las "
                    f"columnas son una lista cerrada: {COLUMNAS_BROKER}")
            fila[k] = "" if v is None else v
        tocada = True

    if not tocada:
        return False
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BROKER)
        w.writeheader()
        w.writerows(filas)
    if ruta == ARCHIVO_BITACORA_BROKER:
        from . import diario
        diario.anotar("actualizar", id=id_orden, campos=campos)
    return True


def filas_de_sesion(sesion: str, ruta: Path | None = None) -> list[dict]:
    """Las filas de `bitacora_broker.csv` de una sesión concreta."""
    ruta = ruta or ARCHIVO_BITACORA_BROKER
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8", newline="") as f:
        return [fila for fila in csv.DictReader(f, restval="")
                if fila.get("sesion") == sesion]
