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

#: Los estados en que puede estar un vigilante. Lista cerrada: añadir uno
#: obliga a decidir también si cuenta como "caído".
#:
#: La distinción que importa es entre MUERTO y EN REPOSO. Un vigilante que
#: termina porque no hay nada que vigilar no está caído: hizo su trabajo y se
#: fue. Tratarlo igual que a uno que se murió a media sesión daba un rojo falso
#: —"lleva 62 min sin latir"— con la cuenta vacía y nada que vigilar, que es
#: exactamente el ruido que este panel existe para no generar.
VIVO = "vivo"
EN_REPOSO = "en-reposo"
ESPERANDO_RELEVO = "esperando-relevo"
ESTADOS = (VIVO, EN_REPOSO, ESPERANDO_RELEVO)

#: Los que significan "terminó por su cuenta y está bien". Un latido con uno de
#: estos no se juzga por su antigüedad.
TERMINALES = (EN_REPOSO,)

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


#: Cuántos latidos se guardan en el historial. Una sesión entera a uno cada
#: dos minutos son ~195; con 240 cabe la sesión y el relevo.
HISTORIAL_MAX = 240


def construir(arrancado: str, vigiladas: list[dict], estado: str = VIVO,
              relevo: str | None = None, motivo: str = "",
              historial: list[dict] | None = None,
              broker: dict | None = None, aviso: dict | None = None) -> dict:
    """El contenido del latido. Sin un solo dato de sesión: es público.

    `motivo` solo tiene sentido con `en-reposo`, y ahí es obligatorio de hecho:
    un vigilante en reposo sin explicación se lee igual que uno caído, que es
    justo lo que se quiere evitar.
    """
    if estado not in ESTADOS:
        raise ValueError(
            f"Estado desconocido: {estado!r}. La lista es cerrada ({ESTADOS}) "
            f"para que añadir uno obligue a decidir si cuenta como caído.")
    if estado == EN_REPOSO and not motivo:
        raise ValueError(
            "Un vigilante en reposo tiene que decir POR QUÉ. Sin motivo se lee "
            "igual que uno caído.")
    cuando = datetime.now(config.TZ_ET).isoformat()
    run = os.environ.get("GITHUB_RUN_ID")
    # EL HISTORIAL. Un solo latido dice "estoy vivo ahora"; no dice si hubo
    # un corte a las 11:40 que se arregló solo. Con los últimos ~240 se puede
    # comprobar después que la sesión estuvo cubierta entera, latido a latido.
    hist = list(historial or [])
    hist.append({"cuando": cuando, "estado": estado, "run": run,
                 "n": len(vigiladas)})
    hist = hist[-HISTORIAL_MAX:]
    return {
        "cuando": cuando,
        "historial": hist,
        # La foto de la cuenta, leída en este latido. La página la prefiere a
        # la de estado/broker.json cuando es más nueva: así la cuenta que se
        # ve tiene como mucho la edad del latido, no la del último commit.
        "broker": broker,
        # Si el aviso externo (healthchecks.io) está montado y responde.
        "aviso": aviso,
        "arrancado": arrancado,
        "estado": estado,
        "motivo": motivo or None,
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
             "bid": v.get("bid"), "sale_hoy": bool(v.get("sale_hoy"))}
            for v in vigiladas
        ],
    }


def remoto_autenticado() -> str:
    """La URL del repositorio con credenciales, para poder empujar desde aquí.

    `actions/checkout` deja el token en la configuración del repositorio QUE
    CLONA, como una cabecera extra. Este módulo trabaja en un repositorio
    aparte —a propósito, para no tocar nunca el árbol de `main`— y ahí esa
    cabecera no existe: sin esto, el push muere con "could not read Username
    for https://github.com", que fue exactamente lo que pasó el 2026-09-29 en
    el primer intento real.

    El token no se escribe en ningún log: se usa para construir la URL y esa URL
    no se imprime nunca. Los mensajes de error de git que sí se propagan vienen
    de `_git`, que solo muestra los argumentos, y la URL viaja en la
    configuración del remoto, no en la línea de órdenes.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if token and repo:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    # Fuera de Actions (pruebas locales), el origen del repositorio de siempre.
    return subprocess.run(["git", "remote", "get-url", "origin"],
                          cwd=str(config.BASE_DIR), capture_output=True,
                          text=True).stdout.strip()


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd or config.BASE_DIR),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} falló ({r.returncode}): "
            f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def preparar(trabajo: Path) -> None:
    """El repositorio mínimo donde vive la rama del latido, si no existe ya."""
    trabajo.mkdir(parents=True, exist_ok=True)
    if not (trabajo / ".git").exists():
        _git("init", "-q", "-b", RAMA, cwd=trabajo)
        _git("config", "user.name", "centinela-bot", cwd=trabajo)
        _git("config", "user.email", "actions@users.noreply.github.com",
             cwd=trabajo)
        _git("remote", "add", "origin", remoto_autenticado(), cwd=trabajo)


def publicar(datos: dict, trabajo: Path) -> None:
    """Reescribe la rama del latido con un único commit.

    `trabajo` es un directorio aparte: se construye ahí un repositorio mínimo
    con el fichero y nada más, y se empuja a la rama. Así el árbol de `main`
    nunca se toca, ni siquiera temporalmente, y un vigilante que muera a mitad
    no puede dejar el repositorio de trabajo en un estado raro.
    """
    preparar(trabajo)
    (trabajo / ARCHIVO).write_text(
        json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")

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


def publicar_tolerante(datos: dict, trabajo: Path, ultimo_ok: float,
                       ahora: float) -> float:
    """Publica el latido sin morirse por un hipo de red. Devuelve cuándo fue el
    último éxito.

    El PRIMER latido sí es mortal y se publica con `publicar()` a secas: si el
    vigilante no puede demostrar que está vivo desde el principio, es mejor que
    muera en rojo ya. Pero matar a un vigilante sano a media sesión porque un
    push falló una vez sería cambiar un problema pequeño por uno grande: deja de
    mirar precios de verdad. Se reintenta en el siguiente ciclo, y solo si lleva
    más de `MUERTO_MINUTOS` sin conseguirlo se da por perdido — que es cuando el
    Vigilante general va a levantar otro de todas formas.
    """
    try:
        publicar(datos, trabajo)
        return ahora
    except (RuntimeError, OSError) as exc:
        perdido = (ahora - ultimo_ok) / 60.0
        print(f"[latido] no se pudo publicar ({exc!r}); "
              f"{perdido:.1f} min sin latir.", flush=True)
        if perdido > MUERTO_MINUTOS:
            raise RuntimeError(
                f"Llevo {perdido:.0f} minutos sin poder publicar el latido "
                f"(más de {MUERTO_MINUTOS}). Nadie puede saber que sigo vivo y "
                f"el Vigilante va a levantar otro: me retiro.") from exc
        return ultimo_ok


def leer(trabajo: Path) -> dict | None:
    """El último latido publicado, leído del remoto. None si no hay ninguno.

    Lo usa el relevo: el vigilante saliente espera a ver el latido de su
    sucesor antes de soltar el testigo.
    """
    try:
        preparar(trabajo)
        _git("fetch", "-q", "--depth", "1", "origin", RAMA, cwd=trabajo)
        crudo = _git("show", f"FETCH_HEAD:{ARCHIVO}", cwd=trabajo)
    except RuntimeError:
        return None
    try:
        return json.loads(crudo)
    except json.JSONDecodeError:
        return None


def en_reposo(latido: dict | None) -> bool:
    """¿Terminó por su cuenta y está bien? Entonces su edad da igual."""
    return bool(latido) and latido.get("estado") in TERMINALES


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


def historial_de_hoy(latido: dict | None, hoy: str) -> list[dict]:
    """Los latidos de HOY de un latido anterior, para seguir su historial.

    Lo usa el vigilante al arrancar —sea el primero del día o un relevo— para
    no empezar el historial desde cero y poder demostrar continuidad entre uno
    y otro.
    """
    if not latido:
        return []
    return [h for h in latido.get("historial") or []
            if str(h.get("cuando", ""))[:10] == hoy]


def huecos(historial: list[dict], max_minutos: float = MUERTO_MINUTOS) -> list[dict]:
    """Los cortes: tramos de más de `max_minutos` entre dos latidos seguidos.

    Solo cuenta mientras el vigilante estaba VIVO: un tramo que empieza en un
    latido "en-reposo" no es un corte, es que no había nada que vigilar.
    """
    cortes = []
    previo = None
    for h in sorted(historial, key=lambda x: str(x.get("cuando", ""))):
        try:
            t = datetime.fromisoformat(str(h["cuando"]))
        except (KeyError, ValueError):
            continue
        if previo is not None and previo[1] != EN_REPOSO:
            minutos = (t - previo[0]).total_seconds() / 60.0
            if minutos > max_minutos:
                cortes.append({"desde": previo[0].isoformat(),
                               "hasta": t.isoformat(),
                               "minutos": round(minutos, 1)})
        previo = (t, h.get("estado"))
    return cortes
