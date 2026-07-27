"""Contrato de persistencia: un escaneo que trabaja SIEMPRE deja algo en disco.

Este es el test que hace imposible volver a la situación del 2026-07-27, donde
los workflows salían verdes sin escribir nada. Comprueba tres cosas:

  1. Con --forzar, cada escaneo escribe su línea en logs/decisiones-<fecha>.log
     y actualiza estado/estado.json. Aunque no haya candidatos, aunque no se abra
     ni se cierre nada: siempre queda huella, y por tanto siempre hay commit.
  2. El vocabulario de resultados es cerrado y coherente entre Python y bash.
  3. Perder la ventana devuelve un fallo (rojo), no un "omitido" (verde).

No toca la red: los datos de mercado se sustituyen por dobles mínimos.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, resultados  # noqa: E402


# --------------------------------------------------------------------------- #
# Dobles de prueba
# --------------------------------------------------------------------------- #
def _precios_falsos():
    """Un ticker con historial suficiente para que nada reviente aguas abajo."""
    fechas = pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=300)
    base = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000_000},
        index=fechas,
    )
    return {"TEST": base}


@pytest.fixture
def repo_temporal(tmp_path, monkeypatch):
    """Redirige TODAS las rutas de escritura a un directorio limpio."""
    for nombre, sub in (("DATOS_DIR", "datos"), ("MODELOS_DIR", "modelos"),
                        ("ESTADO_DIR", "estado"), ("LOGS_DIR", "logs"),
                        ("REPORTES_DIR", "reportes")):
        destino = tmp_path / sub
        destino.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, nombre, destino)

    monkeypatch.setattr(config, "ARCHIVO_ESTADO", tmp_path / "estado" / "estado.json")
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_CSV", tmp_path / "bitacora.csv")
    monkeypatch.setattr(config, "ARCHIVO_BITACORA_SQLITE", tmp_path / "bitacora.sqlite")
    monkeypatch.setattr(config, "ARCHIVO_ATH", tmp_path / "datos" / "ath.json")
    return tmp_path


def _huella(raiz: Path) -> set[tuple[str, int, float]]:
    """Foto del árbol: ruta, tamaño y mtime de cada archivo."""
    return {(str(p.relative_to(raiz)), p.stat().st_size, p.stat().st_mtime)
            for p in raiz.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- #
# 1. Un escaneo que trabaja siempre escribe
# --------------------------------------------------------------------------- #
def test_preapertura_forzada_siempre_escribe_en_disco(repo_temporal, monkeypatch):
    """Aunque el screener no encuentre NI UN candidato, tiene que quedar huella.

    Ese es el caso peligroso: un día tranquilo sin señales se parecía demasiado a
    un día en el que el sistema no corrió.
    """
    import escaneo_preapertura as ep
    from centinela import runtime, screener, estado as est_mod

    monkeypatch.setattr(runtime, "preparar_datos",
                        lambda *a, **k: (_precios_falsos(), {}, {}))
    monkeypatch.setattr(ep.Modelo, "cargar",
                        classmethod(lambda cls: type("M", (), {"tipo": "test", "umbral": 0.5})()))
    # Sin candidatos: el escenario que antes podía terminar sin escribir nada.
    monkeypatch.setattr(screener, "escanear",
                        lambda *a, **k: ([], ["sin candidatos hoy"],
                                         {"universo": 0, "en_drawdown": 0, "con_senal": 0}))

    antes = _huella(repo_temporal)
    monkeypatch.setattr(sys, "argv", ["escaneo_preapertura.py", "--forzar",
                                      "--fecha", "2026-07-24"])
    assert ep.main() == resultados.PROCESADO
    despues = _huella(repo_temporal)

    assert despues != antes, ("la pre-apertura forzada no dejó NINGÚN cambio en "
                              "disco: eso es exactamente el fallo silencioso")
    log = config.LOGS_DIR / "decisiones-2026-07-24.log"
    assert log.exists() and log.stat().st_size > 0, \
        "falta la línea del día en decisiones.log"
    estado = json.loads(config.ARCHIVO_ESTADO.read_text())
    assert estado["ultima_preapertura"] == "2026-07-24"
    assert "preapertura" in estado["ultima_ejecucion"], \
        "falta el sello de prueba de vida en estado.json"
    assert est_mod.cargar()["actualizado"] is not None


def test_postcierre_forzado_siempre_escribe_en_disco(repo_temporal, monkeypatch):
    """Sin entradas pendientes ni posiciones abiertas: igual tiene que escribir."""
    import escaneo_postcierre as pc
    from centinela import runtime, ath as ath_mod

    monkeypatch.setattr(runtime, "preparar_datos",
                        lambda *a, **k: (_precios_falsos(), {}, {}))
    monkeypatch.setattr(ath_mod, "actualizar_ath", lambda *a, **k: None)

    antes = _huella(repo_temporal)
    monkeypatch.setattr(sys, "argv", ["escaneo_postcierre.py", "--forzar",
                                      "--fecha", "2026-07-24"])
    assert pc.main() == resultados.PROCESADO
    despues = _huella(repo_temporal)

    assert despues != antes, ("el post-cierre forzado no dejó NINGÚN cambio en "
                              "disco: eso es exactamente el fallo silencioso")
    log = config.LOGS_DIR / "decisiones-2026-07-24.log"
    assert log.exists() and log.stat().st_size > 0
    estado = json.loads(config.ARCHIVO_ESTADO.read_text())
    assert estado["ultima_postcierre"] == "2026-07-24"
    assert "postcierre" in estado["ultima_ejecucion"]


# --------------------------------------------------------------------------- #
# 2. Perder la ventana es ROJO, no verde
# --------------------------------------------------------------------------- #
def test_ventana_perdida_es_fallo_no_omitido(repo_temporal, monkeypatch):
    """Reproduce el 2026-07-27: arrancar pasada la apertura sin haber procesado.

    Antes esto devolvía 'omitido:fuera-de-ventana' y el workflow salía verde.
    """
    import escaneo_preapertura as ep

    args = type("A", (), {"forzar": False, "fecha": None})()
    tarde = datetime(2026, 7, 27, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    salida = ep._control_de_ventana(args, tarde, tarde.date(), "2026-07-27")

    assert salida == resultados.FALLO_VENTANA_PERDIDA
    assert resultados.es_fallo(salida), "perder la sesión tiene que verse ROJO"
    assert not resultados.es_legitimo_sin_commit(salida)


def test_llegar_pronto_es_legitimo_y_no_pierde_el_dia(repo_temporal, monkeypatch):
    """Llegar antes de la ventana es inofensivo: se espera o se cede el turno."""
    import escaneo_preapertura as ep

    args = type("A", (), {"forzar": False, "fecha": None})()
    # Muy por delante: más que el tope de espera, así que cede el turno.
    pronto = datetime(2026, 7, 27, 1, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(ep.calendario, "es_dia_de_mercado", lambda *a, **k: True)
    salida = ep._control_de_ventana(args, pronto, pronto.date(), "2026-07-27")

    assert salida == resultados.OMITIDO_ANTES_DE_VENTANA
    assert resultados.es_legitimo_sin_commit(salida)
    assert not resultados.es_fallo(salida)


# --------------------------------------------------------------------------- #
# 3. El vocabulario de resultados no puede desincronizarse
# --------------------------------------------------------------------------- #
def test_ningun_fallo_cuenta_como_legitimo():
    """La regla que se rompió el 2026-07-27: 'omitido:' no puede ser un cheque
    en blanco. Ningún resultado de fallo puede colarse como legítimo."""
    for r in (resultados.FALLO_VENTANA_PERDIDA, resultados.FALLO_SESION_PENDIENTE):
        assert not resultados.es_legitimo_sin_commit(r)
        assert resultados.es_fallo(r)
        assert r in resultados.MOTIVOS_FALLO, \
            f"{r} no explica por qué falló; un rojo sin explicación no sirve"
    assert not resultados.es_fallo(resultados.PROCESADO)


def test_resultado_desconocido_no_pasa_por_legitimo():
    """Un motivo nuevo inventado sobre la marcha NO hereda el permiso de terminar
    sin commit: hay que añadirlo al vocabulario a conciencia."""
    assert not resultados.es_legitimo_sin_commit("omitido:motivo-inventado")
    assert not resultados.es_legitimo_sin_commit("fuera-de-ventana")


def test_scripts_de_shell_no_silencian_errores():
    """Prohibido `|| true`, `2>/dev/null`, `set +e` y `continue-on-error`.

    Un fallo escondido devuelve el sistema al punto de partida. La única
    excepción tolerada está marcada y justificada en commit_y_push.sh, donde los
    `|| true` del bucle de reintentos existen para que el bucle llegue a su
    mensaje de error en vez de morir con un `fatal:` de git.
    """
    sospechosos = (r"\|\|\s*true", r"2>\s*/dev/null", r"set \+e", r"continue-on-error")
    permitidos = {"scripts/commit_y_push.sh"}

    for ruta in list(RAIZ.glob(".github/workflows/*.yml")) + list(RAIZ.glob("scripts/*.sh")):
        rel = str(ruta.relative_to(RAIZ))
        if rel in permitidos:
            continue
        texto = ruta.read_text()
        for patron in sospechosos:
            assert not re.search(patron, texto), \
                f"{rel} silencia errores con `{patron}`: un fallo oculto es peor que uno rojo"


def test_bash_y_python_comparten_el_mismo_vocabulario():
    """commit_y_push.sh lee la lista de legítimos de resultados.py, no la copia.

    Si alguien la duplicara, las dos mitades acabarían divergiendo y volvería el
    agujero: bash dando por bueno lo que Python considera un fallo.
    """
    texto = (RAIZ / "scripts" / "commit_y_push.sh").read_text()
    assert "resultados.LEGITIMOS_SIN_COMMIT" in texto
    for r in resultados.LEGITIMOS_SIN_COMMIT:
        assert f'"{r}"' not in texto, \
            f"{r} está escrito a mano en commit_y_push.sh; debe leerse de resultados.py"


def test_los_workflows_de_escaneo_verifican_la_persistencia():
    """Los dos escaneos tienen que tener su job de verificación independiente."""
    for nombre in ("preapertura.yml", "postcierre.yml"):
        texto = (RAIZ / ".github/workflows" / nombre).read_text()
        assert "verificar_persistencia.sh" in texto, \
            f"{nombre} no verifica contra el remoto que su trabajo se guardó"
        assert "permissions:" in texto and "contents: write" in texto
        assert "commit_y_push.sh" in texto


# --------------------------------------------------------------------------- #
# 4. El vigilante detecta el silencio
# --------------------------------------------------------------------------- #
def test_vigilante_detecta_una_sesion_saltada(repo_temporal, monkeypatch):
    """Si estado.json se quedó atrás, el vigilante tiene que gritar."""
    import vigilante

    config.ARCHIVO_ESTADO.write_text(json.dumps({
        "ultima_preapertura": "2026-07-20",
        "ultima_postcierre": "2026-07-20",
    }))
    monkeypatch.setattr(vigilante, "_commits_del_bot", lambda horas: [])
    monkeypatch.setattr(vigilante.calendario, "ultima_sesion_exigible",
                        lambda *a, **k: pd.Timestamp("2026-07-24"))

    problemas = vigilante.revisar(1)
    assert problemas, "el vigilante no vio que faltaban sesiones enteras"
    assert any("pre-apertura de 2026-07-24" in p for p in problemas)
    assert any("post-cierre de 2026-07-24" in p for p in problemas)


def test_vigilante_calla_cuando_todo_esta_al_dia(repo_temporal, monkeypatch):
    import vigilante

    config.ARCHIVO_ESTADO.write_text(json.dumps({
        "ultima_preapertura": "2026-07-24",
        "ultima_postcierre": "2026-07-24",
    }))
    from centinela import bitacora
    (config.LOGS_DIR / "decisiones-2026-07-24.log").write_text(
        bitacora.CABECERA_ESCANEO.format(escaneo="preapertura", marca="x") + "\nok\n"
        + bitacora.CABECERA_ESCANEO.format(escaneo="postcierre", marca="x") + "\nok\n")
    monkeypatch.setattr(vigilante, "_commits_del_bot",
                        lambda horas: ["abc1234 2026-07-24 post-cierre"])
    monkeypatch.setattr(vigilante.calendario, "ultima_sesion_exigible",
                        lambda *a, **k: pd.Timestamp("2026-07-24"))

    assert vigilante.revisar(1) == []


def test_vigilante_detecta_un_escaneo_que_falto_en_una_sesion(repo_temporal, monkeypatch):
    """El caso exacto del 2026-07-27: se pierde la pre-apertura pero el
    post-cierre sí corre, así que el día "parece" completo.

    Sin desglose por escaneo, al día siguiente el estado ya habría pasado de
    largo y la sesión mutilada quedaría impune.
    """
    import vigilante
    from centinela import bitacora

    config.ARCHIVO_ESTADO.write_text(json.dumps({
        "ultima_preapertura": "2026-07-24",     # se quedó atrás: se perdió la ventana
        "ultima_postcierre": "2026-07-27",
    }))
    (config.LOGS_DIR / "decisiones-2026-07-27.log").write_text(
        bitacora.CABECERA_ESCANEO.format(escaneo="postcierre", marca="x") + "\nok\n")
    monkeypatch.setattr(vigilante, "_commits_del_bot",
                        lambda horas: ["abc1234 2026-07-27 post-cierre"])
    monkeypatch.setattr(vigilante.calendario, "ultima_sesion_exigible",
                        lambda *a, **k: pd.Timestamp("2026-07-27"))

    problemas = vigilante.revisar(1)
    assert any("preapertura" in p and "2026-07-27" in p for p in problemas), \
        f"el vigilante no vio la pre-apertura que faltaba: {problemas}"
