#!/usr/bin/env python
"""Anota en estado/salud.json cómo acabó un componente. Lo llaman los workflows.

    registrar_salud.py <componente> [--salida X] [--job Y] [--detalle Z]

POR QUÉ SE REGISTRA DESDE EL WORKFLOW Y NO DENTRO DEL SCRIPT
------------------------------------------------------------
Un `salud.registrar()` al final del escaneo solo se ejecuta si el escaneo llega
al final. Los fallos que más importan son justo los otros: el runner que se
queda sin tiempo, el job cancelado, el `pip install` que revienta antes de
empezar. Por eso quien registra es un job posterior con `needs` e `if: always()`,
que ve el `result` del job aunque el job muriese sin decir nada.

Dos fuentes, con prioridad clara:
  --salida  el resultado que publicó el propio componente (vocabulario rico:
            `procesado`, `omitido:no-es-sesion`, `fallo:ventana-perdida`...).
            Manda cuando existe, porque dice MÁS que el job.
  --job     el `result` del job de Actions (success/failure/cancelled/skipped).
            Es el que queda cuando el componente no llegó ni a publicar nada.

Un job en rojo con salida `procesado` sigue siendo rojo: primero se mira si el
job murió, y solo un job que terminó bien puede contar su propia versión.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import salud  # noqa: E402

#: Cómo se traduce el `result` de un job de Actions al vocabulario de salud.
#: Los `fallo:` son los que el semáforo pinta en rojo (ver generar_operativa),
#: así que esta tabla es la que decide si un job muerto se ve o no.
DESDE_JOB = {
    "success": "",                      # que hable el componente
    "failure": "fallo:job-en-rojo",
    "cancelled": "fallo:job-cancelado",
    # GitHub apagó el runner a mitad del trabajo (ver `causa_en_github`). Sigue
    # siendo rojo —el componente dejó de trabajar de verdad—, pero con su causa:
    # un "job-en-rojo" a secas manda a buscar un fallo en el código que no hay.
    "runner-perdido": "fallo:runner-apagado-por-github",
    "skipped": "omitido:job-saltado",
    "": "",
}


def resolver(salida: str, job: str) -> str:
    """El resultado que se anota, con el job por delante del componente."""
    job = (job or "").strip()
    salida = (salida or "").strip()
    if job and job != "success":
        traducido = DESDE_JOB.get(job)
        if traducido is None:
            # Un `result` nuevo de Actions no puede colarse como si nada: es
            # más seguro llamarlo fallo y que alguien lo mire.
            return f"fallo:job-{job}"
        if traducido:
            # Si además el componente dijo algo, se conserva: "fallo:job-en-rojo
            # (procesado)" cuenta que el trabajo se hizo y el job murió después.
            return f"{traducido} ({salida})" if salida else traducido
    if salida:
        return salida
    if job == "success":
        return "ok"
    return "desconocido"


#: Lo que dice GitHub cuando el job se pasó de su tiempo máximo. Ese corte
#: también deja el paso "cancelled", y no es lo mismo que perder el runner.
_TIMEOUT = "exceeded the maximum execution time"


def causa_en_github(job: str, nombre_job: str, paso: str, api=None) -> str:
    """'runner-perdido' si GitHub apagó el runner del job; si no, `job` tal cual.

    FALLO DEL 2026-10-05, 15:57 ET. El vigilante de relevo (run 37365551670)
    funcionaba con normalidad y GitHub apagó su runner a mitad del paso, durante
    un incidente crítico de Actions (19:11–22:49 UTC): "The runner has received
    a shutdown signal". El job quedó en `failure` y la salud lo anotó como
    `fallo:job-en-rojo`, igual que si el vigilante hubiera reventado.

    La huella en la API: el job acaba en `failure` con el paso principal
    `cancelled` y sin la anotación de tiempo agotado. Un fallo del código deja
    el paso en `failure`; una cancelación a mano deja el JOB en `cancelled`.
    Si la API no responde, no se adivina: se devuelve el `job` original.
    """
    if job != "failure":
        return job
    api = api or _api_github
    try:
        jobs = api(f"actions/runs/{os.environ['GITHUB_RUN_ID']}/jobs")["jobs"]
        j = next(x for x in jobs if x["name"] == nombre_job)
        pasos = [s for s in j.get("steps", []) if s["name"].startswith(paso)]
        if not pasos or pasos[0].get("conclusion") != "cancelled":
            return job
        notas = api(f"check-runs/{j['id']}/annotations")
        if any(_TIMEOUT in str(n.get("message", "")) for n in notas):
            return job
        return "runner-perdido"
    except Exception as exc:  # noqa: BLE001 — sin API, el result tal cual
        print(f"[salud] no se pudo preguntar a GitHub por la causa ({exc!r}).",
              flush=True)
        return job


def _api_github(ruta: str):
    import json
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{ruta}",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser(description="Registra el estado de un componente.")
    ap.add_argument("componente", choices=sorted(salud.COMPONENTES))
    ap.add_argument("--salida", default="",
                    help="Resultado publicado por el propio componente.")
    ap.add_argument("--job", default="",
                    help="result del job de Actions (success/failure/...).")
    ap.add_argument("--detalle", default="")
    ap.add_argument("--causa-job", nargs=2, metavar=("JOB", "PASO"),
                    help="si el job acabó en failure, preguntar a GitHub si fue "
                         "porque apagó el runner (nombre del job y del paso)")
    ap.add_argument("--solo-si-falta", action="store_true",
                    help="No pisar lo que el propio componente ya anotó en "
                         "este run: él sabe más que el result del job.")
    args = ap.parse_args()

    # Un job SALTADO no es una noticia: pasa cada vez que un escaneo es
    # idempotente y su ejecutor no llega a correr. Registrarlo pisaría el
    # resultado real de hace unas horas con un "no corrí", que dice menos y
    # además confunde a quien lo lea. Si un componente deja de correr de verdad,
    # quien lo denuncia es la regla de "lleva X h sin aparecer".
    if args.job == "skipped" and not args.salida.strip():
        print(f"[salud] {args.componente}: el job se saltó; no hay nada que "
              f"registrar.", flush=True)
        return 0

    if args.solo_si_falta and salud.hablo_en_este_run(args.componente):
        actual = salud.cargar()["runs"][args.componente]
        print(f"[salud] {args.componente} ya se registró en este run "
              f"({actual['resultado']}); no se pisa.", flush=True)
        return 0

    job = args.job
    if args.causa_job:
        job = causa_en_github(job, *args.causa_job)
        if job == "runner-perdido" and not args.detalle:
            args.detalle = ("GitHub apagó el runner a mitad del trabajo (\"The "
                            "runner has received a shutdown signal\"); no es un "
                            "fallo del código. Mira githubstatus.com.")
    resultado = resolver(args.salida, job)
    salud.registrar(args.componente, resultado, args.detalle)
    print(f"[salud] {args.componente} -> {resultado}"
          + (f" ({args.detalle})" if args.detalle else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
