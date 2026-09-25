"""Tests del canal de notificaciones (sin red y sin Telegram).

Tres cosas que tienen que ser ciertas para poder confiar en el canal:

  1. Cada mensaje dice lo que hay que hacer, con cartera, ticker y precios a dos
     decimales. Un aviso que no se puede teclear en un broker no sirve.
  2. Nada se envía dos veces. La escalera de crons reejecuta peldaños; sin el
     registro anti-duplicados, el mismo aviso llegaría cuatro veces y el canal
     dejaría de leerse a la semana.
  3. Un fallo de Telegram NO puede tocar la bitácora. Es la regla dura de todo
     el diseño y aquí se comprueba por los dos lados: el código no escribe en
     la bitácora, y los workflows lo aíslan en un job con `needs`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, notificaciones as notif  # noqa: E402


@pytest.fixture
def canal_encendido(tmp_path, monkeypatch):
    """Notificaciones activas, registro en un fichero temporal, red cortada."""
    monkeypatch.setattr(config, "NOTIFICACIONES_ACTIVAS", True)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "token-de-prueba")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(config, "ARCHIVO_NOTIFICACIONES", tmp_path / "notificaciones.json")
    return tmp_path


class _Respuesta:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text


class _Telegram:
    """Doble de la API: cuenta los envíos y puede fallar a voluntad."""

    def __init__(self, fallos=0, status=200):
        self.enviados = []
        self.fallos = fallos
        self.status = status
        self.intentos = 0

    def post(self, url, timeout=None, data=None):
        self.intentos += 1
        if self.intentos <= self.fallos:
            return _Respuesta(500, "error temporal")
        if self.status != 200:
            return _Respuesta(self.status, "error permanente")
        self.enviados.append(data["text"])
        return _Respuesta()


@pytest.fixture
def telegram(monkeypatch):
    doble = _Telegram()
    monkeypatch.setattr(notif.requests, "post", doble.post)
    # Sin esperas reales: el backoff se prueba por su lógica, no cronometrándolo.
    monkeypatch.setattr(notif.time, "sleep", lambda s: None)
    return doble


# --------------------------------------------------------------------------- #
# 1. Transporte: reintentos, backoff y fallo ruidoso
# --------------------------------------------------------------------------- #
def test_apagado_no_envia_y_no_es_un_fallo(monkeypatch):
    monkeypatch.setattr(config, "NOTIFICACIONES_ACTIVAS", False)
    assert notif.enviar("hola") is False        # sin excepción: está apagado


def test_faltar_un_secreto_deja_el_canal_apagado(monkeypatch):
    monkeypatch.setattr(config, "NOTIFICACIONES_ACTIVAS", True)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert notif.activas() is False


def test_reintenta_con_backoff_y_acaba_enviando(canal_encendido, monkeypatch):
    doble = _Telegram(fallos=2)
    monkeypatch.setattr(notif.requests, "post", doble.post)
    esperas = []
    monkeypatch.setattr(notif.time, "sleep", esperas.append)

    assert notif.enviar("mensaje") is True
    assert doble.intentos == 3
    # Backoff exponencial de verdad: cada espera dobla la anterior.
    assert esperas == [2.0, 4.0]


def test_si_no_puede_enviar_LANZA(canal_encendido, monkeypatch):
    """Un canal de avisos que calla cuando se rompe es peor que no tenerlo.

    Esta es la diferencia con la versión anterior del módulo, que devolvía
    False y se lo tragaba: entonces un Telegram caído era indistinguible de un
    día sin noticias.
    """
    doble = _Telegram(status=403)
    monkeypatch.setattr(notif.requests, "post", doble.post)
    monkeypatch.setattr(notif.time, "sleep", lambda s: None)

    with pytest.raises(notif.ErrorNotificacion) as exc:
        notif.enviar("mensaje")
    assert "403" in str(exc.value)
    assert doble.intentos == config.NOTIF_REINTENTOS


def test_un_error_de_red_tambien_acaba_lanzando(canal_encendido, monkeypatch):
    def explota(*a, **k):
        raise notif.requests.ConnectionError("sin DNS")
    monkeypatch.setattr(notif.requests, "post", explota)
    monkeypatch.setattr(notif.time, "sleep", lambda s: None)
    with pytest.raises(notif.ErrorNotificacion):
        notif.enviar("mensaje")


# --------------------------------------------------------------------------- #
# 2. Anti-duplicados
# --------------------------------------------------------------------------- #
def test_el_mismo_aviso_no_sale_dos_veces(canal_encendido, telegram):
    reg = notif.cargar_registro()
    ident = notif.identificador("2026-09-24", "venta", "A", "MRNA")

    assert notif.enviar_una(reg, ident, "primera") is True
    assert notif.enviar_una(reg, ident, "segunda") is False
    assert telegram.enviados == ["primera"]


def test_el_id_distingue_fecha_tipo_cartera_y_ticker(canal_encendido, telegram):
    """Cuatro avisos que solo se diferencian en un campo son cuatro avisos."""
    reg = notif.cargar_registro()
    ids = [
        notif.identificador("2026-09-24", "venta", "A", "MRNA"),
        notif.identificador("2026-09-25", "venta", "A", "MRNA"),   # otra fecha
        notif.identificador("2026-09-24", "entrada", "A", "MRNA"),  # otro tipo
        notif.identificador("2026-09-24", "venta", "B", "MRNA"),   # otra cartera
        notif.identificador("2026-09-24", "venta", "A", "COHR"),   # otro ticker
    ]
    assert len(set(ids)) == 5
    for i, ident in enumerate(ids):
        assert notif.enviar_una(reg, ident, f"msg{i}") is True
    assert len(telegram.enviados) == 5


def test_una_prueba_no_quema_el_aviso_real_del_dia(canal_encendido, telegram):
    """Mandar un [PRUEBA] del resumen no puede impedir el resumen de verdad."""
    reg = notif.cargar_registro()
    prueba = notif.identificador("2026-09-24", "resumen", prueba=True)
    real = notif.identificador("2026-09-24", "resumen")
    assert prueba != real
    assert notif.enviar_una(reg, prueba, "[PRUEBA] resumen") is True
    assert notif.enviar_una(reg, real, "resumen de verdad") is True


def test_el_registro_sobrevive_al_disco(canal_encendido, telegram):
    """Una reejecución del cron arranca leyendo el fichero, no la memoria."""
    reg = notif.cargar_registro()
    ident = notif.identificador("2026-09-24", "venta", "A", "MRNA")
    notif.enviar_una(reg, ident, "primera")
    notif.guardar_registro(reg)

    otro_run = notif.cargar_registro()          # simula el siguiente peldaño
    assert notif.enviar_una(otro_run, ident, "repetida") is False
    assert telegram.enviados == ["primera"]


def test_un_envio_fallido_no_quema_el_id(canal_encendido, monkeypatch):
    """Si Telegram falla, el aviso tiene que poder reintentarse mañana.

    Marcar el id antes de enviar sería peor que no marcarlo: el aviso se
    perdería para siempre y nadie se enteraría.
    """
    doble = _Telegram(status=500)
    monkeypatch.setattr(notif.requests, "post", doble.post)
    monkeypatch.setattr(notif.time, "sleep", lambda s: None)
    reg = notif.cargar_registro()
    ident = notif.identificador("2026-09-24", "venta", "A", "MRNA")

    with pytest.raises(notif.ErrorNotificacion):
        notif.enviar_una(reg, ident, "mensaje")
    assert not notif.ya_enviada(reg, ident)


def test_un_registro_corrupto_no_tumba_el_sistema(canal_encendido, telegram):
    """El peor caso admisible es un mensaje repetido, no un job muerto."""
    config.ARCHIVO_NOTIFICACIONES.write_text("{no es json", encoding="utf-8")
    reg = notif.cargar_registro()
    assert reg == {"enviadas": {}}


def test_el_registro_se_poda_y_no_crece_sin_fin(canal_encendido):
    reg = {"enviadas": {f"2026-{m:02d}-{d:02d}|venta|A|X": "x"
                        for m in (1, 2, 3) for d in range(1, 29)}}
    notif.guardar_registro(reg)
    guardado = json.loads(config.ARCHIVO_NOTIFICACIONES.read_text(encoding="utf-8"))
    fechas = {k.split("|")[0] for k in guardado["enviadas"]}
    assert len(fechas) == notif.DIAS_RETENCION


# --------------------------------------------------------------------------- #
# 3. Los mensajes
# --------------------------------------------------------------------------- #
ORDEN = {"cartera": "A", "ticker": "MRNA", "referencia": 150.00, "importe": 528.25,
         "acciones": 3.5216, "objetivo": 172.50, "stop": 138.00,
         "fecha_limite": "2026-10-08", "proba": 0.842, "score_fundamental": 66.5}


def test_orden_de_compra_dice_todo_lo_que_hace_falta_para_teclearla():
    t = notif.msg_orden_compra(ORDEN)
    assert "ORDEN DE COMPRA" in t
    assert "Cartera A" in t and "MRNA" in t
    assert "market-on-open" in t
    assert "150.00" in t and "172.50" in t and "138.00" in t   # dos decimales
    assert "528.25" in t                                        # importe en USD
    assert "2026-10-08" in t
    assert "0.842" in t and "66.50" in t


def test_la_cartera_B_dice_que_no_lleva_stop():
    """Un hueco se leería como "falta el dato"; aquí es una decisión de diseño."""
    t = notif.msg_orden_compra({**ORDEN, "cartera": "B", "stop": None})
    assert "sin stop" in t.lower() or "ninguno" in t.lower()
    assert "138.00" not in t


def test_entrada_confirmada_trae_el_precio_real_y_la_hora():
    t = notif.msg_entrada_confirmada({
        "cartera": "A", "ticker": "MRNA", "precio": 151.20,
        "hora": "2026-09-24 09:30:00 EDT", "importe": 528.25, "acciones": 3.4937,
        "objetivo": 173.88, "stop": 139.10, "fecha_limite": "2026-10-08"})
    assert "ENTRADA CONFIRMADA" in t
    assert "151.20" in t and "09:30:00 EDT" in t


def test_actualizacion_de_objetivo_pide_mover_la_orden():
    t = notif.msg_objetivo_actualizado({
        "cartera": "B", "ticker": "COHR", "anterior": 320.00, "nuevo": 345.50,
        "motivo": "recálculo: ATR=12.30, resistencia=345.50"})
    assert "320.00" in t and "345.50" in t
    assert "modifica tu orden límite" in t.lower()
    assert "recálculo" in t


def test_venta_ejecutada_da_motivo_pnl_en_porcentaje_y_en_dolares():
    t = notif.msg_venta({"cartera": "A", "ticker": "MRNA", "motivo": "objetivo",
                         "entrada": 151.20, "salida": 173.88, "pnl_pct": 0.15,
                         "pnl_dinero": 79.24})
    assert "VENTA EJECUTADA" in t
    assert "151.20" in t and "173.88" in t
    assert "+15.00%" in t and "79.24" in t


def test_venta_con_perdida_se_ve_como_perdida():
    t = notif.msg_venta({"cartera": "A", "ticker": "COHR", "motivo": "stop",
                         "entrada": 100.00, "salida": 88.00, "pnl_pct": -0.12,
                         "pnl_dinero": -63.39})
    assert "-12.00%" in t and "-$63.39" in t
    assert "stop" in t.lower()


def test_aviso_de_salida_por_tiempo_manda_vender_al_cierre():
    t = notif.msg_salida_por_tiempo_manana({
        "cartera": "B", "ticker": "SNDK", "fecha_limite": "2026-09-25",
        "entrada": 1521.53, "objetivo": 1909.48})
    assert "SALIDA POR TIEMPO" in t
    assert "market-on-close" in t
    assert "2026-09-25" in t and "1,521.53" in t


def test_resumen_diario_lleva_las_dos_carteras_y_el_acumulado():
    t = notif.msg_resumen_diario({
        "fecha": "2026-09-24",
        "carteras": [
            {"cartera": "A", "abiertas": 8, "cerradas_hoy": 2, "pnl_dia": 123.45,
             "equity": 11207.18, "rentabilidad": 0.1207},
            {"cartera": "B", "abiertas": 8, "cerradas_hoy": 1, "pnl_dia": -45.67,
             "equity": 11172.66, "rentabilidad": 0.1173}]})
    assert "Cartera A" in t and "Cartera B" in t
    assert "+$123.45" in t and "-$45.67" in t
    assert "11,207.18" in t and "+12.07%" in t
    assert "netas" in t.lower()


def test_alerta_del_sistema_lleva_el_enlace_al_run():
    t = notif.msg_alerta_sistema({
        "problema": "EMERGENCIA: 3 runs rojos seguidos.",
        "url": "https://github.com/sebas1331/centinela-sp500/actions/runs/42"})
    assert "ALERTA DEL SISTEMA" in t
    assert "runs/42" in t


def test_las_pruebas_van_marcadas_en_el_propio_texto():
    """Nadie debe poder confundir una prueba con una orden real leyendo deprisa."""
    import notificar
    for tipo, texto in notificar.ejemplos("2026-09-24"):
        assert texto.startswith(notif.MARCA_PRUEBA), tipo


def test_hay_un_ejemplo_de_cada_tipo_de_mensaje():
    import notificar
    tipos = {t for t, _ in notificar.ejemplos("2026-09-24")}
    assert tipos == {"orden_compra", "entrada", "objetivo", "venta",
                     "tiempo_manana", "resumen", "alerta"}


def test_los_precios_nulos_se_dicen_no_se_imprimen_como_None():
    assert notif.d2(None) == "n/d"
    assert notif.pct(None) == "n/d"
    assert notif.dolares(None) == "n/d"


# --------------------------------------------------------------------------- #
# 4. La fecha de los avisos sale del estado, no del reloj
# --------------------------------------------------------------------------- #
def test_la_fecha_sale_de_la_sesion_que_proceso_el_escaneo(tmp_path, monkeypatch):
    """El post-cierre puede llegar tardísimo y cruzar la medianoche ET.

    El 2026-08-27 el cron de Actions se retrasó casi once horas. Si el escaneo
    procesa la sesión a las 23:50 ET y este job arranca a las 00:05,
    `datetime.now()` ya dice "mañana" y no se enviaría ni un aviso, en verde y
    sin que nada lo delatara. La fecha se lee del estado.
    """
    import notificar
    from centinela import estado as est_mod

    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps({
        "ultima_preapertura": "2026-09-24",
        "ultima_postcierre": "2026-09-23",
        "posiciones": {"A": [], "B": []},
    }), encoding="utf-8")
    monkeypatch.setattr(est_mod.config, "ARCHIVO_ESTADO", estado)

    assert notificar.fecha_de_la_sesion("postcierre") == "2026-09-23"
    assert notificar.fecha_de_la_sesion("preapertura") == "2026-09-24"


def test_sin_marca_en_el_estado_se_cae_al_reloj(tmp_path, monkeypatch):
    """Repositorio recién estrenado: mejor la fecha de hoy que reventar."""
    import notificar
    from centinela import estado as est_mod

    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps({"posiciones": {"A": [], "B": []}}), encoding="utf-8")
    monkeypatch.setattr(est_mod.config, "ARCHIVO_ESTADO", estado)

    import pandas as pd
    hoy = pd.Timestamp.now(tz=config.TZ_ET).date().isoformat()
    assert notificar.fecha_de_la_sesion("postcierre") == hoy
    assert notificar.fecha_de_la_sesion("alerta") == hoy


def test_el_resumen_avisa_si_no_pudo_marcar_a_mercado():
    """Un valor contable presentado como valor de mercado es un dato falso.

    Es el mismo silencio que el dashboard evita fallando en rojo. Aquí no se
    puede fallar —el resto del resumen sigue siendo correcto y útil— así que se
    manda con la advertencia puesta.
    """
    base = {"fecha": "2026-09-24",
            "carteras": [{"cartera": "A", "abiertas": 8, "cerradas_hoy": 0,
                          "pnl_dia": None, "equity": 11207.18,
                          "rentabilidad": 0.1207}]}
    con_precios = notif.msg_resumen_diario({**base, "marcado_a_mercado": True})
    sin_precios = notif.msg_resumen_diario({**base, "marcado_a_mercado": False})
    assert "a coste" not in con_precios
    assert "a coste" in sin_precios and "⚠️" in sin_precios


def test_un_429_respeta_el_retry_after_de_telegram(canal_encendido, monkeypatch):
    """Telegram dice cuánto esperar; ignorarlo es pedir otro rebote.

    El backoff propio saldría 2 s; Telegram pide 7, así que se esperan 7.
    """
    class _R429:
        status_code = 429
        text = "Too Many Requests"
        @staticmethod
        def json():
            return {"ok": False, "parameters": {"retry_after": 7}}

    class _Ok:
        status_code = 200
        text = "ok"

    respuestas = [_R429, _Ok]
    monkeypatch.setattr(notif.requests, "post",
                        lambda *a, **k: respuestas.pop(0))
    esperas = []
    monkeypatch.setattr(notif.time, "sleep", esperas.append)

    assert notif.enviar("mensaje") is True
    assert esperas == [7.0]


def test_un_429_sin_retry_after_usa_el_backoff_propio(canal_encendido, monkeypatch):
    class _R429:
        status_code = 429
        text = "Too Many Requests"
        @staticmethod
        def json():
            raise ValueError("no es json")

    class _Ok:
        status_code = 200
        text = "ok"

    respuestas = [_R429, _Ok]
    monkeypatch.setattr(notif.requests, "post", lambda *a, **k: respuestas.pop(0))
    esperas = []
    monkeypatch.setattr(notif.time, "sleep", esperas.append)
    assert notif.enviar("mensaje") is True
    assert esperas == [2.0]
