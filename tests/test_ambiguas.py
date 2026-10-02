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
    """Un broker que sigue un guion y se comporta como uno de verdad.

    `respuestas` son las Ejecuciones que devuelve al mandar, en orden. Y lo
    importante: cuando una respuesta dice que la orden entró, el broker EMPIEZA
    a enseñar la posición. Antes el doble llevaba una lista de lecturas con
    índices a mano y se rompía cada vez que cambiaba el número de consultas;
    esto modela lo que pasa de verdad y no hay que ajustarlo.

    `entra` dice si la orden llega a existir en XTB: con False, el broker dice
    que sí al mandar y luego no hay nada — que es exactamente lo que hizo el
    2026-09-30.
    """

    def __init__(self, respuestas, entra=True, en_cola=False, acciones=1,
                 la_ambigua_entro=False):
        self.respuestas = list(respuestas)
        self.entra = entra
        # Una ambigua que SÍ se colocó: el broker dice "no sé" y la posición
        # aparece igual. Es el caso que no se puede reintentar.
        self.la_ambigua_entro = la_ambigua_entro
        self.en_cola_tras_mandar = en_cola
        self.acciones = acciones
        self.envios = 0
        self.consultas = 0
        self._tiene = False
        self._cola = False

    def mandar(self):
        self.envios += 1
        e = self.respuestas[min(self.envios - 1, len(self.respuestas) - 1)]
        if e.estado in ("ejecutada", "en_cola") and self.entra:
            self._tiene = not self.en_cola_tras_mandar
            self._cola = self.en_cola_tras_mandar
        elif e.estado == "ambigua" and self.la_ambigua_entro:
            self._tiene = True
        return e

    def posiciones(self):
        self.consultas += 1
        return [_pos(acciones=self.acciones)] if self._tiene else []

    def ordenes_pendientes(self):
        return [{"ticker": "F.US", "orden": 777}] if self._cola else []


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


def _en_cola():
    """Lo que XTB devuelve con el mercado cerrado: aceptada y encolada."""
    return bx.Ejecucion(ticker="F.US", lado="compra", acciones=1,
                        estado="en_cola", orden=916785162)


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
    b = BrokerGuion([], entra=True)
    b._tiene = True                      # la orden ambigua sí había entrado
    desenlace, detalle = amb.resolver(b, "F.US", 0.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.EJECUTADA_TRAS_AMBIGUA
    assert detalle["concluyente"] and detalle["acciones"] == 1.0


def test_si_queda_en_cola_tambien_entro():
    """Una compra mandada antes de abrir se queda en cola: no hay posición
    todavía, pero la orden existe y volver a mandarla la duplicaría."""
    b = BrokerGuion([], entra=True)
    b._cola = True                       # se colocó y espera al mercado
    desenlace, detalle = amb.resolver(b, "F.US", 0.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.EN_COLA_TRAS_AMBIGUA
    assert detalle["ordenes"] == ["777"]


def test_si_no_aparece_nada_no_entro():
    b = BrokerGuion([], entra=False)
    desenlace, detalle = amb.resolver(b, "F.US", 0.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.NO_EJECUTADA and detalle["concluyente"]
    assert b.consultas == amb.INTENTOS_DE_COMPROBACION, \
        "se conformó con mirar una vez"


def test_una_posicion_que_ya_estaba_no_cuenta_como_nueva():
    """Sin la foto de antes, operar sobre algo que ya se tenía daría siempre un
    falso 'sí entró'."""
    b = BrokerGuion([], acciones=3)
    b._tiene = True
    desenlace, _ = amb.resolver(b, "F.US", 3.0, set(), dormir=_sin_dormir)
    assert desenlace == amb.NO_EJECUTADA


def test_una_orden_que_ya_estaba_en_cola_tampoco():
    b = BrokerGuion([], entra=True)
    b._cola = True
    desenlace, _ = amb.resolver(b, "F.US", 0.0, {777}, dormir=_sin_dormir)
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
    b = BrokerGuion([_ambigua(), _ok()], la_ambigua_entro=True)
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ejecutada" and e.error is None
    assert b.envios == 1, "reintentó una orden que ya había entrado"


def test_una_ambigua_que_no_entro_se_reintenta_y_la_segunda_vale():
    """La segunda entra Y se confirma contra XTB: la posición aparece."""
    b = BrokerGuion([_ambigua(), _ok()], entra=True)
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ejecutada" and b.envios == 2


def test_una_rechazada_no_se_reintenta():
    """XTB dijo por qué. Reintentar sin cambiar nada solo repetiría el motivo."""
    b = BrokerGuion([_rechazada()], entra=False)
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "rechazada" and b.envios == 1


def test_si_todas_salen_ambiguas_se_para_tras_los_reintentos():
    b = BrokerGuion([_ambigua()], entra=False)
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
    b = BrokerGuion([_ambigua(), _ok()], entra=True)
    amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    r = fiabilidad.resumen(7, ruta=diario)
    assert r["enviadas"] == 2
    assert r["desglose"][fiabilidad.AMBIGUA_RESUELTA_NO] == 1
    assert r["desglose"][fiabilidad.EJECUTADA] == 1


def test_una_ambigua_resuelta_que_si_entro_cuenta_como_confirmada(diario):
    """La orden existe: lo que la distingue es que costó comprobarlo, y eso se
    ve en el desglose de ambiguas, no en el recuento de confirmadas."""
    b = BrokerGuion([_ambigua()], la_ambigua_entro=True)
    amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    r = fiabilidad.resumen(7, ruta=diario)
    assert r["ejecutadas"] == 1 and r["ambiguas"] == 1 and r["rechazadas"] == 0
    assert r["fiabilidad_pct"] == 100.0


def test_la_metrica_cuenta_bien_los_siete_de_ocho(diario):
    """El caso real del 2026-09-29, tal cual."""
    for _ in range(7):
        fiabilidad.anotar(fiabilidad.AMBIGUA_RESUELTA_NO, "F.US", "compra",
                          "cuerpo vacío", ruta=diario)
    fiabilidad.anotar(fiabilidad.EJECUTADA, "F.US", "compra", "hay posición",
                      ruta=diario)
    r = fiabilidad.resumen(7, ruta=diario)
    assert (r["enviadas"], r["ejecutadas"], r["ambiguas"], r["rechazadas"]) \
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
                + [fiabilidad.EJECUTADA])

    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    s = go.semaforo(salud_ok, None, [], [], ahora)
    assert s["color"] == "ambar"
    assert any("EJECUTÓ 1 de 8" in m for m in s["motivos"]), s["motivos"]


def test_con_pocas_ordenes_el_porcentaje_no_juzga(diario):
    """Una sola ambigua sobre dos órdenes daría un 50 % que no significa que el
    broker esté roto."""
    import generar_operativa as go

    ahora = datetime(2026, 10, 20, 18, 0, tzinfo=bx.config.TZ_ET)
    _diario_con(diario, ahora, [fiabilidad.AMBIGUA_RESUELTA_NO,
                                fiabilidad.EJECUTADA])
    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    assert go.semaforo(salud_ok, None, [], [], ahora)["color"] == "verde"


# --------------------------------------------------------------------------- #
# 5. El motivo que el cliente tiraba
# --------------------------------------------------------------------------- #
def test_un_error_de_grpc_se_traduce_a_algo_legible():
    """El 29/09 ocho compras fallaron con 'respuesta vacía' y nada más. El
    motivo estaba en las cabeceras desde el primer intento."""
    from xtb_api.grpc.client import _centinela_motivo
    assert _centinela_motivo(
        {"grpc-status": "3", "grpc-message": "Could not find instrument for id: 335"}
    ) == "INVALID_ARGUMENT (3): Could not find instrument for id: 335"


def test_sin_grpc_status_no_se_inventa_un_motivo():
    from xtb_api.grpc.client import _centinela_motivo
    assert _centinela_motivo({"content-type": "x"}) is None


def test_el_motivo_llega_al_error_de_la_ejecucion(monkeypatch):
    """Para que salga en el log del ejecutor y en la página, no solo en un
    diagnóstico que hay que acordarse de lanzar."""
    from xtb_api.grpc.client import CENTINELA_ULTIMO_ERROR

    class Respuesta:
        status = "AMBIGUOUS"
        price = None
        order_number = None
        error = "gRPC trade endpoint returned an empty response"

    monkeypatch.setitem(CENTINELA_ULTIMO_ERROR, "motivo",
                        "INVALID_ARGUMENT (3): Could not find instrument for id: 277")
    e = bx.BrokerXTB._traducir(Respuesta(), "INTC.US", "compra", 1)
    assert e.estado == "ambigua"
    assert "Could not find instrument for id: 277" in e.error


# --------------------------------------------------------------------------- #
# 6. "Aceptada" no es "ejecutada" (fallo del 2026-09-30)
# --------------------------------------------------------------------------- #
def test_una_orden_aceptada_que_XTB_no_ejecuta_se_anota_RECHAZADA(diario):
    """EL CASO. XTB acepta una orden de mercado con el mercado cerrado,
    devuelve 'en cola' y la descarta después. El 30/09 el sistema anotó
    'en_cola' para MRNA y FICO, no volvió a preguntar, y la página dijo 'todo
    en orden' con cero compras.

    Comprobado a propósito el 01/10 con el mercado cerrado: XTB aceptó una
    compra (orden 916785162) y tres minutos después no había ni posición ni
    orden.
    """
    b = BrokerGuion([_ok()], entra=False)     # dice que sí y no aparece nada
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "rechazada", "se tragó un 'aceptada' por 'ejecutada'"
    assert "no tiene ni posición ni orden" in (e.error or "")
    assert fiabilidad.resumen(7, ruta=diario)["rechazadas"] == 1


def test_una_orden_que_SI_se_ejecuta_se_confirma_contra_XTB(diario):
    b = BrokerGuion([_ok()], entra=True)
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ejecutada"
    r = fiabilidad.resumen(7, ruta=diario)
    assert r["ejecutadas"] == 1 and r["rechazadas"] == 0


def test_una_que_sigue_en_cola_no_se_da_por_ejecutada_ni_por_rechazada(diario):
    """Mientras la orden exista en XTB es un estado real, no una suposición."""
    b = BrokerGuion([_ok()], entra=True, en_cola=True)
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "en_cola"
    r = fiabilidad.resumen(7, ruta=diario)
    assert r["ejecutadas"] == 0 and r["rechazadas"] == 0 and r["en_cola"] == 1


def test_la_fiabilidad_se_calcula_sobre_EJECUTADAS(diario):
    """Una orden que el broker aceptó y no ejecutó no es media orden: es
    ninguna. Con el cálculo viejo, el 30/09 daba 100 %."""
    ahora = datetime(2026, 10, 20, 18, 0, tzinfo=bx.config.TZ_ET)
    _diario_con(diario, ahora, [fiabilidad.EJECUTADA,
                                fiabilidad.RECHAZADA,
                                fiabilidad.RECHAZADA,
                                fiabilidad.EN_COLA])
    r = fiabilidad.resumen(7, ruta=diario, ahora=ahora)
    assert r["enviadas"] == 4 and r["ejecutadas"] == 1
    assert r["rechazadas"] == 2 and r["en_cola"] == 1
    assert r["fiabilidad_pct"] == 25.0


def test_el_caso_real_del_30_de_septiembre(diario):
    """Dos enviadas, cero ejecutadas: 0 %, no 100 %."""
    ahora = datetime(2026, 10, 1, 10, 0, tzinfo=bx.config.TZ_ET)
    _diario_con(diario, ahora, [fiabilidad.RECHAZADA, fiabilidad.RECHAZADA])
    r = fiabilidad.resumen(7, ruta=diario, ahora=ahora)
    assert (r["enviadas"], r["ejecutadas"], r["rechazadas"]) == (2, 0, 2)
    assert r["fiabilidad_pct"] == 0.0


def test_no_poder_preguntar_no_convierte_una_orden_en_rechazada(diario):
    """Un corte de red no puede hacer que una orden que existe se dé por
    perdida: eso llevaría a comprarla otra vez.

    El caso es el de una orden ENCOLADA, que es la que no es definitiva. Si XTB
    dice FILLED, eso sí es una afirmación del broker y se respeta.
    """
    class Mudo(BrokerGuion):
        def posiciones(self):
            raise RuntimeError("sin conexión")

        def ordenes_pendientes(self):
            raise RuntimeError("sin conexión")

    b = Mudo([_en_cola()])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "en_cola", "dio por rechazada una orden sin poder mirar"


def test_no_se_da_por_rechazada_al_primer_vistazo(diario):
    """Una orden de mercado recién enviada tarda unos segundos en aparecer como
    posición. Declararla muerta a los tres segundos es el mismo error que darla
    por ejecutada sin mirar, solo que al revés.

    Pasó el 2026-10-02: CTVA se dio por rechazada seis segundos después de
    mandarla, "tras 1 comprobación".
    """
    class TardaEnAparecer(BrokerGuion):
        def posiciones(self):
            self.consultas += 1
            # Aparece a la cuarta, como una ejecución real con latencia.
            return [_pos()] if self.consultas >= 4 else []

        def ordenes_pendientes(self):
            return []

    b = TardaEnAparecer([_ok()])
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "ejecutada", "se rindió antes de que la posición apareciera"


def test_pero_si_de_verdad_no_esta_se_concluye_rechazada(diario):
    b = BrokerGuion([_ok()], entra=False)
    e = amb.enviar_resolviendo(b, "F.US", "compra", b.mandar, dormir=_sin_dormir)
    assert e.estado == "rechazada"
    # Y habiendo mirado las veces que dice que mira.
    assert f"de {amb.CONFIRMACIONES} comprobaciones" in (e.error or "")
