"""La caché diaria de qué símbolos ofrece XTB como acción al contado.

COHR el 2026-10-13 no existía en XTB y el sistema lo mandó —y reintentó— cuatro
veces. Comprobarlo antes, con caché diaria, evita la orden imposible. Estos
tests fijan: se pregunta una vez por día, un fallo de red no descarta la señal,
y el día nuevo reinicia la caché.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import disponibilidad as disp  # noqa: E402


class BrokerFake:
    def __init__(self, existen, fallar=False):
        self.existen = set(existen)
        self.fallar = fallar
        self.consultas = []

    def existe_contado(self, simbolo):
        self.consultas.append(simbolo)
        if self.fallar:
            raise RuntimeError("XTB no responde")
        return simbolo in self.existen


def test_un_simbolo_que_existe_se_cachea_y_no_se_vuelve_a_preguntar(tmp_path):
    ruta = tmp_path / "disp.json"
    b = BrokerFake({"WDC.US"})
    assert disp.disponible(b, "WDC.US", hoy="2026-10-14", ruta=ruta) is True
    assert disp.disponible(b, "WDC.US", hoy="2026-10-14", ruta=ruta) is True
    assert b.consultas == ["WDC.US"]          # una sola consulta, el resto caché


def test_un_simbolo_que_no_existe_se_cachea_como_false(tmp_path):
    ruta = tmp_path / "disp.json"
    b = BrokerFake(set())                      # COHR.US no está
    assert disp.disponible(b, "COHR.US", hoy="2026-10-14", ruta=ruta) is False
    assert disp.disponible(b, "COHR.US", hoy="2026-10-14", ruta=ruta) is False
    assert b.consultas == ["COHR.US"]


def test_el_dia_nuevo_reinicia_la_cache(tmp_path):
    ruta = tmp_path / "disp.json"
    b = BrokerFake({"WDC.US"})
    disp.disponible(b, "WDC.US", hoy="2026-10-14", ruta=ruta)
    disp.disponible(b, "WDC.US", hoy="2026-10-15", ruta=ruta)   # otro día
    assert b.consultas == ["WDC.US", "WDC.US"]  # se vuelve a preguntar


def test_un_fallo_al_comprobar_no_descarta_la_senal(tmp_path):
    """No poder mirar (un hipo de red) NO es 'no existe': se asume disponible y
    decide el envío, y no se cachea (para reintentar la comprobación luego)."""
    ruta = tmp_path / "disp.json"
    b = BrokerFake(set(), fallar=True)
    assert disp.disponible(b, "WDC.US", hoy="2026-10-14", ruta=ruta) is True
    assert disp.cargar(ruta)["simbolos"] == {}  # no se cacheó el fallo
