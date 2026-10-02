"""Incidentes con causa identificada y arreglo publicado.

POR QUÉ ESTO EXISTE
-------------------
Un rechazo de XTB tiene que pintar rojo: es una decisión del sistema que no
ocurrió. Pero una vez que la causa está identificada y el arreglo está en el
repositorio, ese mismo rojo deja de informar y empieza a estorbar — se queda
días en la pantalla, baja la fiabilidad y entrena a mirar el panel sin leerlo.

La diferencia entre las dos cosas no la puede adivinar un umbral de días: la
tiene que declarar alguien, diciendo QUÉ pasó, POR QUÉ y CON QUÉ COMMIT se
arregló. Eso es este fichero.

LO QUE NO HACE
--------------
Esto no silencia nada. Un incidente resuelto sigue viéndose en la página como
dato histórico; lo único que cambia es que no grita. Y solo se apagan las
órdenes que se declaran explícitamente: un rechazo nuevo, con cualquier causa
no registrada, sigue siendo rojo inmediato. Si la causa vuelve, el incidente
viejo no la tapa, porque las órdenes van listadas una a una.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config

ARCHIVO = config.ESTADO_DIR / "incidentes.json"

#: Los campos que un incidente tiene que traer. Sin el commit no es un
#: incidente resuelto: es un incidente que alguien prefiere no mirar.
OBLIGATORIOS = ("fecha", "titulo", "causa", "commit", "ordenes")


def cargar(ruta: Path | None = None) -> list[dict]:
    ruta = ruta or ARCHIVO
    if not ruta.exists():
        return []
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("[incidentes] fichero ilegible; se ignora.", flush=True)
        return []
    return datos.get("incidentes", []) if isinstance(datos, dict) else []


def guardar(incidentes: list[dict], ruta: Path | None = None) -> None:
    ruta = ruta or ARCHIVO
    for i in incidentes:
        faltan = [c for c in OBLIGATORIOS if not i.get(c)]
        if faltan:
            raise ValueError(
                f"Incidente sin {', '.join(faltan)}: {i.get('titulo') or i}. "
                f"Un incidente resuelto sin commit del arreglo no es un "
                f"incidente resuelto, es uno que alguien prefiere no mirar.")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps({"actualizado": datetime.now(config.TZ_ET).isoformat(),
                    "incidentes": incidentes},
                   ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")


def ordenes_resueltas(ruta: Path | None = None) -> set[str]:
    """Los identificadores de orden que un incidente resuelto ya explica."""
    fuera: set[str] = set()
    for i in cargar(ruta):
        fuera.update(str(o) for o in i.get("ordenes", []))
    return fuera


def esta_resuelta(id_orden: str, ruta: Path | None = None) -> bool:
    return str(id_orden) in ordenes_resueltas(ruta)


def excusa_componente(componente: str, fecha: str,
                      ruta: Path | None = None) -> dict | None:
    """El incidente que explica que ese componente fallara ESE día, si lo hay.

    El job de compras del 02/10 murió en rojo porque XTB rechazó la orden, y
    ese rechazo tiene causa y arreglo. Dejar el componente en rojo repetiría en
    el semáforo lo que ya está contado abajo, con su causa y su commit, y con
    menos información.

    La excusa va de la fecha del incidente a la de su arreglo, ambas incluidas,
    porque un fallo así no se queda en un día: la reconciliación del 30/09
    siguió dando diferencias cada mañana hasta que se arregló. Pasado el
    arreglo, no hay excusa: si vuelve a fallar, vuelve el rojo.
    """
    if not fecha:
        return None
    for i in cargar(ruta):
        if componente not in (i.get("componentes") or []):
            continue
        desde = str(i.get("fecha", ""))
        hasta = str(i.get("arreglado") or i.get("fecha", ""))
        if desde <= fecha <= hasta:
            return i
    return None


def ultimo_arreglo(ruta: Path | None = None) -> str | None:
    """La fecha del arreglo más reciente, en ISO.

    Es el corte desde el que la fiabilidad vuelve a medir algo útil: antes de
    esa fecha estaba midiendo un fallo conocido y ya corregido, así que
    arrastrarlo solo sirve para que el porcentaje tarde una semana en contar la
    verdad de hoy.
    """
    fechas = [str(i.get("arreglado") or i.get("fecha"))
              for i in cargar(ruta) if i.get("arreglado") or i.get("fecha")]
    return max(fechas) if fechas else None
