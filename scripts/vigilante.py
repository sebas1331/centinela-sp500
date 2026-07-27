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

Se le da a cada sesión un margen (VIGILANTE_MARGEN_HORAS) antes de exigirla,
para no dar falsos positivos por los retrasos normales del cron de Actions.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

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
    """Commits del bot en las últimas `horas` (según el árbol ya clonado)."""
    salida = subprocess.run(
        ["git", "log", f"--since={horas} hours ago", f"--author={AUTOR_BOT}",
         "--format=%h %ad %s", "--date=iso"],
        cwd=config.BASE_DIR, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return [ln for ln in salida.splitlines() if ln]


def sesiones_a_exigir(n: int, ahora: datetime | None = None) -> list[str]:
    """Las n sesiones más recientes cuyo trabajo ya debería estar commiteado."""
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

    if problemas:
        for p in problemas:
            print(f"::error::{p}", flush=True)
        _log("")
        _log(f"❌ {len(problemas)} problema(s). El sistema se saltó trabajo que ya "
             f"debería estar hecho. Revisa los runs de 'Escaneo pre-apertura' y "
             f"'Escaneo post-cierre' de las fechas señaladas.")
        return 1

    _log("✅ Sin silencios: todas las sesiones exigibles están procesadas y "
         "commiteadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
