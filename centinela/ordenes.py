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
TIPOS = (COMPRA, VENTA_TIEMPO)

ARCHIVO_PENDIENTES = config.BASE_DIR / "ordenes" / "pendientes.json"
ARCHIVO_ENVIADAS = config.ESTADO_DIR / "ordenes_enviadas.json"
ARCHIVO_BITACORA_BROKER = config.BASE_DIR / "bitacora_broker.csv"

COLUMNAS_BROKER = [
    "id", "sesion", "cuando_et", "cartera", "ticker", "simbolo_xtb", "tipo",
    "acciones", "estado", "precio", "orden_xtb", "objetivo", "stop",
    "precio_simulador", "slippage_pct", "error",
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
def guardar_pendientes(ordenes: list[Orden], sesion: str, cartera: str) -> Path:
    ARCHIVO_PENDIENTES.parent.mkdir(parents=True, exist_ok=True)
    datos = {
        "sesion": sesion,
        "cartera_broker": cartera,
        "generado": datetime.now(config.TZ_ET).isoformat(),
        "ordenes": [asdict(o) for o in ordenes],
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
    registro.setdefault("enviadas", {})[id_orden] = {
        "cuando": datetime.now(config.TZ_ET).isoformat(), **detalle}


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
        bruto = ejecucion.precio / orden.precio_simulador - 1.0
        slippage = round(100.0 * (bruto if orden.tipo == COMPRA else -bruto), 4)

    fila = {
        "id": orden.id, "sesion": orden.sesion, "cuando_et": ejecucion.cuando,
        "cartera": orden.cartera, "ticker": orden.ticker,
        "simbolo_xtb": orden.simbolo, "tipo": orden.tipo,
        "acciones": orden.acciones, "estado": ejecucion.estado,
        "precio": ejecucion.precio, "orden_xtb": ejecucion.orden,
        "objetivo": orden.objetivo, "stop": orden.stop,
        "precio_simulador": orden.precio_simulador,
        "slippage_pct": slippage, "error": ejecucion.error,
    }
    with open(ruta, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BROKER)
        if nuevo:
            w.writeheader()
        w.writerow(fila)
