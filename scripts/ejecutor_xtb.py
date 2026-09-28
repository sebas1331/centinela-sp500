#!/usr/bin/env python
"""Ejecutor: manda a XTB las órdenes que el sistema decidió. Corre en el Mac.

Por qué en el Mac y no en GitHub Actions: las credenciales de XTB abren TAMBIÉN
la cuenta real —XTB no da credenciales separadas para la demo— así que ponerlas
en los secrets de un repositorio remoto era un riesgo que no compensaba. Aquí
viven en el Llavero de macOS y no salen del ordenador. Además, el login de
xStation5 pasa por un WAF que bloquea el acceso automatizado, y las IPs de
datacenter de GitHub son justo lo que ese WAF existe para frenar.

TRES MOMENTOS AL DÍA
--------------------
  compras      antes de la apertura. XTB deja la orden de mercado EN COLA y la
               ejecuta al abrir, que es exactamente lo que simula la estrategia.
  ventas       antes del cierre, para las salidas por tiempo del día 10. Es el
               momento que menos altera la estrategia: 0,03 pp de desviación
               frente a los 0,40 (±3,14) de cerrar al día siguiente.
  reconcilia   después del cierre. Compara XTB con el estado del simulador y
               ROMPE EN ROJO ante cualquier diferencia.

CADA EJECUCIÓN EMPIEZA POR EL CANDADO
-------------------------------------
`broker_xtb.BrokerXTB` verifica en cada conexión que la cuenta es demo, por
endpoint y por número. Si no puede determinarlo, no manda nada y sale en rojo.

IDEMPOTENCIA
------------
launchd dispara dos horarios por ejecución (verano y invierno: no entiende de
husos), el Mac puede despertar dos veces y alguien puede lanzarlo a mano. Cada
orden lleva un id determinista registrado en `estado/ordenes_enviadas.json`; la
segunda vez no se manda nada.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (config, calendario, broker_xtb as bx,  # noqa: E402
                       ordenes as ords, estado as est_mod)

MOMENTOS = ("compras", "ventas", "reconcilia")


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%Y-%m-%d %H:%M:%S ET}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Git: el Mac es un colaborador más del repositorio
# --------------------------------------------------------------------------- #
def _git(*args: str, permitir_fallo: bool = False) -> str:
    r = subprocess.run(["git", *args], cwd=config.BASE_DIR,
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0 and not permitir_fallo:
        raise RuntimeError(
            f"git {' '.join(args)} falló ({r.returncode}):\n{r.stderr.strip()}")
    return r.stdout.strip()


def traer_ordenes() -> None:
    """`git pull` antes de leer nada. Las órdenes las escribió GitHub."""
    log("actualizando el repositorio...")
    _git("fetch", "origin", "main")
    _git("reset", "--hard", "origin/main")


def publicar(mensaje: str, rutas: list[str]) -> bool:
    """Commit y push de lo que el ejecutor escribió. True si hubo algo."""
    _git("add", "--", *rutas)
    if not _git("diff", "--cached", "--name-only"):
        log("sin cambios que publicar.")
        return False
    _git("-c", "user.name=centinela-mac",
         "-c", "user.email=actions@users.noreply.github.com",
         "commit", "-m", mensaje)
    _git("push", "origin", "HEAD:main")
    log(f"publicado: {mensaje}")
    return True


# --------------------------------------------------------------------------- #
# Ventanas
# --------------------------------------------------------------------------- #
def en_ventana(momento: str, ahora=None) -> tuple[bool, str]:
    """¿Toca trabajar ahora? Devuelve (sí/no, explicación).

    launchd dispara a dos horas fijas por momento para cubrir EDT y EST; el que
    caiga fuera de su ventana se calla y deja el turno al otro. Igual que la
    escalera de crons de los escaneos, y por la misma razón.
    """
    ahora = ahora or datetime.now(config.TZ_ET)
    hoy = ahora.date()
    if not calendario.es_dia_de_mercado(hoy):
        return False, "hoy no hay mercado"

    ac = calendario.apertura_cierre_et(hoy.isoformat())
    if ac is None:
        return False, "sin horario de mercado para hoy"
    apertura, cierre = ac

    if momento == "compras":
        faltan = (apertura - ahora).total_seconds() / 60
        lo, hi = config.EJECUTOR_COMPRAS_MIN_ANTES_APERTURA
        if faltan < lo:
            return False, (f"faltan {faltan:.0f} min para la apertura: menos de "
                           f"{lo}. Una compra ahora ya no sería al open.")
        if faltan > hi:
            return False, f"faltan {faltan:.0f} min para la apertura: más de {hi}"
        return True, f"faltan {faltan:.0f} min para la apertura"

    if momento == "ventas":
        faltan = (cierre - ahora).total_seconds() / 60
        lo, hi = config.EJECUTOR_VENTAS_MIN_ANTES_CIERRE
        if faltan < lo:
            return False, (f"faltan {faltan:.0f} min para el cierre: menos de "
                           f"{lo}, la venta ya no entraría a tiempo")
        if faltan > hi:
            return False, f"faltan {faltan:.0f} min para el cierre: más de {hi}"
        return True, f"faltan {faltan:.0f} min para el cierre"

    pasados = (ahora - cierre).total_seconds() / 60
    if pasados < config.EJECUTOR_RECONCILIA_MIN_DESPUES_CIERRE:
        return False, f"solo han pasado {pasados:.0f} min del cierre"
    return True, f"{pasados:.0f} min tras el cierre"


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #
def enviar(broker: bx.BrokerXTB, pendientes: dict, momento: str,
           registro: dict) -> list:
    """Manda las órdenes del momento que toca. Devuelve las ejecuciones."""
    tipo = ords.COMPRA if momento == "compras" else ords.VENTA_TIEMPO
    hoy = datetime.now(config.TZ_ET).date().isoformat()
    cola = [o for o in pendientes["ordenes"]
            if o.tipo == tipo and o.sesion == hoy]
    if not cola:
        log(f"no hay órdenes de tipo {tipo} para {hoy}.")
        return []

    hechas = []
    for o in cola:
        if ords.ya_enviada(registro, o.id):
            log(f"  {o.id}: ya enviada, se omite.")
            continue
        log(f"  enviando {o.tipo} {o.ticker} x{o.acciones}...")
        if o.tipo == ords.COMPRA:
            e = broker.comprar(o.simbolo, o.acciones,
                               objetivo=o.objetivo, stop=o.stop)
        else:
            e = broker.vender(o.simbolo, o.acciones)
        log(f"    -> {e.estado}"
            + (f" a {e.precio}" if e.precio else "")
            + (f" (orden {e.orden})" if e.orden else "")
            + (f" ERROR: {e.error}" if e.error else ""))
        ords.registrar_ejecucion(o, e)
        if e.ok:
            ords.marcar_enviada(registro, o.id,
                                {"estado": e.estado, "orden": e.orden})
        hechas.append((o, e))

    fallidas = [(o, e) for o, e in hechas if not e.ok]
    if fallidas:
        raise RuntimeError(
            "Órdenes que NO llegaron al broker:\n" + "\n".join(
                f"  {o.id}: {e.estado} — {e.error}" for o, e in fallidas))
    return hechas


# --------------------------------------------------------------------------- #
# Vigilancia de niveles: el stop y el objetivo los lleva el ejecutor
# --------------------------------------------------------------------------- #
def vigilar_niveles(broker: bx.BrokerXTB, registro: dict) -> list:
    """Cierra las posiciones cuyo precio cruzó el stop o el objetivo.

    POR QUÉ ESTO EXISTE, Y LO QUE CUESTA
    ------------------------------------
    XTB ignora el stop loss y el take profit en acciones al contado: se
    comprobó con una orden real el 2026-09-28 —se compró 1 acción de F.US
    pasando stop y objetivo, y la posición apareció con los dos a None, sin
    ningún error— así que los niveles tiene que vigilarlos alguien, y ese
    alguien es este ejecutor.

    El coste está MEDIDO sobre la bitácora, y no es pequeño. El ejecutor solo
    puede vender con el mercado abierto, así que en la práctica tiene UNA
    ventana al día (15:45 ET). Con esa frecuencia, la Cartera A pasa de
    +12,07% con drawdown -10,44% a **+9,32% con drawdown -12,50%**: pierde
    rentabilidad Y gana riesgo. De las 21 operaciones que el simulador cerró
    por stop, 7 no se habrían cerrado ese día porque el precio tocó el nivel
    intradía pero cerró por encima.

    Para comparar: la Cartera B, que no tiene stop por diseño, hizo +11,73% con
    drawdown -12,16%. Es decir, el stop vigilado una vez al día sale peor que
    no tener stop en las dos dimensiones. Se implementa igualmente porque es
    una decisión tomada a la vista de estos números, no a pesar de ellos.

    La regla es la CONSERVADORA del simulador: si un mismo vistazo ve el precio
    por debajo del stop y por encima del objetivo (no puede pasar, pero el
    orden importa), gana el stop.
    """
    estado = est_mod.cargar()
    cartera = config.CARTERA_BROKER
    posiciones = estado.get("posiciones", {}).get(cartera, [])
    if not posiciones:
        return []

    reales = {p["ticker"].replace(".US", "").replace("-", "."): p
              for p in broker.posiciones() if p["lado"] == "buy"}
    hoy = datetime.now(config.TZ_ET).date().isoformat()
    hechas = []

    for pos in posiciones:
        if str(pos.get("fecha_entrada", "")) < config.EJECUCION_DESDE:
            continue                      # heredada: no existe en el broker
        real = reales.get(pos["ticker"])
        if real is None:
            continue                      # lo verá la reconciliación
        precio = real.get("precio_actual") or 0.0
        if precio <= 0:
            log(f"  {pos['ticker']}: sin precio actual; no se puede vigilar su "
                f"nivel en esta pasada.")
            continue

        stop = pos.get("stop")
        objetivo = pos.get("objetivo")
        if stop is not None and precio <= float(stop):
            motivo, nivel = ords.VENTA_STOP, float(stop)
        elif objetivo is not None and precio >= float(objetivo):
            motivo, nivel = ords.VENTA_OBJETIVO, float(objetivo)
        else:
            continue

        acciones = int(real["acciones"])
        if acciones < 1:
            continue
        orden = ords.Orden(
            id=ords.identificador(hoy, cartera, pos["ticker"], motivo),
            tipo=motivo, cartera=cartera, ticker=pos["ticker"],
            acciones=acciones, sesion=hoy, precio_simulador=nivel,
            id_operacion=int(pos["id"]))
        if ords.ya_enviada(registro, orden.id):
            continue

        log(f"  {pos['ticker']}: precio {precio:.2f} cruzó el "
            f"{'STOP' if motivo == ords.VENTA_STOP else 'OBJETIVO'} "
            f"{nivel:.2f} -> cerrando")
        e = broker.vender(orden.simbolo, acciones)
        log(f"    -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
            + (f" ERROR: {e.error}" if e.error else ""))
        ords.registrar_ejecucion(orden, e)
        if e.ok:
            ords.marcar_enviada(registro, orden.id,
                                {"estado": e.estado, "orden": e.orden})
        hechas.append((orden, e))

    fallidas = [(o, e) for o, e in hechas if not e.ok]
    if fallidas:
        raise RuntimeError(
            "Cierres por nivel que NO llegaron al broker:\n" + "\n".join(
                f"  {o.id}: {e.estado} — {e.error}" for o, e in fallidas))
    return hechas


# --------------------------------------------------------------------------- #
# Reconciliación
# --------------------------------------------------------------------------- #
def reconciliar(broker: bx.BrokerXTB) -> list[str]:
    """Compara XTB con el estado del simulador. Devuelve las diferencias.

    Cualquier diferencia es un fallo: o el broker tiene algo que el simulador no
    sabe, o al revés, y en las dos direcciones significa que las cifras que se
    publican dejaron de describir lo que hay en el mercado.

    La única diferencia que se tolera es el NÚMERO DE ACCIONES, y solo a la
    baja: XTB no admite fracciones, así que el ejecutor compra menos de lo que
    el simulador asigna. Eso está medido y es el precio de operar de verdad.
    """
    estado = est_mod.cargar()
    cartera = config.CARTERA_BROKER
    posiciones = estado.get("posiciones", {}).get(cartera, [])

    # Las posiciones anteriores a que existiera el ejecutor nunca se compraron
    # en XTB. Denunciarlas sería un rojo diario por algo que no es un fallo, y
    # un rojo que sale siempre enseña a ignorar los rojos.
    heredadas = {p["ticker"] for p in posiciones
                 if str(p.get("fecha_entrada", "")) < config.EJECUCION_DESDE}
    simuladas = {p["ticker"] for p in posiciones} - heredadas
    reales = {p["ticker"].replace(".US", "").replace("-", ".")
              for p in broker.posiciones() if p["lado"] == "buy"}

    if heredadas:
        log(f"posiciones heredadas del paper trading (anteriores a "
            f"{config.EJECUCION_DESDE}, no se exigen en XTB): "
            f"{', '.join(sorted(heredadas))}")

    problemas = []
    for t in sorted(simuladas - reales):
        problemas.append(
            f"{t}: abierta en el simulador (cartera {cartera}) y NO en XTB. "
            f"O la compra no llegó, o se cerró en el broker por su cuenta "
            f"(¿saltó el stop o el take profit?) y el simulador no se enteró.")
    for t in sorted(reales - simuladas):
        problemas.append(
            f"{t}: abierta en XTB y NO en el simulador. Hay dinero expuesto que "
            f"el sistema no está siguiendo.")
    return problemas


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("momento", choices=MOMENTOS)
    ap.add_argument("--forzar", action="store_true",
                    help="ignora la comprobación de ventana")
    ap.add_argument("--sin-git", action="store_true",
                    help="no hace pull ni push (pruebas locales)")
    args = ap.parse_args()

    if not config.EJECUCION_BROKER:
        log("ejecución en broker DESACTIVADA en config. No se hace nada.")
        return 0

    ok, motivo = (True, "forzado") if args.forzar else en_ventana(args.momento)
    log(f"momento={args.momento}: {motivo}")
    if not ok:
        return 0

    if not args.sin_git:
        traer_ordenes()

    pendientes = ords.cargar_pendientes()
    registro = ords.cargar_enviadas()
    credenciales = bx.credenciales_del_entorno_o_llavero()

    problemas: list[str] = []
    try:
        with bx.BrokerXTB(credenciales, demo=True) as broker:
            saldo = broker.saldo()
            log(f"cuenta {saldo['cuenta']} (DEMO): saldo {saldo['saldo']:,.2f} "
                f"{saldo['divisa']} | equity {saldo['equity']:,.2f}")

            if args.momento in ("compras", "ventas"):
                enviar(broker, pendientes, args.momento, registro)
            # El stop y el objetivo los lleva el ejecutor porque XTB no los
            # acepta en acciones al contado. Se miran en las dos ventanas con
            # mercado abierto; la de después del cierre no sirve para vender.
            if args.momento == "ventas":
                log("vigilando stops y objetivos...")
                vigilar_niveles(broker, registro)
            problemas = reconciliar(broker)
    finally:
        # El registro se guarda PASE LO QUE PASE: si la orden número tres
        # revienta, las dos que sí salieron tienen que quedar marcadas o el
        # siguiente disparo las repetiría.
        ords.guardar_enviadas(registro)
        if not args.sin_git:
            publicar(f"ejecutor XTB: {args.momento} [skip ci]",
                     ["estado", "bitacora_broker.csv", "ordenes"])

    if problemas:
        for p in problemas:
            print(f"::error::{p}", flush=True)
        log(f"❌ {len(problemas)} diferencia(s) entre XTB y el simulador.")
        return 1

    log("✅ sin diferencias entre XTB y el simulador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
