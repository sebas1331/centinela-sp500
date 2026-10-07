"""Un git colgado no puede dejar al vigilante sin mirar precios.

07/10, 11:05 ET: GitHub tuvo un incidente de Git Operations/Actions de 10 minutos
y el `git push` del latido se quedó esperando. `_git` no tenía timeout, así que
el bucle de precios —que publica el latido en línea— se paró hasta que el
supervisor lo mató a los 6 minutos y lo relanzó dos veces.
"""
from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import latido as lat  # noqa: E402

# El conftest sustituye `publicar` y `leer` por bloqueos para que ningún test
# toque GitHub de verdad. Aquí se necesitan los reales, y es seguro: corren
# contra un `git` falso que se queda esperando, nunca contra uno de verdad.
_PUBLICAR, _LEER = lat.publicar, lat.leer


@pytest.fixture
def git_colgado(tmp_path, monkeypatch):
    """Un `git` que nunca contesta, el primero en el PATH."""
    falso = tmp_path / "bin" / "git"
    falso.parent.mkdir()
    falso.write_text("#!/bin/sh\nsleep 30\n")
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{falso.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(lat, "GIT_TIMEOUT_SEG", 0.5)
    monkeypatch.setattr(lat, "publicar", _PUBLICAR)
    monkeypatch.setattr(lat, "leer", _LEER)
    return tmp_path / "trabajo"


def test_un_git_colgado_es_un_error_y_no_una_espera_infinita(git_colgado):
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="no respondió"):
        lat._git("push", "--force", "origin", "HEAD:refs/heads/latido",
                 cwd=git_colgado.parent)
    assert time.monotonic() - t0 < 5


def test_publicar_tolerante_aguanta_un_git_colgado_y_sigue(git_colgado):
    """El vigilante NO muere ni se bloquea: devuelve el último éxito."""
    t0 = time.monotonic()
    datos = lat.construir("2026-10-07T09:35:00-04:00", [])
    r = lat.publicar_tolerante(datos, git_colgado, ultimo_ok=100.0, ahora=160.0)
    assert r == 100.0
    assert time.monotonic() - t0 < 5


def test_si_el_git_sigue_colgado_mas_de_10_min_se_retira(git_colgado):
    datos = lat.construir("2026-10-07T09:35:00-04:00", [])
    with pytest.raises(RuntimeError, match="me retiro"):
        lat.publicar_tolerante(datos, git_colgado, ultimo_ok=0.0, ahora=700.0)


def test_leer_con_git_colgado_devuelve_none(git_colgado):
    assert lat.leer(git_colgado) is None


# --- sin_stop por latido ----------------------------------------------------
def _v(ticker, stop=378.0):
    return {"ticker": ticker, "stop": stop, "acciones": 4}


def test_sin_stop_distingue_confirmado_ausente_y_desconocido():
    broker = {"stops": {"WDC.US": {"orden": 917531939}}}
    assert lat.sin_stop_en_xtb([_v("WDC")], broker) == []
    assert lat.sin_stop_en_xtb([_v("WDC"), _v("FICO")], broker) == ["FICO"]
    # sin nivel de stop no se exige stop en XTB (cartera B)
    assert lat.sin_stop_en_xtb([_v("FICO", stop=None)], broker) == []
    # clases de acciones: BRK.B -> BRK-B.US
    assert lat.sin_stop_en_xtb([_v("BRK.B")], {"stops": {"BRK-B.US": {"orden": 1}}}) == []
    # orden sin número = no confirmada
    assert lat.sin_stop_en_xtb([_v("WDC")], {"stops": {"WDC.US": {}}}) == ["WDC"]
    # no se sabe: sin foto, sin lista de stops, o con la foto fallida
    assert lat.sin_stop_en_xtb([_v("WDC")], None) is None
    assert lat.sin_stop_en_xtb([_v("WDC")], {"stops": None}) is None
    assert lat.sin_stop_en_xtb([_v("WDC")], broker, error_broker="boom") is None


def test_cada_latido_del_historial_lleva_su_sin_stop():
    broker = {"stops": {}}
    d = lat.construir("x", [_v("WDC")], broker=broker)
    assert d["historial"][-1]["sin_stop"] == ["WDC"]
    d = lat.construir("x", [_v("WDC")], historial=d["historial"],
                      broker={"stops": {"WDC.US": {"orden": 1}}})
    assert [h["sin_stop"] for h in d["historial"]] == [["WDC"], []]
