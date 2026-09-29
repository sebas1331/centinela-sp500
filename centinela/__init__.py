"""Centinela SP500 — sistema autónomo de paper trading (dinero 100% simulado).

Experimento educativo. No promete rentabilidad. Todas las métricas se reportan
tal cual, aunque sean malas. Presupuesto $0: solo datos gratuitos.
"""

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# LA COPIA DEL CLIENTE DE XTB VA POR DELANTE DE LO INSTALADO
#
# `vendor/xtb_api/` es una copia de xtb-api-python 0.10.0 con nuestros parches
# aplicados dentro (ver vendor/xtb_api/CAMBIOS.md). Está ahí porque el
# repositorio original desapareció —devuelve 404— y un sistema que manda
# órdenes a un broker no puede depender de que un paquete siga publicado.
#
# Se inserta al PRINCIPIO de sys.path: si algún día vuelve a instalarse el
# paquete de PyPI, gana la copia, que es la que está probada aquí. Y se hace en
# el __init__ del paquete y no en cada script porque basta con que alguien
# importe `xtb_api` antes que nosotros para que gane el otro.
# ---------------------------------------------------------------------------
import sys as _sys
from pathlib import Path as _Path

_VENDOR = str(_Path(__file__).resolve().parent.parent / "vendor")
if _VENDOR not in _sys.path:
    _sys.path.insert(0, _VENDOR)
