"""La copia del cliente de XTB: que exista, que se use y que lleve sus parches.

El repositorio del proyecto devuelve 404 desde el 2026-09-29. El paquete sigue
publicado, pero un sistema que manda órdenes a un broker no puede depender de
que siga estando mañana. Está copiado en vendor/xtb_api/ y estos tests fijan lo
que no puede romperse sin que alguien se entere.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
VENDOR = RAIZ / "vendor" / "xtb_api"
sys.path.insert(0, str(RAIZ))

import centinela  # noqa: F401,E402  — pone vendor/ en sys.path


def test_la_copia_esta_en_el_repositorio():
    assert (VENDOR / "client.py").exists()
    assert (VENDOR / "grpc" / "client.py").exists()


def test_viene_con_su_licencia():
    """Es MIT y hay que respetarla: se copia el texto, sin tocar."""
    licencia = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licencia
    assert "Łukasz Lis" in licencia


def test_los_cambios_estan_documentados_uno_a_uno():
    cambios = (VENDOR / "CAMBIOS.md").read_text(encoding="utf-8")
    assert "Parche 1" in cambios and "Parche 2" in cambios
    # Y dice cómo actualizarla, que es lo que se olvida siempre.
    assert "Cómo actualizar esta copia" in cambios


def test_es_la_copia_la_que_se_usa_y_no_lo_instalado():
    """Si algún día alguien instala el paquete de PyPI, gana la copia: es la
    que está probada aquí y la que lleva los parches."""
    import xtb_api
    assert str(VENDOR) in xtb_api.__file__, \
        f"se está usando {xtb_api.__file__}, no la copia"


def test_funciona_sin_el_paquete_instalado():
    """La prueba de fuego: un intérprete limpio, sin el paquete, importando el
    sistema entero."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util, sys;"
         "sys.path.insert(0, %r);" % str(RAIZ) +
         "import centinela, xtb_api;"
         "from xtb_api.client import XTBClient;"
         "print(xtb_api.__file__)"],
        capture_output=True, text=True, cwd=str(RAIZ))
    assert r.returncode == 0, r.stderr[-400:]
    assert "vendor" in r.stdout


def test_ya_no_se_descarga_de_PyPI():
    reqs = (RAIZ / "requirements-broker.txt").read_text(encoding="utf-8")
    lineas = [l.strip() for l in reqs.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert not any("xtb-api-python" in l for l in lineas), \
        "sigue instalándose el paquete: la copia no sirve de nada"
    # Pero SUS dependencias sí hacen falta, que antes venían de arrastre.
    for dep in ("httpx", "playwright", "pydantic", "websockets", "pyotp"):
        assert any(l.startswith(dep) for l in lineas), f"falta {dep}"


# --------------------------------------------------------------------------- #
# Los parches, vivos dentro de la copia
# --------------------------------------------------------------------------- #
def test_el_parche_del_instrumento_esta_aplicado():
    from xtb_api.client import _is_cash_instrument  # noqa: F401
    fuente = (VENDOR / "client.py").read_text(encoding="utf-8")
    assert "CENTINELA" in fuente and "CAMBIOS.md, parche 1" in fuente
    # Y lo que el original hacía y ya no: devolver el primero sin más.
    assert "return results[0].instrument_id" not in fuente


def test_el_parche_de_las_cabeceras_esta_aplicado():
    from xtb_api.grpc.client import _centinela_motivo, CENTINELA_ULTIMO_ERROR
    assert _centinela_motivo({"grpc-status": "3", "grpc-message": "x"}) \
        == "INVALID_ARGUMENT (3): x"
    assert isinstance(CENTINELA_ULTIMO_ERROR, dict)
    fuente = (VENDOR / "grpc" / "client.py").read_text(encoding="utf-8")
    assert "CAMBIOS.md, parche 2" in fuente


def test_el_parche_no_cambia_lo_que_devuelve_la_llamada():
    """Solo deja de tirar la información: con cuerpo vacío sigue devolviendo
    b'', como el original. Si cambiara eso, cambiaría el comportamiento del
    cliente y no sería un parche, sería un fork."""
    fuente = (VENDOR / "grpc" / "client.py").read_text(encoding="utf-8")
    bloque = fuente.split("if not resp.text:")[1].split("return base64")[0]
    assert 'return b""' in bloque
