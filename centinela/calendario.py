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


# Fases de una ventana de escaneo. Saber en CUÁL de ellas estamos es lo que
# separa un "todavía no toca" (inofensivo: vendrá otro disparo) de un "se pasó
# el techo" (fallo real: el día se perdió). Antes ambos casos devolvían el mismo
# False y el sistema los trataba igual -> el fallo del 2026-07-27 salió en verde.
ANTES = "antes"
DENTRO = "dentro"
DESPUES = "despues"
SIN_MERCADO = "sin-mercado"


def fase_preapertura(ahora: datetime | None = None) -> str:
    """ANTES / DENTRO / DESPUES / SIN_MERCADO respecto de la ventana pre-apertura.

    DESPUES es irrecuperable: pasada la apertura ya no se puede decidir la
    entrada sin ver el precio al que luego se simula la compra (look-ahead).
    """
    m = minutos_para_apertura(ahora)
    if m is None:
        return SIN_MERCADO
    if m > config.PREAPERTURA_MAX_ANTES:
        return ANTES
    if m < config.PREAPERTURA_MIN_ANTES:
        return DESPUES
    return DENTRO


def fase_postcierre(ahora: datetime | None = None) -> str:
    """ANTES / DENTRO / SIN_MERCADO respecto de la ventana post-cierre.

    Nunca devuelve DESPUES: esta ventana no tiene techo (una vez cerrada la
    sesión, procesarla más tarde da exactamente el mismo resultado).
    """
    m = minutos_desde_cierre(ahora)
    if m is None:
        return SIN_MERCADO
    return ANTES if m < config.POSTCIERRE_MIN_DESPUES else DENTRO


def momento_preferido_preapertura(fecha=None):
    """Hora ET a la que conviene hacer la pre-apertura (08:45 ET), o None.

    Es hasta aquí que duerme un run que llegó demasiado pronto. No se usa el
    borde de la ventana (4 h antes de la apertura) porque eso movería las
    decisiones a las 05:30 ET y cambiaría el carácter del sistema; y no se usa
    para nada al run que ya llega dentro de la ventana, que trabaja de inmediato.
    """
    if fecha is None:
        fecha = datetime.now(config.TZ_ET).date()
    ac = apertura_cierre_et(fecha)
    if ac is None:
        return None
    apertura, _ = ac
    return apertura - pd.Timedelta(minutes=config.PREAPERTURA_OBJETIVO_ANTES)


def momento_preferido_postcierre(fecha=None):
    """Hora ET a la que conviene hacer el post-cierre (18:00 ET), o None."""
    if fecha is None:
        fecha = datetime.now(config.TZ_ET).date()
    ac = apertura_cierre_et(fecha)
    if ac is None:
        return None
    _, cierre = ac
    return cierre + pd.Timedelta(minutes=config.POSTCIERRE_OBJETIVO_DESPUES)


def en_ventana_preapertura(ahora: datetime | None = None) -> bool:
    """¿Estamos en la ventana pre-apertura (hoy hay mercado y falta el rango
    configurado para la apertura)? Tolerante a retrasos del cron."""
    return fase_preapertura(ahora) == DENTRO


def en_ventana_postcierre(ahora: datetime | None = None) -> bool:
    """¿Estamos en la ventana post-cierre (hoy hay mercado y ya cerró hace al
    menos el margen configurado)?"""
    return fase_postcierre(ahora) == DENTRO


def ultima_sesion_exigible(ahora: datetime | None = None):
    """Última sesión cuyos DOS escaneos ya deberían estar hechos y commiteados.

    La usa el vigilante para saber qué exigir sin dar falsos positivos. Se
    retrocede sesión a sesión hasta encontrar una cuyo post-cierre debería haber
    terminado hace al menos `VIGILANTE_MARGEN_HORAS` (margen que absorbe los
    retrasos del cron de Actions, que llegan a superar las 3 h).

    Devuelve un Timestamp de sesión, o None si aún no hay ninguna exigible.
    """
    ahora = (ahora or datetime.now(config.TZ_ET)).astimezone(config.TZ_ET)
    limite = ahora - pd.Timedelta(hours=config.VIGILANTE_MARGEN_HORAS)
    cal = _cal()
    sesion = _a_sesion(ahora.date(), "previous")
    for _ in range(10):
        apertura_cierre = apertura_cierre_et(sesion)
        if apertura_cierre is not None:
            _, cierre = apertura_cierre
            debido = cierre + pd.Timedelta(minutes=config.POSTCIERRE_MIN_DESPUES)
            if debido <= limite:
                return sesion
        sesion = cal.previous_session(sesion)
    return None


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
