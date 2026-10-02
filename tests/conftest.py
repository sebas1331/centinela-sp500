"""Nada de lo que hagan los tests puede tocar el estado real del sistema.

POR QUÉ ESTO EXISTE (2026-09-29)
--------------------------------
Al añadir el diario de fiabilidad del broker apareció que los tests lo estaban
escribiendo **de verdad**: `enviar_resolviendo` anota cada intento, y los tests
que lo ejercitan no redirigían ese fichero. El resultado fue un
`estado/fiabilidad_broker.csv` con once órdenes inventadas —incluida una venta
"fallida" de MRNA que nunca ocurrió— que la página publicó como métrica real.

No es un descuido de un test concreto: es que cada fichero de tests tenía que
acordarse por su cuenta. Aquí se hace una sola vez y para todos, así que añadir
un test nuevo no puede volver a ensuciar nada.

Los ficheros que un test podría escribir sin querer se redirigen a un temporal
por test. Si mañana aparece otro, se añade aquí y no en veinte sitios.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))


@pytest.fixture(autouse=True)
def nada_toca_el_estado_real(tmp_path, monkeypatch):
    """Redirige a un temporal todo lo que se escribe fuera de docs/."""
    from centinela import config, datos_erroneos, fiabilidad, latido, ordenes, salud

    # La bitácora y el estado del simulador. Un test que cierre una posición
    # —el cierre por dato erróneo lo hace— escribiría si no la bitácora REAL
    # del repositorio, que es la fuente de verdad de todo el sistema. Hasta
    # ahora cada fichero de tests se acordaba por su cuenta; esto lo hace una
    # vez para todos, que es de lo que va este conftest.
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_SQLITE",
                        tmp_path / "bitacora.sqlite")
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_CSV", tmp_path / "bitacora.csv")
    monkeypatch.setattr(config, "ARCHIVO_ESTADO", tmp_path / "estado.json")
    monkeypatch.setattr(datos_erroneos, "ARCHIVO",
                        tmp_path / "datos_erroneos.json")
    monkeypatch.setattr(fiabilidad, "ARCHIVO", tmp_path / "fiabilidad.csv")
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setattr(ordenes, "ARCHIVO_BITACORA_BROKER",
                        tmp_path / "bitacora_broker.csv")
    monkeypatch.setattr(ordenes, "ARCHIVO_PENDIENTES", tmp_path / "pendientes.json")
    monkeypatch.setattr(ordenes, "ARCHIVO_ENVIADAS", tmp_path / "enviadas.json")
    # El latido empuja a una rama por force-push: que ningún test pueda ni
    # rozarlo.
    monkeypatch.setattr(latido, "publicar",
                        lambda *_a, **_k: pytest.fail(
                            "un test intentó publicar el latido de verdad"))
    return tmp_path
