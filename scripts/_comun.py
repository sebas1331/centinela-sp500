"""Utilidades comunes de los scripts de escaneo."""
from __future__ import annotations

import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import config, calendario  # noqa: E402


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
