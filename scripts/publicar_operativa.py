#!/usr/bin/env python
"""Regenera la página de operativa y la publica, sin rebase y sin choques.

Uso: publicar_operativa.py --mensaje "operativa: republicada tras ... [skip ci]"

POR QUÉ (13 rojos en la semana del 28/09)
-----------------------------------------
Cada workflow republicaba la página con `commit_y_push.sh`, que rebasa. Dos
workflows con grupos de concurrencia distintos escribían `docs/operativa.json`
a la vez, el rebase no sabía juntar dos versiones de un JSON generado y el run
moría con "could not apply ... operativa".

Ahora hay un solo grupo (`centinela-operativa`) para todas las regeneraciones
y, además, la página no se rebasa nunca: si el push choca, se baja el origin
nuevo, se REGENERA encima y se vuelve a empujar. La página es una función de
los datos; rehacerla sobre los datos más nuevos siempre es correcto, y rebasar
una versión vieja nunca lo es.

Este job solo toca `docs/`. La salud de cada componente la registra el propio
componente y la publica por el diario: si GitHub cancela una regeneración que
esperaba turno, no se pierde nada, porque la siguiente parte de lo último.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

INTENTOS = 5
AUTOR = ("centinela-bot", "actions@users.noreply.github.com")


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(RAIZ), capture_output=True,
                       text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} falló ({r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mensaje", required=True)
    ap.add_argument("--rama", default=os.environ.get("GITHUB_REF_NAME", "main"))
    args = ap.parse_args()

    import generar_operativa as go

    ultimo = ""
    for intento in range(1, INTENTOS + 1):
        try:
            # Siempre desde el origin más nuevo: este job no tiene nada propio
            # que conservar, solo regenera lo que digan los datos publicados.
            _git("fetch", "-q", "origin", args.rama)
            _git("reset", "-q", "--hard", f"origin/{args.rama}")
            datos, cambios = go.generar()
            if not cambios:
                print("operativa sin cambios: nada que publicar.", flush=True)
                return 0
            _git("add", "--", "docs")
            _git("-c", f"user.name={AUTOR[0]}", "-c", f"user.email={AUTOR[1]}",
                 "commit", "-q", "-m", args.mensaje, "-m",
                 f"run: {os.environ.get('GITHUB_RUN_ID', 'local')} | "
                 f"semáforo: {datos['semaforo']['color']}")
            sha = _git("rev-parse", "HEAD")
            _git("push", "-q", "origin", f"HEAD:{args.rama}")
            _git("fetch", "-q", "origin", args.rama)
            _git("merge-base", "--is-ancestor", sha, f"origin/{args.rama}")
            print(f"✅ operativa publicada {sha[:7]} (semáforo "
                  f"{datos['semaforo']['color']}, intento {intento}).", flush=True)
            return 0
        except RuntimeError as exc:
            ultimo = str(exc)
            print(f"intento {intento}/{INTENTOS}: {ultimo}. Se regenera sobre el "
                  f"origin nuevo.", flush=True)
            time.sleep(3 * intento)
    print(f"::error::No se pudo publicar la página de operativa tras {INTENTOS} "
          f"intentos: {ultimo}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
