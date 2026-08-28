#!/usr/bin/env python
"""VIGILANTE: denuncia en rojo cualquier día bursátil que el sistema se saltó.

Todo lo demás de este repositorio comprueba que un run concreto hizo su trabajo.
Nadie comprobaba lo contrario: que NO FALTARA ningún run. El 2026-07-27 los
cinco disparos de la pre-apertura se retrasaron más de dos horas, aterrizaron
pasada la apertura, terminaron los cinco en verde y la sesión se perdió sin
dejar rastro. En la pestaña Actions todo estaba impecable.

Este script mira el problema desde el otro lado: en vez de preguntar "¿este run
salió bien?", pregunta "¿tiene el repositorio la huella de todas las sesiones
que ya deberían estar hechas?". Un silencio, que es justo lo que ningún workflow
podía detectar, se convierte aquí en un fallo rojo y visible.

Comprueba, para las últimas `--sesiones` sesiones ya exigibles:
  1. estado/estado.json registra esa sesión como pre-apertura y post-cierre
     procesadas (o posterior),
  2. existe su logs/decisiones-<sesion>.log,
  3. hay commits del bot recientes.

Y, desde la emergencia del 2026-08-27, también el problema simétrico: una RACHA
de runs rojos consecutivos del mismo workflow. Aquel día siete pre-aperturas
murieron seguidas por un retraso de ~10 h del cron de GitHub; cada run gritó por
su cuenta, pero el vigilante de esa noche salió en verde porque las sesiones que
él exigía (las del día anterior) sí estaban hechas. Un hueco se ve mirando hacia
atrás; una avería en curso, mirando los runs.

Se le da a cada sesión un margen (VIGILANTE_MARGEN_HORAS) antes de exigirla,
para no dar falsos positivos por los retrasos normales del cron de Actions.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# BLINDAJE CONTRA CUELGUES (raíz: el run 31119690487 del 2026-08-06)
#
# Aquel run murió a los 15 minutos, pero no por culpa de este script: GitHub
# nunca le asignó runner y el job se quedó encolado hasta agotar el timeout (ver
# la cabecera de .github/workflows/vigilante.yml). Aun así el episodio dejó claro
# que un Vigilante tardando minutos es indistinguible de uno colgado, así que
# aquí se cierran todas las vías por las que ESTE código podría bloquearse:
#
#   - Timeout de socket global. Hoy el Vigilante no hace ni una petición de red
#     (solo lee ficheros del repo y llama a `git log`), pero un import futuro que
#     la hiciera heredaría el default de Python, que es ESPERAR PARA SIEMPRE.
#     Con esto, cualquier socket que alguien introduzca aquí muere a los 30 s.
#   - Timeout duro en el subproceso `git`.
#   - Cota superior explícita en todo lo que se itera (sesiones y commits), para
#     que el coste no crezca con el repositorio.
# ---------------------------------------------------------------------------
TIMEOUT_RED_S = 30
socket.setdefaulttimeout(TIMEOUT_RED_S)

#: Techo para el subproceso `git log`. En un repo sano tarda milisegundos.
TIMEOUT_GIT_S = 60

#: Cota superior de sesiones a revisar. El uso normal es 1-5; el techo existe
#: para que un `--sesiones 100000` no ponga a recorrer el calendario sin fin.
MAX_SESIONES = 30

#: Cota superior de commits del bot a listar. El repo crece un puñado de commits
#: al día, pero la ventana es de tiempo, no de tamaño: sin este techo un día raro
#: (reprocesado masivo, migración) podría devolver miles de líneas y llenar el
#: log del run. Solo se usan para informar y para saber si hay ALGUNO.
MAX_COMMITS_LISTADOS = 200

#: Runs a pedirle a la API por workflow. Con 16 peldaños de pre-apertura y 9 de
#: post-cierre, 50 cubren de sobra las últimas 24 h.
MAX_RUNS_CONSULTADOS = 50

#: Reintentos y backoff de la llamada a la API de Actions. Un 5xx transitorio de
#: GitHub no puede tumbar al vigilante, pero tampoco puede pasar desapercibido:
#: si tras los reintentos sigue sin responder, se reporta como problema (rojo).
API_REINTENTOS = 3
API_BACKOFF_S = 2
TIMEOUT_API_S = 20

#: Workflows de escaneo que se vigilan, y cómo nombrarlos en el mensaje.
WORKFLOWS_VIGILADOS = {
    "preapertura.yml": "pre-aperturas",
    "postcierre.yml": "post-cierres",
}

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import config, calendario, estado as est_mod, bitacora  # noqa: E402

AUTOR_BOT = "centinela-bot"

#: Los dos escaneos que tiene que haber dejado su marca en cada sesión.
ESCANEOS = ("preapertura", "postcierre")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _marca(escaneo: str) -> str:
    """Cabecera que deja un escaneo en el log del día, sin la parte variable."""
    return bitacora.CABECERA_ESCANEO.format(escaneo=escaneo, marca="").rstrip(" =")


def _commits_del_bot(horas: int) -> list[str]:
    """Commits del bot en las últimas `horas` (según el árbol ya clonado).

    Acotado por los dos lados: `-n` limita cuántos commits puede devolver git por
    muy grande que se ponga el repositorio, y `timeout` impide que un `git` que se
    quede pensando (o esperando un lock del índice) cuelgue el run entero.
    """
    salida = subprocess.run(
        ["git", "log", f"-n{MAX_COMMITS_LISTADOS}", f"--since={horas} hours ago",
         f"--author={AUTOR_BOT}", "--format=%h %ad %s", "--date=iso"],
        cwd=config.BASE_DIR, capture_output=True, text=True, check=True,
        timeout=TIMEOUT_GIT_S,
    ).stdout.strip()
    return [ln for ln in salida.splitlines() if ln]


# ---------------------------------------------------------------------------
# RACHAS DE ROJOS (raíz: la emergencia del 2026-08-27)
#
# Aquella tarde los siete disparos de la pre-apertura murieron en rojo con
# `fallo:ventana-perdida` porque el scheduler de cron de GitHub se retrasó ~10 h.
# El diseño funcionó: cada run gritó. Pero nadie SUMABA. Siete rojos seguidos y
# el vigilante de esa noche salió en verde, porque las sesiones que él exigía
# (las del día anterior) sí estaban hechas. Su pregunta —"¿falta trabajo ya
# vencido?"— es correcta pero mira hacia atrás; una racha de rojos es la misma
# avería mirada en tiempo real, y merece un grito propio.
# ---------------------------------------------------------------------------
def _api_actions(ruta: str, token: str, repo: str) -> dict:
    """GET a la API de Actions con timeout y backoff exponencial.

    Lanza la excepción si tras `API_REINTENTOS` sigue fallando: quien llama
    decide qué hacer, y lo que hace es reportarlo como problema. Un fallo de la
    API deja al vigilante CIEGO para esta comprobación, y un vigilante ciego que
    dice "todo bien" es exactamente el silencio verde que este repo persigue.
    """
    url = f"https://api.github.com/repos/{repo}/{ruta}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    ultimo = None
    for intento in range(API_REINTENTOS):
        if intento:
            espera = API_BACKOFF_S * (2 ** (intento - 1))
            _log(f"  reintento {intento}/{API_REINTENTOS - 1} en {espera}s ({ultimo})")
            time.sleep(espera)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_API_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            ultimo = e
    raise RuntimeError(f"la API de Actions no respondió tras {API_REINTENTOS} "
                       f"intentos: {ultimo}")


def racha_de_rojos(runs: list[dict], ahora: datetime | None = None) -> dict | None:
    """La racha de fallos consecutivos más reciente, o None si no la hay.

    `runs` viene de la API ordenado del más reciente al más antiguo. Se recorre
    desde el presente hacia atrás y se cuenta mientras haya fallos:
      - "failure" / "timed_out" alargan la racha,
      - "success" la CORTA (el sistema volvió a funcionar),
      - "cancelled", "skipped" y los runs aún en curso se saltan sin cortarla:
        no son un fallo, pero tampoco son prueba de que algo funcione, y con el
        grupo de concurrencia de este repo los cancelados son rutina.
    Solo se miran los runs de las últimas `VIGILANTE_RACHA_HORAS`.
    """
    ahora = ahora or datetime.now(timezone.utc)
    corte = ahora - timedelta(hours=config.VIGILANTE_RACHA_HORAS)

    rojos: list[dict] = []
    for run in runs:
        creado = datetime.fromisoformat(
            str(run.get("created_at", "")).replace("Z", "+00:00"))
        if creado < corte:
            break
        conclusion = run.get("conclusion")
        if conclusion in ("failure", "timed_out"):
            rojos.append({"id": run.get("id"), "creado": creado})
        elif conclusion == "success":
            break
        # cancelled / skipped / None (en curso): ni suman ni cortan.

    if len(rojos) < config.VIGILANTE_RACHA_MINIMA:
        return None
    return {
        "cuantos": len(rojos),
        # `rojos` va del más reciente al más antiguo: el inicio de la racha es
        # el último de la lista.
        "desde": rojos[-1]["creado"],
        "ids": [r["id"] for r in rojos],
    }


def revisar_rachas(ahora: datetime | None = None) -> list[str]:
    """Problemas por rachas de runs rojos. Vacía = ningún workflow en racha."""
    problemas: list[str] = []
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        # Fuera de Actions (ejecución local, tests) no hay a quién preguntar.
        # Esto NO es silenciar un error: es que la comprobación no aplica.
        _log("Sin GITHUB_TOKEN/GITHUB_REPOSITORY: no se revisan rachas de rojos "
             "(esta comprobación solo corre dentro de Actions).")
        return problemas

    for fichero, nombre in WORKFLOWS_VIGILADOS.items():
        try:
            datos = _api_actions(
                f"actions/workflows/{fichero}/runs"
                f"?per_page={MAX_RUNS_CONSULTADOS}", token, repo)
        except RuntimeError as e:
            problemas.append(
                f"No se pudo revisar la racha de rojos de {fichero}: {e}. El "
                f"vigilante queda ciego a este workflow, así que sale en rojo en "
                f"vez de dar un verde que no puede respaldar.")
            continue

        racha = racha_de_rojos(datos.get("workflow_runs", []), ahora)
        if racha is None:
            _log(f"  {fichero}: sin racha de rojos.")
            continue
        ids = ", ".join(str(i) for i in racha["ids"])
        problemas.append(
            f"EMERGENCIA: {racha['cuantos']} {nombre} rojas seguidas desde "
            f"{racha['desde'].astimezone(config.TZ_ET):%H:%M} ET "
            f"({racha['desde']:%H:%M} UTC). Ver runs {ids}.")
    return problemas


def sesiones_a_exigir(n: int, ahora: datetime | None = None) -> list[str]:
    """Las n sesiones más recientes cuyo trabajo ya debería estar commiteado."""
    n = max(1, min(int(n), MAX_SESIONES))
    ultima = calendario.ultima_sesion_exigible(ahora)
    if ultima is None:
        return []
    sesiones = [ultima]
    for _ in range(n - 1):
        sesiones.append(calendario._cal().previous_session(sesiones[-1]))
    return [s.date().isoformat() for s in reversed(sesiones)]


def revisar(n_sesiones: int, ahora: datetime | None = None) -> list[str]:
    """Devuelve la lista de problemas encontrados (vacía = todo en orden)."""
    problemas: list[str] = []
    sesiones = sesiones_a_exigir(n_sesiones, ahora)

    if not sesiones:
        _log("Todavía no hay ninguna sesión exigible (mercado recién cerrado o "
             "festivo largo). Nada que vigilar.")
        return problemas

    estado = est_mod.cargar()
    pre = estado.get("ultima_preapertura") or ""
    post = estado.get("ultima_postcierre") or ""
    _log(f"Sesiones exigibles: {', '.join(sesiones)}")
    _log(f"Estado del repo -> ultima_preapertura={pre or '(ninguna)'} "
         f"ultima_postcierre={post or '(ninguna)'}")

    # Las fechas ISO se comparan bien como cadenas, así que ">=" basta para
    # decir "esta sesión ya quedó atrás y por tanto se procesó".
    ultima = sesiones[-1]
    if pre < ultima:
        problemas.append(
            f"La pre-apertura de {ultima} no está procesada (estado.json va por "
            f"{pre or 'ninguna'}). Se perdió la ventana de decisión de esa sesión.")
    if post < ultima:
        problemas.append(
            f"El post-cierre de {ultima} no está procesado (estado.json va por "
            f"{post or 'ninguna'}). Esa sesión no tiene entradas ejecutadas ni "
            f"salidas evaluadas.")

    # Comprobación sesión a sesión. La de arriba solo dice si el sistema está
    # vivo HOY; esta detecta el hueco concreto, que es lo que de verdad se
    # escapaba: si el lunes se pierde la pre-apertura pero el martes va bien,
    # `ultima_preapertura` ya habrá pasado de largo y el lunes quedaría impune.
    for sesion in sesiones:
        log_sesion = config.LOGS_DIR / f"decisiones-{sesion}.log"
        if not log_sesion.exists():
            problemas.append(
                f"Falta logs/decisiones-{sesion}.log: no hay constancia de qué se "
                f"evaluó el {sesion}.")
            continue
        texto = log_sesion.read_text(encoding="utf-8")
        if not any(_marca(e) in texto for e in ESCANEOS):
            # Log anterior al 2026-07-27, cuando la cabecera aún no decía qué
            # escaneo la había escrito. No se puede comprobar, y no tiene sentido
            # dar por rota una sesión histórica que sí se procesó.
            _log(f"  {sesion}: log en formato antiguo, no se puede desglosar por escaneo.")
            continue
        for escaneo in ESCANEOS:
            if _marca(escaneo) not in texto:
                problemas.append(
                    f"La sesión {sesion} no tiene bloque de '{escaneo}' en "
                    f"logs/decisiones-{sesion}.log: ese escaneo no llegó a correr.")

    # 30 h de calendario cubren un fin de semana normal solo si el vigilante
    # corre a diario; se informa siempre y solo se exige si hay sesión exigible
    # dentro de esa franja.
    recientes = _commits_del_bot(30)
    _log(f"Commits del bot en las últimas 30 h: {len(recientes)}")
    for c in recientes:
        _log(f"  {c}")
    if not recientes and ultima >= (datetime.now(config.TZ_ET).date().isoformat()):
        problemas.append("Ninguna escritura del bot en las últimas 30 h pese a "
                         "haber una sesión exigible de hoy.")

    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description="Vigilante de silencios de Centinela")
    ap.add_argument("--sesiones", type=int, default=1,
                    help="cuántas sesiones exigibles hacia atrás revisar")
    args = ap.parse_args()

    _log(f"[vigilante {datetime.now(config.TZ_ET):%Y-%m-%d %H:%M:%S ET}]")
    problemas = revisar(args.sesiones)

    # Las rachas van DESPUÉS y en su propia lista: son un eje distinto (avería en
    # curso, no hueco ya consumado) y tienen que verse aunque las sesiones
    # exigibles estén todas en orden, que es justo lo que pasó el 2026-08-27.
    _log("")
    _log("Rachas de runs rojos en las últimas "
         f"{config.VIGILANTE_RACHA_HORAS} h:")
    problemas.extend(revisar_rachas())

    if problemas:
        for p in problemas:
            print(f"::error::{p}", flush=True)
        _log("")
        emergencias = sum(1 for p in problemas if p.startswith("EMERGENCIA:"))
        _log(f"❌ {len(problemas)} problema(s)"
             + (f", {emergencias} de ellos EMERGENCIA (racha de runs rojos en "
                f"curso, no un fallo suelto)" if emergencias else "")
             + ". Revisa los runs de 'Escaneo pre-apertura' y 'Escaneo "
               "post-cierre' de las fechas y los IDs señalados.")
        return 1

    _log("✅ Sin silencios: todas las sesiones exigibles están procesadas y "
         "commiteadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
