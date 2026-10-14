"""Qué símbolos ofrece XTB como acción al contado, cacheado por día.

POR QUÉ
-------
COHR el 2026-10-13: el modelo lo eligió, pero XTB no lo ofrece como acción al
contado. El ejecutor mandó la orden igual, XTB la rechazó ("Symbol not found")
y la reposición la reintentó tres veces más antes de rendirse en rojo. Un
símbolo inexistente es un error PERMANENTE: no cambia en 60 minutos y no cambia
mañana. Comprobarlo antes de decidir la entrada ahorra la orden, los reintentos
y un rojo de fiabilidad por algo que no es un fallo del broker.

Preguntárselo a XTB por cada candidato en cada run sería lento y repetido —la
lista de instrumentos no cambia en horas—, así que la respuesta se cachea por
día. La caché se reinicia sola cuando cambia el día.

LO QUE NO HACE
--------------
Si no se puede comprobar (la búsqueda falla), NO se descarta la entrada: se deja
pasar y que decida el envío, porque saltarse una compra válida por un fallo de
red es peor que intentarla. Contra el bucle de reintentos infinitos protege
además la reposición, que no reintenta un símbolo que no existe.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config

ARCHIVO = config.ESTADO_DIR / "xtb_disponibles.json"


def cargar(ruta: Path | None = None) -> dict:
    ruta = ruta or ARCHIVO
    if not ruta.exists():
        return {"dia": "", "simbolos": {}}
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("simbolos"), dict):
            return {"dia": str(d.get("dia", "")), "simbolos": d["simbolos"]}
    except (OSError, ValueError):
        pass
    return {"dia": "", "simbolos": {}}


def guardar(cache: dict, ruta: Path | None = None) -> None:
    ruta = ruta or ARCHIVO
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(cache, ensure_ascii=False, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[disponibilidad] no se pudo guardar la caché ({exc!r}); se sigue.",
              flush=True)


def disponible(broker, simbolo: str, hoy: str | None = None,
               ruta: Path | None = None) -> bool:
    """¿XTB ofrece `simbolo` como acción al contado hoy? Con caché diaria.

    Un acierto de caché del mismo día no vuelve a preguntar. Un fallo al
    comprobar (no una ausencia: un error) devuelve True y NO se cachea: mejor
    intentar la compra que saltarse una señal válida por un hipo de red.
    """
    hoy = hoy or datetime.now(config.TZ_ET).date().isoformat()
    cache = cargar(ruta)
    if cache.get("dia") != hoy:
        cache = {"dia": hoy, "simbolos": {}}          # día nuevo: se reinicia
    if simbolo in cache["simbolos"]:
        return bool(cache["simbolos"][simbolo])
    try:
        existe = bool(broker.existe_contado(simbolo))
    except Exception as exc:  # noqa: BLE001 — no poder mirar no descarta la señal
        print(f"[disponibilidad] no se pudo comprobar {simbolo} ({exc!r}); se "
              f"asume disponible y decide el envío.", flush=True)
        return True
    cache["simbolos"][simbolo] = existe
    guardar(cache, ruta)
    return existe
