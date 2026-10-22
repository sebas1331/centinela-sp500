#!/usr/bin/env python
"""Gate del pre-apertura: ¿arrancar el vigilante por posiciones previas?

Cuenta las posiciones de la última foto publicada de broker.json SIN depender
del campo `lado`. estado_broker.volcar las escribe ya filtradas a compra pero NO
guarda ese campo; contar `lado == "buy"` daba SIEMPRE 0 y dejaba las posiciones
sin vigilar de madrugada (22-oct), igual que le pasaba a
vigilante_precios._posiciones_conocidas. Aquí se cuenta como en
hace_falta_vigilante.posiciones_publicadas: por las acciones de la foto.

Stdlib pura a propósito: este job del workflow no instala las dependencias del
repositorio, así que no puede importar `centinela.*`.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def posiciones_en_foto(ruta: Path) -> int:
    """Posiciones abiertas en la foto de broker.json (0 si no hay o es ilegible)."""
    if not ruta.exists():
        return 0
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return sum(1 for p in data.get("posiciones", [])
               if float(p.get("acciones") or 0) > 0)


def arrancar(n_posiciones: int, hora_utc: int) -> tuple[bool, str]:
    """¿Llamar al vigilante? Solo si hay posiciones y el mercado se acerca.

    EDT: apertura 13:30 UTC; EST: 14:30 UTC. Con 3 h de margen, desde las 10 UTC;
    cierre máximo 21:00 UTC. Fuera de esa franja es de madrugada y se espera al
    cron. La hora exacta (y si ya hay un vigilante vivo, o si falta demasiado) la
    afina el gate `hace_falta` del propio vigilante; aquí solo se evita llamarlo
    de madrugada o sin nada que vigilar.
    """
    if n_posiciones == 0:
        return False, "sin posiciones conocidas en broker.json"
    if not (10 <= hora_utc < 21):
        return False, f"son las {hora_utc:02d}h UTC; todavía de madrugada, se espera al cron"
    return True, f"{n_posiciones} posición(es) + hora compatible: llamar al vigilante"


def main() -> int:
    n = posiciones_en_foto(Path("estado/broker.json"))
    ok, motivo = arrancar(n, datetime.now(timezone.utc).hour)
    print(f"{n} posición(es) en broker.json; {motivo}", flush=True)
    if ok:
        with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a",
                  encoding="utf-8") as f:
            f.write("arrancar=si\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
