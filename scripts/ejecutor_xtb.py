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
import json
import os
import subprocess
import sys
import time

import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (config, calendario, broker_xtb as bx, cuenta,  # noqa: E402
                       ordenes as ords, estado as est_mod, salud)

MOMENTOS = ("compras", "ventas", "apertura", "reconcilia")

#: Cada momento del ejecutor deja su huella en el componente que le toca de
#: `salud.COMPONENTES`, que es lo que la página de operativa pinta.
_COMPONENTE = {"compras": "compras", "ventas": "ventas", "apertura": "apertura",
               "reconcilia": "reconcilia"}


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

    if momento == "apertura":
        pasados = (ahora - apertura).total_seconds() / 60
        lo, hi = config.VERIFICACION_MIN_TRAS_APERTURA
        if pasados < lo:
            return False, (f"solo han pasado {pasados:.0f} min de la apertura: "
                           f"menos de {lo}, el precio todavía se mueve solo")
        if pasados > hi:
            return False, (f"han pasado {pasados:.0f} min de la apertura: más "
                           f"de {hi}, comparar con el open ya no mide nada")
        return True, f"{pasados:.0f} min tras la apertura"

    pasados = (ahora - cierre).total_seconds() / 60
    if pasados < config.EJECUTOR_RECONCILIA_MIN_DESPUES_CIERRE:
        return False, f"solo han pasado {pasados:.0f} min del cierre"
    return True, f"{pasados:.0f} min tras el cierre"


#: Recuento del último `enviar()`, por tipo de orden. Global a propósito: lo
#: escribe el envío y lo lee el resumen final, y pasarlo a mano por media
#: docena de firmas solo para eso haría el código peor.
ENVIADAS: dict[str, dict] = {}

#: Cuánto se espera entre intentos a que aparezca la decisión del día.
ESPERA_DECISION_SEG = 30


class SinDecision(RuntimeError):
    """La decisión del día no apareció dentro de la ventana."""


def decision_del_dia(momento: str, sin_git: bool = False,
                     ahora=None, dormir=time.sleep) -> dict:
    """Las órdenes de hoy, esperando a que quien decide haya escrito.

    POR QUÉ ESPERAR Y NO DAR "ok" (fallo del 2026-09-29)
    -----------------------------------------------------
    Un ejecutor que no encuentra la decisión del día no sabe distinguir dos
    cosas que se parecen mucho en un log y no se parecen en nada de verdad:
    que no hubiera nada que comprar, y que él haya llegado antes que quien lo
    decide. Decir "ok" en el segundo caso es contar una sesión perdida como
    una sesión tranquila.

    Ese día los jobs `ordenes` y `ejecutor` arrancaron EL MISMO SEGUNDO
    (12:45:53) porque los dos colgaban solo del escaneo; el ejecutor hizo
    checkout a las 12:45:55 y las órdenes se empujaron a las 12:46:18. Leyó
    una copia rancia. No se notó porque aquel día había 0 órdenes de todas
    formas, pero con decisiones habría enviado nada y terminado en verde.

    El arreglo de fondo es el `needs: ordenes` del workflow, que ordena los dos
    jobs. Esto es el cinturón por si alguien lanza el ejecutor a mano, cambia el
    workflow, o el push de las órdenes tarda: se relee el repositorio hasta que
    la decisión sea la de hoy, y si la ventana se agota, ROJO.
    """
    ahora = ahora or datetime.now(config.TZ_ET)
    hoy = ahora.date().isoformat()
    intento = 0
    while True:
        intento += 1
        if not sin_git:
            traer_ordenes()
        pendientes = ords.cargar_pendientes()
        if pendientes.get("sesion") == hoy:
            if intento > 1:
                log(f"la decisión de {hoy} apareció en el intento {intento}.")
            return pendientes

        vigente, motivo = en_ventana(momento)
        if not vigente:
            raise SinDecision(
                f"La decisión de {hoy} no apareció y la ventana se agotó "
                f"({motivo}). El fichero de órdenes va por la sesión "
                f"{pendientes.get('sesion')!r}. Nadie ha enviado nada y NO se "
                f"puede dar por buena la sesión: revisa si el escaneo de hoy "
                f"llegó a decidir y si su job de órdenes terminó.")
        log(f"la decisión de {hoy} todavía no está (el fichero va por "
            f"{pendientes.get('sesion')!r}); reintento en "
            f"{ESPERA_DECISION_SEG} s — {motivo}.")
        dormir(ESPERA_DECISION_SEG)


def resultado_explicito(momento: str, motivo_cero: str = "") -> str:
    """El veredicto del ejecutor en una línea, nunca un "ok" a secas.

    Un "ok" sin número no distingue "no había nada que hacer" de "no hice lo
    que había que hacer", que es justo la diferencia que hay que poder leer de
    un vistazo en la página.
    """
    if momento == "apertura":
        r = ENVIADAS.get("apertura")
        if r is None:
            return "ok"
        if r["filas"] == 0:
            return (f"ok: 0 órdenes que verificar — "
                    f"{motivo_cero or 'no se envió ninguna hoy'}")
        return (f"ok: {r['ejecutadas']} de {r['filas']} órdenes ejecutadas"
                + (f", {r['sin_niveles']} sin stop/objetivo en XTB"
                   if r["sin_niveles"] else ""))

    tipo = {"compras": ords.COMPRA, "ventas": ords.VENTA_TIEMPO}.get(momento)
    if tipo is None:
        return "ok"
    r = ENVIADAS.get(tipo)
    if r is None:
        return "ok: no se llegó a mirar la cola de órdenes"
    cubiertas = r["enviadas"] + r["ya_estaban"]
    if r["decididas"] == 0:
        return f"ok: 0 órdenes — {motivo_cero or 'no se decidió ninguna entrada'}"
    detalle = f"ok: {cubiertas} de {r['decididas']} órdenes"
    if r["ya_estaban"]:
        detalle += f" ({r['ya_estaban']} ya estaban enviadas)"
    return detalle


def motivo_de_cero(momento: str) -> str:
    """Por qué no había nada que enviar, con nombres y apellidos.

    Se lee del log de decisiones del día, que es quien lo sabe. Sin esto la
    página decía "0 órdenes" y punto, y averiguar el porqué obligaba a abrir
    Actions y leer 187 líneas.
    """
    if momento == "apertura":
        return "no se envió ninguna orden hoy"
    if momento != "compras":
        return "no había salidas por tiempo para hoy"
    hoy = datetime.now(config.TZ_ET).date().isoformat()
    ruta = config.LOGS_DIR / f"decisiones-{hoy}.log"
    if not ruta.exists():
        return "no hay log de decisiones de hoy"
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "no se pudo leer el log de decisiones"

    senales = sum(1 for ln in lineas if "| ENTRAR |" in ln)
    descartes = [ln.split("ENTRADA DESCARTADA:")[-1].strip()
                 for ln in lineas if "ENTRADA DESCARTADA" in ln]
    if senales == 0:
        return "el modelo no dio ninguna señal hoy"
    if descartes:
        tickers = [ln.split("|")[0].strip() for ln in lineas
                   if "ENTRADA DESCARTADA" in ln]
        return (f"{senales} señal(es) descartada(s): "
                + "; ".join(f"{t} — {d}" for t, d in zip(tickers, descartes))[:200])
    return f"{senales} señal(es), ninguna llegó a orden"


def publicar_resultado(resultado: str) -> None:
    """Deja el veredicto donde el workflow pueda recogerlo."""
    salida = os.environ.get("GITHUB_OUTPUT")
    if not salida:
        return
    with open(salida, "a", encoding="utf-8") as fh:
        fh.write(f"resultado={resultado}\n")


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
        ENVIADAS[tipo] = {"decididas": 0, "enviadas": 0, "ya_estaban": 0}
        return []
    log(f"{len(cola)} orden(es) de tipo {tipo} para {hoy}.")

    hechas, ya_estaban = [], 0
    for o in cola:
        if ords.ya_enviada(registro, o.id):
            log(f"  {o.id}: ya enviada, se omite.")
            ya_estaban += 1
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

    # El recuento es lo que después permite decir "ok: 3 órdenes enviadas" en
    # vez de un "ok" a secas, y detectar que se decidieron 3 y salieron 2.
    ENVIADAS[tipo] = {"decididas": len(cola),
                      "enviadas": sum(1 for _, e in hechas if e.ok),
                      "ya_estaban": ya_estaban}
    return hechas


def volcar_estado_broker(broker: bx.BrokerXTB, candado_ok: bool = True) -> None:
    """Deja en estado/broker.json lo que XTB dice de la cuenta ahora mismo.

    Lo consume la página de operativa, que es estática y no puede preguntarle
    nada a nadie. Se escribe SIN un solo dato de sesión: ni TGT, ni cookies, ni
    credenciales — ese fichero acaba en una página pública.

    El coste de las posiciones sale de la cuenta simulada, porque XTB no lo
    devuelve: el broker da precio de entrada y volumen, y multiplicar los dos
    ignoraría las fricciones que la cuenta sí modela.
    """
    try:
        saldo = broker.saldo()
        posiciones = broker.posiciones()
    except Exception as exc:  # noqa: BLE001
        log(f"no se pudo volcar el estado del broker: {exc!r}")
        return

    bit = pd.read_csv(config.BASE_DIR / "bitacora.csv")
    bit["duplicada"] = cuenta.marcar_duplicadas(bit)
    limpias = bit[~bit["duplicada"]]
    cta = cuenta.simular(limpias[limpias["portafolio"] == config.CARTERA_BROKER],
                         fricciones=True)
    coste = {t["ticker"]: t["coste"] for t in cta["abiertas"]}

    datos = {
        "leido": datetime.now(config.TZ_ET).isoformat(),
        "candado_ok": bool(candado_ok),
        "saldo": saldo["saldo"], "equity": saldo["equity"],
        "divisa": saldo["divisa"],
        "posiciones": [
            {"ticker": p["ticker"], "acciones": p["acciones"],
             "precio_entrada": p["precio_entrada"],
             "precio_actual": p["precio_actual"], "pnl": p["pnl"],
             "coste": coste.get(p["ticker"].replace(".US", "").replace("-", "."))}
            for p in posiciones if p["lado"] == "buy"
        ],
    }
    ruta = config.ESTADO_DIR / "broker.json"
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    log(f"estado del broker volcado: {len(datos['posiciones'])} posiciones")


# --------------------------------------------------------------------------- #
# Verificación posterior a la apertura
# --------------------------------------------------------------------------- #
def _apertura_del_dia(tickers: list[str]) -> dict:
    """El precio de apertura de hoy de cada ticker, o {} si no se puede saber."""
    if not tickers:
        return {}
    from centinela import datos
    hoy = pd.Timestamp(datetime.now(config.TZ_ET).date())
    aperturas = {}
    try:
        series = datos.actualizar_precios(sorted(set(tickers)))
    except Exception as exc:  # noqa: BLE001 — sin precios se verifica igual
        log(f"no se pudieron traer las aperturas de hoy: {exc!r}")
        return {}
    for tk, df in series.items():
        if df is None or len(df) == 0 or hoy not in df.index:
            continue
        try:
            aperturas[tk] = float(df.loc[hoy, "Open"])
        except (KeyError, TypeError, ValueError):
            continue
    return aperturas


def _num(v):
    """Un número, o None. La bitácora es un CSV: todo llega como texto, y pasar
    un "138.0" donde el broker espera un float es un error que no se ve hasta
    que la llamada falla del otro lado."""
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def verificar_apertura(broker: bx.BrokerXTB, pendientes: dict) -> list[str]:
    """Con el mercado ya abierto: ¿pasó de verdad lo que se decidió?

    POR QUÉ ESTE PASO EXISTE (fallo del 2026-09-29)
    -----------------------------------------------
    Hasta hoy nadie miraba el resultado de la apertura el mismo día. El
    ejecutor manda las compras entre 20 y 45 minutos antes de abrir, XTB las
    deja EN COLA —que es justo lo que la estrategia quiere, ejecutar al open— y
    ahí terminaba todo hasta el post-cierre. Entre medias no había forma de
    saber si esas órdenes llegaron a ejecutarse, a qué precio, ni si alguna se
    quedó colgada.

    Este paso no decide nada nuevo. Solo mira y compara tres listas que tienen
    que cuadrar: lo decidido, lo enviado y lo que XTB tiene de verdad.
    """
    hoy = datetime.now(config.TZ_ET).date().isoformat()
    problemas: list[str] = []

    decididas = [o for o in pendientes["ordenes"] if o.sesion == hoy]
    filas = ords.filas_de_sesion(hoy)
    posiciones = {p["ticker"]: p for p in broker.posiciones() if p["lado"] == "buy"}
    en_cola = broker.ordenes_pendientes()

    log(f"decididas hoy: {len(decididas)} | filas en la bitácora: {len(filas)} "
        f"| posiciones en XTB: {len(posiciones)} | órdenes aún en cola: "
        f"{len(en_cola)}")

    # --- 1. Decisiones que nunca llegaron a orden -------------------------
    ids_en_bitacora = {f["id"] for f in filas}
    for o in decididas:
        if o.id not in ids_en_bitacora:
            problemas.append(
                f"{o.ticker}: se decidió {o.tipo} de {o.acciones} acciones y no "
                f"hay ninguna orden en la bitácora ({o.id}). El ejecutor no la "
                f"llegó a enviar.")

    # --- 2. Órdenes rechazadas -------------------------------------------
    for f in filas:
        if f.get("estado") == "rechazada":
            problemas.append(
                f"{f['ticker']}: la orden {f['id']} fue RECHAZADA por XTB — "
                f"{f.get('error') or 'sin motivo'}.")

    # --- 3. Órdenes enviadas que no se ejecutaron -------------------------
    aperturas = _apertura_del_dia([f["ticker"] for f in filas])
    for f in filas:
        if f.get("estado") == "rechazada" or f.get("tipo") != ords.COMPRA:
            continue
        simbolo = ords.simbolo_xtb(f["ticker"])
        pos = posiciones.get(simbolo)
        if pos is None:
            sigue_en_cola = any(o["ticker"] == simbolo for o in en_cola)
            problemas.append(
                f"{f['ticker']}: la compra se envió (orden "
                f"{f.get('orden_xtb') or '?'}) y "
                + ("SIGUE EN COLA sin ejecutar" if sigue_en_cola
                   else "XTB no tiene la posición")
                + f", {config.VERIFICACION_MIN_TRAS_APERTURA[0]}+ minutos "
                  f"después de la apertura.")
            continue

        # --- 4. El precio de verdad, que al enviar no se conocía ----------
        real = pos["precio_entrada"]
        apertura = aperturas.get(f["ticker"])
        campos = {"precio": real, "estado": "ejecutada"}
        if apertura:
            # Positivo = se compró más caro que el open, que es lo que el
            # simulador asume. Esta columna es la factura de operar de verdad.
            campos["slippage_pct"] = round(100.0 * (real / apertura - 1.0), 4)
        if ords.actualizar_ejecucion(f["id"], **campos):
            log(f"  {f['ticker']}: ejecutada a {real}"
                + (f" (open {apertura}, slippage "
                   f"{campos.get('slippage_pct')}%)" if apertura else ""))

    # --- 5. Niveles en XTB ------------------------------------------------
    sin_niveles = revisar_niveles_en_xtb(broker, filas, posiciones)
    problemas.extend(sin_niveles["problemas"])

    ENVIADAS["apertura"] = {
        "filas": len(filas),
        "ejecutadas": sum(1 for f in ords.filas_de_sesion(hoy)
                          if f.get("estado") == "ejecutada"),
        "sin_niveles": sin_niveles["sin_niveles"],
    }
    return problemas


def revisar_niveles_en_xtb(broker: bx.BrokerXTB, filas: list[dict],
                           posiciones: dict) -> list[str]:
    """Que cada posición nueva tenga su objetivo y su stop puestos en XTB.

    SE INTENTA Y SE REGISTRA LO QUE PASE. Lo medido hasta hoy es que XTB
    IGNORA en silencio el stop_loss y el take_profit en acciones al contado
    —comprobado con una orden real el 28/09/2026: F.US volvió con STOP=None y
    OBJETIVO=None— y que el cliente no oficial tampoco permite ponerlos después
    sobre una posición ya abierta.

    Por eso los niveles los vigila el ejecutor, que es la decisión que ya se
    tomó y está medida en el README. Este paso no la cambia: comprueba, intenta
    corregir, y deja escrito el resultado. Si algún día XTB empieza a
    aceptarlos, se verá aquí el mismo día en vez de dentro de seis meses.

    Lo que NO se hace es pintarlo de rojo: es una limitación conocida y
    aceptada del broker, y un rojo que sale todos los días deja de significar
    nada — la lección del 2026-09-28.
    """
    problemas: list[str] = []
    sin_niveles = 0
    for f in filas:
        if f.get("tipo") != ords.COMPRA or f.get("estado") == "rechazada":
            continue
        pos = posiciones.get(ords.simbolo_xtb(f["ticker"]))
        if pos is None:
            continue                       # ya se denunció arriba
        faltan = [n for n, v in (("stop", pos.get("stop")),
                                 ("objetivo", pos.get("objetivo"))) if not v]
        if not faltan:
            log(f"  {f['ticker']}: stop y objetivo puestos en XTB.")
            continue
        sin_niveles += 1
        try:
            broker.modificar_objetivo(pos["orden"],
                                      objetivo=_num(f.get("objetivo")),
                                      stop=_num(f.get("stop")))
            log(f"  {f['ticker']}: {' y '.join(faltan)} colocado(s) en XTB.")
        except bx.OperacionNoSoportada as exc:
            log(f"  {f['ticker']}: sin {' ni '.join(faltan)} en XTB — {exc}. "
                f"Los vigila el ejecutor.")
    return {"problemas": problemas, "sin_niveles": sin_niveles}


# --------------------------------------------------------------------------- #
# Plan B: salidas por tiempo que se quedaron sin ventana
# --------------------------------------------------------------------------- #
def cerrar_tiempos_atrasados(broker: bx.BrokerXTB, registro: dict) -> list:
    """Cierra a la apertura lo que debió salir por tiempo y no salió.

    La ventana de ventas son 25 minutos justo antes del cierre —tiene que ser
    así: cerrar antes regala sesión y cerrar después ya no entra— y el cron de
    Actions se ha retrasado horas en este repositorio más de una vez. Cuando esa
    ventana se pierde, la posición debía haber salido y amanece viva.

    El plan B es cerrarla con orden de mercado en la pre-apertura siguiente, que
    XTB ejecuta al abrir. No es gratis: se come el gap overnight, que es
    exactamente el ruido que la estrategia no contempla. Por eso se registra con
    su propio motivo, `tiempo_diferido`, y con el cierre del día en que DEBÍA
    haber salido como precio de referencia: así la columna de slippage de
    `bitacora_broker.csv` mide, operación a operación, lo que cuesta el plan B.

    Lo que no se hace es dejarla abierta un día más. Una posición pasada de su
    fecha de salida deja de ser la estrategia que el simulador mide.
    """
    estado = est_mod.cargar()
    cartera = config.CARTERA_BROKER
    hoy = datetime.now(config.TZ_ET).date().isoformat()

    reales = {p["ticker"].replace(".US", "").replace("-", "."): p
              for p in broker.posiciones() if p["lado"] == "buy"}
    hechas = []

    for pos in estado.get("posiciones", {}).get(cartera, []):
        if str(pos.get("fecha_entrada", "")) < config.EJECUCION_DESDE:
            continue                       # heredada: no existe en el broker
        limite = pos.get("dia_limite")
        if not limite or limite >= hoy:
            continue                       # aún no le toca, o le toca hoy
        real = reales.get(pos["ticker"])
        if real is None:
            continue                       # lo verá la reconciliación

        acciones = int(real["acciones"])
        if acciones < 1:
            continue
        orden = ords.Orden(
            id=ords.identificador(hoy, cartera, pos["ticker"],
                                  ords.VENTA_TIEMPO_DIFERIDO),
            tipo=ords.VENTA_TIEMPO_DIFERIDO, cartera=cartera,
            ticker=pos["ticker"], acciones=acciones, sesion=hoy,
            fecha_limite=limite, id_operacion=int(pos["id"]),
            # El cierre del día en que DEBÍA salir: contra eso se mide el coste.
            precio_simulador=_cierre_del_dia_limite(pos, limite))
        if ords.ya_enviada(registro, orden.id):
            continue

        log(f"  {pos['ticker']}: debía salir por tiempo el {limite} y sigue "
            f"abierta -> cerrando a la apertura de hoy (plan B)")
        e = broker.vender(orden.simbolo, acciones)
        log(f"    -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
            + (f" ERROR: {e.error}" if e.error else ""))
        ords.registrar_ejecucion(orden, e)
        if e.ok:
            ords.marcar_enviada(registro, orden.id,
                                {"estado": e.estado, "orden": e.orden,
                                 "diferida_desde": limite})
        hechas.append((orden, e))

    fallidas = [(o, e) for o, e in hechas if not e.ok]
    if fallidas:
        raise RuntimeError(
            "Salidas por tiempo diferidas que NO llegaron al broker:\n"
            + "\n".join(f"  {o.id}: {e.estado} — {e.error}" for o, e in fallidas))
    return hechas


def _cierre_del_dia_limite(pos: dict, limite: str) -> float | None:
    """El precio de cierre del día en que la posición debía haber salido.

    Sale de la bitácora del simulador, que sí cerró la posición ese día en su
    mundo de papel. Si no está, se devuelve None y la columna de slippage queda
    vacía: mejor un hueco honesto que un número inventado.
    """
    try:
        bit = pd.read_csv(config.BASE_DIR / "bitacora.csv")
    except (OSError, pd.errors.ParserError):
        return None
    fila = bit[(bit["id"] == pos.get("id")) & (bit["fecha_salida"] == limite)]
    if fila.empty or pd.isna(fila.iloc[0]["precio_salida"]):
        return None
    return float(fila.iloc[0]["precio_salida"])


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

    # Los momentos que ENVÍAN exigen la decisión del día; los que solo miran
    # (reconcilia) se apañan con lo que haya.
    try:
        if args.momento in ("compras", "ventas", "apertura"):
            pendientes = decision_del_dia(args.momento, args.sin_git)
        else:
            if not args.sin_git:
                traer_ordenes()
            pendientes = ords.cargar_pendientes()
    except SinDecision as exc:
        print(f"::error::{exc}", flush=True)
        salud.registrar(_COMPONENTE[args.momento], "fallo:sin-decision",
                        str(exc)[:300])
        publicar_resultado("fallo:sin-decision")
        return 1

    registro = ords.cargar_enviadas()
    credenciales = bx.credenciales_del_entorno_o_llavero()

    problemas: list[str] = []
    try:
        with bx.BrokerXTB(credenciales, demo=True) as broker:
            saldo = broker.saldo()
            log(f"cuenta {saldo['cuenta']} (DEMO): saldo {saldo['saldo']:,.2f} "
                f"{saldo['divisa']} | equity {saldo['equity']:,.2f}")

            # ANTES de comprar nada: cerrar lo que debió salir ayer. Una
            # posición pasada de su fecha deja de ser la estrategia que el
            # simulador mide, y el slot que ocupa hace falta hoy.
            if args.momento == "compras":
                log("comprobando salidas por tiempo atrasadas...")
                cerrar_tiempos_atrasados(broker, registro)

            if args.momento in ("compras", "ventas"):
                enviar(broker, pendientes, args.momento, registro)
            # El stop y el objetivo los lleva el ejecutor porque XTB no los
            # acepta en acciones al contado. Se miran en las dos ventanas con
            # mercado abierto; la de después del cierre no sirve para vender.
            if args.momento == "ventas":
                log("vigilando stops y objetivos...")
                vigilar_niveles(broker, registro)

            # No decide nada: compara lo decidido, lo enviado y lo que XTB
            # tiene de verdad, con el mercado ya abierto.
            if args.momento == "apertura":
                log("verificando la apertura...")
                problemas.extend(verificar_apertura(broker, pendientes))

            problemas.extend(reconciliar(broker))
            volcar_estado_broker(broker, candado_ok=True)
    except bx.CuentaNoDemo as exc:
        # El candado saltó: queda anotado para que la página lo pinte en rojo.
        salud.registrar(_COMPONENTE[args.momento], "candado", str(exc)[:200])
        raise
    except bx.SesionCaducada as exc:
        salud.registrar(_COMPONENTE[args.momento], bx.MARCA_SESION_CADUCADA,
                        str(exc)[:200])
        raise
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
        salud.registrar(_COMPONENTE[args.momento], "diferencias",
                        " | ".join(problemas)[:400])
        publicar_resultado("diferencias")
        log(f"❌ {len(problemas)} diferencia(s) entre XTB y el simulador.")
        return 1

    # Se decidió comprar tres cosas y salieron dos: eso es ROJO aunque las dos
    # que salieron fueran perfectas. Un ejecutor que envía de menos y termina
    # en verde es exactamente el fallo que esta sesión vino a cerrar.
    tipo = {"compras": ords.COMPRA, "ventas": ords.VENTA_TIEMPO}.get(args.momento)
    r = ENVIADAS.get(tipo) if tipo else None
    if r and r["enviadas"] + r["ya_estaban"] < r["decididas"]:
        faltan = r["decididas"] - r["enviadas"] - r["ya_estaban"]
        msg = (f"Se decidieron {r['decididas']} orden(es) de {tipo} para hoy y "
               f"solo {r['enviadas'] + r['ya_estaban']} llegaron al broker: "
               f"faltan {faltan}.")
        print(f"::error::{msg}", flush=True)
        salud.registrar(_COMPONENTE[args.momento], "fallo:envio-incompleto", msg)
        publicar_resultado("fallo:envio-incompleto")
        log(f"❌ {msg}")
        return 1

    resultado = resultado_explicito(args.momento, motivo_de_cero(args.momento))
    salud.registrar(_COMPONENTE[args.momento], resultado)
    publicar_resultado(resultado)
    log(f"✅ {resultado} · sin diferencias entre XTB y el simulador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
