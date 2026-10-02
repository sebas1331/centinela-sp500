"""Un incidente resuelto deja de gritar, pero no desaparece.

Los rechazos del 30/09 y del 02/10 tienen causa identificada y arreglo
publicado. Seguir pintándolos de rojo durante una semana no informa de nada:
entrena a mirar el panel sin leerlo. Pero borrarlos sería peor. El punto medio
es declararlos, con su commit, y bajarlos a dato histórico.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, fiabilidad, incidentes, ordenes as ords  # noqa: E402
import generar_operativa as go  # noqa: E402


@pytest.fixture
def registro(tmp_path, monkeypatch):
    ruta = tmp_path / "incidentes.json"
    monkeypatch.setattr(incidentes, "ARCHIVO", ruta)
    return ruta


def _incidente(**extra):
    base = {"fecha": "2026-10-02", "titulo": "Órdenes rechazadas por los niveles",
            "causa": "XTB no acepta stop ni take profit en acciones al contado",
            "commit": "6f538dc", "ordenes": ["2026-10-02|A|CTVA|compra"]}
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# 1. Qué es un incidente resuelto
# --------------------------------------------------------------------------- #
def test_sin_commit_del_arreglo_no_es_un_incidente_resuelto(registro):
    """Es uno que alguien prefiere no mirar."""
    with pytest.raises(ValueError, match="commit"):
        incidentes.guardar([_incidente(commit="")], ruta=registro)


def test_sin_causa_tampoco(registro):
    with pytest.raises(ValueError, match="causa"):
        incidentes.guardar([_incidente(causa="")], ruta=registro)


def test_sin_las_ordenes_afectadas_tampoco(registro):
    """Sin la lista no se puede distinguir el fallo viejo del nuevo."""
    with pytest.raises(ValueError, match="ordenes"):
        incidentes.guardar([_incidente(ordenes=[])], ruta=registro)


def test_un_incidente_bien_formado_se_guarda_y_se_lee(registro):
    incidentes.guardar([_incidente()], ruta=registro)
    leidos = incidentes.cargar(ruta=registro)
    assert len(leidos) == 1 and leidos[0]["commit"] == "6f538dc"
    assert incidentes.ordenes_resueltas(ruta=registro) == {"2026-10-02|A|CTVA|compra"}


# --------------------------------------------------------------------------- #
# 2. El semáforo
# --------------------------------------------------------------------------- #
def _sem(ordenes, ahora):
    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    return go.semaforo(salud_ok, None, [], ordenes, ahora)


def test_un_rechazo_YA_RESUELTO_no_pinta_rojo(registro, monkeypatch):
    incidentes.guardar([_incidente()], ruta=registro)
    ahora = datetime(2026, 10, 2, 18, 0, tzinfo=config.TZ_ET)
    orden = {"tipo": ords.COMPRA, "tipo_nombre": "Compra", "ticker": "CTVA",
             "sesion": "2026-10-02", "estado": "rechazada", "error": "x",
             "id_interno": "2026-10-02|A|CTVA|compra"}
    assert _sem([orden], ahora)["color"] == "verde"


def test_pero_un_rechazo_NUEVO_sigue_siendo_rojo(registro):
    """Con cualquier causa no registrada. Las órdenes van listadas una a una,
    así que si la causa vuelve, vuelve el rojo."""
    incidentes.guardar([_incidente()], ruta=registro)
    ahora = datetime(2026, 10, 2, 18, 0, tzinfo=config.TZ_ET)
    orden = {"tipo": ords.COMPRA, "tipo_nombre": "Compra", "ticker": "OTRA",
             "sesion": "2026-10-02", "estado": "rechazada", "error": "sin fondos",
             "id_interno": "2026-10-02|A|OTRA|compra"}
    s = _sem([orden], ahora)
    assert s["color"] == "rojo"
    assert any("OTRA" in m for m in s["motivos"])


def test_el_mismo_ticker_otro_dia_tambien_es_rojo(registro):
    """Declarar un incidente no es declarar inmune a un ticker."""
    incidentes.guardar([_incidente()], ruta=registro)
    ahora = datetime(2026, 10, 5, 18, 0, tzinfo=config.TZ_ET)
    orden = {"tipo": ords.COMPRA, "tipo_nombre": "Compra", "ticker": "CTVA",
             "sesion": "2026-10-05", "estado": "rechazada", "error": "x",
             "id_interno": "2026-10-05|A|CTVA|compra"}
    assert _sem([orden], ahora)["color"] == "rojo"


# --------------------------------------------------------------------------- #
# 3. La fiabilidad
# --------------------------------------------------------------------------- #
def test_las_ordenes_resueltas_no_cuentan_para_la_fiabilidad(registro, tmp_path,
                                                             monkeypatch):
    """Arrastrar un fallo ya corregido hace que el porcentaje conteste a una
    pregunta vieja: "¿se podía fiar uno la semana pasada?" en vez de "¿puedo
    fiarme hoy?"."""
    diario = tmp_path / "fiabilidad.csv"
    monkeypatch.setattr(fiabilidad, "ARCHIVO", diario)
    incidentes.guardar([_incidente()], ruta=registro)

    fiabilidad.anotar(fiabilidad.RECHAZADA, "CTVA.US", ords.COMPRA, "x",
                      ruta=diario, id_orden="2026-10-02|A|CTVA|compra")
    fiabilidad.anotar(fiabilidad.EJECUTADA, "CTVA.US", ords.ENTRADA_TARDIA, "ok",
                      ruta=diario, id_orden="2026-10-02|A|CTVA|entrada_tardia")

    r = fiabilidad.resumen(7, ruta=diario)
    assert r["enviadas"] == 1 and r["ejecutadas"] == 1 and r["rechazadas"] == 0
    assert r["fiabilidad_pct"] == 100.0


def test_la_fiabilidad_se_muestra_desde_el_ultimo_arreglo(tmp_path, monkeypatch):
    diario = tmp_path / "fiabilidad.csv"
    monkeypatch.setattr(fiabilidad, "ARCHIVO", diario)
    ahora = datetime(2026, 10, 8, 12, 0, tzinfo=config.TZ_ET)
    fiabilidad.anotar(fiabilidad.EJECUTADA, "X.US", ords.COMPRA, "ok", ruta=diario)

    r = fiabilidad.resumen(7, ruta=diario, ahora=ahora, desde="2026-10-05")
    assert r["desde"] == "2026-10-05" and r["acotada_por_arreglo"] is True

    # Y si el arreglo es más viejo que la ventana, manda la ventana.
    r2 = fiabilidad.resumen(7, ruta=diario, ahora=ahora, desde="2026-01-01")
    assert r2["desde"] == "2026-10-01" and r2["acotada_por_arreglo"] is False


def test_los_incidentes_viajan_a_la_pagina(registro):
    incidentes.guardar([_incidente()], ruta=registro)
    ahora = datetime(2026, 10, 2, 18, 0, tzinfo=config.TZ_ET)
    assert len(incidentes.cargar(ruta=registro)) == 1


# --------------------------------------------------------------------------- #
# 4. Los dos incidentes de verdad, los del repositorio
# --------------------------------------------------------------------------- #
def test_los_incidentes_registrados_estan_completos():
    """Los del 30/09 y el 02/10, con su commit de arreglo."""
    reales = incidentes.cargar()
    assert len(reales) >= 2
    for i in reales:
        for campo in incidentes.OBLIGATORIOS:
            assert i.get(campo), f"{i.get('titulo')} sin {campo}"
        assert len(i["commit"]) >= 7, "el commit tiene que ser identificable"

    ordenes = incidentes.ordenes_resueltas()
    assert "2026-09-30|A|MRNA|compra" in ordenes
    assert "2026-09-30|A|FICO|compra" in ordenes
    assert "2026-10-02|A|CTVA|compra" in ordenes


# --------------------------------------------------------------------------- #
# 5. La reconciliación durante el día
# --------------------------------------------------------------------------- #
def test_lo_comprado_hoy_no_es_una_diferencia_hasta_el_post_cierre(
        tmp_path, monkeypatch):
    """El simulador mueve las entradas de `entradas_pendientes` a `posiciones`
    en el post-cierre. Entre la compra de la mañana y el cierre, la posición
    existe en XTB y no en el estado — y eso no es una discrepancia, es el mismo
    día funcionando como debe. Sin esto, la verificación de la apertura daba un
    rojo falso todos los días que se compra.
    """
    import csv
    import ejecutor_xtb as ej
    from centinela import estado as est_mod

    hoy = "2026-10-02"
    monkeypatch.setattr(ej, "hoy_iso", lambda: hoy)
    monkeypatch.setattr(est_mod, "cargar",
                        lambda: {"posiciones": {config.CARTERA_BROKER: []}})
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "b.csv")

    fila = {c: "" for c in ords.COLUMNAS_BROKER}
    fila.update({"id": f"{hoy}|A|CTVA|entrada_tardia", "sesion": hoy,
                 "cartera": "A", "ticker": "CTVA", "simbolo_xtb": "CTVA.US",
                 "tipo": ords.ENTRADA_TARDIA, "acciones": 134,
                 "estado": "ejecutada"})
    with open(ords.ARCHIVO_BITACORA_BROKER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ords.COLUMNAS_BROKER)
        w.writeheader(); w.writerow(fila)

    class ConCTVA:
        def posiciones(self):
            return [{"ticker": "CTVA.US", "acciones": 134.0, "lado": "buy",
                     "precio_entrada": 12.71, "precio_actual": 12.9,
                     "orden": 1, "pnl": 0.0}]

    assert ej.reconciliar(ConCTVA()) == []


def test_pero_una_posicion_que_nadie_compro_hoy_SI_lo_es(tmp_path, monkeypatch):
    """Dinero expuesto que el sistema no sigue: eso nunca se excusa."""
    import ejecutor_xtb as ej
    from centinela import estado as est_mod

    monkeypatch.setattr(ej, "hoy_iso", lambda: "2026-10-02")
    monkeypatch.setattr(est_mod, "cargar",
                        lambda: {"posiciones": {config.CARTERA_BROKER: []}})
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "vacia.csv")

    class ConAjena:
        def posiciones(self):
            return [{"ticker": "AJENA.US", "acciones": 5.0, "lado": "buy",
                     "precio_entrada": 10.0, "precio_actual": 10.0,
                     "orden": 2, "pnl": 0.0}]

    problemas = ej.reconciliar(ConAjena())
    assert len(problemas) == 1 and "AJENA" in problemas[0]


# --------------------------------------------------------------------------- #
# 6. La excusa tiene principio y fin
# --------------------------------------------------------------------------- #
def test_la_excusa_cubre_del_incidente_al_arreglo(registro):
    """Un fallo así no se queda en un día: la reconciliación del 30/09 siguió
    dando diferencias cada mañana hasta que se arregló el 02/10."""
    incidentes.guardar([_incidente(fecha="2026-09-30", arreglado="2026-10-02",
                                   componentes=["reconcilia"])], ruta=registro)
    for dia in ("2026-09-30", "2026-10-01", "2026-10-02"):
        assert incidentes.excusa_componente("reconcilia", dia, ruta=registro), dia


def test_pero_no_cubre_ni_antes_ni_despues(registro):
    """Declarar un incidente no vuelve inmune a un componente."""
    incidentes.guardar([_incidente(fecha="2026-09-30", arreglado="2026-10-02",
                                   componentes=["reconcilia"])], ruta=registro)
    assert incidentes.excusa_componente("reconcilia", "2026-09-29", ruta=registro) is None
    assert incidentes.excusa_componente("reconcilia", "2026-10-03", ruta=registro) is None


def test_ni_a_un_componente_que_no_se_declaro(registro):
    incidentes.guardar([_incidente(componentes=["compras"])], ruta=registro)
    assert incidentes.excusa_componente("ventas", "2026-10-02", ruta=registro) is None


def test_un_componente_en_rojo_sin_incidente_sigue_siendo_rojo(registro):
    import generar_operativa as go
    incidentes.guardar([_incidente(componentes=["compras"])], ruta=registro)
    ahora = datetime(2026, 10, 2, 18, 0, tzinfo=config.TZ_ET)
    salud_datos = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                            for c in go.CRITICOS}}
    salud_datos["runs"]["ventas"] = {"cuando": ahora.isoformat(),
                                     "resultado": "fallo:lo-que-sea"}
    s = go.semaforo(salud_datos, None, [], [], ahora)
    assert s["color"] == "rojo"
    assert any("Ventas en XTB" in m for m in s["motivos"])
