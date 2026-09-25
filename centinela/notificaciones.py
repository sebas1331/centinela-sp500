"""Notificaciones por Telegram: las órdenes que habría que poner en el broker.

QUÉ ES ESTO Y QUÉ NO ES
-----------------------
No es un resumen bonito del día. Es el puente entre el sistema y una mano
humana: cada mensaje dice QUÉ ORDEN poner, a qué precio y con qué tamaño, de
forma que quien lo reciba pueda teclearlo en un broker sin abrir el repositorio
ni interpretar nada. Por eso todos los mensajes llevan cartera, ticker y precios
con dos decimales, y por eso el tamaño sale de la cuenta simulada
(`centinela/cuenta.py`) y no de un nominal inventado.

TRES REGLAS DURAS
-----------------
1. **Un fallo de Telegram jamás toca la bitácora.** El envío vive en un job
   aparte de los workflows, con `needs`, igual que el análisis MFE y el
   dashboard. Si Telegram está caído, el job de notificación se pone ROJO y la
   sesión de trading ya está persistida y verificada varios jobs antes.
2. **Nada se envía dos veces.** La escalera de crons puede reejecutar un
   peldaño; cada mensaje lleva un id único (fecha, tipo, cartera, ticker) que se
   registra en `estado/notificaciones.json`. Un reenvío se descarta en silencio.
3. **Fallar es ruidoso.** `enviar` reintenta con backoff y, si se agota, LANZA.
   Nada de tragarse el error y devolver False: un canal de avisos que calla
   cuando se rompe es peor que no tener canal.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import requests

from . import config

API = "https://api.telegram.org/bot{token}/sendMessage"

#: Prefijo que marca los mensajes de prueba. Va en el id y en el texto, para que
#: una prueba nunca bloquee el envío real del mismo aviso ese día.
MARCA_PRUEBA = "[PRUEBA]"


# --------------------------------------------------------------------------- #
# Transporte
# --------------------------------------------------------------------------- #
def activas() -> bool:
    return (config.NOTIFICACIONES_ACTIVAS and bool(config.TELEGRAM_TOKEN)
            and bool(config.TELEGRAM_CHAT_ID))


class ErrorNotificacion(RuntimeError):
    """El envío falló después de agotar los reintentos."""


def enviar(mensaje: str) -> bool:
    """Envía un mensaje por Telegram. Reintenta con backoff; si no puede, LANZA.

    Devuelve False (sin lanzar) solo si las notificaciones están apagadas: eso
    no es un fallo, es una configuración. Cualquier otro problema es un fallo y
    tiene que verse.

    El texto va en `plain text`: los precios y los tickers llevan puntos,
    guiones y signos que Markdown interpreta, y un mensaje que Telegram rechaza
    por formato es un aviso perdido. La legibilidad se consigue con saltos de
    línea y emojis, que no necesitan escapes.
    """
    if not activas():
        return False

    url = API.format(token=config.TELEGRAM_TOKEN)
    ultimo = None
    for intento in range(config.NOTIF_REINTENTOS):
        espera = config.NOTIF_BACKOFF_BASE_SEG * (2 ** intento)
        try:
            r = requests.post(url, timeout=config.NOTIF_TIMEOUT_SEG, data={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": mensaje,
                "disable_web_page_preview": True,
            })
            if r.status_code == 200:
                return True
            # 429: Telegram dice EXACTAMENTE cuánto hay que esperar. Ignorarlo y
            # aplicar el backoff propio es pedir que el siguiente intento vuelva
            # a rebotar; se le hace caso a él, no a la fórmula.
            if r.status_code == 429:
                espera = max(espera, _segundos_de_espera(r))
            ultimo = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as exc:
            ultimo = repr(exc)
        if intento < config.NOTIF_REINTENTOS - 1:
            print(f"[notif] intento {intento + 1}/{config.NOTIF_REINTENTOS} "
                  f"falló ({ultimo}); reintento en {espera:.0f}s", flush=True)
            time.sleep(espera)

    raise ErrorNotificacion(
        f"No se pudo enviar la notificación tras {config.NOTIF_REINTENTOS} "
        f"intentos. Último error: {ultimo}")


def _segundos_de_espera(respuesta) -> float:
    """Los segundos que Telegram pide esperar en un 429, si los dice.

    Vienen en `parameters.retry_after` del JSON. Si la respuesta no se puede
    leer, se devuelve 0 y manda el backoff exponencial de siempre.
    """
    try:
        return float(respuesta.json().get("parameters", {}).get("retry_after", 0))
    except (ValueError, AttributeError, TypeError):
        return 0.0


# --------------------------------------------------------------------------- #
# Registro anti-duplicados
# --------------------------------------------------------------------------- #
#: Cuántos días de ids se conservan. Suficiente para cubrir cualquier reejecución
#: tardía de la escalera de crons sin que el fichero crezca sin fin.
DIAS_RETENCION = 30


def cargar_registro() -> dict:
    ruta = config.ARCHIVO_NOTIFICACIONES
    if not ruta.exists():
        return {"enviadas": {}}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Un registro corrupto no puede tumbar el sistema, pero tampoco puede
        # pasar desapercibido: se avisa y se empieza de cero (el peor caso es un
        # mensaje repetido, no uno perdido).
        print("[notif] registro de notificaciones ilegible; se reinicia.", flush=True)
        return {"enviadas": {}}
    datos.setdefault("enviadas", {})
    return datos


def guardar_registro(registro: dict) -> None:
    registro["enviadas"] = _podar(registro.get("enviadas", {}))
    registro["actualizado"] = datetime.now(config.TZ_ET).isoformat()
    config.ARCHIVO_NOTIFICACIONES.write_text(
        json.dumps(registro, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")


def _podar(enviadas: dict) -> dict:
    """Se queda con los ids de los últimos DIAS_RETENCION días registrados."""
    fechas = sorted({k.split("|", 1)[0] for k in enviadas})
    if len(fechas) <= DIAS_RETENCION:
        return enviadas
    corte = fechas[-DIAS_RETENCION]
    return {k: v for k, v in enviadas.items() if k.split("|", 1)[0] >= corte}


def identificador(fecha_iso: str, tipo: str, cartera: str = "-",
                  ticker: str = "-", prueba: bool = False) -> str:
    """Id único de una notificación: fecha, tipo, cartera y ticker.

    Las pruebas llevan su propio espacio de nombres para que enviar un
    [PRUEBA] de un tipo no impida que ese mismo día salga el aviso de verdad.
    """
    base = f"{fecha_iso}|{tipo}|{cartera}|{ticker}"
    return f"PRUEBA|{base}" if prueba else base


def ya_enviada(registro: dict, ident: str) -> bool:
    return ident in registro.get("enviadas", {})


def enviar_una(registro: dict, ident: str, mensaje: str) -> bool:
    """Envía si ese id no se había enviado. True si salió, False si era repetida.

    El registro se marca DESPUÉS de un envío correcto: si Telegram falla, la
    excepción sube y el id no queda quemado, de forma que el siguiente intento
    pueda enviarlo.
    """
    if ya_enviada(registro, ident):
        print(f"[notif] omitida (ya enviada): {ident}", flush=True)
        return False
    enviado = enviar(mensaje)
    if enviado:
        registro.setdefault("enviadas", {})[ident] = \
            datetime.now(config.TZ_ET).isoformat()
    return enviado


# --------------------------------------------------------------------------- #
# Formato
# --------------------------------------------------------------------------- #
def d2(x) -> str:
    """Precio con dos decimales. 'n/d' si no hay dato, nunca un None impreso."""
    return "n/d" if x is None else f"{float(x):,.2f}"


def pct(x, decimales: int = 2) -> str:
    return "n/d" if x is None else f"{float(x) * 100:+.{decimales}f}%"


def dolares(x) -> str:
    return "n/d" if x is None else f"{'-' if float(x) < 0 else '+'}${abs(float(x)):,.2f}"


def _encabezado(titulo: str, prueba: bool) -> str:
    return f"{MARCA_PRUEBA} {titulo}" if prueba else titulo


# --------------------------------------------------------------------------- #
# Los mensajes
#
# Todas estas funciones son PURAS: reciben datos y devuelven texto. Ni tocan la
# red ni leen ficheros, y por eso se pueden probar enteras sin Telegram delante.
# --------------------------------------------------------------------------- #
def msg_orden_compra(o: dict, prueba: bool = False) -> str:
    """Pre-apertura: la orden de compra a ejecutar en la apertura de hoy."""
    L = [_encabezado(f"🟢 ORDEN DE COMPRA · Cartera {o['cartera']} · {o['ticker']}", prueba),
         "",
         "Comprar a la APERTURA (market-on-open).",
         f"Referencia (último cierre): {d2(o['referencia'])}",
         f"Tamaño: {d2(o['importe'])} USD  ({o['acciones']:.4f} acciones)",
         "",
         f"🎯 Objetivo (orden LÍMITE de venta): {d2(o['objetivo'])}  "
         f"({pct(o['objetivo'] / o['referencia'] - 1, 1)} vs referencia)"]
    if o.get("stop") is not None:
        L.append(f"🛑 Stop (orden STOP): {d2(o['stop'])}  "
                 f"({pct(o['stop'] / o['referencia'] - 1, 1)} vs referencia)")
    else:
        L.append("🛑 Stop: ninguno (Cartera B opera sin stop, por diseño)")
    L += ["",
          f"📅 Salida por tiempo si no salta nada: {o['fecha_limite']} (al cierre)",
          f"📊 Probabilidad del modelo: {o['proba']:.3f}   "
          f"Score fundamental: {d2(o['score_fundamental'])}"]
    return "\n".join(L)


def msg_entrada_confirmada(e: dict, prueba: bool = False) -> str:
    """Post-cierre: la compra se ejecutó, este es el precio real."""
    return "\n".join([
        _encabezado(f"✅ ENTRADA CONFIRMADA · Cartera {e['cartera']} · {e['ticker']}", prueba),
        "",
        f"Precio real de apertura: {d2(e['precio'])}",
        f"Hora: {e['hora']}",
        f"Tamaño: {d2(e['importe'])} USD  ({e['acciones']:.4f} acciones)",
        "",
        f"🎯 Objetivo: {d2(e['objetivo'])}"
        + (f"    🛑 Stop: {d2(e['stop'])}" if e.get("stop") is not None else "    🛑 Sin stop"),
        f"📅 Salida por tiempo: {e['fecha_limite']}",
    ])


def msg_objetivo_actualizado(c: dict, prueba: bool = False) -> str:
    """Post-cierre: el objetivo cambió, hay que mover la orden límite."""
    return "\n".join([
        _encabezado(f"🎯 ACTUALIZACIÓN DE OBJETIVO · Cartera {c['cartera']} · {c['ticker']}", prueba),
        "",
        f"Objetivo anterior: {d2(c['anterior'])}",
        f"Objetivo nuevo:    {d2(c['nuevo'])}   ({pct(c['nuevo'] / c['anterior'] - 1, 1)})",
        f"Motivo: {c['motivo']}",
        "",
        "👉 Modifica tu orden límite de venta al nuevo objetivo.",
    ])


def msg_venta(v: dict, prueba: bool = False) -> str:
    """Post-cierre: la posición se cerró."""
    MOTIVOS = {"objetivo": "🎯 objetivo alcanzado",
               "stop": "🛑 stop disparado",
               "tiempo": "⏳ límite de tiempo (10 sesiones)"}
    signo = "🟩" if (v["pnl_pct"] or 0) >= 0 else "🟥"
    return "\n".join([
        _encabezado(f"{signo} VENTA EJECUTADA · Cartera {v['cartera']} · {v['ticker']}", prueba),
        "",
        f"Motivo: {MOTIVOS.get(v['motivo'], v['motivo'])}",
        f"Entrada: {d2(v['entrada'])}    Salida: {d2(v['salida'])}",
        "",
        f"P&L: {pct(v['pnl_pct'])}   ({dolares(v['pnl_dinero'])})",
    ])


def msg_salida_por_tiempo_manana(s: dict, prueba: bool = False) -> str:
    """Post-cierre: aviso de que mañana toca cerrar por tiempo."""
    return "\n".join([
        _encabezado(f"⏳ SALIDA POR TIEMPO MAÑANA · Cartera {s['cartera']} · {s['ticker']}", prueba),
        "",
        f"Mañana ({s['fecha_limite']}) esta posición cumple su décima sesión.",
        f"Entrada: {d2(s['entrada'])}    Objetivo vigente: {d2(s['objetivo'])}",
        "",
        "👉 Vender AL CIERRE (market-on-close) si no ha saltado antes el objetivo.",
    ])


def msg_resumen_diario(r: dict, prueba: bool = False) -> str:
    """Post-cierre: cómo va la cuenta hoy y en total."""
    L = [_encabezado(f"📊 RESUMEN DEL DÍA · {r['fecha']}", prueba), ""]
    for cartera in r["carteras"]:
        L += [
            f"— Cartera {cartera['cartera']} —",
            f"Abiertas: {cartera['abiertas']}    Cerradas hoy: {cartera['cerradas_hoy']}",
            f"P&L del día: {dolares(cartera['pnl_dia'])}",
            f"Cuenta: ${cartera['equity']:,.2f}   "
            f"acumulado {pct(cartera['rentabilidad'])}",
            "",
        ]
    L.append("Cifras netas de comisiones, spread y slippage. Paper trading.")
    # Sin precios no hay marca a mercado y la cifra de la cuenta es el valor
    # CONTABLE: las posiciones abiertas cuentan por su coste. Decirlo o callarlo
    # es la diferencia entre un dato y un dato equivocado, así que se dice.
    if not r.get("marcado_a_mercado", True):
        L.append("⚠️ Sin precios de hoy: las abiertas van a coste, no a mercado.")
    return "\n".join(L)


def msg_alerta_sistema(a: dict, prueba: bool = False) -> str:
    """El Vigilante encontró algo. Esto no es una orden, es una avería."""
    return "\n".join([
        _encabezado("🚨 ALERTA DEL SISTEMA · Centinela SP500", prueba),
        "",
        a["problema"],
        "",
        f"Run: {a['url']}",
    ])
