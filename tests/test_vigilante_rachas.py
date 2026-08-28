"""Tests de la detección de rachas de runs rojos del vigilante.

Regresión de la emergencia del 2026-08-27: siete "Escaneo pre-apertura" rojos
seguidos en una tarde y el vigilante de esa noche en verde, porque las sesiones
que él exigía (las del día anterior) sí estaban hechas. La racha es una avería
en curso; el hueco, una avería ya consumada. El vigilante tiene que ver las dos.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from centinela import config
import vigilante


AHORA = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)


def _run(id_, minutos_atras, conclusion):
    """Un run tal y como lo devuelve la API, del más reciente al más antiguo."""
    return {
        "id": id_,
        "conclusion": conclusion,
        "created_at": (AHORA - timedelta(minutes=minutos_atras)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }


# Los siete runs reales de aquella tarde, del más reciente al más antiguo, con
# sus IDs y sus horas de creación (UTC) de verdad.
RUNS_2026_08_27 = [
    _run(33124245743, 67, "failure"),    # 22:53 UTC
    _run(33121657602, 106, "failure"),   # 22:14 UTC
    _run(33116319865, 177, "failure"),   # 21:03 UTC
    _run(33114423249, 201, "failure"),   # 20:39 UTC
    _run(33111130470, 241, "failure"),   # 19:59 UTC
    _run(33107633605, 284, "failure"),   # 19:16 UTC
    _run(33106300280, 300, "failure"),   # 19:00 UTC
    _run(32977319252, 610, "success"),   # 13:57 UTC del 26: aquí el sistema iba bien
]


def test_detecta_la_racha_del_2026_08_27():
    racha = vigilante.racha_de_rojos(RUNS_2026_08_27, AHORA)
    assert racha is not None, "los siete rojos del 27-ago tienen que dar racha"
    assert racha["cuantos"] == 7
    # La racha empieza en el MÁS ANTIGUO de los rojos, no en el más reciente.
    assert racha["desde"] == AHORA - timedelta(minutes=300)
    assert racha["ids"][0] == 33124245743
    assert racha["ids"][-1] == 33106300280
    # Y el verde anterior no entra en la racha.
    assert 32977319252 not in racha["ids"]


def test_el_verde_corta_la_racha():
    """Un éxito por medio significa que el sistema volvió a funcionar."""
    runs = [_run(3, 10, "failure"), _run(2, 20, "failure"),
            _run(1, 30, "success"), _run(0, 40, "failure")]
    assert vigilante.racha_de_rojos(runs, AHORA) is None


def test_cancelados_y_en_curso_no_cortan_la_racha():
    """Con el grupo de concurrencia de este repo los cancelados son rutina.

    No son un fallo, pero tampoco prueban que nada funcione: si los tratáramos
    como un verde, un cancelado por concurrencia entre dos rojos escondería la
    racha justo el día que importa.
    """
    runs = [_run(4, 10, "failure"), _run(3, 20, None),        # en curso
            _run(2, 30, "cancelled"), _run(1, 40, "failure"),
            _run(0, 50, "failure")]
    racha = vigilante.racha_de_rojos(runs, AHORA)
    assert racha is not None and racha["cuantos"] == 3
    assert racha["ids"] == [4, 1, 0]


def test_por_debajo_del_minimo_no_es_emergencia():
    """Dos rojos son un fallo; tres son una racha. El umbral es el que manda."""
    assert config.VIGILANTE_RACHA_MINIMA == 3
    runs = [_run(1, 10, "failure"), _run(0, 20, "failure")]
    assert vigilante.racha_de_rojos(runs, AHORA) is None
    assert vigilante.racha_de_rojos([_run(2, 5, "failure")] + runs, AHORA) is not None


def test_los_rojos_viejos_no_cuentan():
    """La ventana es de 24 h: una racha de la semana pasada no es una avería."""
    viejos = [_run(i, config.VIGILANTE_RACHA_HORAS * 60 + 10 * (i + 1), "failure")
              for i in range(5)]
    assert vigilante.racha_de_rojos(viejos, AHORA) is None


def test_timed_out_cuenta_como_rojo():
    """El fallo del 2026-08-06 fue un timeout por falta de runner, no un verde."""
    runs = [_run(2, 10, "timed_out"), _run(1, 20, "failure"),
            _run(0, 30, "timed_out")]
    racha = vigilante.racha_de_rojos(runs, AHORA)
    assert racha is not None and racha["cuantos"] == 3


def test_sin_token_no_revisa_rachas_pero_lo_dice(monkeypatch, capsys):
    """Fuera de Actions la comprobación no aplica; eso se anuncia, no se calla."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert vigilante.revisar_rachas() == []
    assert "no se revisan rachas" in capsys.readouterr().out


def test_api_caida_sale_en_rojo_no_en_verde(monkeypatch):
    """Un vigilante ciego que dice 'todo bien' es el silencio verde de siempre."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "sebas1331/centinela-sp500")

    def _explota(ruta, token, repo):
        raise RuntimeError("la API de Actions no respondió tras 3 intentos: 503")

    monkeypatch.setattr(vigilante, "_api_actions", _explota)
    problemas = vigilante.revisar_rachas()
    assert len(problemas) == len(vigilante.WORKFLOWS_VIGILADOS)
    assert all("No se pudo revisar la racha" in p for p in problemas)


def test_el_mensaje_de_emergencia_nombra_cuantos_desde_cuando_y_los_runs(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "sebas1331/centinela-sp500")

    def _fake(ruta, token, repo):
        if "preapertura" in ruta:
            return {"workflow_runs": RUNS_2026_08_27}
        return {"workflow_runs": [_run(99, 5, "success")]}

    monkeypatch.setattr(vigilante, "_api_actions", _fake)
    problemas = vigilante.revisar_rachas(AHORA)
    assert len(problemas) == 1, "solo la pre-apertura estaba en racha"
    msg = problemas[0]
    assert msg.startswith("EMERGENCIA: 7 pre-aperturas rojas seguidas desde ")
    assert "19:00 UTC" in msg          # inicio real de la racha
    assert "33106300280" in msg and "33124245743" in msg


# ---------------------------------------------------------------------------
# El log que se tapa a sí mismo
# ---------------------------------------------------------------------------
def test_documentar_un_hueco_no_lo_tapa():
    """La nota de una sesión perdida no puede citar la cabecera de escaneo.

    Al documentar la pre-apertura perdida del 2026-08-27 se escribió en el log
    del día una frase que CITABA literalmente la cabecera que busca el vigilante
    ("no lleva la cabecera <...>"). El vigilante no lee prosa: busca esa cadena
    en el fichero, la encontró dentro de la explicación y dio la pre-apertura por
    corrida. Documentar el hueco casi lo tapó.

    Este test fija la invariante para cualquier sesión: si el log de un día no
    tiene bloque real de un escaneo, su marca no puede aparecer en el fichero ni
    de pasada.
    """
    for log in sorted(vigilante.config.LOGS_DIR.glob("decisiones-*.log")):
        texto = log.read_text(encoding="utf-8")
        for escaneo in vigilante.ESCANEOS:
            marca = vigilante._marca(escaneo)
            if marca not in texto:
                continue
            # Si la marca está, tiene que ser una CABECERA de verdad: al
            # principio de su línea, no citada dentro de un párrafo.
            assert any(ln.lstrip().startswith(marca) for ln in texto.splitlines()), (
                f"{log.name} menciona la marca de '{escaneo}' sin que sea una "
                f"cabecera real: el vigilante daría ese escaneo por corrido")


def test_el_log_del_2026_08_27_deja_ver_el_hueco():
    """Regresión anclada al día de la emergencia."""
    log = vigilante.config.LOGS_DIR / "decisiones-2026-08-27.log"
    texto = log.read_text(encoding="utf-8")
    assert vigilante._marca("postcierre") in texto, "el post-cierre sí se recuperó"
    assert vigilante._marca("preapertura") not in texto, (
        "la pre-apertura del 27-ago se perdió: su marca no puede estar en el log, "
        "o el vigilante dejaría de denunciar el hueco")
