"""El presupuesto de tamaño de las páginas, medido donde se nota.

EL TECHO DE 50 KB EN CRUDO ERA UN MAL NÚMERO (cambiado el 2026-09-29). Medía el
fichero sin comprimir, y el 20 % de estas páginas son comentarios: 10,4 KB de
explicación solo en el dashboard. Al añadir el coste de los disparos en vivo, el
fichero pasó de 50 KB y la única forma de volver a entrar era borrar
explicaciones — que es exactamente lo que este repositorio no quiere perder.

Lo que un teléfono siente es lo que VIAJA, y eso va comprimido: el dashboard son
16,9 KB por el cable, no 51,8. Así que el techo pasa a medir eso.

El segundo techo, en crudo y mucho más holgado, existe para lo que el primero no
ve: que alguien meta sin querer un volcado de datos dentro del HTML. Un JSON
grande comprime muy bien y no movería la aguja del primero.

Los dos rompen la generación en rojo.
"""
from __future__ import annotations

import gzip
from pathlib import Path

#: Lo que viaja al navegador, comprimido. Es el número que se nota.
TECHO_COMPRIMIDO = 25 * 1024
#: En crudo, para cazar un volcado de datos incrustado por descuido.
TECHO_CRUDO = 80 * 1024
#: Los JSON de datos sí se miden en crudo: son datos, no explicación.
TECHO_JSON = 500 * 1024


def medir(ruta: Path) -> tuple[int, int]:
    """(bytes en disco, bytes comprimidos)."""
    crudo = ruta.read_bytes()
    return len(crudo), len(gzip.compress(crudo))


def exigir(ruta: Path) -> str:
    """Rompe en rojo si la página se pasa. Devuelve la línea para el log."""
    crudo, comprimido = medir(ruta)
    if comprimido > TECHO_COMPRIMIDO:
        raise RuntimeError(
            f"{ruta.name} viaja {comprimido / 1024:.1f} KB comprimidos y el "
            f"techo son {TECHO_COMPRIMIDO / 1024:.0f} KB.")
    if crudo > TECHO_CRUDO:
        raise RuntimeError(
            f"{ruta.name} pesa {crudo / 1024:.0f} KB en crudo y el techo son "
            f"{TECHO_CRUDO / 1024:.0f} KB. Comprimido cabe, así que casi seguro "
            f"que hay datos incrustados en el HTML que deberían ir aparte.")
    return (f"{ruta.name}: {comprimido / 1024:.1f} KB por el cable "
            f"({crudo / 1024:.1f} KB en disco)")
