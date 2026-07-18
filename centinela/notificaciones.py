"""Notificaciones (Telegram) — DESACTIVADO por defecto.

Módulo listo para activar en el futuro sin rehacer nada. Se activa poniendo la
variable de entorno CENTINELA_NOTIF=on y definiendo CENTINELA_TELEGRAM_TOKEN y
CENTINELA_TELEGRAM_CHAT_ID. Mientras esté 'off', enviar() no hace nada.
"""
from __future__ import annotations

import requests

from . import config


def activas() -> bool:
    return (config.NOTIFICACIONES_ACTIVAS and bool(config.TELEGRAM_TOKEN)
            and bool(config.TELEGRAM_CHAT_ID))


def enviar(mensaje: str) -> bool:
    """Envía un mensaje por Telegram si las notificaciones están activas.

    Devuelve True si se envió, False si están desactivadas o falló (sin lanzar).
    """
    if not activas():
        return False
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, timeout=15, data={
            "chat_id": config.TELEGRAM_CHAT_ID, "text": mensaje,
            "parse_mode": "Markdown"})
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False
