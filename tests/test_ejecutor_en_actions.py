"""El ejecutor en GitHub Actions: aislamiento, secretos y ventanas.

Desde que el ejecutor salió del Mac, estas son las propiedades que ningún
cambio de workflow puede romper sin que alguien se entere.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import config  # noqa: E402

WORKFLOWS = RAIZ / ".github" / "workflows"


def _wf(nombre: str) -> dict:
    return yaml.safe_load((WORKFLOWS / nombre).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. El broker NUNCA puede tumbar la persistencia de la sesión de trading
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fichero,escaneo", [
    ("preapertura.yml", "preapertura"),
    ("postcierre.yml", "postcierre"),
])
def test_el_ejecutor_va_en_un_job_aparte_que_depende_del_escaneo(fichero, escaneo):
    jobs = _wf(fichero)["jobs"]
    assert "ejecutor" in jobs, f"{fichero} no tiene job de ejecutor"

    ejecutor = jobs["ejecutor"]
    assert ejecutor["needs"] == escaneo
    assert "success" in ejecutor["if"] and "procesado" in ejecutor["if"]

    # Y sobre todo: el escaneo NO sabe que el ejecutor existe. Si dependiera de
    # él, un XTB caído dejaría la sesión de trading sin commitear.
    assert "needs" not in jobs[escaneo]
    pasos = " ".join(json.dumps(p) for p in jobs[escaneo]["steps"])
    assert "ejecutor_xtb" not in pasos
    assert "XTB_" not in pasos


@pytest.mark.parametrize("fichero", ["preapertura.yml", "postcierre.yml"])
def test_la_verificacion_de_persistencia_no_depende_del_ejecutor(fichero):
    """`verificar` comprueba que la sesión se guardó. Que eso quede colgando de
    que el broker responda sería exactamente al revés de lo que hace falta."""
    needs = _wf(fichero)["jobs"]["verificar"]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert "ejecutor" not in needs


# --------------------------------------------------------------------------- #
# 2. Los tres momentos existen y cada uno cae donde debe
# --------------------------------------------------------------------------- #
def test_los_tres_momentos_del_ejecutor_tienen_workflow():
    assert "compras" in str(_wf("preapertura.yml")["jobs"]["ejecutor"]["steps"])
    assert "reconcilia" in str(_wf("postcierre.yml")["jobs"]["ejecutor"]["steps"])
    assert "ventas" in str(_wf("ventas.yml")["jobs"]["ventas"]["steps"])


def test_las_ventas_se_disparan_pegadas_al_cierre_en_los_dos_husos():
    """La ventana es de 25 min y el cierre se mueve una hora con el horario de
    EE.UU., así que hacen falta las dos franjas: 19:xx y 20:xx UTC."""
    cron = _wf("ventas.yml")[True]["schedule"]   # `on:` lo parsea yaml como True
    horas = {c["cron"].split()[1] for c in cron}
    assert horas == {"19", "20"}, f"faltan franjas horarias: {horas}"
    # Varios disparos por franja: un solo cron que llegue tarde pierde el día.
    minutos = cron[0]["cron"].split()[0].split(",")
    assert len(minutos) >= 3


# --------------------------------------------------------------------------- #
# 3. Secretos y Chromium
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fichero,job", [
    ("preapertura.yml", "ejecutor"), ("postcierre.yml", "ejecutor"),
    ("ventas.yml", "ventas"), ("probar_broker.yml", "probar"),
])
def test_cada_job_del_broker_trae_sus_secretos_y_su_navegador(fichero, job):
    pasos = _wf(fichero)["jobs"][job]["steps"]
    texto = json.dumps(pasos)
    for secreto in ("XTB_EMAIL", "XTB_CUENTA", "XTB_PASSWORD"):
        assert f"secrets.{secreto}" in texto, f"{fichero}: falta {secreto}"
    # El login de xStation5 cae a un navegador porque el WAF bloquea el REST.
    assert "playwright install" in texto, f"{fichero}: sin Chromium el login muere"
    # Y la sesión cacheada, que es lo que evita el 2FA en cada ejecución.
    assert "xtb-sesion-" in texto, f"{fichero}: sin caché de sesión habría 2FA"


@pytest.mark.parametrize("fichero", ["preapertura.yml", "postcierre.yml",
                                     "ventas.yml", "probar_broker.yml"])
def test_ningun_workflow_del_broker_silencia_errores(fichero):
    texto = (WORKFLOWS / fichero).read_text(encoding="utf-8")
    for prohibido in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
        assert prohibido not in texto, f"{fichero} contiene '{prohibido}'"


def test_la_prueba_manual_no_opera_por_defecto():
    """Una prueba que abre posiciones por defecto acaba abriéndolas cuando
    alguien solo quería mirar."""
    wf = _wf("probar_broker.yml")
    disparo = wf[True]["workflow_dispatch"]
    assert disparo["inputs"]["operar"]["default"] is False
    assert "schedule" not in wf[True], "la prueba NO debe correr sola"


# --------------------------------------------------------------------------- #
# 4. Lo ejecutado se persiste aunque el ejecutor falle a medias
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fichero,job", [
    ("preapertura.yml", "ejecutor"), ("postcierre.yml", "ejecutor"),
    ("ventas.yml", "ventas"),
])
def test_lo_ya_ejecutado_se_commitea_aunque_el_job_muera(fichero, job):
    """Si mandó tres órdenes y falló en la cuarta, las tres tienen que quedar
    registradas o el siguiente disparo las repetiría."""
    pasos = _wf(fichero)["jobs"][job]["steps"]
    commit = [p for p in pasos if "commit_y_push" in str(p.get("run", ""))]
    assert commit, f"{fichero}: el ejecutor no persiste lo que hizo"
    assert "always()" in commit[0]["if"]
    assert "bitacora_broker.csv" in commit[0]["run"]
    # NO commitea la bitácora del simulador: esa la escribe el escaneo.
    assert "bitacora.csv " not in commit[0]["run"]


# --------------------------------------------------------------------------- #
# 5. El candado sigue anclado al repositorio
# --------------------------------------------------------------------------- #
def test_la_cuenta_permitida_vive_en_el_codigo_y_no_en_un_secret():
    """Un secret se cambia desde una web sin dejar diff; esto exige un commit."""
    fuente = (RAIZ / "centinela" / "config.py").read_text(encoding="utf-8")
    assert f"CUENTA_DEMO = {config.CUENTA_DEMO}" in fuente
    assert 'TIPO_CUENTA_BROKER = "demo"' in fuente
