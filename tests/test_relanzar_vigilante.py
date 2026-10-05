"""Si el vigilante se cae con posiciones abiertas, se levanta solo y se ve.

Tres capas, de la más rápida a la más lenta:
  1. el supervisor, en el mismo job: proceso muerto o colgado -> segundos;
  2. el guardián, en otro runner: runner muerto -> rescate a los 11 min;
  3. la escalera del pre-apertura: llama al vigilante en cada peldaño, y
     `hace_falta` decide con el latido y con las posiciones publicadas.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, latido as lat, salud  # noqa: E402
import guardian_vigilante as gv  # noqa: E402
import hace_falta_vigilante as hf  # noqa: E402
import supervisor_vigilante as sv  # noqa: E402

LUNES = "2026-10-05"


def _et(h, m, dia=LUNES):
    return datetime.fromisoformat(f"{dia}T{h:02d}:{m:02d}:00").replace(tzinfo=config.TZ_ET)


# --------------------------------------------------------------------------- #
# 1. El supervisor
# --------------------------------------------------------------------------- #
@pytest.fixture
def rapido(monkeypatch):
    monkeypatch.setattr(sv, "MIRAR_CADA_SEG", 0.05)
    monkeypatch.setattr(sv, "COLGADO_MIN", 0.02)          # 1,2 s


def test_un_vigilante_que_muere_se_relanza_y_el_job_acaba_en_rojo(tmp_path, rapido):
    contador = tmp_path / "n"
    # Muere la primera vez; la segunda termina bien (cerró la sesión).
    hijo = (f"import pathlib,sys; f=pathlib.Path({str(contador)!r}); "
            f"n=int(f.read_text()) if f.exists() else 0; f.write_text(str(n+1)); "
            f"sys.exit(1 if n == 0 else 0)")
    rc = sv.supervisar([sys.executable, "-c", hijo], tmp_path / "lat",
                       dormir=lambda _s: None)
    assert contador.read_text() == "2", "no se relanzó"
    assert rc == 1, "un vigilante que se cae es un fallo aunque se levante solo"
    r = salud.cargar()["runs"]["vigilante_precios"]
    assert r["resultado"] == "fallo:1-reinicio(s)" and "código 1" in r["detalle"]


def test_un_vigilante_COLGADO_se_mata_y_se_relanza(tmp_path, rapido):
    contador = tmp_path / "n"
    # La primera vez se queda vivo sin latir; la segunda termina bien.
    hijo = (f"import pathlib,sys,time; f=pathlib.Path({str(contador)!r}); "
            f"n=int(f.read_text()) if f.exists() else 0; f.write_text(str(n+1)); "
            f"time.sleep(60) if n == 0 else sys.exit(0)")
    import time
    rc = sv.supervisar([sys.executable, "-c", hijo], tmp_path / "lat",
                       dormir=time.sleep)
    assert contador.read_text() == "2"
    assert rc == 1
    assert "colgado" in salud.cargar()["runs"]["vigilante_precios"]["detalle"]


def test_un_final_limpio_no_se_relanza(tmp_path, rapido):
    rc = sv.supervisar([sys.executable, "-c", "pass"], tmp_path / "lat",
                       dormir=lambda _s: None)
    assert rc == 0
    assert "vigilante_precios" not in salud.cargar().get("runs", {})


def test_un_hijo_que_late_no_esta_colgado(tmp_path):
    trabajo = tmp_path / "lat"
    lat.tocar(trabajo)
    import time
    ahora = time.time()
    assert not sv.colgado(trabajo, arranque_hijo=ahora - 3600, ahora=ahora)
    # Un hijo recién arrancado no se juzga por el latido viejo de su predecesor.
    assert not sv.colgado(tmp_path / "nada", arranque_hijo=ahora - 60, ahora=ahora)
    assert sv.colgado(tmp_path / "nada", arranque_hijo=ahora - 3600, ahora=ahora)


# --------------------------------------------------------------------------- #
# 2. El guardián
# --------------------------------------------------------------------------- #
def _latido(run, cuando, estado=lat.VIVO, arrancado=None, motivo=None):
    return {"run": run, "cuando": cuando.isoformat(), "estado": estado,
            "arrancado": (arrancado or cuando).isoformat(), "motivo": motivo}


ARRANQUE = _et(9, 40)


def test_el_guardian_rescata_si_su_vigilante_deja_de_latir():
    l = _latido("1", _et(10, 0), arrancado=_et(9, 45))
    assert gv.decidir(l, "1", ARRANQUE, _et(10, 5))[0] == gv.SEGUIR
    accion, motivo = gv.decidir(l, "1", ARRANQUE, _et(10, 12))
    assert accion == gv.RESCATAR and "12 min" in motivo


def test_el_umbral_deja_retirarse_antes_al_vigilante_que_no_puede_publicar():
    """Nunca dos vigilando a la vez: uno vivo que no puede publicar se retira a
    los 10 min; el guardián no rescata hasta pasados esos 10."""
    assert gv.RESCATE_MIN > lat.MUERTO_MINUTOS


def test_el_guardian_se_va_si_su_vigilante_termino_bien():
    l = _latido("1", _et(11, 0), estado=lat.EN_REPOSO, motivo="nada que vigilar")
    assert gv.decidir(l, "1", ARRANQUE, _et(12, 0))[0] == gv.SALIR


def test_el_guardian_se_va_cuando_cierra_la_sesion():
    l = _latido("1", _et(15, 30))
    assert gv.decidir(l, "1", ARRANQUE, _et(16, 1))[0] == gv.SALIR


def test_en_un_relevo_el_guardian_del_sucesor_NO_se_va_por_el_saliente():
    """El saliente sigue latiendo mientras espera: no es "otro más nuevo"."""
    sucesor_desde = _et(15, 15)
    saliente = _latido("1", _et(15, 17), estado=lat.ESPERANDO_RELEVO,
                       arrancado=_et(9, 31))
    assert gv.decidir(saliente, "2", sucesor_desde, _et(15, 18))[0] == gv.SEGUIR


def test_el_guardian_del_saliente_se_va_cuando_late_el_sucesor():
    sucesor = _latido("2", _et(15, 20), arrancado=_et(15, 19))
    assert gv.decidir(sucesor, "1", _et(9, 30), _et(15, 21))[0] == gv.SALIR


def test_si_su_vigilante_no_llega_a_latir_nunca_rescata_a_los_30_min():
    viejo = _latido("0", _et(15, 59, "2026-10-02"), estado=lat.EN_REPOSO,
                    motivo="la sesión cerró", arrancado=_et(9, 31, "2026-10-02"))
    assert gv.decidir(viejo, "1", ARRANQUE, _et(10, 0))[0] == gv.SEGUIR
    assert gv.decidir(viejo, "1", ARRANQUE, _et(10, 11))[0] == gv.RESCATAR


def test_antes_de_abrir_el_guardian_espera():
    assert gv.decidir(None, "1", _et(9, 0), _et(9, 20))[0] == gv.SEGUIR


# --------------------------------------------------------------------------- #
# 3. La escalera: hace_falta decide con el latido y con las posiciones
# --------------------------------------------------------------------------- #
def test_un_latido_muerto_con_la_sesion_abierta_hace_arrancar():
    l = _latido("1", _et(10, 0))
    assert hf.decidir(l, _et(10, 30), forzado=False, posiciones_xtb=1)[0]


def test_en_reposo_y_sin_posiciones_no_se_arranca_nada():
    l = _latido("1", _et(9, 40), estado=lat.EN_REPOSO, motivo="nada que vigilar")
    arrancar, motivo = hf.decidir(l, _et(11, 0), forzado=False, posiciones_xtb=0)
    assert not arrancar and "no tiene posiciones" in motivo


def test_en_reposo_pero_con_posiciones_SI_se_arranca():
    """05/10: el vigilante de las 09:56 se fue en reposo y a las 09:58 se compró
    WDC. Con posiciones en XTB, un reposo anterior no es razón para no vigilar."""
    l = _latido("1", _et(9, 56), estado=lat.EN_REPOSO, motivo="nada que vigilar")
    assert hf.decidir(l, _et(10, 0), forzado=False, posiciones_xtb=1)[0]


def test_mucho_antes_de_abrir_no_se_arranca():
    arrancar, motivo = hf.decidir(None, _et(6, 0), forzado=False, posiciones_xtb=1)
    assert not arrancar and "apertura" in motivo


def test_el_rescate_fuerza_el_arranque(monkeypatch):
    monkeypatch.setenv("RESCATE_DE", "123")
    monkeypatch.setattr(hf.lat, "leer", lambda _t: _latido("123", _et(10, 0)))
    salida = []
    monkeypatch.setattr(hf, "_salida", lambda a, m: salida.append((a, m)) or 0)
    hf.main()
    assert salida[0][0] is True


def test_las_tres_capas_estan_cableadas():
    import yaml
    wf = yaml.safe_load((RAIZ / ".github/workflows/vigilante_precios.yml").read_text())
    pasos = " ".join(str(p.get("run", "")) for p in wf["jobs"]["vigilar"]["steps"])
    assert "supervisor_vigilante.py" in pasos
    assert "guardian_vigilante.py" in " ".join(
        str(p.get("run", "")) for p in wf["jobs"]["guardian"]["steps"])
    assert "rescate" in str(wf["concurrency"]["group"])
    pre = yaml.safe_load((RAIZ / ".github/workflows/preapertura.yml").read_text())
    assert "skipped" in pre["jobs"]["arrancar_vigilante"]["if"]


def test_ningun_workflow_instala_chromium_con_apt_directamente():
    """05/10: `--with-deps` tardó 14 min por un mirror de apt atascado. Todos
    los workflows pasan por la acción, que cachea y solo usa apt si hace falta."""
    import yaml
    for f in (RAIZ / ".github" / "workflows").glob("*.yml"):
        texto = f.read_text(encoding="utf-8")
        assert "--with-deps" not in texto, f.name
        for job in (yaml.safe_load(texto).get("jobs") or {}).values():
            for paso in job.get("steps", []):
                if "Chromium" in str(paso.get("name", "")):
                    assert paso.get("uses") == "./.github/actions/chromium", f.name
    accion = (RAIZ / ".github/actions/chromium/action.yml").read_text(encoding="utf-8")
    assert "~/.cache/ms-playwright" in accion and "probar_chromium.py" in accion


def test_la_auditoria_no_da_por_sobrante_una_compra_registrada_de_hoy(monkeypatch):
    """05/10: marcó WDC (entrada tardía de las 09:58) como «NO DEBERÍA ESTAR»;
    con --cerrar la habría vendido."""
    import auditar_cuenta_xtb as au
    from centinela import broker_xtb as bx, estado as est_mod, ordenes as ords
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    monkeypatch.setattr(est_mod, "cargar", lambda: {"posiciones": {"A": []}})
    assert "WDC.US" not in au.esperadas()
    ords.registrar_ejecucion(
        ords.Orden(id="2026-10-05|A|WDC|entrada_tardia", tipo=ords.ENTRADA_TARDIA,
                   cartera="A", ticker="WDC", acciones=4, sesion=LUNES),
        bx.Ejecucion(ticker="WDC.US", lado="compra", acciones=4,
                     estado="ejecutada", precio=439.71))
    assert "WDC.US" in au.esperadas()
