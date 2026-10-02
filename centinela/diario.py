"""El diario local: lo que el broker hizo, a salvo de los choques al publicar.

EL PROBLEMA (semana del 2026-09-28)
-----------------------------------
Trece runs murieron en una semana con "could not apply ...": dos workflows
escribían a la vez `bitacora_broker.csv` o `estado/salud.json`, y el rebase de
`commit_y_push.sh` no sabe juntar dos filas añadidas al final del mismo CSV.
Ninguno de esos trece llevaba una venta, por suerte. Pero el commit de "cierre
de sesión" del vigilante de precios lleva la bitácora del broker y el registro
de órdenes enviadas: el día que choque con una venta dentro, la venta existe en
XTB y no existe en ningún sitio más.

LA SOLUCIÓN: NO REBASAR NUNCA LO QUE EL BROKER HIZO
----------------------------------------------------
Cada escritura que importa se anota PRIMERO aquí, en un fichero de líneas JSON
que vive FUERA del repositorio (en el runner, o donde diga `CENTINELA_DIARIO`).
Publicar no es rebasar un commit: es

  1. bajar el `origin/main` más reciente a un árbol aparte,
  2. aplicarle el diario entero (las operaciones son idempotentes: aplicar dos
     veces lo mismo deja lo mismo),
  3. commit y push,
  4. y si otro empujó entretanto, tirar ese árbol y volver al paso 1.

No hay conflicto posible porque nunca se mezclan dos historias: el diario se
aplica sobre la última versión que haya, sea cual sea. Y el árbol de trabajo
del job no se toca, así que un fallo a mitad no deja nada a medias.

QUÉ SE ANOTA
------------
  orden        una fila nueva de `bitacora_broker.csv`
  actualizar   campos corregidos de una fila ya escrita (el precio de verdad)
  enviada      una entrada del registro de idempotencia
  fiabilidad   una fila del diario de fiabilidad del broker
  salud        el resultado de un componente en `estado/salud.json`
  broker       la foto de la cuenta para `estado/broker.json`
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import config

#: Las operaciones que el diario sabe aplicar. Lista cerrada.
OPERACIONES = ("orden", "actualizar", "enviada", "fiabilidad", "salud", "broker")

#: Las rutas, RELATIVAS a la raíz del repositorio, que el diario toca. Son las
#: únicas que puede cambiar un commit del publicador.
RUTA_BITACORA = "bitacora_broker.csv"
RUTA_ENVIADAS = "estado/ordenes_enviadas.json"
RUTA_FIABILIDAD = "estado/fiabilidad_broker.csv"
RUTA_SALUD = "estado/salud.json"
RUTA_BROKER = "estado/broker.json"
RUTAS = (RUTA_BITACORA, RUTA_ENVIADAS, RUTA_FIABILIDAD, RUTA_SALUD, RUTA_BROKER)


def _ruta_por_defecto() -> Path:
    """Dónde vive el diario. Fuera del repositorio, siempre.

    En Actions, en `RUNNER_TEMP`: sobrevive a todos los pasos del job y muere
    con él, que es justo lo que hace falta — cada job publica lo suyo antes de
    terminar. Fuera de Actions, en la caché local (ignorada por git).
    """
    if os.environ.get("CENTINELA_DIARIO"):
        return Path(os.environ["CENTINELA_DIARIO"])
    if os.environ.get("RUNNER_TEMP"):
        return Path(os.environ["RUNNER_TEMP"]) / "centinela-diario.jsonl"
    return config.BASE_DIR / ".cache" / "diario.jsonl"


ARCHIVO = _ruta_por_defecto()


# --------------------------------------------------------------------------- #
# Anotar
# --------------------------------------------------------------------------- #
def anotar(op: str, **datos) -> None:
    """Añade una línea al diario. Si no puede, REVIENTA.

    Al revés que el diario de fiabilidad, que nunca falla hacia arriba: esto
    no es una métrica, es la única copia que sobrevive a un push fallido. Una
    venta que no queda anotada aquí es una venta que puede perderse, y eso
    tiene que verse en rojo en el momento, no descubrirse una semana después.
    """
    if op not in OPERACIONES:
        raise ValueError(f"Operación de diario desconocida: {op!r} "
                         f"(la lista es cerrada: {OPERACIONES}).")
    linea = {"op": op, "anotado": datetime.now(config.TZ_ET).isoformat(),
             **datos}
    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(linea, ensure_ascii=False, sort_keys=True,
                            default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def leer(ruta: Path | None = None) -> list[dict]:
    """Las entradas del diario, en orden. Una línea ilegible PARA en rojo."""
    ruta = ruta or ARCHIVO
    if not ruta.exists():
        return []
    entradas = []
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        if not linea.strip():
            continue
        try:
            entradas.append(json.loads(linea))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"La línea {n} del diario {ruta} es ilegible ({exc}). No se "
                f"publica nada con el diario a medias: hay que mirarlo.") from exc
    return entradas


# --------------------------------------------------------------------------- #
# Aplicar (idempotente)
# --------------------------------------------------------------------------- #
def _leer_csv(ruta: Path, columnas: list[str]) -> list[dict]:
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8", newline="") as f:
        return [{c: (fila.get(c) or "") for c in columnas}
                for fila in csv.DictReader(f, restval="")]


def _escribir_csv(ruta: Path, columnas: list[str], filas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(filas)


def _texto(v) -> str:
    return "" if v is None else str(v)


def aplicar(entradas: list[dict], raiz: Path) -> list[str]:
    """Aplica el diario a los ficheros de `raiz`. Devuelve las rutas tocadas.

    Idempotente a propósito: el mismo diario aplicado dos veces sobre el mismo
    árbol no cambia nada la segunda vez. Es lo que permite reintentar la
    publicación desde cero tantas veces como haga falta.
    """
    from . import fiabilidad, ordenes as ords   # import tardío: evita ciclos

    tocadas: set[str] = set()

    # --- bitacora_broker.csv: filas por (id, cuando) y correcciones por id ---
    ruta_b = raiz / RUTA_BITACORA
    filas = _leer_csv(ruta_b, ords.COLUMNAS_BROKER)
    antes = [dict(f) for f in filas]
    vistas = {(f["id"], f["cuando_et"]) for f in filas}
    for e in entradas:
        if e["op"] == "orden":
            fila = {c: _texto(e["fila"].get(c)) for c in ords.COLUMNAS_BROKER}
            clave = (fila["id"], fila["cuando_et"])
            if clave not in vistas:
                filas.append(fila)
                vistas.add(clave)
        elif e["op"] == "actualizar":
            for f in filas:
                if f["id"] == e["id"]:
                    for k, v in e["campos"].items():
                        f[k] = _texto(v)
    if filas != antes:
        _escribir_csv(ruta_b, ords.COLUMNAS_BROKER, filas)
        tocadas.add(RUTA_BITACORA)

    # --- registro de idempotencia --------------------------------------------
    ruta_e = raiz / RUTA_ENVIADAS
    enviadas_e = [e for e in entradas if e["op"] == "enviada"]
    if enviadas_e:
        reg = (json.loads(ruta_e.read_text(encoding="utf-8"))
               if ruta_e.exists() else {"enviadas": {}})
        reg.setdefault("enviadas", {})
        cambio = False
        for e in enviadas_e:
            if reg["enviadas"].get(e["id"]) != e["detalle"]:
                reg["enviadas"][e["id"]] = e["detalle"]
                reg["actualizado"] = e["anotado"]
                cambio = True
        if cambio:
            ruta_e.parent.mkdir(parents=True, exist_ok=True)
            ruta_e.write_text(json.dumps(reg, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n", encoding="utf-8")
            tocadas.add(RUTA_ENVIADAS)

    # --- diario de fiabilidad: filas únicas -----------------------------------
    ruta_f = raiz / RUTA_FIABILIDAD
    filas_f = _leer_csv(ruta_f, fiabilidad.COLUMNAS)
    n_f = len(filas_f)
    vistas_f = {tuple(f[c] for c in fiabilidad.COLUMNAS) for f in filas_f}
    for e in entradas:
        if e["op"] != "fiabilidad":
            continue
        fila = {c: _texto(e["fila"].get(c)) for c in fiabilidad.COLUMNAS}
        clave = tuple(fila[c] for c in fiabilidad.COLUMNAS)
        if clave not in vistas_f:
            filas_f.append(fila)
            vistas_f.add(clave)
    if len(filas_f) != n_f:
        _escribir_csv(ruta_f, fiabilidad.COLUMNAS, filas_f)
        tocadas.add(RUTA_FIABILIDAD)

    # --- salud: la entrada más nueva de cada componente gana ------------------
    ruta_s = raiz / RUTA_SALUD
    salud_e = [e for e in entradas if e["op"] == "salud"]
    if salud_e:
        datos = (json.loads(ruta_s.read_text(encoding="utf-8"))
                 if ruta_s.exists() else {"runs": {}})
        datos.setdefault("runs", {})
        cambio = False
        for e in salud_e:
            actual = datos["runs"].get(e["componente"], {})
            if str(e["entrada"].get("cuando", "")) >= str(actual.get("cuando", "")) \
                    and actual != e["entrada"]:
                datos["runs"][e["componente"]] = e["entrada"]
                datos["actualizado"] = max(str(datos.get("actualizado", "")),
                                           str(e["entrada"]["cuando"]))
                cambio = True
        if cambio:
            ruta_s.parent.mkdir(parents=True, exist_ok=True)
            ruta_s.write_text(json.dumps(datos, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n", encoding="utf-8")
            tocadas.add(RUTA_SALUD)

    # --- foto de la cuenta: la más reciente gana -------------------------------
    ruta_k = raiz / RUTA_BROKER
    fotos = [e["datos"] for e in entradas if e["op"] == "broker"]
    if fotos:
        nueva = max(fotos, key=lambda d: str(d.get("leido", "")))
        actual = (json.loads(ruta_k.read_text(encoding="utf-8"))
                  if ruta_k.exists() else {})
        if str(nueva.get("leido", "")) > str(actual.get("leido", "")):
            ruta_k.parent.mkdir(parents=True, exist_ok=True)
            ruta_k.write_text(json.dumps(nueva, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n", encoding="utf-8")
            tocadas.add(RUTA_BROKER)

    return sorted(tocadas)


# --------------------------------------------------------------------------- #
# Publicar
# --------------------------------------------------------------------------- #
#: Cuántas veces se intenta publicar antes de rendirse, y cuánto se espera
#: entre intentos (crece). Cinco vueltas cubren de sobra el caso real: dos o
#: tres workflows empujando en el mismo minuto.
INTENTOS = 5
ESPERA_BASE_SEG = 4.0

AUTOR = ("centinela-bot", "actions@users.noreply.github.com")


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=180)


def _exigir(r: subprocess.CompletedProcess, que: str) -> str:
    if r.returncode != 0:
        raise RuntimeError(f"git {que} falló ({r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def publicar(mensaje: str, rama: str = "main", repo: Path | None = None,
             entradas: list[dict] | None = None, dormir=time.sleep,
             log=print, intentos: int | None = None) -> str | None:
    """Publica el diario en `rama`. Devuelve el sha publicado, o None si no
    había nada nuevo que publicar. Si no lo consigue, REVIENTA.

    Trabaja en un árbol aparte (`git worktree`) creado desde `origin/<rama>`,
    así que el árbol del job no se toca nunca y un intento fallido se tira
    entero sin dejar restos.
    """
    repo = repo or config.BASE_DIR
    entradas = leer() if entradas is None else entradas
    if not entradas:
        log("[diario] vacío: nada que publicar.")
        return None

    intentos = intentos or INTENTOS
    ultimo_error = ""
    for intento in range(1, intentos + 1):
        arbol = Path(tempfile.mkdtemp(prefix="centinela-diario-"))
        arbol.rmdir()                     # `worktree add` quiere crearlo él
        try:
            _exigir(_git("fetch", "-q", "origin", rama, cwd=repo), "fetch")
            _exigir(_git("worktree", "add", "-q", "--detach", str(arbol),
                         f"origin/{rama}", cwd=repo), "worktree add")
            tocadas = aplicar(entradas, arbol)
            if not tocadas:
                log(f"[diario] {len(entradas)} entrada(s) ya estaban en "
                    f"origin/{rama}: nada nuevo que publicar.")
                return None
            _exigir(_git("add", "--", *tocadas, cwd=arbol), "add")
            _exigir(_git("-c", f"user.name={AUTOR[0]}",
                         "-c", f"user.email={AUTOR[1]}",
                         "commit", "-q", "-m", mensaje, "-m",
                         f"run: {os.environ.get('GITHUB_RUN_ID', 'local')} | "
                         f"diario: {len(entradas)} entrada(s) | "
                         f"toca: {', '.join(tocadas)}", cwd=arbol), "commit")
            sha = _exigir(_git("rev-parse", "HEAD", cwd=arbol), "rev-parse")
            r = _git("push", "-q", "origin", f"HEAD:{rama}", cwd=arbol)
            if r.returncode == 0:
                # Verificación contra el remoto, como commit_y_push.sh: que el
                # push devuelva 0 no es lo mismo que que el commit esté allí.
                _exigir(_git("fetch", "-q", "origin", rama, cwd=repo), "fetch")
                ancestro = _git("merge-base", "--is-ancestor", sha,
                                f"origin/{rama}", cwd=repo)
                if ancestro.returncode != 0:
                    raise RuntimeError(f"el commit {sha} no aparece en "
                                       f"origin/{rama} tras el push")
                log(f"[diario] publicado {sha[:7]} en origin/{rama} "
                    f"({', '.join(tocadas)}; intento {intento}).")
                return sha
            ultimo_error = (r.stderr or r.stdout).strip()[:300]
            log(f"[diario] push rechazado (intento {intento}/{intentos}): "
                f"{ultimo_error}. Se rehace desde el origin nuevo.")
        except RuntimeError as exc:
            # Un fetch que falla por la red o un remoto que aún no refleja el
            # push no son motivo para rendirse al primer intento: se dice y se
            # vuelve a empezar desde cero. Si se agotan los intentos, rojo.
            ultimo_error = str(exc)[:300]
            log(f"[diario] intento {intento}/{intentos} fallido: {ultimo_error}")
        finally:
            quitar = _git("worktree", "remove", "--force", str(arbol), cwd=repo)
            if quitar.returncode != 0 and arbol.exists():
                shutil.rmtree(arbol)
                _git("worktree", "prune", cwd=repo)
        if intento < intentos:
            dormir(ESPERA_BASE_SEG * intento)

    raise RuntimeError(
        f"No se pudo publicar el diario tras {intentos} intento(s) "
        f"({ultimo_error}). Lo que el broker hizo NO está en el repositorio: "
        f"el diario sigue en {ARCHIVO} y la reconciliación lo denunciará.")


#: El mismo `publicar`, con otro nombre que los tests no tapan: el conftest
#: sustituye `publicar` para que ningún test empuje a main por accidente, y los
#: tests del propio diario lo usan con este nombre contra un remoto temporal.
publicar_de_verdad = publicar
