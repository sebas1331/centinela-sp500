"""Una orden ambigua no puede acabar en una compra doble ni en una perdida.

El 2026-09-29 el endpoint de trading de XTB devolvió cuerpo vacío en 7 de 8
compras. `ambigua` significa que el POST llegó y la respuesta vino vacía: la
orden PUDO o no haberse colocado. Tratarla como rechazada compra dos veces;
tratarla como ejecutada apunta una compra que quizá no existe. Estos tests
fijan la tercera vía: preguntar.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from centinela import ambiguas as amb, broker_xtb as bx, fiabilidad  # noqa: E402


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
class BrokerGuion:
    """Un broker que sigue un guion.

    `respuestas` son las Ejecuciones que devuelve al mandar, en orden. `lecturas`
    es lo que enseña cada vez que le preguntan por posiciones: una entrada por
    consulta, y la última se repite. Cuenta también la consulta que hace la foto
    de ANTES de mandar, porque esa es justo la que da sentido a la comparación.
    """

    def __init__(self, respuestas, lecturas=None, cola=None):
        self.respuestas = list(respuestas)
        self.lecturas = list(lecturas or [[]])
        self.cola = list(cola or [[]])
        self.envios = 0
        self.consultas = 0

    def mandar(self):
        self.envios += 1
        return self.respuestas[min(self.envios - 1, len(self.respuestas) - 1)]

    def posiciones(self):
        i = min(self.consultas, len(self.lecturas) - 1)
        self.consultas += 1
        return list(self.lecturas[i])

    def ordenes_pendientes(self):
        # Va emparejada con la consulta de posiciones que acaba de ocurrir.
        i = min(max(self.consultas - 1, 0), len(self.cola) - 1)
        return list(self.cola[i])


def _pos(simbolo="F.US", acciones=1):
    return {"ticker": simbolo, "acciones": float(acciones), "lado": "buy",
            "precio_entrada": 12.3, "precio_actual": 12.3, "orden": 1}


def _ambigua():
    return bx.Ejecucion(ticker="F.US", lado="compra", acciones=1,
                        estado="ambigua",
                        error="gRPC trade endpoint returned an empty response")


def _ok():
    return bx.Ejecucion(ticker="F.US", lado="compra", acciones=1,
                        estado="ejecutada", precio=12.31, orden=999)


def _rechazada():
    return bx.Ejecucion(ticker="F.US", lado="compra", acciones=1,
                        estado="rechazada", error="Not enough funds")


@pytest.fixture(autouse=True)
def diario(tmp_path, monkeypatch):
    monkeypatch.setattr(fiabilidad, "ARCHIVO", tmp_path / "fiabilidad.csv")
    return tmp_path / "fiabilidad.csv"


def _sin_dormir(_s):
    return None


# --------------------------------------------------------------------------- #
# 1. La resolución
# --------------------------------------------------------------------------- #
def test_si_la_posicion_aparece_la_orden_SI_entro():
    """El caso peligroso: si esto se leyera como 'no entró' se compraría otra
    vez y habría dos posiciones donde debía haber una."""
    b = BrokerGuion([], lecturas=[[], [_pos()]])
    desenlace, detalle = amb.resolver(b, "F.US", 0.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.EJECUTADA_TRAS_AMBIGUA
    assert detalle["concluyente"] and detalle["acciones"] == 1.0


def test_si_queda_en_cola_tambien_entro():
    """Una compra mandada antes de abrir se queda en cola: no hay posición
    todavía, pero la orden existe y volver a mandarla la duplicaría."""
    b = BrokerGuion([], lecturas=[[]],
                    cola=[[], [{"ticker": "F.US", "orden": 777}]])
    desenlace, detalle = amb.resolver(b, "F.US", 0.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.EN_COLA_TRAS_AMBIGUA
    assert detalle["ordenes"] == ["777"]


def test_si_no_aparece_nada_no_entro():
    b = BrokerGuion([], lecturas=[[]], cola=[[]])
    desenlace, detalle = amb.resolver(b, "F.US", 0.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.NO_EJECUTADA and detalle["concluyente"]
    assert b.consultas == amb.INTENTOS_DE_COMPROBACION, \
        "se conformó con mirar una vez"


def test_una_posicion_que_ya_estaba_no_cuenta_como_nueva():
    """Sin la foto de antes, operar sobre algo que ya se tenía daría siempre un
    falso 'sí entró'."""
    b = BrokerGuion([], lecturas=[[_pos(acciones=3)]])
    desenlace, _ = amb.resolver(b, "F.US", 3.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.NO_EJECUTADA


def test_una_orden_que_ya_estaba_en_cola_tampoco():
    b = BrokerGuion([], lecturas=[[]],
                    cola=[[{"ticker": "F.US", "orden": 555}]])
    desenlace, _ = amb.resolver(b, "F.US", 0.0, {555}, dormir=_sin_dormir)
    assert desenlace == amb.NO_EJECUTADA


def test_no_poder_preguntar_no_es_lo_mismo_que_un_no():
    """Si un corte de red se leyera como 'no entró', acabaría en compra doble."""
    class Mudo:
        def posiciones(self):
            raise RuntimeError("sin conexión")

        def ordenes_pendientes(self):
            raise RuntimeError("sin conexión")

    desenlace, detalle = amb.resolver(Mudo(), "F.US", 0.0, set(),
                                      dormir=_sin_dormir)
    assert desenlace == amb.NO_EJECUTADA
    assert detalle["concluyente"] is False, "dio por buena una comprobación rota"


# --------------------------------------------------------------------------- #
# 2. El envío completo
# --------------------------------------------------------------------------- #
def test_una_ambigua_que_si_entro_se_devuelve_como_ejecutada_y_NO_se_reintenta():
    b = BrokerGuion([_ambigua(), _ok()], lecturas=[[], [_pos()]])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ejecutada" and e.error is None
    assert b.envios == 1, "reintentó una orden que ya había entrado"


def test_una_ambigua_que_no_entro_se_reintenta_y_la_segunda_vale():
    b = BrokerGuion([_ambigua(), _ok()], lecturas=[[]])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ejecutada" and b.envios == 2


def test_una_rechazada_no_se_reintenta():
    """XTB dijo por qué. Reintentar sin cambiar nada solo repetiría el motivo."""
    b = BrokerGuion([_rechazada()], lecturas=[[]])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "rechazada" and b.envios == 1


def test_si_todas_salen_ambiguas_se_para_tras_los_reintentos():
    b = BrokerGuion([_ambigua()], lecturas=[[]])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ambigua"
    assert b.envios == amb.REINTENTOS + 1


def test_si_no_se_pudo_comprobar_no_se_reintenta():
    """Preguntar falló: reintentar a ciegas es justo lo que no se puede hacer."""
    class Mudo(BrokerGuion):
        def posiciones(self):
            raise RuntimeError("sin conexión")

        def ordenes_pendientes(self):
            raise RuntimeError("sin conexión")

    b = Mudo([_ambigua()])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ambigua" and b.envios == 1


# --------------------------------------------------------------------------- #
# 3. El diario de fiabilidad
# --------------------------------------------------------------------------- #
def test_cada_desenlace_queda_anotado(diario):
    b = BrokerGuion([_ambigua(), _ok()], lecturas=[[]])
    amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    r = fiabilidad.resumen(7, ruta=diario)
    assert r["enviadas"] == 2
    assert r["desglose"][fiabilidad.AMBIGUA_RESUELTA_NO] == 1
    assert r["desglose"][fiabilidad.CONFIRMADA] == 1


def test_una_ambigua_resuelta_que_si_entro_cuenta_como_confirmada(diario):
    """La orden existe: lo que la distingue es que costó comprobarlo, y eso se
    ve en el desglose de ambiguas, no en el recuento de confirmadas."""
    b = BrokerGuion([_ambigua()], lecturas=[[], [_pos()]])
    amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    r = fiabilidad.resumen(7, ruta=diario)
    assert r["confirmadas"] == 1 and r["ambiguas"] == 1 and r["fallidas"] == 0
    assert r["fiabilidad_pct"] == 100.0


def test_la_metrica_cuenta_bien_los_siete_de_ocho(diario):
    """El caso real del 2026-09-29, tal cual."""
    for _ in range(7):
        fiabilidad.anotar(fiabilidad.AMBIGUA_RESUELTA_NO, "F.US", "compra",
                          "cuerpo vacío", ruta=diario)
    fiabilidad.anotar(fiabilidad.CONFIRMADA, "F.US", "compra", "en_cola",
                      ruta=diario)
    r = fiabilidad.resumen(7, ruta=diario)
    assert (r["enviadas"], r["confirmadas"], r["ambiguas"], r["fallidas"]) \
        == (8, 1, 7, 0)
    assert r["fiabilidad_pct"] == 12.5
    assert r["ultimo_problema"]["simbolo"] == "F.US"


def test_lo_viejo_no_cuenta(diario, monkeypatch):
    import csv
    with open(diario, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fiabilidad.COLUMNAS)
        w.writeheader()
        w.writerow({"cuando": "2026-01-01T10:00:00", "sesion": "2026-01-01",
                    "simbolo": "F.US", "tipo": "compra",
                    "estado": fiabilidad.FALLIDA, "detalle": "vieja"})
    assert fiabilidad.resumen(7, ruta=diario)["enviadas"] == 0


def test_un_estado_inventado_no_cuela(diario):
    with pytest.raises(ValueError, match="Estado desconocido"):
        fiabilidad.anotar("regular", "F.US", "compra", ruta=diario)


def test_no_poder_anotar_no_tumba_una_operacion(tmp_path, monkeypatch, capsys):
    """Lo peor por no anotar es una métrica incompleta; lo peor por reventar
    aquí es no vender una posición que cruzó su stop."""
    monkeypatch.setattr(fiabilidad, "ARCHIVO",
                        tmp_path / "no" / "existe" / "x.csv")
    monkeypatch.setattr(Path, "mkdir",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no")))
    fiabilidad.anotar(fiabilidad.CONFIRMADA, "F.US", "compra")
    assert "no se pudo anotar" in capsys.readouterr().out


def _diario_con(ruta, ahora, estados):
    """Escribe el diario con fechas que caen dentro de la ventana de `ahora`.

    `anotar` usa el reloj de verdad, así que para probar el semáforo en una
    fecha inventada hay que poner las filas a mano.
    """
    import csv
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fiabilidad.COLUMNAS)
        w.writeheader()
        for estado in estados:
            w.writerow({"cuando": ahora.isoformat(),
                        "sesion": ahora.date().isoformat(),
                        "simbolo": "F.US", "tipo": "compra",
                        "estado": estado, "detalle": "cuerpo vacío"})


# --------------------------------------------------------------------------- #
# 4. Lo que la página hace con la métrica
# --------------------------------------------------------------------------- #
def test_una_venta_del_vigilante_que_no_llega_al_broker_es_ROJO(diario):
    """El peor fallo posible del sistema. Una compra que no entra cuesta una
    oportunidad; una venta por nivel que no entra deja una posición con su stop
    cruzado y a merced del mercado, que es lo que el stop existe para impedir.
    """
    import generar_operativa as go
    from centinela import ordenes as ords

    ahora = datetime(2026, 10, 20, 18, 0, tzinfo=bx.config.TZ_ET)
    orden = {"tipo": ords.VENTA_STOP_INTRADIA, "tipo_nombre": "Stop en vivo",
             "ticker": "MRNA", "sesion": "2026-10-20", "estado": "ambigua",
             "error": "respuesta vacía"}
    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    s = go.semaforo(salud_ok, None, [], [orden], ahora)
    assert s["color"] == "rojo"
    assert any("MRNA" in m and "NO llegó al broker" in m for m in s["motivos"])


def test_una_venta_del_vigilante_que_sí_entro_no_es_roja(diario):
    import generar_operativa as go
    from centinela import ordenes as ords

    ahora = datetime(2026, 10, 20, 18, 0, tzinfo=bx.config.TZ_ET)
    orden = {"tipo": ords.VENTA_STOP_INTRADIA, "tipo_nombre": "Stop en vivo",
             "ticker": "MRNA", "sesion": "2026-10-20", "estado": "ejecutada"}
    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    assert go.semaforo(salud_ok, None, [], [orden], ahora)["color"] == "verde"


def test_un_broker_poco_fiable_pinta_ambar(diario, monkeypatch):
    import generar_operativa as go

    ahora = datetime(2026, 10, 20, 18, 0, tzinfo=bx.config.TZ_ET)
    _diario_con(diario, ahora, [fiabilidad.AMBIGUA_RESUELTA_NO] * 7
                + [fiabilidad.CONFIRMADA])

    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    s = go.semaforo(salud_ok, None, [], [], ahora)
    assert s["color"] == "ambar"
    assert any("1 de 8" in m for m in s["motivos"]), s["motivos"]


def test_con_pocas_ordenes_el_porcentaje_no_juzga(diario):
    """Una sola ambigua sobre dos órdenes daría un 50 % que no significa que el
    broker esté roto."""
    import generar_operativa as go

    ahora = datetime(2026, 10, 20, 18, 0, tzinfo=bx.config.TZ_ET)
    _diario_con(diario, ahora, [fiabilidad.AMBIGUA_RESUELTA_NO,
                                fiabilidad.CONFIRMADA])
    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    assert go.semaforo(salud_ok, None, [], [], ahora)["color"] == "verde"
