#!/usr/bin/env python
"""Publica el diario local en el repositorio. Último paso de cada job que opera.

Uso: publicar_diario.py --mensaje "ejecutor XTB: compras [skip ci]"

Aplica el diario (ver `centinela/diario.py`) sobre el `origin/main` más reciente
y lo empuja, reintentando desde cero si otro workflow empujó entretanto. Nunca
rebasa, así que no puede chocar. Si después de todos los intentos no lo
consigue, sale en ROJO: lo que el broker hizo no está en el repositorio.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import diario  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mensaje", required=True)
    ap.add_argument("--rama", default="main")
    args = ap.parse_args()
    entradas = diario.leer()
    print(f"[diario] {len(entradas)} entrada(s) en {diario.ARCHIVO}", flush=True)
    sha = diario.publicar(args.mensaje, rama=args.rama, entradas=entradas)
    print(f"✅ diario publicado: {sha}" if sha else "✅ nada nuevo que publicar",
          flush=True)

    # RED DE SEGURIDAD. Este paso sustituye al commit de "estado bitacora_broker
    # ordenes": si el job escribió en alguno de esos sitios algo que el diario
    # NO cubre, se perdería sin que nadie lo viera. Se mira el árbol del job y
    # se rompe en rojo si hay algo fuera de lo que el diario sabe publicar.
    import subprocess
    r = subprocess.run(["git", "status", "--porcelain", "--", "estado",
                        "bitacora_broker.csv", "ordenes"],
                       cwd=str(diario.config.BASE_DIR), capture_output=True,
                       text=True)
    if r.returncode != 0:
        print(f"::error::no se pudo mirar el árbol del job: {r.stderr.strip()}",
              flush=True)
        return 1
    sueltos = [ln[3:] for ln in r.stdout.splitlines()
               if ln[3:].strip() not in diario.RUTAS]
    if sueltos:
        print(f"::error::El job dejó cambios que el diario no publica: "
              f"{', '.join(sueltos)}. Se perderían: hay que añadirlos al diario "
              f"(centinela/diario.py) o publicarlos por otro camino.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
