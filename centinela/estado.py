"""Estado persistente del sistema (estado/estado.json), pequeño y commiteable.

Guarda:
  - posiciones abiertas de cada cartera (A con stop, B sin stop),
  - entradas decididas en la pre-apertura y pendientes de ejecutar al open,
  - fechas de la última pre-apertura / post-cierre procesadas (IDEMPOTENCIA:
    evita doble ejecución cuando los dos crons UTC caen el mismo día),
  - un contador para asignar ids de decisión (grupo A/B).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import config


def nuevo_estado() -> dict:
    return {
        "ultima_preapertura": None,
        "ultima_postcierre": None,
        "entradas_pendientes": [],   # decididas pre-apertura, sin ejecutar
        "posiciones": {"A": [], "B": []},
        "contador_grupo": 0,
        "actualizado": None,
        "ultima_ejecucion": {},      # sello de prueba de vida por escaneo
    }


def sellar_ejecucion(estado: dict, escaneo: str) -> None:
    """Deja constancia de QUIÉN escribió y CUÁNDO.

    Es la prueba de vida que se lee desde el celular sin abrir los logs: si un
    día laborable el sello no avanza, el sistema no corrió. Guardar el run de
    Actions permite además saltar directo al log del run culpable.
    """
    sello = {"cuando": datetime.now(config.TZ_ET).isoformat()}
    run = os.environ.get("GITHUB_RUN_ID")
    if run:
        sello["run"] = run
        sello["url"] = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run}")
    estado.setdefault("ultima_ejecucion", {})[escaneo] = sello


def cargar() -> dict:
    if config.ARCHIVO_ESTADO.exists():
        with open(config.ARCHIVO_ESTADO, encoding="utf-8") as f:
            est = json.load(f)
        base = nuevo_estado()
        base.update(est)
        base.setdefault("posiciones", {"A": [], "B": []})
        base["posiciones"].setdefault("A", [])
        base["posiciones"].setdefault("B", [])
        return base
    return nuevo_estado()


def guardar(estado: dict) -> None:
    estado["actualizado"] = datetime.now(config.TZ_ET).isoformat()
    with open(config.ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2, sort_keys=True)


def siguiente_grupo(estado: dict) -> int:
    estado["contador_grupo"] = int(estado.get("contador_grupo", 0)) + 1
    return estado["contador_grupo"]


def ya_proceso_preapertura(estado: dict, fecha_iso: str) -> bool:
    return estado.get("ultima_preapertura") == fecha_iso


def ya_proceso_postcierre(estado: dict, fecha_iso: str) -> bool:
    return estado.get("ultima_postcierre") == fecha_iso


def tickers_con_posicion(estado: dict, cartera: str) -> set:
    return {p["ticker"] for p in estado["posiciones"].get(cartera, [])}
