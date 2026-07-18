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
    }


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
