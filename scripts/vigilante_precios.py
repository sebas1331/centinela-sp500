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

LO QUE NO HACE
--------------
No decide nada. Los niveles son los que el simulador calculó la víspera y se
leen del estado tal cual. No los recalcula, no los ajusta y no inventa ninguno.

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
                       broker_xtb as bx, latido as lat,
                       niveles as niv, ordenes as ords, estado as est_mod,
                       salud)

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
def _niveles_de_las_compras_de_hoy(ya_conocidas: dict) -> dict:
    """Objetivo y stop de lo comprado hoy, leídos de la bitácora del broker.

    Solo para lo que el estado del simulador todavía no conoce: si la posición
    ya está ahí, manda el estado, que es la fuente.
    """
    hoy = datetime.now(config.TZ_ET).date().isoformat()
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


def cargar_vigiladas(broker) -> list[dict]:
    """Las posiciones que XTB tiene de verdad, con sus niveles de la víspera.

    Se cruzan dos fuentes porque ninguna lo sabe todo: XTB conoce el símbolo y
    el volumen; el simulador conoce el objetivo vigente y el stop. Solo se
    vigila lo que está en las dos.

    Las heredadas del paper trading (anteriores a EJECUCION_DESDE) se quedan
    fuera: nunca existieron en el broker.
    """
    estado = est_mod.cargar()
    cartera = config.CARTERA_BROKER
    simuladas = {p["ticker"]: p
                 for p in estado.get("posiciones", {}).get(cartera, [])
                 if str(p.get("fecha_entrada", "")) >= config.EJECUCION_DESDE}

    # LAS COMPRAS DE HOY TODAVÍA NO SON POSICIONES. El simulador las mueve de
    # `entradas_pendientes` a `posiciones` en el post-cierre, así que entre la
    # compra de la mañana y el cierre no tienen niveles en el estado — y el
    # vigilante decía "no hay nada que vigilar" con la posición recién abierta.
    # Era un agujero de una sesión entera, justo el día en que la posición está
    # más lejos de su precio de entrada.
    #
    # Los niveles SÍ existen: se decidieron al generar la orden y están en la
    # bitácora del broker. De ahí se leen.
    simuladas.update(_niveles_de_las_compras_de_hoy(simuladas))

    vigiladas = []
    for real in broker.posiciones():
        if real["lado"] != "buy":
            continue
        ticker = real["ticker"].replace(".US", "").replace("-", ".")
        sim = simuladas.get(ticker)
        if sim is None:
            continue
        if sim.get("stop") is None and sim.get("objetivo") is None:
            continue                       # nada que vigilar
        vigiladas.append({
            "ticker": ticker,
            "simbolo": real["ticker"],
            "acciones": int(real["acciones"]),
            "objetivo": sim.get("objetivo"),
            "stop": sim.get("stop"),
            "id_operacion": sim.get("id"),
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
            "id_operacion": None, "bid": None,
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
        id_orden=id_orden)
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


def publicar_venta(ticker: str, tipo: str) -> None:
    """Commit y push de la venta, para que el resto del sistema se entere.

    Importa que sea inmediato y no al final: el ejecutor de ventas por tiempo
    hace `git pull` antes de trabajar, y si esta venta no está publicada cuando
    él mire, intentará vender unas acciones que ya no existen.
    """
    for orden in (("add", "--", "bitacora_broker.csv", "ordenes"),
                  ("-c", "user.name=centinela-bot",
                   "-c", "user.email=actions@users.noreply.github.com",
                   "commit", "-q", "-m",
                   f"vigilante de precios: {tipo} de {ticker} [skip ci]")):
        r = subprocess.run(["git", *orden], cwd=str(config.BASE_DIR),
                           capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            raise RuntimeError(f"git {' '.join(orden)}: "
                               f"{(r.stderr or r.stdout).strip()[:200]}")
    # El push se reintenta rebasando: otro workflow puede haber escrito mientras.
    for intento in (1, 2, 3):
        r = subprocess.run(["git", "push", "origin", "HEAD:main"],
                           cwd=str(config.BASE_DIR), capture_output=True, text=True)
        if r.returncode == 0:
            log(f"    venta publicada en el repositorio.")
            return
        log(f"    push rechazado (intento {intento}/3); rebase y reintento.")
        subprocess.run(["git", "fetch", "origin", "main"],
                       cwd=str(config.BASE_DIR), capture_output=True, text=True)
        subprocess.run(["git", "rebase", "origin/main"],
                       cwd=str(config.BASE_DIR), capture_output=True, text=True)
    raise RuntimeError(
        "La venta se ejecutó en XTB pero NO se pudo publicar en el "
        "repositorio tras 3 intentos. El resto del sistema no lo sabe.")


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
                    vigiladas: list[dict], arrancado: str) -> bool:
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
        lat.publicar(lat.construir(arrancado, vigiladas, estado=lat.ESPERANDO_RELEVO,
                                   relevo=mi_run), trabajo)
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
            quedan.append(v)
            continue
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

        Antes estas salidas no publicaban nada, y un latido viejo de la sesión
        anterior se quedaba ahí envejeciendo hasta que la página lo daba por
        muerto: "lleva 62 min sin latir" con la cuenta vacía y nada que vigilar.
        Un vigilante que se va porque no hay trabajo tiene que decirlo.
        """
        log(motivo)
        latir(lat.construir(arrancado, [], estado=lat.EN_REPOSO, motivo=motivo),
             trabajo)
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

    mi_run = os.environ.get("GITHUB_RUN_ID", "local")
    relevo_a_las = time.monotonic() + RELEVO_TRAS_HORAS * 3600
    credenciales = bx.credenciales_del_entorno_o_llavero()
    VENDIDAS["n"] = 0

    with bx.BrokerXTB(credenciales, demo=True) as broker:
        saldo = broker.saldo()
        log(f"cuenta {saldo['cuenta']} (DEMO): equity {saldo['equity']:,.2f}")

        if args.prueba:
            vigiladas = vigiladas_de_prueba(broker, args.prueba)
        else:
            vigiladas = cargar_vigiladas(broker)
        registro = {"enviadas": {}} if args.prueba else ords.cargar_enviadas()
        if not vigiladas:
            return reposo("no hay ninguna posición con nivel que vigilar",
                          "ok: 0 posiciones — ninguna con objetivo ni stop")

        for v in vigiladas:
            log(f"  {v['ticker']} x{v['acciones']}: objetivo {v['objetivo']} | "
                f"stop {v['stop']}")

        precios = Precios()
        broker._precios = precios          # lo lee vigilar_un_rato
        caido = {"si": False}

        broker.al_recibir_tick(precios.encajar)
        broker.al_perder_conexion(
            lambda *_a: (caido.__setitem__("si", True),
                         log("WebSocket caído; el cliente reconecta solo y "
                             "mientras tanto se pregunta por petición.")))
        broker.al_recuperar_conexion(
            lambda *_a: (caido.__setitem__("si", False),
                         log("WebSocket recuperado.")))

        suscribir_todo(broker, vigiladas)
        respaldo_por_peticion(broker, vigiladas, precios)   # foto inicial
        latir(lat.construir(arrancado, vigiladas), trabajo)

        ultimo_latido_ok = time.monotonic()
        proximo_latido = time.monotonic() + lat.CADA_SEGUNDOS
        proximo_respaldo = time.monotonic() + RESPALDO_SEGUNDOS
        n_inicial = len(vigiladas)

        while datetime.now(config.TZ_ET) < cierre and vigiladas:
            vigilar_un_rato(broker, registro, vigiladas, segundos=1,
                            precios=precios)
            ahora_mono = time.monotonic()
            mudo = ahora_mono - precios.ultimo_tick > SILENCIO_SOSPECHOSO_SEG
            if (caido["si"] or mudo) and ahora_mono >= proximo_respaldo:
                if mudo and not caido["si"]:
                    log(f"sin ticks desde hace "
                        f"{ahora_mono - precios.ultimo_tick:.0f} s; se pregunta "
                        f"por petición mientras tanto.")
                respaldo_por_peticion(broker, vigiladas, precios)
                proximo_respaldo = ahora_mono + RESPALDO_SEGUNDOS
                if not caido["si"] and broker.conectado:
                    suscribir_todo(broker, vigiladas)
                    precios.ultimo_tick = ahora_mono

            if ahora_mono >= proximo_latido:
                if en_prueba():
                    latir(lat.construir(arrancado, vigiladas), trabajo)
                else:
                    ultimo_latido_ok = lat.publicar_tolerante(
                        lat.construir(arrancado, vigiladas), trabajo,
                        ultimo_latido_ok, ahora_mono)
                proximo_latido = ahora_mono + lat.CADA_SEGUNDOS

            if ahora_mono >= relevo_a_las:
                log("5 h 45 min: es hora del relevo.")
                if lanzar_sucesor() and esperar_sucesor(
                        trabajo, mi_run, broker, registro, vigiladas, arrancado):
                    salud.registrar(
                        "vigilante_precios",
                        f"ok: relevado tras {RELEVO_TRAS_HORAS} h, "
                        f"{VENDIDAS['n']} venta(s)")
                    return 0
                raise RuntimeError(
                    "El relevo falló: el sucesor no llegó a latir en "
                    f"{ESPERA_SUCESOR_SEG // 60} minutos. Este vigilante se va "
                    f"a morir por el límite de 6 h de GitHub y no hay quien "
                    f"mire los precios.")

        motivo_final = ("la sesión cerró" if not vigiladas
                        else "la sesión cerró con "
                             f"{len(vigiladas)} posición(es) todavía abiertas")
        latir(lat.construir(arrancado, vigiladas, estado=lat.EN_REPOSO,
                            motivo=motivo_final), trabajo)

    resultado = (f"ok: {VENDIDAS['n']} venta(s) por nivel, {len(vigiladas)} de "
                 f"{n_inicial} posición(es) siguen abiertas")
    salud.registrar("vigilante_precios", resultado)
    log(f"✅ {resultado}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
