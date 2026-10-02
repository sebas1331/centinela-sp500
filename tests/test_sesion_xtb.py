"""Caducidad de la sesión de XTB: detectarla, decirlo y poder renovarla.

XTB no ofrece TOTP —sus métodos de segundo factor son SMS, notificación push y
correo— y la sesión dura 8 horas. Eso significa que cada cierto tiempo hace
falta una persona, y todo lo que sigue existe para que esa persona sepa qué
hacer con el móvil en la mano y sin abrir un terminal.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import broker_xtb as bx, config  # noqa: E402
from conftest import CUENTA_PRUEBA  # noqa: E402
import vigilante  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. Detectar que XTB pide código
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mensaje", [
    "2FA is required but no totp_secret was provided",
    "AUTH_MANAGER_2FA_NO_SECRET",
    "server returned requires_2fa",
    "TWO_FACTOR challenge pending",
])
def test_se_reconoce_cuando_XTB_pide_codigo(mensaje):
    """El cliente lo lanza como un CASError genérico con el motivo dentro."""
    assert bx._pide_codigo(RuntimeError(mensaje))


@pytest.mark.parametrize("mensaje", [
    "connection refused", "Invalid email or password", "HTTP 500",
])
def test_otros_errores_no_se_confunden_con_una_sesion_caducada(mensaje):
    """Tratar un fallo de red como sesión caducada mandaría al usuario a pegar
    un código que no arregla nada."""
    assert not bx._pide_codigo(RuntimeError(mensaje))


def test_el_mensaje_de_sesion_caducada_dice_QUE_HACER():
    """Un rojo que no dice qué hacer obliga a leerse el código para entenderlo.

    La marca va literal y en una línea para poder buscarla de un vistazo en el
    log de un run desde el móvil.
    """
    exc = bx.SesionCaducada("detalle del cliente")
    texto = str(exc)
    assert texto.startswith(bx.MARCA_SESION_CADUCADA)
    assert "Renovar sesión XTB" in texto
    assert "correo" in texto
    assert "terminal" in texto        # dice explícitamente que NO hace falta


def test_conectar_traduce_el_error_del_cliente(monkeypatch):
    """De un traceback ilegible a una instrucción que se puede seguir."""
    class _ClienteQueExigeCodigo:
        async def connect(self):
            raise RuntimeError("CASError: 2FA is required but no totp_secret")
        async def disconnect(self):
            pass

    b = bx.BrokerXTB(
        bx.Credenciales(email="x@y.z", cuenta=CUENTA_PRUEBA, password="p"),
        cliente=_ClienteQueExigeCodigo())
    with pytest.raises(bx.SesionCaducada) as exc:
        b.conectar()
    assert bx.MARCA_SESION_CADUCADA in str(exc.value)


def test_un_fallo_normal_de_conexion_sigue_saliendo_tal_cual(monkeypatch):
    class _ClienteSinRed:
        async def connect(self):
            raise ConnectionError("no route to host")
        async def disconnect(self):
            pass

    b = bx.BrokerXTB(
        bx.Credenciales(email="x@y.z", cuenta=CUENTA_PRUEBA, password="p"),
        cliente=_ClienteSinRed())
    with pytest.raises(ConnectionError):
        b.conectar()


# --------------------------------------------------------------------------- #
# 2. El Vigilante avisa ANTES de que caduque
# --------------------------------------------------------------------------- #
def _sesion(tmp_path, monkeypatch, horas_restantes):
    ruta = tmp_path / "sesion.json"
    expira = datetime.now(UTC) + timedelta(hours=horas_restantes)
    ruta.write_text(json.dumps({
        "tgt": "xxx", "extracted_at": datetime.now(UTC).isoformat(),
        "expires_at": expira.isoformat()}), encoding="utf-8")
    monkeypatch.setattr(bx, "ARCHIVO_SESION", ruta)
    monkeypatch.setattr(config, "EJECUCION_BROKER", True)


def test_una_sesion_con_cuerda_de_sobra_no_molesta(tmp_path, monkeypatch):
    _sesion(tmp_path, monkeypatch, horas_restantes=7)
    assert vigilante.revisar_sesion_xtb() == []


def test_que_caduque_pronto_se_informa_pero_no_es_un_problema(tmp_path, monkeypatch,
                                                              capsys):
    """Esta regla nació esperando que renovar exigiera a una persona. El
    2026-09-29 se midió que NO: el TGT caduca de madrugada todas las noches y el
    login en frío de la mañana entra solo con la cookie de dispositivo de
    confianza.

    Avisar igualmente era un rojo cada mañana laborable por algo que se arregla
    solo. Se informa en el log, que es donde no molesta, y ya está.
    """
    _sesion(tmp_path, monkeypatch, horas_restantes=1.5)
    assert vigilante.revisar_sesion_xtb() == []
    assert "caduca en 1.5 h" in capsys.readouterr().out


def test_una_sesion_ya_caducada_tampoco(tmp_path, monkeypatch, capsys):
    _sesion(tmp_path, monkeypatch, horas_restantes=-2)
    assert vigilante.revisar_sesion_xtb() == []
    salida = capsys.readouterr().out
    assert "caducada hace 2.0 h" in salida
    # Y dice qué pasaría si la cookie dejara de valer, que es el caso que sí
    # exigiría a una persona.
    assert bx.MARCA_SESION_CADUCADA in salida


def test_no_tener_sesion_todavia_NO_es_un_fallo(tmp_path, monkeypatch):
    """Puede ser la primera vez. Se dice y ya está."""
    monkeypatch.setattr(bx, "ARCHIVO_SESION", tmp_path / "no-existe.json")
    monkeypatch.setattr(config, "EJECUCION_BROKER", True)
    assert vigilante.revisar_sesion_xtb() == []


def test_una_sesion_ilegible_SI_es_un_fallo(tmp_path, monkeypatch):
    """Ahí el próximo run morirá pidiendo código, así que hay que avisar."""
    ruta = tmp_path / "rota.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    monkeypatch.setattr(bx, "ARCHIVO_SESION", ruta)
    monkeypatch.setattr(config, "EJECUCION_BROKER", True)
    problemas = vigilante.revisar_sesion_xtb()
    assert len(problemas) == 1 and "ilegible" in problemas[0]


# --------------------------------------------------------------------------- #
# 3. El workflow de renovación, tal como se usa desde el móvil
# --------------------------------------------------------------------------- #
def test_el_workflow_de_renovacion_se_lanza_a_mano_con_un_campo_de_texto():
    import yaml
    wf = yaml.safe_load(
        (RAIZ / ".github" / "workflows" / "renovar_sesion.yml").read_text(
            encoding="utf-8"))
    disparo = wf[True]["workflow_dispatch"]
    campo = disparo["inputs"]["codigo"]
    assert campo["type"] == "string"
    # NO obligatorio: sin código es la fase 1, la que pide el correo.
    assert campo.get("required") is False
    assert "schedule" not in wf[True], "renovar la sesión no se automatiza sola"


def test_la_renovacion_comparte_turno_con_los_escaneos():
    """Renovar la sesión mientras un ejecutor la usa deja a los dos peleándose
    por el mismo fichero."""
    import yaml
    wf = yaml.safe_load(
        (RAIZ / ".github" / "workflows" / "renovar_sesion.yml").read_text(
            encoding="utf-8"))
    assert wf["concurrency"]["group"] == "centinela-escritura"


def test_el_vigilante_mantiene_viva_la_cache_de_sesion():
    """GitHub borra una caché que nadie usa en 7 días, y el Vigilante es el
    único que corre TODOS los días, fines de semana incluidos."""
    import yaml
    wf = yaml.safe_load(
        (RAIZ / ".github" / "workflows" / "vigilante.yml").read_text(
            encoding="utf-8"))
    pasos = json.dumps(wf["jobs"]["vigilar"]["steps"])
    assert "actions/cache" in pasos
    assert "centinela_xtb_session" in pasos
    # Clave nueva en cada run: con la misma, `actions/cache` no reescribe y la
    # caché seguiría envejeciendo hasta que GitHub la borre.
    assert "xtb-sesion-${{ github.run_id }}" in pasos


def test_el_README_explica_que_hacer_en_tres_pasos():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert bx.MARCA_SESION_CADUCADA in readme
    assert "Renovar sesión XTB" in readme


# --------------------------------------------------------------------------- #
# Una sesión caducada NO es un problema: está medido que se renueva sola
# --------------------------------------------------------------------------- #
def test_una_sesion_caducada_no_es_un_problema(tmp_path, monkeypatch):
    """Medido el 2026-09-29: el TGT dura 8 h y se refresca con el último login
    del día, así que caduca de madrugada TODAS las noches. Aquel día caducó a
    las 05:11 UTC y el run de compras de las 12:46 hizo login en frío y entró
    sin pedir código — la cookie de dispositivo de confianza sigue valiendo.

    Denunciarlo sería un rojo cada mañana laborable por algo que se arregla
    solo, y un rojo diario deja de significar nada.
    """
    import sys
    from datetime import datetime, timedelta
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import vigilante as vig
    from centinela import broker_xtb as bx, config, salud

    ruta = tmp_path / "sesion.json"
    expira = datetime.now(config.TZ_ET) - timedelta(hours=9)
    ruta.write_text(json.dumps({"tgt": "x", "expires_at": expira.isoformat()}),
                    encoding="utf-8")
    monkeypatch.setattr(bx, "ARCHIVO_SESION", ruta)
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setattr(config, "EJECUCION_BROKER", True)

    assert vig.revisar_sesion_xtb() == []


def test_pero_un_login_que_falla_de_verdad_si_lo_es(tmp_path, monkeypatch):
    """Es el único síntoma que prueba que la cookie dejó de valer."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import vigilante as vig
    from centinela import broker_xtb as bx, config, salud

    monkeypatch.setattr(bx, "ARCHIVO_SESION", tmp_path / "no-hay.json")
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setattr(config, "EJECUCION_BROKER", True)
    salud.registrar("compras", bx.MARCA_SESION_CADUCADA, "la sesión caducó")

    problemas = vig.revisar_sesion_xtb()
    assert len(problemas) == 1
    assert "Renovar sesión XTB" in problemas[0]
    assert "Compras en XTB" in problemas[0]
