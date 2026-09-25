"""La regla dura: un fallo de Telegram NUNCA puede tocar la bitácora.

Es la razón por la que el envío vive en un job aparte y no como un paso más del
escaneo. Se comprueba por los dos lados, porque cada uno solo protege la mitad:

  - CÓDIGO: notificar.py no abre la bitácora ni el estado en modo escritura, y
    un envío que revienta deja los ficheros byte a byte como estaban.
  - WORKFLOWS: el job de notificación depende del escaneo con `needs` y arranca
    solo si el escaneo ya terminó bien. Aunque el código fuera inofensivo, un
    paso dentro del job del escaneo podría tumbar el commit de la sesión.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, notificaciones as notif  # noqa: E402


def _huella(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# 1. El código no toca la bitácora
# --------------------------------------------------------------------------- #
def test_un_telegram_caido_deja_la_bitacora_intacta(tmp_path, monkeypatch):
    """Se revienta el envío a propósito y se comprueba byte a byte."""
    import notificar

    bitacora = tmp_path / "bitacora.csv"
    shutil.copy(RAIZ / "bitacora.csv", bitacora)
    estado = tmp_path / "estado.json"
    shutil.copy(RAIZ / "estado" / "estado.json", estado)
    antes = {"bitacora": _huella(bitacora), "estado": _huella(estado)}

    monkeypatch.setattr(notificar, "RUTA_BITACORA", bitacora)
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_CSV", bitacora)
    monkeypatch.setattr(config, "ARCHIVO_ESTADO", estado)
    monkeypatch.setattr(config, "ARCHIVO_NOTIFICACIONES", tmp_path / "notif.json")
    monkeypatch.setattr(config, "NOTIFICACIONES_ACTIVAS", True)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notif.time, "sleep", lambda s: None)

    def telegram_caido(*a, **k):
        raise notif.requests.ConnectionError("Telegram no responde")
    monkeypatch.setattr(notif.requests, "post", telegram_caido)

    # Red de precios cortada también: este test mide qué le pasa a los ficheros
    # cuando Telegram cae, no la disponibilidad de yfinance.
    monkeypatch.setattr(notificar, "precios_de", lambda tickers: {})

    registro = notif.cargar_registro()
    with pytest.raises(notif.ErrorNotificacion):
        notificar.evento_postcierre("2026-09-24", registro)

    assert _huella(bitacora) == antes["bitacora"], "¡la bitácora cambió!"
    assert _huella(estado) == antes["estado"], "¡el estado cambió!"


def test_lo_ya_enviado_queda_registrado_aunque_el_resto_falle(tmp_path, monkeypatch):
    """Si el envío nº3 revienta, los dos primeros no se repiten mañana.

    Perder un aviso es malo; repetirlo cuatro veces mata la confianza en el
    canal, que es lo único que hace que se lea. Por eso `notificar.py` guarda
    el registro en un `finally` y el workflow lo commitea con `always()`.
    """
    monkeypatch.setattr(config, "ARCHIVO_NOTIFICACIONES", tmp_path / "notif.json")
    monkeypatch.setattr(config, "NOTIFICACIONES_ACTIVAS", True)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notif.time, "sleep", lambda s: None)

    enviados = []

    class _Resp:
        status_code = 200
        text = "ok"

    def post(url, timeout=None, data=None):
        if len(enviados) >= 2:
            raise notif.requests.ConnectionError("cae al tercero")
        enviados.append(data["text"])
        return _Resp()
    monkeypatch.setattr(notif.requests, "post", post)

    registro = notif.cargar_registro()
    ids = [notif.identificador("2026-09-24", "venta", "A", t)
           for t in ("AAA", "BBB", "CCC")]
    with pytest.raises(notif.ErrorNotificacion):
        for i, ident in enumerate(ids):
            notif.enviar_una(registro, ident, f"msg{i}")
    notif.guardar_registro(registro)

    guardado = notif.cargar_registro()
    assert notif.ya_enviada(guardado, ids[0])
    assert notif.ya_enviada(guardado, ids[1])
    assert not notif.ya_enviada(guardado, ids[2])   # el que falló, reintentable


def test_notificar_no_importa_el_modulo_de_bitacora():
    """Sin la puerta, nadie puede entrar por ella.

    `centinela.bitacora` es el único módulo que ESCRIBE operaciones. Que
    notificar.py no lo importe hace imposible por construcción que un aviso
    llegue a modificar una operación, no solo improbable.
    """
    fuente = (RAIZ / "scripts" / "notificar.py").read_text(encoding="utf-8")
    assert "bitacora" not in fuente.replace("RUTA_BITACORA", "").replace(
        "cargar_bitacora", "").replace("bitacora.csv", "")


# --------------------------------------------------------------------------- #
# 2. Los workflows lo aíslan
# --------------------------------------------------------------------------- #
def _wf(nombre: str) -> dict:
    return yaml.safe_load((RAIZ / ".github" / "workflows" / nombre).read_text(
        encoding="utf-8"))


@pytest.mark.parametrize("fichero,escaneo", [
    ("preapertura.yml", "preapertura"),
    ("postcierre.yml", "postcierre"),
])
def test_el_envio_va_en_un_job_aparte_que_depende_del_escaneo(fichero, escaneo):
    wf = _wf(fichero)
    jobs = wf["jobs"]
    assert "notificar" in jobs, f"{fichero} no tiene job de notificación"

    notificar_job = jobs["notificar"]
    assert notificar_job["needs"] == escaneo
    # Solo corre si el escaneo TRABAJÓ y terminó bien: un aviso de una sesión
    # que no se procesó sería ruido, y uno de una sesión fallida, mentira.
    assert "success" in notificar_job["if"]
    assert "procesado" in notificar_job["if"]

    # Y, sobre todo: el job del escaneo NO sabe que este existe. Si dependiera
    # de él, un Telegram caído dejaría la sesión sin commitear.
    assert "needs" not in jobs[escaneo]
    pasos = " ".join(json.dumps(p) for p in jobs[escaneo]["steps"])
    assert "notificar.py" not in pasos
    assert "TELEGRAM" not in pasos


@pytest.mark.parametrize("fichero", ["preapertura.yml", "postcierre.yml"])
def test_la_verificacion_de_persistencia_no_depende_del_envio(fichero):
    """`verificar` es la segunda opinión sobre el commit de la sesión.

    Si llegara a depender de `notificar`, un fallo del canal de avisos podría
    impedir que se comprobara la persistencia, que es justo al revés de lo que
    hace falta.
    """
    jobs = _wf(fichero)["jobs"]
    needs = jobs["verificar"]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert "notificar" not in needs


def test_el_vigilante_avisa_en_un_job_aparte():
    """Si el aviso fallara DENTRO del vigilante, un Telegram caído podría
    enmascarar justo la avería que el vigilante acaba de encontrar."""
    jobs = _wf("vigilante.yml")["jobs"]
    assert "alertar" in jobs
    assert jobs["alertar"]["needs"] == "vigilar"
    assert "failure" in jobs["alertar"]["if"]
    assert "needs" not in jobs["vigilar"]
    pasos = " ".join(json.dumps(p) for p in jobs["vigilar"]["steps"])
    assert "TELEGRAM" not in pasos


@pytest.mark.parametrize("fichero", ["preapertura.yml", "postcierre.yml",
                                     "vigilante.yml"])
def test_el_registro_se_commitea_aunque_el_envio_falle(fichero):
    """`always()` en el paso de commit: ver la nota del test de arriba."""
    jobs = _wf(fichero)["jobs"]
    job = jobs.get("notificar") or jobs["alertar"]
    commit = [p for p in job["steps"] if "commit_y_push" in str(p.get("run", ""))]
    assert commit, f"{fichero}: el job de notificación no persiste el registro"
    assert "always()" in commit[0]["if"]
    # Y solo commitea el registro, no la bitácora ni los reportes.
    assert "estado" in commit[0]["run"]
    assert "bitacora" not in commit[0]["run"]


@pytest.mark.parametrize("fichero", ["preapertura.yml", "postcierre.yml",
                                     "vigilante.yml"])
def test_ningun_workflow_se_traga_los_fallos(fichero):
    """Prohibido `|| true`, `continue-on-error`, `2>/dev/null` y `set +e`.

    Un fallo silenciado en el canal de avisos se vería igual que un día sin
    noticias, que es el modo exacto en que este repositorio ha perdido días
    enteros dos veces.
    """
    texto = (RAIZ / ".github" / "workflows" / fichero).read_text(encoding="utf-8")
    for prohibido in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
        assert prohibido not in texto, f"{fichero} contiene '{prohibido}'"


def test_el_resultado_sin_notificaciones_esta_en_el_vocabulario():
    """Terminar sin enviar nada es legítimo; terminar sin poder enviar, no.

    El vocabulario de resultados es CERRADO por diseño (ver resultados.py):
    añadir un motivo obliga a decidir explícitamente si puede terminar sin
    commit, en vez de heredar el silencio.
    """
    from centinela import resultados
    assert resultados.OMITIDO_SIN_NOTIFICACIONES in resultados.LEGITIMOS_SIN_COMMIT
    assert resultados.es_legitimo_sin_commit(resultados.OMITIDO_SIN_NOTIFICACIONES)
    assert not resultados.es_fallo(resultados.OMITIDO_SIN_NOTIFICACIONES)
