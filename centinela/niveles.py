"""Cuándo una posición abierta tiene que cerrarse por su nivel.

UNA SOLA DEFINICIÓN, DOS VIGILANTES
-----------------------------------
El mismo cruce lo miran dos cosas distintas: el vigilante de precios, tick a
tick mientras la sesión está abierta, y el ejecutor de ventas, una vez al día
justo antes del cierre como red de seguridad. Si cada uno llevara su propia
regla, tarde o temprano discreparían en un caso raro —y el caso raro es
precisamente cuando importa—, así que la regla vive aquí y los dos la llaman.

QUÉ PRECIO SE MIRA
------------------
El **bid**: el precio al que uno vende de verdad. Mirar el último precio
negociado o el ask daría disparos que el mercado no habría pagado, y en una
acción con spread ancho esa diferencia es exactamente el dinero que se pierde.

EL ORDEN IMPORTA
----------------
Si un mismo vistazo ve el precio por debajo del stop y por encima del objetivo
—no puede pasar con un bid, pero sí con dos lecturas separadas o con un hueco
de apertura—, gana el stop. Es la regla conservadora del simulador y no se
cambia aquí: este módulo no decide niveles, solo compara.
"""
from __future__ import annotations

from . import ordenes as ords

#: Qué tipo de orden nace de cada disparo, según quién lo vea.
INTRADIA = {"stop": ords.VENTA_STOP_INTRADIA,
            "objetivo": ords.VENTA_OBJETIVO_INTRADIA}
UNA_VEZ_AL_DIA = {"stop": ords.VENTA_STOP, "objetivo": ords.VENTA_OBJETIVO}


def cruce(bid: float | None, stop: float | None,
          objetivo: float | None) -> str | None:
    """'stop', 'objetivo' o None. La única comparación del sistema.

    Un bid que no existe o no es positivo no dispara nada: no se vende a ciegas
    porque falte un dato.
    """
    try:
        precio = float(bid)
    except (TypeError, ValueError):
        return None
    if precio <= 0:
        return None

    if stop is not None:
        try:
            if precio <= float(stop):
                return "stop"
        except (TypeError, ValueError):
            pass
    if objetivo is not None:
        try:
            if precio >= float(objetivo):
                return "objetivo"
        except (TypeError, ValueError):
            pass
    return None


def tipo_de_orden(disparo: str, en_vivo: bool) -> str:
    """El nombre con el que se registra la venta."""
    tabla = INTRADIA if en_vivo else UNA_VEZ_AL_DIA
    if disparo not in tabla:
        raise ValueError(
            f"Disparo desconocido: {disparo!r}. Solo hay dos, 'stop' y "
            f"'objetivo', y añadir uno obliga a decidir también cómo se "
            f"registra y cómo se pinta.")
    return tabla[disparo]


def sigue_abierta(broker, simbolo: str, acciones: int) -> bool:
    """¿Sigue XTB teniendo esa posición con al menos ese volumen?

    EL CANDADO COMPARTIDO, y no es un fichero a propósito. Al final de la sesión
    hay dos procesos que pueden querer cerrar la misma posición: el vigilante de
    precios, si el nivel se cruza, y el ejecutor de ventas por tiempo, si le
    toca salir ese día. Corren en máquinas distintas, no se ven entre sí y no
    hay forma de que se pongan de acuerdo a tiempo.

    Un fichero de bloqueo en el repositorio sería una promesa: entre que uno lo
    escribe y el otro lo lee caben varios segundos y un `git push`. Preguntarle
    al broker es un hecho, y es la misma fuente para los dos. El que llega
    segundo se encuentra la posición cerrada y se calla.
    """
    # SE SUMAN todas las entradas del símbolo. XTB enseña una posición por
    # orden de apertura, así que dos compras del mismo ticker son dos entradas;
    # mirar solo la primera daba "ya no está" con las acciones ahí.
    total = 0.0
    for p in broker.posiciones():
        if p.get("lado") != "buy" or p.get("ticker") != simbolo:
            continue
        try:
            total += float(p["acciones"])
        except (TypeError, ValueError, KeyError):
            return False
    return total >= int(acciones) and total > 0
