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
                           dormir=_sin_espera)
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
