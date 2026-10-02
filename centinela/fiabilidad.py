"""Si el broker está respondiendo, que es otra pregunta que si la estrategia gana.

POR QUÉ UN FICHERO APARTE DE LA BITÁCORA
-----------------------------------------
`bitacora_broker.csv` dice qué operaciones hizo la estrategia. Esto dice si XTB
está aceptando órdenes. Mezclarlas haría que una racha de fallos del broker
pareciera una racha de operaciones, y son cosas que se leen con ojos distintos:
una la mira quien quiere saber cuánto gana esto; la otra, quien quiere saber si
mañana se va a poder operar.

Aquí entra **cada intento**, incluidos los que no llegaron a nada. La bitácora
solo recoge lo que acabó siendo una operación, así que los siete fallos del
2026-09-29 no dejaron rastro en ninguna parte: el sistema supo que algo iba mal
esa tarde porque yo estaba mirando, no porque lo midiera.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

from . import config

ARCHIVO = config.ESTADO_DIR / "fiabilidad_broker.csv"
COLUMNAS = ["cuando", "sesion", "simbolo", "tipo", "estado", "detalle"]

#: El vocabulario, cerrado. Cada uno responde a una pregunta distinta y por eso
#: no se pueden juntar:
EJECUTADA = "ejecutada"            #: XTB tiene la posición. Lo único que cuenta.
RECHAZADA = "rechazada"            #: XTB la descartó. Rojo.
EN_COLA = "en-cola"                #: aceptada y todavía viva, sin desenlace.
CONFIRMADA = "confirmada"          #: aceptada, sin poder comprobarla después.
AMBIGUA_RESUELTA_SI = "ambigua-si"  #: respondió vacío pero la orden SÍ entró
AMBIGUA_RESUELTA_NO = "ambigua-no"  #: respondió vacío y NO entró: se reintenta
AMBIGUA_SIN_RESOLVER = "ambigua-?"  #: respondió vacío y no se pudo comprobar
FALLIDA = "fallida"                #: XTB la rechazó al enviarla, con motivo

ESTADOS = (EJECUTADA, RECHAZADA, EN_COLA, CONFIRMADA, AMBIGUA_RESUELTA_SI,
           AMBIGUA_RESUELTA_NO, AMBIGUA_SIN_RESOLVER, FALLIDA)

#: Los que cuentan como "algo fue mal" para el semáforo.
MALOS = (RECHAZADA, FALLIDA, AMBIGUA_RESUELTA_NO, AMBIGUA_SIN_RESOLVER)
#: Los que cuentan como ejecutada de verdad. "Aceptada" NO está aquí: fue
#: exactamente lo que engañó el 2026-09-30.
CUENTAN_COMO_EJECUTADA = (EJECUTADA, AMBIGUA_RESUELTA_SI)


def anotar(estado: str, simbolo: str, tipo: str, detalle: str = "",
           ruta: Path | None = None) -> None:
    """Deja constancia de un intento de orden. Nunca falla hacia arriba.

    Un problema al escribir este diario no puede tumbar una operación: lo peor
    que puede pasar por no anotar es que la métrica salga incompleta, y lo peor
    que puede pasar por reventar aquí es no vender una posición que cruzó su
    stop. La prioridad está clara.
    """
    if estado not in ESTADOS:
        raise ValueError(
            f"Estado desconocido: {estado!r}. La lista es cerrada ({ESTADOS}) "
            f"para que añadir uno obligue a decidir también cómo se cuenta.")
    ruta = ruta or ARCHIVO
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not ruta.exists()
        ahora = datetime.now(config.TZ_ET)
        with open(ruta, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS)
            if nuevo:
                w.writeheader()
            w.writerow({
                "cuando": ahora.isoformat(),
                "sesion": ahora.date().isoformat(),
                "simbolo": simbolo,
                "tipo": tipo,
                "estado": estado,
                "detalle": (detalle or "")[:300],
            })
    except OSError as exc:
        print(f"[fiabilidad] no se pudo anotar ({exc!r}); se sigue.", flush=True)


def resumen(dias: int = 7, ruta: Path | None = None,
            ahora: datetime | None = None) -> dict:
    """Cuántos intentos y cómo acabaron, en los últimos `dias`."""
    ruta = ruta or ARCHIVO
    ahora = ahora or datetime.now(config.TZ_ET)
    corte = (ahora - timedelta(days=dias)).date().isoformat()

    cuenta = {e: 0 for e in ESTADOS}
    ultimo_malo = None
    if ruta.exists():
        with open(ruta, encoding="utf-8", newline="") as f:
            for fila in csv.DictReader(f):
                if str(fila.get("sesion", "")) < corte:
                    continue
                estado = fila.get("estado")
                if estado not in cuenta:
                    continue
                cuenta[estado] += 1
                if estado in MALOS:
                    ultimo_malo = {
                        "cuando": fila.get("cuando"),
                        "simbolo": fila.get("simbolo"),
                        "tipo": fila.get("tipo"),
                        "estado": estado,
                        "detalle": fila.get("detalle") or None,
                    }

    enviadas = sum(cuenta.values())
    ejecutadas = sum(cuenta[e] for e in CUENTAN_COMO_EJECUTADA)
    rechazadas = cuenta[RECHAZADA] + cuenta[FALLIDA]
    ambiguas = (cuenta[AMBIGUA_RESUELTA_SI] + cuenta[AMBIGUA_RESUELTA_NO]
                + cuenta[AMBIGUA_SIN_RESOLVER])
    return {
        "dias": dias,
        "enviadas": enviadas,
        # EJECUTADAS, no "confirmadas". La diferencia la pagó el 2026-09-30:
        # dos órdenes aceptadas por XTB, anotadas "en_cola", rechazadas en
        # realidad, y una página que decía "fiabilidad 100 %".
        "ejecutadas": ejecutadas,
        "rechazadas": rechazadas,
        "en_cola": cuenta[EN_COLA] + cuenta[CONFIRMADA],
        "ambiguas": ambiguas,
        "desglose": dict(cuenta),
        "ultimo_problema": ultimo_malo,
        # El porcentaje se calcula sobre EJECUTADAS. Una orden que el broker
        # aceptó y no ejecutó no es media orden: es ninguna.
        "fiabilidad_pct": (round(100.0 * ejecutadas / enviadas, 1)
                           if enviadas else None),
    }
