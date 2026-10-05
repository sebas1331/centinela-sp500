#!/usr/bin/env python
"""Guardián del vigilante de precios: corre en OTRO runner y, si el vigilante
deja de latir con el mercado abierto, lanza uno de rescate.

POR QUÉ HACE FALTA ADEMÁS DEL SUPERVISOR
----------------------------------------
El supervisor (`supervisor_vigilante.py`) relanza el proceso en segundos si
muere o se cuelga, pero vive en el mismo runner: si GitHub pierde la máquina,
se van los dos. Nada dentro de un runner muerto puede relanzar nada, y los
crons de este repositorio llegan con horas de retraso. Este guardián es un job
paralelo del mismo run, en otra máquina, que mira el latido publicado.

CUÁNDO RESCATA
--------------
Cuando el latido del vigilante de SU run lleva más de `RESCATE_MIN` minutos sin
moverse con la sesión abierta. El umbral está por encima de los 10 minutos que
tarda un vigilante vivo pero incapaz de publicar en retirarse solo
(`latido.publicar_tolerante`): así nunca hay dos vigilando a la vez.

El rescate va en su propio grupo de concurrencia (`vigilante-precios-rescate`),
así que no espera a que GitHub dé por muerto el job viejo: arranca ya.

CUÁNDO SE VA SIN HACER NADA
---------------------------
  * Su vigilante terminó bien (latido "en reposo").
  * Otro vigilante más nuevo está latiendo (un relevo o un rescate).
  * La sesión cerró.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import calendario, config, latido as lat  # noqa: E402

#: Minutos sin latido para rescatar. > latido.MUERTO_MINUTOS (10): un vigilante
#: vivo que no puede publicar se retira solo a los 10, y así nunca coinciden dos.
RESCATE_MIN = lat.MUERTO_MINUTOS + 1
#: Cuánto se le da al vigilante para su PRIMER latido desde que abre la sesión
#: o arranca el guardián: instalar Chromium ha llegado a tardar 14 min.
PRIMER_LATIDO_MIN = 30
MIRAR_CADA_SEG = 60

SEGUIR, SALIR, RESCATAR = "seguir", "salir", "rescatar"


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%H:%M:%S ET}] [guardián] {msg}",
          flush=True)


def decidir(latido: dict | None, mi_run: str, arranque: datetime,
            ahora: datetime) -> tuple[str, str]:
    """(acción, motivo). La regla, separada del mundo para poder probarla."""
    ac = calendario.apertura_cierre_et(ahora.date().isoformat())
    if ac is None or not calendario.es_dia_de_mercado(ahora.date()):
        return SALIR, "hoy no hay sesión"
    apertura, cierre = ac
    if ahora >= cierre:
        return SALIR, "la sesión cerró"
    if ahora < apertura:
        return SEGUIR, "aún no abre"

    def _sin_latido_propio() -> tuple[str, str]:
        desde = max(arranque, apertura)
        espera = (ahora - desde).total_seconds() / 60.0
        if espera > PRIMER_LATIDO_MIN:
            return RESCATAR, (f"mi vigilante no ha latido en {espera:.0f} min "
                              f"de sesión (más de {PRIMER_LATIDO_MIN})")
        return SEGUIR, f"esperando el primer latido ({espera:.0f} min)"

    if not latido or not latido.get("cuando"):
        return _sin_latido_propio()
    visto = datetime.fromisoformat(str(latido["cuando"]))
    if str(latido.get("run")) != str(mi_run):
        # "Más nuevo" es que ARRANCÓ después que yo (un relevo o un rescate que
        # vino detrás), no que latiera después: en un relevo, el saliente sigue
        # latiendo mientras espera al sucesor, y el guardián del sucesor no
        # puede tomarlo por su reemplazo e irse.
        try:
            arrancado = datetime.fromisoformat(str(latido.get("arrancado")))
        except ValueError:
            arrancado = None
        if arrancado and arrancado > arranque:
            return SALIR, (f"late otro vigilante arrancado después que yo (run "
                           f"{latido.get('run')}): ya no es cosa mía")
        return _sin_latido_propio()
    if lat.en_reposo(latido):
        return SALIR, f"mi vigilante terminó bien ({latido.get('motivo')})"
    minutos = (ahora - visto).total_seconds() / 60.0
    if minutos > RESCATE_MIN:
        return RESCATAR, (f"mi vigilante lleva {minutos:.0f} min sin latir "
                          f"(más de {RESCATE_MIN}) con la sesión abierta")
    return SEGUIR, f"vivo, último latido hace {minutos:.1f} min"


def leer_estricto(trabajo: Path) -> dict | None:
    """El latido publicado. REVIENTA si no se puede leer (≠ no hay latido).

    Confundir "no pude preguntar" con "está muerto" haría rescatar a un
    vigilante sano cada vez que GitHub tenga un hipo.
    """
    lat.preparar(trabajo)
    lat._git("fetch", "-q", "--depth", "1", "origin", lat.RAMA, cwd=trabajo)
    crudo = lat._git("show", f"FETCH_HEAD:{lat.ARCHIVO}", cwd=trabajo)
    return json.loads(crudo)


def rescatar(mi_run: str) -> bool:
    """Lanza un vigilante de rescate en su propio grupo. True si GitHub aceptó."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log("sin GITHUB_TOKEN o GITHUB_REPOSITORY: no se puede rescatar.")
        return False
    r = subprocess.run(
        ["curl", "-sS", "-X", "POST", "-o", "/dev/null", "-w", "%{http_code}",
         "-H", "Accept: application/vnd.github+json",
         "-H", f"Authorization: Bearer {token}",
         f"https://api.github.com/repos/{repo}/actions/workflows/"
         f"vigilante_precios.yml/dispatches",
         "-d", json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main"),
                           "inputs": {"rescate_de": mi_run}})],
        capture_output=True, text=True)
    log(f"rescate pedido: HTTP {(r.stdout or '').strip()} {r.stderr.strip()}")
    return (r.stdout or "").strip() == "204"


def main() -> int:
    mi_run = os.environ.get("GITHUB_RUN_ID", "local")
    trabajo = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "centinela-guardian"
    arranque = datetime.now(config.TZ_ET)
    log(f"vigilando el latido del run {mi_run}; rescate a los {RESCATE_MIN} min.")
    while True:
        try:
            latido = leer_estricto(trabajo)
        except (RuntimeError, json.JSONDecodeError) as exc:
            log(f"no se pudo leer el latido ({exc}); no se concluye nada.")
            time.sleep(MIRAR_CADA_SEG)
            continue
        accion, motivo = decidir(latido, mi_run, arranque,
                                 datetime.now(config.TZ_ET))
        if accion == SALIR:
            log(f"me voy: {motivo}.")
            return 0
        if accion == RESCATAR:
            print(f"::error::{motivo}. Se lanza un vigilante de rescate.",
                  flush=True)
            if not rescatar(mi_run):
                print("::error::GitHub no aceptó el rescate. Nadie vigila los "
                      "niveles: cierra a mano si hace falta (README, «🆘 "
                      "Emergencia»).", flush=True)
            # En rojo siempre: que haya hecho falta rescatar ya es un fallo.
            return 1
        time.sleep(MIRAR_CADA_SEG)


if __name__ == "__main__":
    sys.exit(main())
