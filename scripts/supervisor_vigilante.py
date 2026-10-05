#!/usr/bin/env python
"""Supervisor del vigilante de precios: si el proceso muere o se cuelga, lo
vuelve a levantar en segundos, en el mismo job.

POR QUÉ (2026-10-05)
--------------------
XTB no acepta stop en acciones al contado: si el vigilante deja de mirar, las
posiciones se quedan sin stop. El fallo más probable no es que GitHub mate el
runner, sino que el proceso muera —una excepción del cliente no oficial, un
WebSocket que no vuelve— o se quede colgado. Para eso no hace falta esperar a
ningún cron (llegan con horas de retraso) ni a otro runner: basta con volver a
arrancarlo aquí mismo.

QUÉ VIGILA
----------
  * Que el proceso TERMINE MAL (código distinto de 0): se reinicia.
  * Que el proceso SE CUELGUE: el vigilante escribe su latido en disco cada dos
    minutos antes de empujarlo; si ese fichero lleva más de `COLGADO_MIN` sin
    tocarse, el proceso está vivo pero no trabaja. Se mata y se reinicia.

Un final con código 0 es un final legítimo —la sesión cerró, no queda nada que
vigilar, o se entregó el testigo al relevo— y el supervisor termina con él.

NADA SE ESCONDE. Cada reinicio sale como `::error::` en el log, el latido lleva
la cuenta de reinicios y la página la enseña; y si hubo alguno, el job termina
en ROJO al final aunque la vigilancia siguiera: un vigilante que se cae es un
fallo aunque se levante solo.

Si el runner entero muere, este supervisor muere con él: para eso está el
guardián (`guardian_vigilante.py`), que corre en otro runner.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import config, latido as lat, salud  # noqa: E402

#: Minutos sin que el latido local cambie para dar el proceso por colgado.
#: Tres latidos perdidos (late cada 2 min): uno puede ser un login lento.
COLGADO_MIN = 6
#: Cuántos reinicios se intentan en una sesión antes de rendirse.
MAX_REINICIOS = 30
#: Cada cuánto se mira al hijo.
MIRAR_CADA_SEG = 15
#: Límite del job de GitHub (6 h) menos margen: más tarde no se reinicia.
LIMITE_JOB_SEG = 5.9 * 3600


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%H:%M:%S ET}] [supervisor] {msg}",
          flush=True)


def ultimo_latido_local(trabajo: Path) -> float | None:
    """Cuándo escribió el vigilante su latido en disco por última vez."""
    f = trabajo / lat.ARCHIVO
    return f.stat().st_mtime if f.exists() else None


def colgado(trabajo: Path, arranque_hijo: float, ahora: float,
            limite_min: float | None = None) -> bool:
    """¿Lleva el hijo más de `limite_min` sin latir?

    Se mide desde lo más reciente entre su arranque y su último latido: un hijo
    recién arrancado todavía está haciendo login y no ha latido nunca, y no se
    le puede juzgar por el latido de su predecesor.
    """
    limite = COLGADO_MIN if limite_min is None else limite_min
    visto = max(arranque_hijo, ultimo_latido_local(trabajo) or 0.0)
    return (ahora - visto) / 60.0 > limite


def supervisar(cmd: list[str], trabajo: Path, env: dict | None = None,
               dormir=time.sleep, reloj=time.time,
               popen=subprocess.Popen) -> int:
    """Ejecuta `cmd` y lo relanza si muere o se cuelga. Devuelve el código final."""
    inicio = reloj()
    env = dict(env or os.environ)
    env.setdefault("CENTINELA_JOB_INICIO", str(inicio))
    reinicios = 0
    causas: list[str] = []
    while True:
        env["CENTINELA_REINICIOS"] = str(reinicios)
        arranque = reloj()
        hijo = popen(cmd, env=env)
        matado = False
        while hijo.poll() is None:
            dormir(MIRAR_CADA_SEG)
            if hijo.poll() is None and colgado(trabajo, arranque, reloj()):
                print(f"::error::el vigilante lleva más de {COLGADO_MIN} min sin "
                      f"latir con el proceso vivo: colgado. Se mata y se "
                      f"reinicia.", flush=True)
                hijo.kill()
                hijo.wait()
                matado = True
        rc = hijo.returncode
        if rc == 0 and not matado:
            break
        reinicios += 1
        causa = ("colgado" if matado else f"terminó con código {rc}")
        causas.append(f"{datetime.now(config.TZ_ET):%H:%M} {causa}")
        print(f"::error::el vigilante {causa}; reinicio {reinicios} de "
              f"{MAX_REINICIOS}.", flush=True)
        if reinicios > MAX_REINICIOS:
            log("demasiados reinicios: me rindo. El guardián o la escalera de "
                "respaldo tendrán que levantar otro.")
            break
        if reloj() - inicio > LIMITE_JOB_SEG:
            log("el job está a punto de agotar sus 6 h: no se reinicia aquí; "
                "lo levantará el guardián.")
            break
        dormir(min(60.0, 5.0 * reinicios))

    if reinicios:
        # El hijo pudo registrar un "ok" al final; esto lo corrige: hubo caídas.
        if not os.environ.get("CENTINELA_PRUEBA"):
            salud.registrar("vigilante_precios",
                            f"fallo:{reinicios}-reinicio(s)",
                            "; ".join(causas)[:300])
        return 1
    return rc if rc is not None else 1


def main() -> int:
    trabajo = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "centinela-latido"
    cmd = [sys.executable, str(RAIZ / "scripts" / "vigilante_precios.py"),
           *sys.argv[1:]]
    return supervisar(cmd, trabajo)


if __name__ == "__main__":
    sys.exit(main())
