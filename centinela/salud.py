"""Estado de salud del sistema: qué corrió, cuándo y cómo acabó.

POR QUÉ UN FICHERO Y NO LA API DE GITHUB
-----------------------------------------
La página de operativa podría preguntarle a la API de Actions qué runs hubo,
pero eso obligaría a poner un token en una página pública o a exponer el
repositorio a llamadas desde el navegador de cualquiera. En vez de eso, cada
workflow deja escrito aquí cómo le fue, y la página solo lee un JSON estático.

Cada componente guarda su ÚLTIMA ejecución y nada más. No es un historial: para
eso están los runs de Actions, que se enlazan desde aquí. Lo que esta página
contesta es "¿está el sistema vivo ahora mismo?", y para eso la última vez de
cada cosa es justo lo que hace falta.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import config

ARCHIVO = config.ESTADO_DIR / "salud.json"

#: Los componentes que se vigilan, con su nombre para la pantalla y cada cuánto
#: se espera que corran. `cada` es informativo para la página; quien decide de
#: verdad es el cron de cada workflow.
COMPONENTES = {
    "preapertura":     ("Escaneo pre-apertura", "cada sesión, antes de abrir"),
    "compras":         ("Compras en XTB", "cada sesión, antes de abrir"),
    "ventas":          ("Ventas en XTB", "cada sesión, antes del cierre"),
    "apertura":        ("Verificación de la apertura", "cada sesión, tras abrir"),
    "postcierre":      ("Escaneo post-cierre", "cada sesión, tras el cierre"),
    "reconcilia":      ("Reconciliación con XTB", "cada sesión, tras el cierre"),
    "vigilante":       ("Vigilante", "todos los días"),
    "reentrenamiento": ("Reentrenamiento", "el día 1 de cada mes"),
}


def cargar() -> dict:
    if not ARCHIVO.exists():
        return {"runs": {}}
    try:
        datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Un fichero de salud ilegible no puede tumbar un escaneo: lo peor que
        # pasa es que la página muestre menos historia de la que hubo.
        print("[salud] salud.json ilegible; se reinicia.", flush=True)
        return {"runs": {}}
    datos.setdefault("runs", {})
    return datos


def hablo_en_este_run(componente: str) -> bool:
    """¿Anotó ya este componente su propio resultado en el run que corre ahora?

    Sirve para no pisar lo que dijo quien más sabe. El ejecutor, por ejemplo,
    distingue entre 'candado', 'diferencias' y la sesión caducada; el workflow
    que lo vigila desde fuera solo ve 'failure'. Si el componente ya habló, su
    versión se queda; si murió antes de poder hablar, la del job es lo único
    que hay.
    """
    run = os.environ.get("GITHUB_RUN_ID")
    if not run:
        return False
    entrada = cargar().get("runs", {}).get(componente)
    return bool(entrada) and str(entrada.get("run")) == str(run)


def registrar(componente: str, resultado: str, detalle: str = "") -> dict:
    """Anota cómo acabó un componente. Devuelve el registro completo.

    `resultado` es texto libre a propósito: cada componente tiene su propio
    vocabulario (los escaneos usan el de `resultados.py`, el ejecutor dice
    'ok' o el error) y forzarlos a uno común solo serviría para perder matices
    que luego hacen falta para diagnosticar.
    """
    if componente not in COMPONENTES:
        raise ValueError(
            f"Componente desconocido: {componente!r}. La lista es cerrada "
            f"({', '.join(COMPONENTES)}) para que añadir uno obligue a decidir "
            f"también cómo se pinta y cuándo se espera.")

    datos = cargar()
    entrada = {
        "cuando": datetime.now(config.TZ_ET).isoformat(),
        "resultado": resultado,
        "detalle": detalle,
    }
    run = os.environ.get("GITHUB_RUN_ID")
    if run:
        entrada["run"] = run
        entrada["url"] = (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run}")
    datos["runs"][componente] = entrada
    datos["actualizado"] = entrada["cuando"]
    guardar(datos)
    return datos


def guardar(datos: dict) -> None:
    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
