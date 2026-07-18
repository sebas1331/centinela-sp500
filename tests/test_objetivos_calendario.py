"""Tests de objetivos/stop y de la lógica de ventanas del calendario."""
from datetime import datetime
from zoneinfo import ZoneInfo

from centinela import objetivos, config, calendario


# ---------------- objetivos / stop ----------------
def test_objetivo_nunca_bajo_piso_5pct():
    obj = objetivos.objetivo_inicial(entrada=100, atr=0.5)  # ATR chico
    assert obj >= 100 * (1 + config.OBJETIVO_MINIMO) - 1e-9


def test_objetivo_usa_tecnico_si_mayor():
    obj = objetivos.objetivo_inicial(entrada=100, atr=5.0)  # 2*ATR=10 -> +10%
    assert obj >= 109.9


def test_objetivo_respeta_tope():
    obj = objetivos.objetivo_inicial(entrada=100, atr=5.0, resistencia=1000)
    tope = 100 + config.ATR_OBJETIVO_TOPE_MULT * 5.0
    assert obj <= tope + 1e-6


def test_stop_acotado():
    stop = objetivos.stop_inicial(entrada=100, atr=50)  # ATR enorme
    assert stop >= 100 * (1 - config.STOP_MAX_PORCENTAJE) - 1e-9
    stop2 = objetivos.stop_inicial(entrada=100, atr=0.1)  # ATR minúsculo
    assert stop2 <= 100 * (1 - config.STOP_MIN_PORCENTAJE) + 1e-9


# ---------------- calendario / ventanas (usa datos locales, sin red) ----------
def _utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("UTC"))


def test_ventana_preapertura_edt():
    # 2026-07-17 (viernes, EDT): 12:45 UTC = 08:45 ET -> en ventana
    assert calendario.en_ventana_preapertura(_utc("2026-07-17T12:45"))
    # 13:45 UTC = 09:45 ET -> ya abrió, fuera
    assert not calendario.en_ventana_preapertura(_utc("2026-07-17T13:45"))


def test_ventana_preapertura_est():
    # 2026-01-15 (jueves, EST): 13:45 UTC = 08:45 ET -> en ventana
    assert calendario.en_ventana_preapertura(_utc("2026-01-15T13:45"))
    # 12:45 UTC = 07:45 ET -> demasiado temprano
    assert not calendario.en_ventana_preapertura(_utc("2026-01-15T12:45"))


def test_ventana_postcierre():
    # 22:00 UTC = 18:00 EDT -> tras el cierre
    assert calendario.en_ventana_postcierre(_utc("2026-07-17T22:00"))


def test_fin_de_semana_sin_mercado():
    assert not calendario.es_dia_de_mercado("2026-07-18")  # sábado
    assert calendario.es_dia_de_mercado("2026-07-17")      # viernes
