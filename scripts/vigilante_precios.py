#!/usr/bin/env python
"""Vigilante de precios: ejecuta objetivos y stops en vivo durante la sesión.

POR QUÉ EXISTE
--------------
XTB ignora el stop loss y el take profit en acciones al contado —comprobado con
una orden real el 2026-09-28: se compró 1 acción de F.US pasando los dos
niveles y la posición apareció con ambos a None, sin ningún error—. Hasta ahora
los niveles los miraba el ejecutor UNA vez al día, a las 15:45 ET, y eso está
medido: la Cartera A pasaba de +12,07 % con drawdown −10,44 % a +9,32 % con
−12,50 %, peor en las dos dimensiones que no tener stop. De las 21 operaciones
que el simulador cerró por stop, 7 no se habrían cerrado ese día porque el
precio tocó el nivel intradía pero cerró por encima.

Este proceso mira el precio tick a tick y dispara en el momento, que es lo que
el simulador supone. La diferencia entre lo que consigue y aquel vistazo diario
es medible: las salidas llevan tipo propio (`stop_intradia`, `objetivo_intradia`)
justamente para poder compararlas.

LA STOP TAMBIÉN VA EN XTB (2026-10-05)
--------------------------------------
Desde el parche 3 del cliente, la stop de cada posición se pone como orden
pendiente en el servidor de XTB (centinela/proteccion.py), que salta aunque este
proceso o GitHub fallen. En cada relectura el vigilante la repone si falta, la
ajusta si no cuadra y detecta si saltó. Si ve el precio cruzar un stop que ya
está en XTB, espera `VIGILANTE_GRACIA_STOP_XTB_SEG` a que salte allí y solo
después vende él a mercado, como respaldo. El objetivo sigue siendo suyo: XTB
admite una sola orden pendiente de venta por acción.

LO QUE NO HACE
--------------
No decide nada. Los niveles son los que el simulador calculó la víspera y se
leen del estado tal cual. No los recalcula, no los ajusta y no inventa ninguno.

TAMBIÉN VENDE POR TIEMPO (2026-10-02)
-------------------------------------
Las salidas por tiempo del día 10 se hacen aquí, unos minutos antes del cierre
(`config.VIGILANTE_TIEMPO_MIN_ANTES_CIERRE`). El workflow de ventas por cron no
llegó a su ventana ni una vez en la semana del 28/09 —los crons de este
repositorio llegan con horas de retraso—, y este proceso está vivo toda la
sesión. El cron queda de respaldo: usa el mismo identificador de orden, así que
si el vigilante ya vendió, el respaldo lo ve y no repite.

LO QUE ANUNCIA ES LO QUE HAY
----------------------------
Relee las posiciones de XTB cada `VIGILANTE_REFRESCO_SEG` y tras cada venta, y
vuelca la foto de la cuenta en cada latido. El latido nunca anuncia una
posición que ya no existe durante más de esos pocos minutos.

CÓMO SE PROTEGE DE VENDER DOS VECES
-----------------------------------
El candado real no es un fichero: es XTB. Antes de cada venta se releen las
posiciones del broker y se comprueba que la que se va a vender siga ahí con el
volumen esperado. Un fichero de bloqueo entre dos runners distintos es una
promesa; preguntarle al broker es un hecho.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (ambiguas as amb, calendario, config,  # noqa: E402
                       broker_xtb as bx, diario, estado_broker, latido as lat,
                       niveles as niv, ordenes as ords, estado as est_mod,
                       proteccion, salud)

#: Cada cuánto se pregunta por precios cuando el WebSocket está caído.
RESPALDO_SEGUNDOS = 20
#: Cuánto puede pasar sin recibir un solo tick antes de sospechar del socket.
SILENCIO_SOSPECHOSO_SEG = 90
#: Cuándo el vigilante llama a su sucesor. El trabajo de GitHub muere a las 6 h
#: y la sesión dura 6 h 30 min, así que hace falta un relevo. A las 5 h 45 min
#: quedan 15 minutos de margen para arrancar al sucesor, verlo latir y salir.
RELEVO_TRAS_HORAS = 5.75
#: Cuánto se espera a que el sucesor confirme antes de dar el relevo por fallido.
ESPERA_SUCESOR_SEG = 600


def en_prueba() -> bool:
    """¿Es una prueba? Entonces ni se publica el latido ni se toca el repo.

    Un latido de prueba haría que la página enseñara un vigilante vigilando
    posiciones que no son de nadie, y el job de pruebas es de solo lectura a
    propósito: no debe poder empujar nada.
    """
    return bool(os.environ.get("CENTINELA_PRUEBA"))


def latir(datos, trabajo) -> None:
    """Publica el latido, salvo en una prueba."""
    if en_prueba():
        print(f"[latido] (prueba: no se publica) {len(datos['vigiladas'])} "
              f"vigilada(s)", flush=True)
        return
    lat.publicar(datos, trabajo)


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%H:%M:%S ET}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Qué se vigila
# --------------------------------------------------------------------------- #
def _niveles_de_las_compras_de_hoy(ya_conocidas: dict,
                                   hoy: str | None = None) -> dict:
    """Objetivo y stop de lo comprado hoy, leídos de la bitácora del broker.

    Solo para lo que el estado del simulador todavía no conoce: si la posición
    ya está ahí, manda el estado, que es la fuente.
    """
    hoy = hoy or datetime.now(config.TZ_ET).date().isoformat()
    fuera = {}
    for f in ords.filas_de_sesion(hoy):
        if f.get("tipo") not in (ords.COMPRA, ords.ENTRADA_TARDIA):
            continue
        if f.get("estado") != "ejecutada" or f.get("ticker") in ya_conocidas:
            continue
        objetivo = _num(f.get("objetivo"))
        stop = _num(f.get("stop"))
        if objetivo is None and stop is None:
            continue
        fuera[f["ticker"]] = {
            "ticker": f["ticker"], "fecha_entrada": hoy,
            "objetivo": objetivo, "stop": stop, "id": None,
        }
        log(f"  {f['ticker']}: niveles leídos de la orden de hoy (el simulador "
            f"aún no la ha convertido en posición)")
    return fuera


def _num(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ventas_por_tiempo_de_hoy(hoy: str) -> dict[str, ords.Orden]:
    """Las salidas por tiempo decididas para HOY, por ticker.

    Las decide el post-cierre de la víspera y las deja en `pendientes.json`,
    el mismo fichero que lee el ejecutor de ventas por cron. Una sola fuente
    para los dos: si el vigilante y el respaldo leyeran listas distintas,
    podrían no ponerse de acuerdo en qué había que vender.
    """
    pendientes = ords.cargar_pendientes()
    return {o.ticker: o for o in pendientes.get("ordenes", [])
            if o.tipo == ords.VENTA_TIEMPO and o.sesion == hoy
            and o.cartera == config.CARTERA_BROKER}


def cargar_vigiladas(broker, hoy: str | None = None) -> list[dict]:
    """Las posiciones que XTB tiene de verdad, con sus niveles de la víspera.

    Se cruzan dos fuentes porque ninguna lo sabe todo: XTB conoce el símbolo y
    el volumen; el simulador conoce el objetivo vigente y el stop. Solo se
    vigila lo que está en las dos — y lo que sale hoy por tiempo, aunque no
    tenga nivel, porque alguien tiene que venderlo antes del cierre.

    Las heredadas del paper trading (anteriores a EJECUCION_DESDE) se quedan
    fuera: nunca existieron en el broker.
    """
    hoy = hoy or datetime.now(config.TZ_ET).date().isoformat()
    estado = est_mod.cargar()
    cartera = config.CARTERA_BROKER
    simuladas = {p["ticker"]: p
                 for p in estado.get("posiciones", {}).get(cartera, [])
                 if str(p.get("fecha_entrada", "")) >= config.EJECUCION_DESDE}

    # LAS COMPRAS DE HOY TODAVÍA NO SON POSICIONES. El simulador las mueve de
    # `entradas_pendientes` a `posiciones` en el post-cierre, así que entre la
    # compra de la mañana y el cierre no tienen niveles en el estado — y el
    # vigilante decía "no hay nada que vigilar" con la posición recién abierta.
    # Los niveles SÍ existen: se decidieron al generar la orden y están en la
    # bitácora del broker. De ahí se leen.
    simuladas.update(_niveles_de_las_compras_de_hoy(simuladas, hoy))
    tiempo = ventas_por_tiempo_de_hoy(hoy)

    # Una entrada por SÍMBOLO, sumando: XTB enseña una posición por orden de
    # apertura, y dos compras del mismo ticker no son dos cosas que vigilar.
    reales: dict[str, float] = {}
    for real in broker.posiciones():
        if real["lado"] != "buy":
            continue
        reales[real["ticker"]] = reales.get(real["ticker"], 0.0) + float(real["acciones"])

    vigiladas = []
    for simbolo, acciones in reales.items():
        ticker = simbolo.replace(".US", "").replace("-", ".")
        sim = simuladas.get(ticker)
        sale_hoy = ticker in tiempo
        if sim is None and not sale_hoy:
            continue
        objetivo = sim.get("objetivo") if sim else None
        stop = sim.get("stop") if sim else None
        if stop is None and objetivo is None and not sale_hoy:
            continue                       # nada que vigilar
        vigiladas.append({
            "ticker": ticker,
            "simbolo": simbolo,
            "acciones": int(acciones),
            "objetivo": objetivo,
            "stop": stop,
            "id_operacion": (sim.get("id") if sim
                             else tiempo[ticker].id_operacion),
            "sale_hoy": sale_hoy,
            "bid": None,
        })
    return vigiladas


def vigiladas_de_prueba(broker, spec: str) -> list[dict]:
    """Una posición con niveles puestos a mano, para probar el disparo de verdad.

    Existe porque la única prueba que vale de verdad es contra XTB con el
    mercado abierto, y montarla tocando `estado/estado.json` sería meter una
    posición inventada en el estado real del simulador. Esto no toca nada: lee
    la posición que ya está en el broker y le pone los niveles del argumento.
    """
    ticker, objetivo, stop = (spec.split(":") + ["", ""])[:3]
    ticker = ticker.strip().upper()
    for p in broker.posiciones():
        if p["lado"] != "buy":
            continue
        if p["ticker"].replace(".US", "").replace("-", ".") != ticker:
            continue
        return [{
            "ticker": ticker, "simbolo": p["ticker"],
            "acciones": int(p["acciones"]),
            "objetivo": float(objetivo) if objetivo else None,
            "stop": float(stop) if stop else None,
            "id_operacion": None, "bid": None, "sale_hoy": False,
        }]
    raise RuntimeError(
        f"Modo prueba: XTB no tiene ninguna posición abierta de {ticker}. "
        f"Cómprala antes de pedirle al vigilante que la vigile.")


# --------------------------------------------------------------------------- #
# El disparo
# --------------------------------------------------------------------------- #
def vender_por_nivel(broker, registro: dict, v: dict, disparo: str,
                     bid: float) -> bool:
    """Cierra una posición porque su precio cruzó el nivel. True si vendió."""
    hoy = datetime.now(config.TZ_ET).date().isoformat()
    tipo = niv.tipo_de_orden(disparo, en_vivo=True)
    nivel = v["stop"] if disparo == "stop" else v["objetivo"]
    id_orden = ords.identificador(hoy, config.CARTERA_BROKER, v["ticker"], tipo)

    if ords.ya_enviada(registro, id_orden):
        log(f"  {v['ticker']}: {tipo} ya enviada hoy; no se repite.")
        return False
    if not niv.sigue_abierta(broker, v["simbolo"], v["acciones"]):
        log(f"  {v['ticker']}: XTB ya no tiene la posición (la cerró otro). "
            f"No se vende.")
        return False

    orden = ords.Orden(
        id=id_orden, tipo=tipo, cartera=config.CARTERA_BROKER,
        ticker=v["ticker"], acciones=v["acciones"], sesion=hoy,
        id_operacion=v.get("id_operacion"),
        # El nivel exacto es el precio de referencia: la diferencia contra el
        # precio ejecutado es lo que cuesta de verdad disparar en vivo, y es el
        # número que esta pieza existe para medir.
        precio_simulador=float(nivel) if nivel is not None else None,
        # El bid que disparó: contra él se mide cuánto se movió el precio
        # entre que se vio el cruce y XTB ejecutó.
        referencia=float(bid))

    log(f"  {v['ticker']}: bid {bid} cruzó {disparo} {nivel} -> vendiendo "
        f"{v['acciones']} acciones a mercado...")
    t0 = time.monotonic()
    e = amb.enviar_resolviendo(
        broker, v["simbolo"], tipo,
        lambda: broker.vender(v["simbolo"], v["acciones"]),
        id_orden=id_orden, referencia=float(bid))
    latencia = time.monotonic() - t0
    log(f"    -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
        + (f" (orden {e.orden})" if e.orden else "")
        + (f" ERROR: {e.error}" if e.error else "")
        + f" [{latencia:.2f} s]")

    ords.registrar_ejecucion(orden, e)
    if e.ok:
        ords.marcar_enviada(registro, id_orden,
                            {"estado": e.estado, "orden": e.orden,
                             "disparo": disparo, "bid": bid,
                             "latencia_seg": round(latencia, 2)})
        ords.guardar_enviadas(registro)
        if not os.environ.get("CENTINELA_PRUEBA"):
            publicar_venta(v["ticker"], tipo)
        else:
            log("    (modo prueba: no se publica en el repositorio)")
        return True

    raise RuntimeError(
        f"La venta por {disparo} de {v['ticker']} NO llegó al broker: "
        f"{e.estado} — {e.error}. La posición sigue abierta y su nivel ya está "
        f"cruzado.")


#: Si hay algo del diario sin publicar. Se reintenta en cada vuelta del bucle
#: hasta que sube; el paso final del workflow lo intenta otra vez con todos sus
#: reintentos y, si tampoco puede, rompe en rojo.
PENDIENTE = {"si": False}


def publicar_venta(ticker: str, tipo: str) -> None:
    """Publica la venta en el repositorio, para que el resto del sistema se entere.

    Importa que sea inmediato y no al final: el ejecutor de ventas por tiempo
    hace `git pull` antes de trabajar, y si esta venta no está publicada cuando
    él mire, intentará vender unas acciones que ya no existen (no podrá: relee
    XTB antes, pero lo apuntaría como algo raro).

    SIN REBASE (fallo de la semana del 28/09). Antes era commit + push +
    rebase, y un rebase que choca pierde la venta. Ahora la venta ya está en el
    diario local antes de llegar aquí, y publicar es aplicar ese diario sobre
    el origin más nuevo: no puede chocar. Si el push falla igualmente (red), se
    deja pendiente y el bucle lo reintenta cada minuto.
    """
    try:
        diario.publicar(f"vigilante de precios: {tipo} de {ticker} [skip ci]",
                        intentos=2, log=log)
        PENDIENTE["si"] = False
    except RuntimeError as exc:
        PENDIENTE["si"] = True
        print(f"::warning::la venta de {ticker} está en el diario local pero "
              f"aún no en el repositorio ({exc}). Se reintenta cada minuto.",
              flush=True)


def reintentar_publicacion() -> None:
    if not PENDIENTE["si"]:
        return
    try:
        diario.publicar("vigilante de precios: publicación pendiente [skip ci]",
                        intentos=1, log=log)
        PENDIENTE["si"] = False
    except RuntimeError as exc:
        log(f"la publicación pendiente sigue sin subir ({exc}); otra vuelta.")


# --------------------------------------------------------------------------- #
# Las salidas por tiempo
# --------------------------------------------------------------------------- #
def vender_por_tiempo(broker, registro: dict, v: dict, orden_dia: ords.Orden,
                      hoy: str) -> str:
    """Vende por tiempo una posición. Devuelve 'vendida', 'ya-estaba' o el error.

    Mismo identificador que el ejecutor de ventas por cron
    (`fecha|cartera|ticker|venta_tiempo`): el que llegue segundo lo encuentra
    en el registro, o encuentra la posición cerrada en XTB, y no repite.
    """
    id_orden = orden_dia.id
    if ords.ya_enviada(registro, id_orden):
        log(f"  {v['ticker']}: la venta por tiempo ya se envió hoy.")
        return "ya-estaba"
    if not niv.sigue_abierta(broker, v["simbolo"], v["acciones"]):
        log(f"  {v['ticker']}: XTB ya no tiene la posición; no se vende.")
        return "ya-estaba"
    orden = ords.Orden(
        id=id_orden, tipo=ords.VENTA_TIEMPO, cartera=config.CARTERA_BROKER,
        ticker=v["ticker"], acciones=int(v["acciones"]), sesion=hoy,
        fecha_limite=orden_dia.fecha_limite,
        id_operacion=v.get("id_operacion") or orden_dia.id_operacion,
        referencia=v.get("bid"))
    log(f"  {v['ticker']}: salida por tiempo -> vendiendo {v['acciones']} "
        f"acciones a mercado (bid {v.get('bid')})...")
    e = amb.enviar_resolviendo(
        broker, v["simbolo"], ords.VENTA_TIEMPO,
        lambda: broker.vender(v["simbolo"], int(v["acciones"])),
        id_orden=id_orden, referencia=v.get("bid"))
    log(f"    -> {e.estado}" + (f" a {e.precio} ({e.precio_fuente})" if e.precio else "")
        + (f" (orden {e.orden})" if e.orden else "")
        + (f" ERROR: {e.error}" if e.error else ""))
    ords.registrar_ejecucion(orden, e)
    if not e.ok:
        return f"{e.estado}: {e.error or 'sin motivo'}"
    ords.marcar_enviada(registro, id_orden, {"estado": e.estado, "orden": e.orden,
                                             "por": "vigilante de precios"})
    ords.guardar_enviadas(registro)
    if not en_prueba():
        publicar_venta(v["ticker"], ords.VENTA_TIEMPO)
    return "vendida"


# --------------------------------------------------------------------------- #
# El relevo
# --------------------------------------------------------------------------- #
def lanzar_sucesor() -> bool:
    """Arranca el siguiente vigilante por la API de Actions. True si aceptó."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log("sin GITHUB_TOKEN o GITHUB_REPOSITORY: no se puede llamar al sucesor.")
        return False
    r = subprocess.run(
        ["curl", "-sS", "-X", "POST", "-o", "/dev/null", "-w", "%{http_code}",
         "-H", "Accept: application/vnd.github+json",
         "-H", f"Authorization: Bearer {token}",
         f"https://api.github.com/repos/{repo}/actions/workflows/"
         f"vigilante_precios.yml/dispatches",
         "-d", json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main"),
                           "inputs": {"relevo_de": os.environ.get("GITHUB_RUN_ID", "")}})],
        capture_output=True, text=True)
    codigo = (r.stdout or "").strip()
    log(f"llamada al sucesor: HTTP {codigo}")
    return codigo == "204"


def esperar_sucesor(trabajo: Path, mi_run: str, broker, registro,
                    vigiladas: list[dict], arrancado: str,
                    construir=None) -> bool:
    """Sigue vigilando hasta que el sucesor confirme que está vivo.

    LA CLAVE DE QUE NO HAYA HUECO. El saliente no se va cuando pide el relevo:
    se va cuando VE latir a otro. Mientras espera sigue mirando precios, así
    que aunque el sucesor tarde cinco minutos en arrancar, esos cinco minutos
    están cubiertos.
    """
    limite = time.monotonic() + ESPERA_SUCESOR_SEG
    while time.monotonic() < limite:
        vigilar_un_rato(broker, registro, vigiladas, segundos=15)
        actual = lat.leer(trabajo)
        if actual and actual.get("run") and str(actual["run"]) != str(mi_run):
            log(f"el sucesor (run {actual['run']}) está latiendo. Testigo "
                f"entregado; me retiro.")
            return True
        # Con `construir` (el de la sesión) el latido de espera lleva el
        # historial: si no, el sucesor lo heredaría vacío y no se podría
        # comprobar que el relevo no dejó hueco.
        lat.publicar(construir() if construir else
                     lat.construir(arrancado, vigiladas,
                                   estado=lat.ESPERANDO_RELEVO, relevo=mi_run),
                     trabajo)
    return False


# --------------------------------------------------------------------------- #
# El bucle
# --------------------------------------------------------------------------- #
class Precios:
    """Los últimos bid de cada símbolo, alimentados por el WebSocket."""

    def __init__(self):
        self.bid: dict[str, float] = {}
        self.ultimo_tick = time.monotonic()

    def encajar(self, tick: dict) -> None:
        simbolo = str(tick.get("symbol", ""))
        bid = tick.get("bid")
        if not simbolo or bid is None:
            return
        try:
            self.bid[simbolo] = float(bid)
        except (TypeError, ValueError):
            return
        self.ultimo_tick = time.monotonic()


#: Cuántas ventas ha disparado este vigilante. Se cuenta aquí y no restando
#: longitudes de la lista: `evaluar` también deja de vigilar las posiciones que
#: ya había cerrado otro, y contar esas como ventas propias inflaría la cifra
#: que luego se publica.
VENDIDAS = {"n": 0}


def evaluar(broker, registro: dict, vigiladas: list[dict],
            precios: Precios) -> list[dict]:
    """Mira cada posición contra su nivel. Devuelve las que siguen vivas."""
    quedan = []
    for v in vigiladas:
        bid = precios.bid.get(v["simbolo"])
        v["bid"] = bid
        disparo = niv.cruce(bid, v.get("stop"), v.get("objetivo"))
        if disparo is None:
            v.pop("_cruce_stop", None)
            quedan.append(v)
            continue
        # La stop está en XTB: que salte allí. El vigilante solo entra como
        # respaldo si, pasada la gracia, la posición sigue abierta.
        if disparo == "stop" and v.get("stop_xtb"):
            nuevo = "_cruce_stop" not in v
            desde = v.setdefault("_cruce_stop", time.monotonic())
            espera = time.monotonic() - desde
            if espera < config.VIGILANTE_GRACIA_STOP_XTB_SEG:
                if nuevo:
                    log(f"  {v['ticker']}: bid {bid} cruzó el stop {v['stop']}; "
                        f"la stop #{v['stop_xtb']} está en XTB, se le dan "
                        f"{config.VIGILANTE_GRACIA_STOP_XTB_SEG} s para saltar.")
                quedan.append(v)
                continue
            log(f"  {v['ticker']}: la stop de XTB no saltó en "
                f"{espera:.0f} s; respaldo a mercado.")
        if vender_por_nivel(broker, registro, v, disparo, bid):
            VENDIDAS["n"] += 1
        # Vendida o ya cerrada por otro, deja de vigilarse: en los dos casos no
        # queda nada que mirar.
    return quedan


def vigilar_un_rato(broker, registro: dict, vigiladas: list[dict],
                    segundos: float, precios: Precios | None = None) -> None:
    """Deja entrar ticks durante un rato y evalúa lo que llegue."""
    precios = precios if precios is not None else getattr(broker, "_precios", None)
    if precios is None:
        return
    broker.bombear(segundos)
    vigiladas[:] = evaluar(broker, registro, vigiladas, precios)


def respaldo_por_peticion(broker, vigiladas: list[dict], precios: Precios) -> None:
    """Pide los precios uno a uno cuando el WebSocket no empuja.

    `get_quote` del cliente se suscribe y se DESUSCRIBE al terminar, así que
    llamarlo mientras hay suscripciones vivas las mataría. Por eso solo se usa
    cuando el socket ya está caído —no hay suscripción que romper— y al
    recuperarlo se vuelve a suscribir todo desde cero.
    """
    for v in vigiladas:
        try:
            q = broker.cotizacion(v["simbolo"])
        except Exception as exc:  # noqa: BLE001 — un símbolo mudo no para el resto
            log(f"  respaldo: sin cotización de {v['simbolo']} — {exc!r}")
            continue
        precios.bid[v["simbolo"]] = q["bid"]


def suscribir_todo(broker, vigiladas: list[dict]) -> None:
    for v in vigiladas:
        broker.suscribir_ticks(v["simbolo"])
    log(f"suscrito a {len(vigiladas)} símbolo(s): "
        + ", ".join(v["simbolo"] for v in vigiladas))


class Sesion:
    """Una sesión de vigilancia: el bucle, con el reloj y el broker inyectables.

    Separado de `main` para poder SIMULAR una sesión entera en los tests —
    compra, objetivo, stop, venta por tiempo, caída, relevo— con un reloj que
    avanza a voluntad y un XTB de mentira, sin esperar seis horas ni tocar la
    cuenta.
    """

    def __init__(self, broker, registro: dict, *, trabajo: Path, arrancado: str,
                 cierre: datetime, mi_run: str = "local", prueba: str = "",
                 reloj=None, mono=time.monotonic, historial: list | None = None):
        self.broker = broker
        self.registro = registro
        self.trabajo = trabajo
        self.arrancado = arrancado
        self.cierre = cierre
        self.mi_run = mi_run
        self.prueba = prueba
        self.reloj = reloj or (lambda: datetime.now(config.TZ_ET))
        self.mono = mono
        self.historial = list(historial or [])
        self.precios = Precios()
        broker._precios = self.precios        # lo lee vigilar_un_rato
        self.vigiladas: list[dict] = []
        self.suscritos: set[str] = set()
        self.caido = {"si": False}
        self.tiempos_hechos = False
        self.ventas_tiempo = 0
        self.foto_broker: dict | None = None
        #: Stops en XTB vistas en la última vuelta (None: aún no se miró).
        self.stops_vistos: dict | None = None
        self.error_broker: str | None = None
        self.ultima_foto_ok = mono()
        self.ultimo_latido_ok = mono()
        ahora = mono()
        self.proximo_latido = ahora
        self.proximo_respaldo = ahora + RESPALDO_SEGUNDOS
        self.proximo_refresco = ahora + config.VIGILANTE_REFRESCO_SEG
        self.proximo_reintento = ahora + 60
        # El relevo se cuenta desde que arrancó el JOB, no este proceso: si el
        # supervisor lo reinicia a mitad, el límite de 6 h de GitHub sigue
        # siendo el del job.
        ya = max(0.0, time.time() - float(os.environ.get("CENTINELA_JOB_INICIO")
                                          or time.time()))
        self.relevo_a_las = ahora + RELEVO_TRAS_HORAS * 3600 - ya
        self.reinicios = int(os.environ.get("CENTINELA_REINICIOS") or 0)

    @property
    def hoy(self) -> str:
        return self.reloj().date().isoformat()

    # --- qué se vigila ---------------------------------------------------
    def refrescar(self, motivo: str = "") -> None:
        """Relee XTB. Lo que ya no está deja de vigilarse y de anunciarse."""
        if self.prueba:
            # En modo prueba la posición se vende a propósito: que ya no esté
            # después es el éxito de la prueba, no un error. Al ARRANCAR sí se
            # exige que esté (lo comprueba main antes de llegar aquí).
            try:
                nuevas = vigiladas_de_prueba(self.broker, self.prueba)
            except RuntimeError:
                log("modo prueba: la posición ya no está en XTB (vendida).")
                nuevas = []
        else:
            nuevas = cargar_vigiladas(self.broker, self.hoy)
        antes = {v["simbolo"] for v in self.vigiladas}
        for v in nuevas:
            v["bid"] = self.precios.bid.get(v["simbolo"])
        for simbolo in sorted({v["simbolo"] for v in nuevas} - self.suscritos):
            self.broker.suscribir_ticks(simbolo)
            self.suscritos.add(simbolo)
        ahora = {v["simbolo"] for v in nuevas}
        if ahora != antes and motivo:
            log(f"posiciones releídas ({motivo}): "
                + (", ".join(sorted(ahora)) or "ninguna")
                + (f" — ya no están: {', '.join(sorted(antes - ahora))}"
                   if antes - ahora else ""))
        # La gracia de una stop cruzada se conserva entre relecturas.
        previas = {v["simbolo"]: v for v in self.vigiladas}
        for v in nuevas:
            if "_cruce_stop" in previas.get(v["simbolo"], {}):
                v["_cruce_stop"] = previas[v["simbolo"]]["_cruce_stop"]
        self.vigiladas = nuevas
        self.proteger()
        self.proximo_refresco = self.mono() + config.VIGILANTE_REFRESCO_SEG

    # --- la stop en XTB ------------------------------------------------------
    def proteger(self) -> None:
        """Stop de cada posición en XTB: detectar las que saltaron, reponer las
        que falten, y marcar en cada vigilada qué stop la protege.

        Nunca tumba al vigilante: si XTB no deja, el stop lo sigue vigilando
        él, que es como funcionaba antes. Pero se dice en voz alta.
        """
        if self.prueba or not config.STOP_EN_XTB or \
                not hasattr(self.broker, "ordenes_contado"):
            return
        if self.stops_vistos is None:
            self.stops_vistos = proteccion.stops_de_la_ultima_foto()
        try:
            r = proteccion.revisar(self.broker, self.stops_vistos, self.hoy, log=log,
                                  bids=dict(self.precios.bid))
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::no se pudo revisar las stops en XTB ({exc!r}); "
                  f"el vigilante sigue vigilándolas.", flush=True)
            return
        for i in r["informe"]:
            if not i["ok"]:
                print(f"::error::{i['simbolo']}: stop NO puesta en XTB — "
                      f"{i['error']}. La vigila solo el vigilante.", flush=True)
        for o in r["ejecutadas"]:
            if not en_prueba():
                publicar_venta(o.ticker, o.tipo)
        self.stops_vistos = r["stops"]
        for v in self.vigiladas:
            v["stop_xtb"] = (self.stops_vistos.get(v["simbolo"]) or {}).get("orden")
        # Un cambio aplicado (p. ej. el stop que la reconciliación de la noche
        # dejó pendiente porque XTB no acepta cambios fuera de sesión) se
        # publica YA: el próximo paso late (latir() fotografía la cuenta y la
        # foto viaja en el latido), y la página lo enseña sin esperar a la
        # reconciliación de la noche.
        if any(i["accion"] in ("modificada", "colocada") for i in r["informe"]):
            self.proximo_latido = self.mono()
        if r["ejecutadas"]:
            simbolos = {ords.simbolo_xtb(o.ticker) for o in r["ejecutadas"]}
            self.vigiladas = [v for v in self.vigiladas
                              if v["simbolo"] not in simbolos
                              or niv.sigue_abierta(self.broker, v["simbolo"], 1)]

    # --- la foto de la cuenta ----------------------------------------------
    def fotografiar(self) -> None:
        """Lee la cuenta en XTB y la vuelca. Si falla, se dice en voz alta.

        Un fallo aislado no mata al vigilante —dejaría de mirar precios, que es
        peor—, pero queda en el latido y en el log como error; y si la cuenta
        lleva más de `MUERTO_MINUTOS` sin poder leerse, el vigilante se retira
        en rojo: algo serio pasa con XTB y hay que mirarlo.
        """
        if self.prueba:
            return
        try:
            self.foto_broker = estado_broker.volcar(
                self.broker, candado_ok=True, precios=dict(self.precios.bid))
            self.error_broker = None
            self.ultima_foto_ok = self.mono()
        except Exception as exc:  # noqa: BLE001 — se dice, y si dura, se muere
            self.error_broker = repr(exc)[:200]
            perdidos = (self.mono() - self.ultima_foto_ok) / 60.0
            print(f"::error::no se pudo leer la cuenta en XTB ({exc!r}); "
                  f"{perdidos:.1f} min sin foto.", flush=True)
            if perdidos > lat.MUERTO_MINUTOS:
                raise RuntimeError(
                    f"Llevo {perdidos:.0f} min sin poder leer la cuenta en XTB. "
                    f"No se puede vigilar lo que no se ve.") from exc

    # --- ventas por tiempo -------------------------------------------------
    def minutos_al_cierre(self) -> float:
        return (self.cierre - self.reloj()).total_seconds() / 60.0

    def toca_vender_por_tiempo(self) -> bool:
        if self.tiempos_hechos or self.prueba:
            return False
        lo, _hi = config.EJECUTOR_VENTAS_MIN_ANTES_CIERRE
        faltan = self.minutos_al_cierre()
        return lo <= faltan <= config.VIGILANTE_TIEMPO_MIN_ANTES_CIERRE

    def vender_tiempos(self) -> str:
        """Hace las salidas por tiempo de hoy y deja escrito qué pasó."""
        self.refrescar("antes de las ventas por tiempo")
        decididas = ventas_por_tiempo_de_hoy(self.hoy)
        hora = self.reloj().strftime("%H:%M ET")
        vendidas, ya, fallos = 0, 0, []
        for ticker, o in sorted(decididas.items()):
            v = next((x for x in self.vigiladas if x["ticker"] == ticker), None)
            if v is None:
                log(f"  {ticker}: sale hoy por tiempo y XTB no la tiene (la "
                    f"cerró otro, o nunca se compró). Nada que vender.")
                ya += 1
                continue
            r = vender_por_tiempo(self.broker, self.registro, v, o, self.hoy)
            if r == "vendida":
                vendidas += 1
            elif r == "ya-estaba":
                ya += 1
            else:
                fallos.append(f"{ticker}: {r}")
        self.tiempos_hechos = True
        self.ventas_tiempo = vendidas
        if not decididas:
            resultado, detalle = "ok: nada que vender", f"vigilante de precios, {hora}"
        elif fallos:
            resultado = f"fallo:venta-no-entro ({len(fallos)} de {len(decididas)})"
            detalle = " | ".join(fallos)
            for f in fallos:
                print(f"::error::venta por tiempo que NO entró: {f}", flush=True)
        else:
            resultado = f"ok: {vendidas} vendidas"
            detalle = (f"vigilante de precios, {hora}"
                       + (f"; {ya} ya estaban cerradas" if ya else ""))
        log(f"ventas por tiempo: {resultado} — {detalle}")
        salud.registrar("ventas", resultado, detalle)
        if vendidas:
            self.refrescar("tras las ventas por tiempo")
            self.fotografiar()
        return resultado

    # --- latido -----------------------------------------------------------
    def construir_latido(self, estado: str = lat.VIVO, motivo: str = "",
                         relevo: str | None = None) -> dict:
        datos = lat.construir(
            self.arrancado, self.vigiladas, estado=estado, motivo=motivo,
            relevo=relevo, historial=self.historial, broker=self.foto_broker,
            error_broker=self.error_broker, reinicios=self.reinicios)
        self.historial = datos["historial"]
        return datos

    def latir(self) -> None:
        self.fotografiar()
        datos = self.construir_latido()
        if en_prueba():
            latir(datos, self.trabajo)
        else:
            self.ultimo_latido_ok = lat.publicar_tolerante(
                datos, self.trabajo, self.ultimo_latido_ok, self.mono())
        self.proximo_latido = self.mono() + lat.CADA_SEGUNDOS

    # --- una vuelta --------------------------------------------------------
    def paso(self) -> None:
        vigilar_un_rato(self.broker, self.registro, self.vigiladas, segundos=1,
                        precios=self.precios)
        if VENDIDAS["n"] != getattr(self, "_vendidas_vistas", 0):
            self._vendidas_vistas = VENDIDAS["n"]
            self.refrescar("tras una venta por nivel")
            self.fotografiar()
        ahora = self.mono()
        if self.toca_vender_por_tiempo():
            self.vender_tiempos()
        if ahora >= self.proximo_refresco:
            self.refrescar("relectura periódica")
        mudo = ahora - self.precios.ultimo_tick > SILENCIO_SOSPECHOSO_SEG
        if (self.caido["si"] or mudo) and ahora >= self.proximo_respaldo:
            if mudo and not self.caido["si"]:
                log(f"sin ticks desde hace {ahora - self.precios.ultimo_tick:.0f} "
                    f"s; se pregunta por petición mientras tanto.")
            respaldo_por_peticion(self.broker, self.vigiladas, self.precios)
            self.proximo_respaldo = ahora + RESPALDO_SEGUNDOS
            if not self.caido["si"] and self.broker.conectado:
                suscribir_todo(self.broker, self.vigiladas)
                self.precios.ultimo_tick = ahora
        if ahora >= self.proximo_latido:
            self.latir()
        if ahora >= self.proximo_reintento:
            reintentar_publicacion()
            self.proximo_reintento = ahora + 60

    def sigue(self) -> bool:
        """¿Queda trabajo? Mientras la sesión esté abierta y haya algo que
        vigilar, o una venta por tiempo que todavía no tocaba."""
        if self.reloj() >= self.cierre:
            return False
        return bool(self.vigiladas)

    def terminar(self) -> str:
        """Último latido, en reposo y diciendo por qué."""
        self.fotografiar()
        quedan = len(self.vigiladas)
        motivo = ("la sesión cerró" if not quedan and self.reloj() >= self.cierre
                  else "no queda nada que vigilar" if not quedan
                  else f"la sesión cerró con {quedan} posición(es) abiertas")
        latir(self.construir_latido(estado=lat.EN_REPOSO, motivo=motivo),
              self.trabajo)
        return motivo

    def arrancar(self) -> None:
        self.refrescar()
        for v in self.vigiladas:
            log(f"  {v['ticker']} x{v['acciones']}: objetivo {v['objetivo']} | "
                f"stop {v['stop']}" + (" | SALE HOY por tiempo" if v.get("sale_hoy") else ""))
        respaldo_por_peticion(self.broker, self.vigiladas, self.precios)
        self.fotografiar()
        datos = self.construir_latido()
        latir(datos, self.trabajo)             # el primero sí es mortal
        self.proximo_latido = self.mono() + lat.CADA_SEGUNDOS


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--relevo-de", default="",
                    help="run del vigilante al que releva (informativo)")
    ap.add_argument("--forzar", action="store_true",
                    help="ignora la comprobación de sesión de mercado")
    ap.add_argument("--minutos", type=float, default=None,
                    help="vigilar solo este rato (pruebas)")
    ap.add_argument("--prueba", default="",
                    help="TICKER:OBJETIVO:STOP — vigila esa posición con esos "
                         "niveles en vez de los del estado. Para probar el "
                         "disparo de verdad sin tocar el estado del simulador.")
    ap.add_argument("--bitacora", default="",
                    help="ruta alternativa de bitacora_broker.csv (pruebas)")
    args = ap.parse_args()

    if args.bitacora:
        ords.ARCHIVO_BITACORA_BROKER = Path(args.bitacora)
        log(f"bitácora desviada a {args.bitacora} (modo prueba).")

    if not config.EJECUCION_BROKER:
        log("ejecución en broker DESACTIVADA en config. No se vigila nada.")
        return 0

    ahora = datetime.now(config.TZ_ET)
    hoy = ahora.date()
    arrancado = ahora.isoformat()
    trabajo = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "centinela-latido"

    def reposo(motivo: str, resultado: str) -> int:
        """Terminar bien, dejándolo dicho.

        Un vigilante que se va porque no hay trabajo tiene que decirlo: si no,
        un latido viejo envejece hasta que la página lo da por muerto.
        """
        log(motivo)
        latir(lat.construir(arrancado, [], estado=lat.EN_REPOSO, motivo=motivo),
             trabajo)
        if not en_prueba():
            salud.registrar("vigilante_precios", resultado)
        return 0

    if not args.forzar and not calendario.es_dia_de_mercado(hoy):
        return reposo("hoy no hay mercado", "omitido:no-es-sesion")

    ac = calendario.apertura_cierre_et(hoy.isoformat())
    if ac is None:
        return reposo("hoy no hay horario de mercado", "omitido:no-es-sesion")
    apertura, cierre = ac
    if args.minutos:
        cierre = min(cierre, ahora + timedelta(minutes=args.minutos))
    if not args.forzar and ahora >= cierre:
        return reposo("la sesión ya cerró", "omitido:fuera-de-sesion")

    # ANTES DE ABRIR NO SE VIGILA. XTB encola las órdenes de mercado con la
    # sesión cerrada y luego las descarta, así que un disparo en el pre-mercado
    # sería una venta que no ocurre. Si falta poco, se espera; si falta mucho,
    # se va en reposo y lo arranca el job de compras, que corre tras abrir.
    if not args.forzar and ahora < apertura:
        faltan = (apertura - ahora).total_seconds() / 60.0
        if faltan > config.VIGILANTE_ESPERA_APERTURA_MAX_MIN:
            return reposo(f"faltan {faltan:.0f} min para la apertura; lo "
                          f"arrancará el job de compras tras abrir",
                          "omitido:antes-de-apertura")
        log(f"faltan {faltan:.0f} min para la apertura: se espera.")
        while datetime.now(config.TZ_ET) < apertura:
            lat.tocar(trabajo)              # vivo, para el supervisor
            time.sleep(min(60.0, max(1.0, (apertura - datetime.now(
                config.TZ_ET)).total_seconds())))

    mi_run = os.environ.get("GITHUB_RUN_ID", "local")
    credenciales = bx.credenciales_del_entorno_o_llavero()
    VENDIDAS["n"] = 0

    with bx.BrokerXTB(credenciales, demo=True) as broker:
        saldo = broker.saldo()
        log(f"cuenta DEMO verificada: equity {saldo['equity']:,.2f}")
        registro = {"enviadas": {}} if args.prueba else ords.cargar_enviadas()
        anterior = None if args.prueba else lat.leer(trabajo)
        sesion = Sesion(broker, registro, trabajo=trabajo, arrancado=arrancado,
                        cierre=cierre, mi_run=mi_run, prueba=args.prueba,
                        historial=lat.historial_de_hoy(anterior, hoy.isoformat()))
        broker.al_recibir_tick(sesion.precios.encajar)
        broker.al_perder_conexion(
            lambda *_a: (sesion.caido.__setitem__("si", True),
                         log("WebSocket caído; el cliente reconecta solo y "
                             "mientras tanto se pregunta por petición.")))
        broker.al_recuperar_conexion(
            lambda *_a: (sesion.caido.__setitem__("si", False),
                         log("WebSocket recuperado.")))

        if args.prueba:
            vigiladas_de_prueba(broker, args.prueba)   # revienta si no está
        sesion.refrescar()
        if not sesion.vigiladas:
            sesion.fotografiar()
            return reposo("no hay ninguna posición que vigilar (ni con nivel ni "
                          "con salida por tiempo hoy)",
                          "ok: 0 posiciones — nada que vigilar")
        sesion.arrancar()
        n_inicial = len(sesion.vigiladas)

        while sesion.sigue():
            sesion.paso()
            if sesion.mono() >= sesion.relevo_a_las:
                log("5 h 45 min: es hora del relevo.")
                if lanzar_sucesor() and esperar_sucesor(
                        trabajo, mi_run, broker, registro, sesion.vigiladas,
                        arrancado, construir=lambda: sesion.construir_latido(
                            estado=lat.ESPERANDO_RELEVO, relevo=mi_run)):
                    salud.registrar(
                        "vigilante_precios",
                        f"ok: relevado tras {RELEVO_TRAS_HORAS} h, "
                        f"{VENDIDAS['n']} venta(s) por nivel, "
                        f"{sesion.ventas_tiempo} por tiempo")
                    return 0
                raise RuntimeError(
                    "El relevo falló: el sucesor no llegó a latir en "
                    f"{ESPERA_SUCESOR_SEG // 60} minutos. Este vigilante se va "
                    f"a morir por el límite de 6 h de GitHub y no hay quien "
                    f"mire los precios.")

        sesion.terminar()
        quedan = len(sesion.vigiladas)

    cortes = lat.huecos(sesion.historial)
    resultado = (f"ok: {VENDIDAS['n']} venta(s) por nivel, "
                 f"{sesion.ventas_tiempo} por tiempo, {quedan} de {n_inicial} "
                 f"posición(es) siguen abiertas"
                 + (f"; {len(cortes)} corte(s) en el latido" if cortes else ""))
    if not en_prueba():
        salud.registrar("vigilante_precios", resultado,
                        "; ".join(f"{c['minutos']} min sin latir desde "
                                  f"{c['desde'][11:16]}" for c in cortes))
    log(f"✅ {resultado}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
