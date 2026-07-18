"""Calendario bursátil (NYSE/Nasdaq) para decidir si hoy hay mercado y si el
escaneo cae en la ventana correcta.

El cron de GitHub Actions corre en UTC y NO entiende el horario de verano (DST),
además puede retrasarse varios minutos. Por eso NUNCA confiamos en la hora del
cron: usamos este calendario para verificar, ya dentro del script, si:
  - hoy es día de mercado, y
  - estamos en la ventana pre-apertura o post-cierre correcta.
Si no corresponde, el script termina sin hacer nada.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

import pandas as pd
import exchange_calendars as xcals

from . import config


@lru_cache(maxsize=1)
def _cal():
    """Devuelve el calendario XNYS (cacheado; construirlo es costoso)."""
    return xcals.get_calendar(config.CALENDARIO_BOLSA)


def _a_sesion(fecha, direccion: str = "none"):
    """Normaliza una fecha a un Timestamp de sesión (medianoche, tz-naive).

    direccion: "none" exige que sea sesión; "next"/"previous" ajustan a la
    sesión más cercana en esa dirección si la fecha no es sesión.
    """
    ts = pd.Timestamp(fecha).normalize()
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    cal = _cal()
    if cal.is_session(ts):
        return ts
    if direccion == "none":
        return None
    return cal.date_to_session(ts, direction=direccion)


def es_dia_de_mercado(fecha=None) -> bool:
    """¿La fecha dada (por defecto hoy en ET) es día de mercado?"""
    if fecha is None:
        fecha = datetime.now(config.TZ_ET).date()
    ts = pd.Timestamp(fecha).normalize()
    return bool(_cal().is_session(ts))


def apertura_cierre_et(fecha):
    """(apertura, cierre) en hora ET para la fecha, o None si no hay mercado."""
    ts = _a_sesion(fecha, "none")
    if ts is None:
        return None
    cal = _cal()
    ap = cal.session_open(ts).tz_convert(config.TZ_ET)
    ci = cal.session_close(ts).tz_convert(config.TZ_ET)
    return ap, ci


def minutos_para_apertura(ahora: datetime | None = None):
    """Minutos que faltan para la apertura de hoy (positivo=futuro).

    None si hoy no hay mercado.
    """
    ahora = (ahora or datetime.now(config.TZ_ET)).astimezone(config.TZ_ET)
    ac = apertura_cierre_et(ahora.date())
    if ac is None:
        return None
    apertura, _ = ac
    return (apertura - ahora).total_seconds() / 60.0


def minutos_desde_cierre(ahora: datetime | None = None):
    """Minutos transcurridos desde el cierre de hoy (positivo=ya cerró).

    None si hoy no hay mercado.
    """
    ahora = (ahora or datetime.now(config.TZ_ET)).astimezone(config.TZ_ET)
    ac = apertura_cierre_et(ahora.date())
    if ac is None:
        return None
    _, cierre = ac
    return (ahora - cierre).total_seconds() / 60.0


def en_ventana_preapertura(ahora: datetime | None = None) -> bool:
    """¿Estamos en la ventana pre-apertura (hoy hay mercado y falta el rango
    configurado para la apertura)? Tolerante a retrasos del cron."""
    m = minutos_para_apertura(ahora)
    if m is None:
        return False
    return config.PREAPERTURA_MIN_ANTES <= m <= config.PREAPERTURA_MAX_ANTES


def en_ventana_postcierre(ahora: datetime | None = None) -> bool:
    """¿Estamos en la ventana post-cierre (hoy hay mercado y ya cerró hace al
    menos el margen configurado)?"""
    m = minutos_desde_cierre(ahora)
    if m is None:
        return False
    return m >= config.POSTCIERRE_MIN_DESPUES


def sesion_actual_o_anterior(fecha=None):
    """Sesión de la fecha, o la sesión de mercado inmediatamente anterior."""
    if fecha is None:
        fecha = datetime.now(config.TZ_ET).date()
    return _a_sesion(fecha, "previous")


def sesion_n_despues(fecha, n: int):
    """La n-ésima sesión de mercado DESPUÉS de la fecha dada."""
    cal = _cal()
    s = _a_sesion(fecha, "previous")
    for _ in range(n):
        s = cal.next_session(s)
    return s


def sesiones_siguientes(fecha, n: int):
    """Lista de las n sesiones de mercado siguientes a la fecha (excluyéndola)."""
    cal = _cal()
    s = _a_sesion(fecha, "previous")
    out = []
    for _ in range(n):
        s = cal.next_session(s)
        out.append(s)
    return out


def sesiones_en_rango(inicio, fin):
    """DatetimeIndex de sesiones de mercado en [inicio, fin]."""
    return _cal().sessions_in_range(
        pd.Timestamp(inicio).normalize(), pd.Timestamp(fin).normalize()
    )
