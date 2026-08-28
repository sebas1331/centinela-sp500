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
        for retraso in range(0, 12 * 60 + 1, 5):        # de 0 a 12 h de retraso
            assert any(_sirve(h, m, retraso, fecha,
                              calendario.fase_preapertura,
                              calendario.momento_preferido_preapertura)
                       for h, m in crons), (
                f"con {retraso} min de retraso el {fecha} ningún cron de la "
                f"pre-apertura llega a tiempo: la sesión se perdería")


def test_escalera_de_crons_postcierre_aguanta_retrasos():
    crons = _crons("postcierre.yml")
    for fecha in ("2026-07-17", "2026-01-15"):
        for retraso in range(0, 12 * 60 + 1, 5):
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


# Retrasos REALES (en minutos) de los siete disparos de la pre-apertura del
# 2026-08-27, medidos como `createdAt - hora del cron` sobre los runs 33106300280
# a 33124245743. Ese día la escalera de siete peldaños solo cubría ~5 h, los
# siete aterrizaron pasada la apertura y la sesión se perdió (en rojo, eso sí).
RETRASOS_2026_08_27 = {
    (8, 3): 657,    # run 33106300280 -> 19:00 UTC
    (8, 53): 623,   # run 33107633605 -> 19:16 UTC
    (9, 43): 616,   # run 33111130470 -> 19:59 UTC
    (10, 33): 606,  # run 33114423249 -> 20:39 UTC
    (11, 23): 580,  # run 33116319865 -> 21:03 UTC
    (12, 13): 601,  # run 33121657602 -> 22:14 UTC
    (13, 3): 590,   # run 33124245743 -> 22:53 UTC
}


def test_regresion_2026_08_27_retraso_de_diez_horas():
    """La escalera tiene que sobrevivir al día en que GitHub se fue ~10 h.

    Este es el escenario que provocó la emergencia: no un bug del código, sino
    un scheduler de cron disparando con 9h40m-10h57m de retraso, muy por encima
    de las ~5 h que cubría la escalera de siete peldaños. La invariante es la
    misma de siempre —para CUALQUIER retraso plausible tiene que quedar al menos
    un peldaño capaz de trabajar—, pero medida contra retrasos reales, no
    hipotéticos.
    """
    crons = _crons("preapertura.yml")

    # 1) El peor retraso observado aquel día, aplicado a TODA la escalera.
    peor = max(RETRASOS_2026_08_27.values())
    assert peor > 5 * 60, "el escenario del 27-ago tiene que superar la vieja cota de 5 h"
    for fecha in ("2026-07-17", "2026-01-15"):          # EDT y EST
        assert any(_sirve(h, m, peor, fecha,
                          calendario.fase_preapertura,
                          calendario.momento_preferido_preapertura)
                   for h, m in crons), (
            f"con el retraso real del 2026-08-27 ({peor} min) ningún peldaño "
            f"llega a tiempo el {fecha}: la sesión se volvería a perder")

    # 2) El día tal cual ocurrió, sobre la fecha real. Aquel retraso fue
    #    SISTÉMICO —el scheduler entero iba con 9h40m-10h57m—, así que la forma
    #    honesta de reproducirlo es aplicar a TODA la escalera cada uno de los
    #    retrasos observados, no darles 0 a los peldaños que ese día aún no
    #    existían (eso los haría llegar puntuales, que es justo lo que no pasó).
    for retraso in sorted(RETRASOS_2026_08_27.values()):
        assert any(_sirve(h, m, retraso, "2026-08-27",
                          calendario.fase_preapertura,
                          calendario.momento_preferido_preapertura)
                   for h, m in crons), (
            f"reproduciendo el 2026-08-27 con {retraso} min de retraso en toda "
            f"la escalera, ningún peldaño trabaja")

    # 3) La escalera vieja NO cubría el caso: el test tiene que estar mirando
    #    algo real, no pasar por vacuidad.
    escalera_vieja = [(8, 3), (8, 53), (9, 43), (10, 33), (11, 23), (12, 13), (13, 3)]
    assert not any(_sirve(h, m, RETRASOS_2026_08_27[(h, m)], "2026-08-27",
                          calendario.fase_preapertura,
                          calendario.momento_preferido_preapertura)
                   for h, m in escalera_vieja), (
        "la escalera vieja debería fallar en este escenario; si pasa, el test no "
        "está reproduciendo la emergencia del 2026-08-27")
