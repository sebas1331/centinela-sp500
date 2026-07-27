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


# La ventana pre-apertura es deliberadamente asimétrica: ancha hacia atrás (hasta
# 4 h antes) para absorber los retrasos del cron de Actions, y tajante en la
# apertura, porque decidir después del open sería look-ahead bias.
def test_ventana_preapertura_edt():
    # 2026-07-17 (viernes, EDT): 12:45 UTC = 08:45 ET -> en ventana
    assert calendario.en_ventana_preapertura(_utc("2026-07-17T12:45"))
    # 10:45 UTC = 06:45 ET -> temprano pero dentro (el cron más madrugador)
    assert calendario.en_ventana_preapertura(_utc("2026-07-17T10:45"))
    # 13:45 UTC = 09:45 ET -> ya abrió, fuera
    assert not calendario.en_ventana_preapertura(_utc("2026-07-17T13:45"))
    # 13:20 UTC = 09:20 ET -> a 10 min del open, demasiado justo, fuera
    assert not calendario.en_ventana_preapertura(_utc("2026-07-17T13:20"))


def test_ventana_preapertura_est():
    # 2026-01-15 (jueves, EST): 13:45 UTC = 08:45 ET -> en ventana
    assert calendario.en_ventana_preapertura(_utc("2026-01-15T13:45"))
    # 12:45 UTC = 07:45 ET -> temprano pero dentro
    assert calendario.en_ventana_preapertura(_utc("2026-01-15T12:45"))
    # 10:00 UTC = 05:00 ET -> más de 4 h antes, fuera
    assert not calendario.en_ventana_preapertura(_utc("2026-01-15T10:00"))
    # 14:35 UTC = 09:35 ET -> ya abrió, fuera
    assert not calendario.en_ventana_preapertura(_utc("2026-01-15T14:35"))


def _crons(nombre_workflow):
    import re
    from pathlib import Path
    wf = Path(__file__).resolve().parent.parent / ".github/workflows" / nombre_workflow
    crons = re.findall(r'cron:\s*"(\d+) (\d+) \* \* 1-5"', wf.read_text())
    assert crons, f"no se encontraron crons en {nombre_workflow}"
    return [(int(h), int(m)) for m, h in crons]


def _sirve(hora, minuto, retraso_min, fecha, fase_fn, pref_fn):
    """¿Este cron, arrancando con `retraso_min` de retraso, acaba trabajando?

    Réplica de la lógica de `_control_de_ventana`: si llega antes del momento
    preferido, espera si cabe en el tope y si no cede el turno; si llega después,
    trabaja siempre que siga dentro de la ventana.
    """
    from datetime import timedelta
    t = (_utc(f"{fecha}T{hora:02d}:{minuto:02d}") + timedelta(minutes=retraso_min))
    t = t.astimezone(config.TZ_ET)
    preferido = pref_fn(t.date())
    if preferido is None:
        return False
    if t < preferido:
        faltan = (preferido - t).total_seconds() / 60.0
        if faltan > config.ESPERA_VENTANA_MAX_MIN:
            return False          # cede el turno a un peldaño más cercano
        t = preferido             # durmió hasta el momento preferido
    return fase_fn(t) == calendario.DENTRO


# Regresión del fallo del 2026-07-27. Ese día los cinco crons de la pre-apertura
# se retrasaron entre 2h14m y 2h55m, TODOS aterrizaron pasada la apertura y la
# sesión se perdió en verde. La invariante correcta no es "cada cron cae dentro
# de la ventana si arranca puntual" (eso era lo que se comprobaba antes, y pasaba
# el 27 de julio), sino "para CUALQUIER retraso plausible queda al menos un cron
# capaz de trabajar". Si tocas la escalera de crons, este test te dice si acabas
# de reabrir el agujero.
def test_escalera_de_crons_preapertura_aguanta_retrasos():
    crons = _crons("preapertura.yml")
    for fecha in ("2026-07-17", "2026-01-15"):          # EDT y EST
        for retraso in range(0, 5 * 60 + 1, 10):        # de 0 a 5 h de retraso
            assert any(_sirve(h, m, retraso, fecha,
                              calendario.fase_preapertura,
                              calendario.momento_preferido_preapertura)
                       for h, m in crons), (
                f"con {retraso} min de retraso el {fecha} ningún cron de la "
                f"pre-apertura llega a tiempo: la sesión se perdería")


def test_escalera_de_crons_postcierre_aguanta_retrasos():
    crons = _crons("postcierre.yml")
    for fecha in ("2026-07-17", "2026-01-15"):
        for retraso in range(0, 5 * 60 + 1, 10):
            assert any(_sirve(h, m, retraso, fecha,
                              calendario.fase_postcierre,
                              calendario.momento_preferido_postcierre)
                       for h, m in crons), (
                f"con {retraso} min de retraso el {fecha} ningún cron del "
                f"post-cierre llega a tiempo")


def test_fases_distinguen_pronto_de_tarde():
    """El corazón del bug: llegar pronto y llegar tarde NO son lo mismo.

    Antes ambos devolvían False y el sistema los trataba igual, así que perder la
    ventana de decisión de un día entero salía en verde igual que un festivo.
    """
    # 2026-07-17 (viernes, EDT), apertura 13:30 UTC
    assert calendario.fase_preapertura(_utc("2026-07-17T08:00")) == calendario.ANTES
    assert calendario.fase_preapertura(_utc("2026-07-17T12:45")) == calendario.DENTRO
    assert calendario.fase_preapertura(_utc("2026-07-17T13:20")) == calendario.DESPUES
    assert calendario.fase_preapertura(_utc("2026-07-17T15:00")) == calendario.DESPUES
    # sábado
    assert calendario.fase_preapertura(_utc("2026-07-18T12:45")) == calendario.SIN_MERCADO
    # El post-cierre no tiene techo: nunca es "tarde".
    assert calendario.fase_postcierre(_utc("2026-07-17T18:00")) == calendario.ANTES
    assert calendario.fase_postcierre(_utc("2026-07-17T22:00")) == calendario.DENTRO
    assert calendario.fase_postcierre(_utc("2026-07-18T03:00")) == calendario.DENTRO


def test_reproduce_el_fallo_del_27_de_julio():
    """Los cinco disparos reales de aquel día caían en DESPUES, no en ANTES.

    Es lo que hace que el resultado correcto sea 'fallo:ventana-perdida' (rojo) y
    no un 'omitido:' cualquiera (verde).
    """
    for hora in ("13:29", "13:43", "13:59", "15:10", "15:30"):
        assert calendario.fase_preapertura(_utc(f"2026-07-27T{hora}")) == \
            calendario.DESPUES


def test_ventana_postcierre():
    # 22:00 UTC = 18:00 EDT -> tras el cierre
    assert calendario.en_ventana_postcierre(_utc("2026-07-17T22:00"))


def test_fin_de_semana_sin_mercado():
    assert not calendario.es_dia_de_mercado("2026-07-18")  # sábado
    assert calendario.es_dia_de_mercado("2026-07-17")      # viernes
