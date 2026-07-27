"""Utilidades comunes de los scripts de escaneo."""
from __future__ import annotations

import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import config, calendario, resultados  # noqa: E402


def parse_args(descripcion: str):
    ap = argparse.ArgumentParser(description=descripcion)
    ap.add_argument("--forzar", action="store_true",
                    help="ignora la verificación de ventana e idempotencia")
    ap.add_argument("--fecha", default=None,
                    help="fuerza la fecha de sesión (YYYY-MM-DD), para pruebas/backfill")
    return ap.parse_args()


def contexto(fecha_override: str | None):
    """Devuelve (ahora_et, hoy_date, hoy_iso)."""
    ahora = datetime.now(config.TZ_ET)
    if fecha_override:
        import pandas as pd
        hoy = pd.Timestamp(fecha_override).date()
    else:
        hoy = ahora.date()
    return ahora, hoy, hoy.isoformat()


def log(msg: str):
    print(f"[{datetime.now(config.TZ_ET):%Y-%m-%d %H:%M:%S ET}] {msg}", flush=True)


def esperar_a_ventana(inicio, etiqueta: str, ahora=None) -> bool:
    """Duerme hasta que abra la ventana. True si llegó a abrirse.

    Llegar pronto a un escaneo es inofensivo (los datos son del cierre anterior),
    así que en vez de matar el run lo dormimos. Eso permite lanzar los crons muy
    por delante y absorber retrasos de horas del cron de Actions, que es lo que
    hizo perder el día 2026-07-27.

    El tope `ESPERA_VENTANA_MAX_MIN` evita retener el turno de concurrencia
    indefinidamente: si se agota, devolvemos False y el run termina en verde con
    "omitido:antes-de-ventana" para que lo recoja el siguiente peldaño de crons.
    """
    if inicio is None:
        return False
    ahora = ahora or datetime.now(config.TZ_ET)
    faltan = (inicio - ahora).total_seconds()
    if faltan <= 0:
        return True

    tope = config.ESPERA_VENTANA_MAX_MIN * 60
    if faltan > tope:
        log(f"la ventana {etiqueta} abre en {faltan/60:.0f} min, más que el tope de "
            f"espera ({config.ESPERA_VENTANA_MAX_MIN} min); dejo el turno al "
            f"siguiente cron.")
        return False

    log(f"llegué pronto: la ventana {etiqueta} abre a las {inicio:%H:%M ET}; "
        f"espero {faltan/60:.0f} min en vez de abortar.")
    # A trocitos y releyendo el reloj: dormir de una tacada el intervalo
    # calculado se pasaría o se quedaría corto si `ahora` venía rancio.
    while True:
        restante = (inicio - datetime.now(config.TZ_ET)).total_seconds()
        if restante <= 0:
            break
        time.sleep(min(restante + 5, 60))
    log(f"ventana {etiqueta} abierta; continúo.")
    return True


def finalizar(resultado: str | None):
    """Publica el desenlace del escaneo y SALE EN ROJO si fue un fallo.

    `resultado` sale del vocabulario cerrado de `centinela.resultados`. El
    workflow lo lee para decidir si la ausencia de commit es legítima. Sin esto,
    un escaneo que pierde su ventana y un escaneo que trabaja pero no persiste se
    ven idénticos: ambos terminan en verde. Ese fue el bug del 2026-07-27.
    """
    resultado = resultado or resultados.PROCESADO
    log(f"RESULTADO={resultado}")
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write(f"resultado={resultado}\n")

    if resultados.es_fallo(resultado):
        motivo = resultados.MOTIVOS_FALLO.get(resultado, "fallo sin motivo registrado.")
        print(f"::error::[{resultado}] {motivo}", flush=True)
        sys.exit(1)

    if not (resultado == resultados.PROCESADO
            or resultados.es_legitimo_sin_commit(resultado)):
        # Un resultado fuera del vocabulario es un bug de programación, no un
        # desenlace: si lo dejáramos pasar volveríamos al silencio verde.
        print(f"::error::Resultado desconocido '{resultado}'. No está en el "
              f"vocabulario de centinela/resultados.py, así que no se puede "
              f"saber si terminar sin commit es legítimo.", flush=True)
        sys.exit(1)
