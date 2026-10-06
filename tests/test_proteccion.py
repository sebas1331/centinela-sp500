"""La stop en el servidor de XTB (centinela/proteccion.py).

El XTB de mentira reproduce lo MEDIDO en la demo el 2026-10-05 (runs
37338861588 y 37359224699): una sola orden pendiente de venta por acción —la
segunda se acepta y se descarta en silencio—, modificar conserva el número y
cancelar la quita de la lista.
"""
from __future__ import annotations

import pytest

from centinela import broker_xtb as bx, config, ordenes as ords, proteccion as prot


class XTBFalso:
    def __init__(self, posiciones=None):
        self.pos = dict(posiciones or {})        # simbolo -> acciones
        self.ordenes: dict[int, dict] = {}       # vivas
        self.siguiente = 900
        self.llamadas: list[tuple] = []
        self.ventas: list[tuple] = []

    # -- lo que usa proteccion / BrokerXTB --
    def posiciones(self):
        return [{"ticker": s, "acciones": float(n), "lado": "buy", "orden": "p"}
                for s, n in self.pos.items() if n > 0]

    def ordenes_contado(self):
        vivas = [dict(o) for o in self.ordenes.values()]
        return {"ordenes": vivas, "todas": vivas, "reglas": {}}

    def poner_orden_venta(self, ticker, acciones, tipo, precio):
        self.llamadas.append(("poner", ticker, tipo, precio, acciones))
        self.siguiente += 1
        n = self.siguiente
        reservadas = sum(o["acciones"] for o in self.ordenes.values() if o["ticker"] == ticker)
        # Medido: se acepta SIEMPRE; si ya hay acciones reservadas, no aparece.
        if reservadas + acciones <= self.pos.get(ticker, 0):
            self.ordenes[n] = {"orden": n, "ticker": ticker, "tipo": tipo, "lado": "sell",
                               "acciones": float(acciones), "precio": float(precio),
                               "estado": "ACCEPTED", "viva": True}
        return bx.OrdenPendiente(ticker=ticker, tipo=tipo, acciones=acciones,
                                 precio=precio, estado="aceptada", orden=n)

    def modificar_orden(self, orden, tipo, precio):
        self.llamadas.append(("modificar", orden, precio))
        if orden not in self.ordenes:
            return bx.OrdenPendiente("", tipo, 0, precio, "rechazada", orden,
                                     "Order modification not allowed")
        self.ordenes[orden]["precio"] = float(precio)
        return bx.OrdenPendiente("", tipo, 0, precio, "aceptada", orden)

    def cancelar_ordenes(self, numeros):
        self.llamadas.append(("cancelar", tuple(numeros)))
        out = {}
        for n in numeros:
            out[n] = (self.ordenes.pop(n, None) is not None, None)
        return out

    # -- XTB ejecuta la stop --
    def saltar_stop(self, simbolo):
        n = next(k for k, o in self.ordenes.items() if o["ticker"] == simbolo)
        o = self.ordenes.pop(n)
        self.pos[simbolo] -= int(o["acciones"])
        return o


def _sin_espera(_s):
    pass


@pytest.fixture(autouse=True)
def _rapido(monkeypatch):
    monkeypatch.setattr(prot, "CONFIRMAR_SEG", 0)


def _deseados(**kw):
    return {s: {"ticker": s.replace(".US", ""), "acciones": a, "stop": p}
            for s, (a, p) in kw.items()}


def test_colocacion_tras_la_compra_con_cantidad_exacta():
    x = XTBFalso({"WDC.US": 4})
    inf = prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                           dormir=_sin_espera)
    assert inf[0]["accion"] == "colocada" and inf[0]["ok"]
    assert x.llamadas == [("poner", "WDC.US", "stop", 365.46, 4)]
    viva = next(iter(x.ordenes.values()))
    assert viva["acciones"] == 4 and viva["precio"] == 365.46
    assert prot.verificar(x, _deseados(**{"WDC.US": (4, 365.46)})) == []


def test_si_ya_esta_bien_no_se_toca():
    x = XTBFalso({"WDC.US": 4})
    d = _deseados(**{"WDC.US": (4, 365.46)})
    prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera)
    x.llamadas.clear()
    inf = prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera)
    assert inf[0]["accion"] == "ya_estaba" and x.llamadas == []


def test_cambio_de_stop_modifica_sin_duplicar():
    x = XTBFalso({"WDC.US": 4})
    prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                     dormir=_sin_espera)
    numero = next(iter(x.ordenes))
    x.llamadas.clear()
    inf = prot.sincronizar(x, _deseados(**{"WDC.US": (4, 370.0)}), log=lambda *_: None,
                           dormir=_sin_espera, minutos=30)
    assert inf[0]["accion"] == "modificada" and inf[0]["orden"] == numero
    assert [c[0] for c in x.llamadas] == ["modificar"]
    assert len(x.ordenes) == 1 and x.ordenes[numero]["precio"] == 370.0


def test_reposicion_de_una_stop_que_falta():
    x = XTBFalso({"WDC.US": 4})
    d = _deseados(**{"WDC.US": (4, 365.46)})
    prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera)
    x.ordenes.clear()                                  # alguien la quitó
    assert prot.verificar(x, d)                        # la reconciliación lo ve: rojo
    inf = prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera)
    assert inf[0]["accion"] == "colocada" and inf[0]["ok"]
    assert prot.verificar(x, d) == []


def test_cantidad_distinta_se_cancela_y_se_pone_la_exacta():
    x = XTBFalso({"WDC.US": 4})
    x.ordenes[1] = {"orden": 1, "ticker": "WDC.US", "tipo": "stop", "lado": "sell",
                    "acciones": 2.0, "precio": 365.46}
    inf = prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                           dormir=_sin_espera)
    assert ("cancelar", (1,)) in x.llamadas
    assert inf[0]["ok"] and [o["acciones"] for o in x.ordenes.values()] == [4.0]


def test_una_limitada_que_reserva_las_acciones_se_quita():
    """Si quedara una limitada, XTB descartaría la stop: se cancela primero."""
    x = XTBFalso({"WDC.US": 4})
    x.ordenes[7] = {"orden": 7, "ticker": "WDC.US", "tipo": "limitada", "lado": "sell",
                    "acciones": 4.0, "precio": 495.49}
    inf = prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                           dormir=_sin_espera)
    assert inf[0]["ok"] and [o["tipo"] for o in x.ordenes.values()] == ["stop"]


def test_stop_aceptada_pero_descartada_es_fallo_y_no_exito():
    """'aceptada' no es 'viva' (medido): sin aparecer en la lista, es fallo."""
    x = XTBFalso({"WDC.US": 4})
    x.pos["WDC.US"] = 0                                # nada que reservar: XTB la tira
    x.pos["WDC.US"] = 4
    x.ordenes[3] = {"orden": 3, "ticker": "OTRA", "tipo": "stop", "lado": "sell",
                    "acciones": 1.0, "precio": 1.0}
    original = x.poner_orden_venta

    def descarta(*a, **k):
        r = original(*a, **k)
        x.ordenes.pop(r.orden, None)
        return r
    x.poner_orden_venta = descarta
    inf = prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                           dormir=_sin_espera)
    assert inf[0]["accion"] == "fallo" and "descartó" in inf[0]["error"]


def test_stop_sin_posicion_se_cancela():
    x = XTBFalso({})
    x.ordenes[5] = {"orden": 5, "ticker": "F.US", "tipo": "stop", "lado": "sell",
                    "acciones": 1.0, "precio": 8.49}
    prot.sincronizar(x, {}, log=lambda *_: None, dormir=_sin_espera)
    assert x.ordenes == {}


def test_venta_a_mercado_cancela_antes_la_stop():
    """Salida por tiempo u objetivo: hay que liberar las acciones antes."""
    x = XTBFalso({"WDC.US": 4})
    prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                     dormir=_sin_espera)

    class Cliente:
        def get_cash_orders(self):
            pass

        def sell(self, ticker, volume):
            x.ventas.append((ticker, volume, dict(x.ordenes)))
            return type("R", (), {"status": "FILLED", "price": 400.0,
                                  "order_number": 1, "error": None})()

    b = bx.BrokerXTB(bx.Credenciales("e", 1, "p"), demo=True, cliente=Cliente())
    b.ordenes_contado = x.ordenes_contado
    b.cancelar_ordenes = x.cancelar_ordenes
    b._ejecutar = lambda c: c
    e = b.vender("WDC.US", 4)
    assert e.estado == "ejecutada"
    ticker, vol, ordenes_al_vender = x.ventas[0]
    assert ordenes_al_vender == {}, "la stop seguía viva al mandar la venta"


def test_stop_ejecutada_por_xtb_se_detecta_y_se_registra(tmp_path, monkeypatch):
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "b.csv")
    x = XTBFalso({"WDC.US": 4})
    prot.sincronizar(x, _deseados(**{"WDC.US": (4, 365.46)}), log=lambda *_: None,
                     dormir=_sin_espera)
    antes = prot.stops_por_simbolo(x)
    x.saltar_stop("WDC.US")
    ahora = prot.stops_por_simbolo(x)
    ej = prot.stops_ejecutadas(x.posiciones(), antes, ahora, {"WDC.US": 4})
    assert ej == [{"simbolo": "WDC.US", "acciones": 4, "precio": 365.46,
                   "orden": antes["WDC.US"]["orden"]}]
    o = prot.registrar_stop_ejecutada(ej[0], hoy="2026-10-06")
    assert o.tipo == ords.VENTA_STOP_XTB and ords.lado_de(o.tipo) == "venta"
    fila = ords.filas_de_sesion("2026-10-06", ruta=tmp_path / "b.csv")[0]
    assert fila["precio_fuente"] == "nivel" and fila["estado"] == "ejecutada"


def test_stop_cancelada_con_la_posicion_intacta_no_es_una_venta():
    x = XTBFalso({"WDC.US": 4})
    antes = {"WDC.US": {"orden": 1, "precio": 365.46, "acciones": 4.0}}
    assert prot.stops_ejecutadas(x.posiciones(), antes, {}, {"WDC.US": 4}) == []


def test_sin_stop_no_se_pide_nada(monkeypatch):
    monkeypatch.setattr(config, "STOP_EN_XTB", False)
    assert prot.stops_deseados([{"ticker": "WDC.US", "acciones": 4, "lado": "buy"}],
                               estado={"posiciones": {}}) == {}


# --------------------------------------------------------------------------- #
# Vigilante, reconciliación y página
# --------------------------------------------------------------------------- #
import sys  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ejecutor_xtb as ej  # noqa: E402
import generar_operativa as go  # noqa: E402
import vigilante_precios as vp  # noqa: E402


def test_vigilante_da_gracia_a_la_stop_de_xtb_y_luego_respalda(monkeypatch):
    vendidas = []
    monkeypatch.setattr(vp, "vender_por_nivel",
                        lambda b, r, v, d, bid: vendidas.append(d) or True)
    reloj = {"t": 1000.0}
    monkeypatch.setattr(vp.time, "monotonic", lambda: reloj["t"])
    precios = vp.Precios()
    precios.bid["WDC.US"] = 360.0                      # por debajo del stop
    v = {"ticker": "WDC", "simbolo": "WDC.US", "acciones": 4, "stop": 365.46,
         "objetivo": 495.49, "stop_xtb": 917531939}
    quedan = vp.evaluar(None, {}, [v], precios)
    assert vendidas == [] and quedan == [v], "con la stop en XTB, primero salta XTB"
    reloj["t"] += config.VIGILANTE_GRACIA_STOP_XTB_SEG + 1
    vp.evaluar(None, {}, quedan, precios)
    assert vendidas == ["stop"], "pasada la gracia, respaldo a mercado"


def test_vigilante_sin_stop_en_xtb_vende_en_el_acto(monkeypatch):
    vendidas = []
    monkeypatch.setattr(vp, "vender_por_nivel",
                        lambda b, r, v, d, bid: vendidas.append(d) or True)
    precios = vp.Precios()
    precios.bid["WDC.US"] = 360.0
    v = {"ticker": "WDC", "simbolo": "WDC.US", "acciones": 4, "stop": 365.46,
         "objetivo": 495.49, "stop_xtb": None}
    vp.evaluar(None, {}, [v], precios)
    assert vendidas == ["stop"]


def test_el_objetivo_no_espera_a_xtb(monkeypatch):
    vendidas = []
    monkeypatch.setattr(vp, "vender_por_nivel",
                        lambda b, r, v, d, bid: vendidas.append(d) or True)
    precios = vp.Precios()
    precios.bid["WDC.US"] = 500.0
    v = {"ticker": "WDC", "simbolo": "WDC.US", "acciones": 4, "stop": 365.46,
         "objetivo": 495.49, "stop_xtb": 917531939}
    vp.evaluar(None, {}, [v], precios)
    assert vendidas == ["objetivo"]


def test_reconciliacion_en_rojo_si_la_stop_no_queda_puesta(monkeypatch):
    x = XTBFalso({"WDC.US": 4})
    original = x.poner_orden_venta

    def descarta(*a, **k):
        r = original(*a, **k)
        x.ordenes.pop(r.orden, None)
        return r
    x.poner_orden_venta = descarta
    monkeypatch.setattr(prot, "stops_de_la_ultima_foto", lambda: {})
    monkeypatch.setattr(prot, "stops_deseados",
                        lambda pos, hoy=None: _deseados(**{"WDC.US": (4, 365.46)}))
    problemas = ej.revisar_stops(x)
    assert len(problemas) == 1 and "WDC.US" in problemas[0]


def test_reconciliacion_repone_y_queda_en_verde(monkeypatch):
    x = XTBFalso({"WDC.US": 4})
    monkeypatch.setattr(prot, "stops_de_la_ultima_foto", lambda: {})
    monkeypatch.setattr(prot, "stops_deseados",
                        lambda pos, hoy=None: _deseados(**{"WDC.US": (4, 365.46)}))
    assert ej.revisar_stops(x) == []
    assert [o["precio"] for o in x.ordenes.values()] == [365.46]


def test_pagina_dice_donde_vive_cada_nivel():
    broker = {"posiciones": [
        {"ticker": "WDC.US", "acciones": 4, "precio_entrada": 439.71, "precio_actual": 450.0},
        {"ticker": "FICO.US", "acciones": 1, "precio_entrada": 600.0, "precio_actual": 610.0}],
        "stops": {"WDC.US": {"orden": 917531939, "precio": 365.46, "acciones": 4.0}}}
    sim = {"posiciones": {"A": [
        {"ticker": "WDC", "stop": 365.46, "objetivo": 495.49, "fecha_entrada": "2026-10-05"},
        {"ticker": "FICO", "stop": 529.78, "objetivo": 719.96, "fecha_entrada": "2026-09-30"}]}}
    filas = {f["ticker"]: f for f in go.bloque_posiciones(
        broker, sim, datetime(2026, 10, 5, 15, 0, tzinfo=config.TZ_ET))}
    assert filas["WDC"]["stop_en"] == "xtb" and filas["WDC"]["stop_orden_xtb"] == 917531939
    assert filas["FICO"]["stop_en"] == "vigilante"
    assert filas["WDC"]["objetivo_en"] == "vigilante"


def test_pagina_no_inventa_si_no_se_pudieron_leer_las_stops():
    broker = {"posiciones": [{"ticker": "WDC.US", "acciones": 4, "precio_entrada": 1,
                              "precio_actual": 2}], "stops": None}
    sim = {"posiciones": {"A": [{"ticker": "WDC", "stop": 1.0, "objetivo": 3.0}]}}
    f = go.bloque_posiciones(broker, sim, datetime(2026, 10, 5, 15, 0, tzinfo=config.TZ_ET))[0]
    assert f["stop_en"] == "?"


def test_vigilante_no_confunde_la_venta_de_otro_runner_con_la_stop(tmp_path, monkeypatch):
    """Otro runner vendió (y canceló la stop) y la bitácora local no lo sabe:
    con el precio lejos del stop, el vigilante no lo registra como stop_xtb."""
    monkeypatch.setattr(ords, "ARCHIVO_BITACORA_BROKER", tmp_path / "b.csv")
    monkeypatch.setattr(ords, "libro_de_acciones", lambda desde=None: {"WDC.US": 4})
    monkeypatch.setattr(prot, "stops_deseados", lambda pos, hoy=None: {})
    x = XTBFalso({"WDC.US": 0})
    antes = {"WDC.US": {"orden": 1, "precio": 365.46, "acciones": 4.0}}
    r = prot.revisar(x, antes, "2026-10-06", log=lambda *_: None, bids={"WDC.US": 450.0})
    assert r["ejecutadas"] == []
    r = prot.revisar(x, antes, "2026-10-06", log=lambda *_: None, bids={"WDC.US": 364.0})
    assert [o.tipo for o in r["ejecutadas"]] == [ords.VENTA_STOP_XTB]



# --------------------------------------------------------------------------- #
# Cambios de stop con el mercado cerrado (2026-10-05, 18:02 ET: XTB dijo
# "aceptada" y dejó 365,46)
# --------------------------------------------------------------------------- #
def _con_stop(precio=365.46):
    x = XTBFalso({"WDC.US": 4})
    prot.sincronizar(x, _deseados(**{"WDC.US": (4, precio)}), log=lambda *_: None,
                     dormir=_sin_espera, minutos=30)
    x.llamadas.clear()
    return x


def test_fuera_de_sesion_no_se_modifica_y_queda_pendiente():
    x = _con_stop()
    d = _deseados(**{"WDC.US": (4, 378.34)})
    inf = prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera, minutos=None)
    assert inf[0]["accion"] == "pendiente" and inf[0]["ok"]
    assert x.llamadas == [], "con el mercado cerrado no se manda nada a XTB"
    assert prot.verificar(x, d, minutos=None) == [], "pendiente fuera de sesión: ámbar, no rojo"


def test_al_abrir_el_vigilante_lo_aplica_y_lo_confirma():
    x = _con_stop()
    d = _deseados(**{"WDC.US": (4, 378.34)})
    inf = prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera, minutos=1)
    assert inf[0]["accion"] == "modificada"
    assert [o["precio"] for o in x.ordenes.values()] == [378.34]
    assert prot.verificar(x, d, minutos=1) == []


def test_modificacion_que_xtb_no_aplica_es_fallo():
    """Lo de ayer: XTB contesta 'aceptada' y el precio no cambia."""
    x = _con_stop()
    x.modificar_orden = lambda orden, tipo, precio: bx.OrdenPendiente(
        "", tipo, 0, precio, "aceptada", orden)
    inf = prot.sincronizar(x, _deseados(**{"WDC.US": (4, 378.34)}), log=lambda *_: None,
                           dormir=_sin_espera, minutos=1)
    assert inf[0]["accion"] == "fallo" and "365.46" in inf[0]["error"]


def test_pendiente_con_mercado_abierto_es_ambar_y_luego_rojo():
    x = _con_stop()
    d = _deseados(**{"WDC.US": (4, 378.34)})
    minimo = config.STOP_XTB_MINUTOS_PARA_APLICAR
    assert prot.verificar(x, d, minutos=minimo - 1) == []
    rojo = prot.verificar(x, d, minutos=minimo + 1)
    assert len(rojo) == 1 and "sigue sin aplicarse" in rojo[0]


def test_estado_precio():
    assert prot.estado_precio(378.34, 378.34, None) is None
    assert prot.estado_precio(378.34, 365.46, None)[0] == "ambar"
    assert prot.estado_precio(378.34, 365.46, 5)[0] == "ambar"
    assert prot.estado_precio(378.34, 365.46, 60)[0] == "rojo"


def test_un_stop_que_baja_no_se_aplica_y_se_avisa_en_rojo():
    x = _con_stop(378.34)
    d = _deseados(**{"WDC.US": (4, 365.46)})
    inf = prot.sincronizar(x, d, log=lambda *_: None, dormir=_sin_espera, minutos=30)
    assert inf[0]["accion"] == "bajada_no_aplicada"
    assert x.llamadas == [] and [o["precio"] for o in x.ordenes.values()] == [378.34]
    for minutos in (None, 30):
        p = prot.verificar(x, d, minutos=minutos)
        assert len(p) == 1 and "BAJAR" in p[0]


def test_minutos_de_sesion():
    tz = config.TZ_ET
    assert prot.minutos_de_sesion(datetime(2026, 10, 5, 18, 2, tzinfo=tz)) is None
    assert prot.minutos_de_sesion(datetime(2026, 10, 6, 9, 0, tzinfo=tz)) is None
    assert round(prot.minutos_de_sesion(datetime(2026, 10, 6, 9, 45, tzinfo=tz))) == 15
    assert prot.minutos_de_sesion(datetime(2026, 10, 10, 11, 0, tzinfo=tz)) is None  # sábado


def test_el_vigilante_late_en_cuanto_aplica_un_cambio_de_stop(monkeypatch, tmp_path):
    """Que la página lo vea sin esperar a la reconciliación de la noche."""
    monkeypatch.setattr(prot, "stops_de_la_ultima_foto", lambda: {})
    s = vp.Sesion.__new__(vp.Sesion)
    s.prueba, s.stops_vistos, s.vigiladas = "", None, []
    s.precios = vp.Precios()
    s.reloj = lambda: datetime(2026, 10, 6, 9, 40, tzinfo=config.TZ_ET)
    s.mono = lambda: 500.0
    s.proximo_latido = 9999.0
    s.broker = type("B", (), {"ordenes_contado": None})()
    monkeypatch.setattr(prot, "revisar", lambda *a, **k: {
        "ejecutadas": [], "stops": {"WDC.US": {"orden": 1, "precio": 378.34}},
        "informe": [{"simbolo": "WDC.US", "accion": "modificada", "ok": True}]})
    s.proteger()
    assert s.proximo_latido == 500.0


# --------------------------------------------------------------------------- #
# El rojo del vigilante del 2026-10-05, 15:57 ET: GitHub apagó el runner
# --------------------------------------------------------------------------- #
import registrar_salud as rs  # noqa: E402


def _api(paso="cancelled", notas=("The operation was canceled.",), job="failure"):
    def api(ruta):
        if ruta.endswith("/jobs"):
            return {"jobs": [{"id": 7, "name": "vigilar", "conclusion": job, "steps": [
                {"name": "Restaurar sesión de XTB", "conclusion": "success"},
                {"name": "Vigilar precios y ejecutar niveles", "conclusion": paso}]}]}
        return [{"message": m} for m in notas]
    return api


def test_runner_apagado_por_github_se_registra_como_tal(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "37365551670")
    assert rs.causa_en_github("failure", "vigilar", "Vigilar precios", _api()) == "runner-perdido"
    assert rs.resolver("", "runner-perdido") == "fallo:runner-apagado-por-github"
    assert rs.resolver("", "runner-perdido").startswith("fallo"), "sigue siendo rojo"


def test_un_fallo_del_codigo_sigue_siendo_job_en_rojo(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    assert rs.causa_en_github("failure", "vigilar", "Vigilar precios",
                              _api(paso="failure")) == "failure"


def test_el_tiempo_agotado_no_se_confunde_con_el_runner_perdido(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    notas = ("The job running on runner X has exceeded the maximum execution time of 355 minutes.",)
    assert rs.causa_en_github("failure", "vigilar", "Vigilar precios",
                              _api(notas=notas)) == "failure"


def test_sin_api_no_se_adivina(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "1")

    def rota(_ruta):
        raise OSError("sin red")
    assert rs.causa_en_github("failure", "vigilar", "Vigilar precios", rota) == "failure"
    assert rs.causa_en_github("success", "vigilar", "Vigilar precios", rota) == "success"
