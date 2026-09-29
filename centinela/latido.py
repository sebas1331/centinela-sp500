"""La señal de vida del vigilante de precios.

DÓNDE SE ESCRIBE, Y POR QUÉ NO EN EL HISTORIAL
-----------------------------------------------
El vigilante vive conectado toda la sesión, 6 h y media, y tiene que demostrar
cada dos minutos que sigue vivo. Eso son ~195 latidos al día. Las opciones eran
tres y ninguna es obvia:

  1. Un commit por latido en `main`. Multiplica por veinticinco los commits
     diarios del repositorio (hoy son ~8) y entierra la bitácora, que es lo que
     de verdad hay que poder leer. Descartada.
  2. Latir solo cada 10-15 minutos para que los commits sean pocos. Entonces un
     latido recién escrito puede tener ya 15 minutos, y la regla de "más de 10
     minutos = rojo" se vuelve imposible de cumplir sin falsos rojos.
  3. Una rama aparte, `latido`, con UN SOLO commit que se reescribe por
     force-push en cada latido. `main` no se entera, el historial no crece
     —la rama siempre tiene exactamente un commit— y la página lo lee por
     `raw.githubusercontent.com`, que es un fichero estático público y no
     necesita ningún token.

Se elige la 3. El force-push da miedo con razón, así que la rama es una
constante de este módulo y nunca un parámetro: este código no puede reescribir
ninguna otra rama ni aunque alguien se lo pida.

EL PRECIO DE LA OPCIÓN 3, medido y no supuesto (2026-09-29): la CDN de
raw.githubusercontent sirve el fichero con `cache-control: max-age=300`, y el
parámetro anticaché de la URL **no la esquiva** —se comprobó: con `?t=` distinto
seguía devolviendo una copia de 159 s—. Así que lo que ve la página puede
tener hasta 5 minutos de retraso sobre la verdad.

Qué significa eso en la práctica:

  * NUNCA hay falsos rojos. El latido más nuevo tiene como mucho 2 minutos en
    origen y la CDN añade como mucho 5: la página nunca ve más de 7, por debajo
    del umbral de 10.
  * Un vigilante muerto de verdad tarda entre 10 y 15 minutos en verse en la
    página, no 10 exactos.
  * Quien SÍ lo ve exacto es el Vigilante general, que lee la rama por `git
    fetch` y no pasa por la CDN. Y es el que relanza. O sea: la página es la
    vista humana, con su margen; la comprobación que actúa no tiene margen.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from . import config

#: La rama del latido. CONSTANTE: nunca sale de aquí y nunca se parametriza,
#: porque lo que se hace con ella es `push --force`.
RAMA = "latido"
ARCHIVO = "latido.json"

#: Cada cuánto late el vigilante.
CADA_SEGUNDOS = 120
#: A partir de cuántos minutos sin latido se da por muerto. Tres latidos
#: perdidos: uno puede ser un hipo de red, tres son otra cosa.
MUERTO_MINUTOS = 10

#: Dónde lo lee la página. Pública, sin token, servida como fichero estático.
URL = (f"https://raw.githubusercontent.com/{{repo}}/{RAMA}/{ARCHIVO}")


def url_publica() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "sebas1331/centinela-sp500")
    return URL.format(repo=repo)


def construir(arrancado: str, vigiladas: list[dict], estado: str = "vivo",
              relevo: str | None = None) -> dict:
    """El contenido del latido. Sin un solo dato de sesión: es público."""
    return {
        "cuando": datetime.now(config.TZ_ET).isoformat(),
        "arrancado": arrancado,
        "estado": estado,
        "run": os.environ.get("GITHUB_RUN_ID"),
        "url_run": (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
            f"{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_RUN_ID") else None),
        "relevo": relevo,
        "vigiladas": [
            {"ticker": v["ticker"], "acciones": v.get("acciones"),
             "objetivo": v.get("objetivo"), "stop": v.get("stop"),
             "bid": v.get("bid")}
            for v in vigiladas
        ],
    }


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd or config.BASE_DIR),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} falló ({r.returncode}): "
            f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def publicar(datos: dict, trabajo: Path) -> None:
    """Reescribe la rama del latido con un único commit.

    `trabajo` es un directorio aparte: se construye ahí un repositorio mínimo
    con el fichero y nada más, y se empuja a la rama. Así el árbol de `main`
    nunca se toca, ni siquiera temporalmente, y un vigilante que muera a mitad
    no puede dejar el repositorio de trabajo en un estado raro.
    """
    trabajo.mkdir(parents=True, exist_ok=True)
    (trabajo / ARCHIVO).write_text(
        json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")

    if not (trabajo / ".git").exists():
        _git("init", "-q", "-b", RAMA, cwd=trabajo)
        _git("config", "user.name", "centinela-bot", cwd=trabajo)
        _git("config", "user.email", "actions@users.noreply.github.com",
             cwd=trabajo)
        origen = _git("remote", "get-url", "origin")
        _git("remote", "add", "origin", origen, cwd=trabajo)

    _git("add", ARCHIVO, cwd=trabajo)
    # `--amend` con `--allow-empty`: la rama se queda SIEMPRE en un commit.
    cabeza = subprocess.run(["git", "rev-parse", "--verify", "HEAD"],
                            cwd=str(trabajo), capture_output=True, text=True)
    if cabeza.returncode == 0:
        _git("commit", "-q", "--amend", "--allow-empty", "-m",
             f"latido {datos['cuando']}", cwd=trabajo)
    else:
        _git("commit", "-q", "--allow-empty", "-m",
             f"latido {datos['cuando']}", cwd=trabajo)

    # La ÚNICA rama a la que este módulo empuja, y es una constante.
    _git("push", "--force", "origin", f"HEAD:refs/heads/{RAMA}", cwd=trabajo)


def leer(trabajo: Path) -> dict | None:
    """El último latido publicado, leído del remoto. None si no hay ninguno.

    Lo usa el relevo: el vigilante saliente espera a ver el latido de su
    sucesor antes de soltar el testigo.
    """
    try:
        _git("fetch", "-q", "--depth", "1", "origin", RAMA, cwd=trabajo)
        crudo = _git("show", f"FETCH_HEAD:{ARCHIVO}", cwd=trabajo)
    except RuntimeError:
        return None
    try:
        return json.loads(crudo)
    except json.JSONDecodeError:
        return None


def minutos_desde(latido: dict | None, ahora: datetime | None = None) -> float | None:
    """Cuántos minutos hace del último latido, o None si no se puede saber."""
    if not latido or not latido.get("cuando"):
        return None
    try:
        visto = datetime.fromisoformat(str(latido["cuando"]))
    except ValueError:
        return None
    ahora = ahora or datetime.now(visto.tzinfo or config.TZ_ET)
    return (ahora - visto).total_seconds() / 60.0
