#!/usr/bin/env python
"""¿Hace falta arrancar un vigilante de precios, o ya hay uno vivo?

Es el peldaño barato que protege a la escalera de respaldo de sí misma. Los
crons de respaldo existen para el día en que el workflow de compras muera antes
de poder llamar a nadie; el resto de los días no tienen nada que hacer, y
arrancar un segundo vigilante sobre uno sano sería peor que no arrancar ninguno.

La pregunta se contesta con el latido publicado, que es la única señal que
sobrevive entre runs distintos.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import calendario, config, latido as lat  # noqa: E402


def _salida(arrancar: bool, motivo: str) -> int:
    print(f"{'ARRANCAR' if arrancar else 'no hace falta'}: {motivo}", flush=True)
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write(f"arrancar={'si' if arrancar else 'no'}\n")
            fh.write(f"motivo={motivo}\n")
    return 0


def decidir(latido: dict | None, ahora: datetime, forzado: bool,
            posiciones_xtb: int | None = None) -> tuple[bool, str]:
    """La regla, separada del mundo para poder probarla.

    `posiciones_xtb` es lo que dice la última foto publicada de la cuenta
    (estado/broker.json); None si no se sabe. Solo se usa para no levantar un
    vigilante cuando el anterior se fue en reposo y no hay nada que vigilar.
    """
    if forzado:
        return True, "lanzado a mano"
    if not calendario.es_dia_de_mercado(ahora.date()):
        return False, "hoy no hay mercado"
    ac = calendario.apertura_cierre_et(ahora.date().isoformat())
    if ac is None:
        return False, "sin horario de mercado para hoy"
    apertura, cierre = ac
    if ahora >= cierre:
        return False, "la sesión ya cerró"
    # Muy antes de abrir no se arranca: el vigilante se iría en reposo igual
    # (no vigila el pre-mercado) y cada peldaño de la escalera dejaría un run
    # inútil. Ya lo arrancará el job de compras o un peldaño posterior.
    faltan = (apertura - ahora).total_seconds() / 60.0
    if faltan > config.VIGILANTE_ESPERA_APERTURA_MAX_MIN:
        return False, f"faltan {faltan:.0f} min para la apertura"

    minutos = lat.minutos_desde(latido, ahora)
    if minutos is None:
        return True, "no hay ningún latido: nadie está vigilando"
    if lat.en_reposo(latido) and posiciones_xtb == 0:
        return False, ("el último vigilante quedó en reposo y XTB no tiene "
                       "posiciones (estado/broker.json): nada que vigilar")
    if lat.en_reposo(latido):
        # Terminó bien, pero eso fue ANTES. Si ahora hay algo que vigilar —una
        # compra que acaba de entrar— hay que levantar otro: el que se fue no
        # va a volver solo.
        return True, (f"el último vigilante quedó en reposo "
                      f"({latido.get('motivo') or 'sin motivo'}); si hay algo "
                      f"que vigilar, hace falta uno nuevo")
    if minutos > lat.MUERTO_MINUTOS:
        return True, (f"el último latido es de hace {minutos:.0f} min "
                      f"(más de {lat.MUERTO_MINUTOS}): el vigilante está muerto")
    return False, (f"hay un vigilante vivo (run {latido.get('run')}), último "
                   f"latido hace {minutos:.1f} min")


def posiciones_publicadas() -> int | None:
    """Cuántas posiciones tiene XTB según la última foto publicada, o None."""
    import json
    ruta = config.ESTADO_DIR / "broker.json"
    if not ruta.exists():
        return None
    try:
        return len(json.loads(ruta.read_text(encoding="utf-8"))["posiciones"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"broker.json ilegible ({exc!r}): no se sabe cuántas posiciones "
              f"hay, así que se actúa como si las hubiera.", flush=True)
        return None


def main() -> int:
    if not config.EJECUCION_BROKER:
        return _salida(False, "ejecución en broker desactivada en config")
    trabajo = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "centinela-latido"
    trabajo.mkdir(parents=True, exist_ok=True)
    # Un repositorio mínimo solo para poder leer la rama del latido.
    if not (trabajo / ".git").exists():
        import subprocess
        subprocess.run(["git", "init", "-q", str(trabajo)], check=True)
        subprocess.run(["git", "remote", "add", "origin",
                        lat.remoto_autenticado()], cwd=str(trabajo), check=True)

    # Forzado SOLO si se pide (casilla `forzar`) o es un relevo. Antes cualquier
    # `workflow_dispatch` forzaba, y como el pre-apertura lo llamaba en cada
    # peldaño de la escalera, el 02/10 hubo once vigilantes en cola cancelados
    # uno detrás de otro. Ahora un dispatch sin forzar mira el latido como los
    # crons de respaldo: si hay uno vivo, no arranca otro.
    forzado = bool(os.environ.get("FORZAR") or os.environ.get("RELEVO_DE")
                   or os.environ.get("RESCATE_DE"))
    arrancar, motivo = decidir(lat.leer(trabajo),
                               datetime.now(config.TZ_ET), forzado,
                               posiciones_publicadas())
    return _salida(arrancar, motivo)


if __name__ == "__main__":
    sys.exit(main())
