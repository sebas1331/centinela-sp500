#!/usr/bin/env python
"""Genera docs/operativa.json: la salud del sistema y lo que hay en el broker.

QUÉ CONTESTA ESTA PÁGINA
------------------------
El dashboard de estrategia contesta "¿cuánto gana esto?". Esta contesta algo
distinto y más urgente: **"¿está funcionando ahora mismo?"**. Son dos preguntas
y mezclarlas en una sola página haría que ninguna se leyera bien.

Todo se calcula AQUÍ, en Python. El HTML solo pinta, igual que el otro panel:
una sola fuente de verdad, y comprobable por los tests sin abrir un navegador.

PRIVACIDAD
----------
El número de cuenta sale enmascarado (solo los tres últimos dígitos) y no se
escribe NADA de la sesión: ni TGT, ni cookies, ni credenciales. Esta página es
pública en GitHub Pages, así que lo que entra aquí lo puede leer cualquiera.
Hay un test que lo comprueba contra la lista de secretos conocidos.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (calendario, config, cuenta, fiabilidad,  # noqa: E402
                       latido as lat, presupuesto,
                       ordenes as ords, salud, broker_xtb as bx)

DOCS = config.BASE_DIR / "docs"
PLANTILLA = Path(__file__).resolve().parent / "plantilla_operativa.html"

#: Cuántos avisos del Vigilante se conservan. Los suficientes para ver una
#: racha; más allá es historia que ya está en los runs de Actions.
MAX_ALERTAS = 20
#: Cuántas órdenes entran en el historial. Con tres al día son varios meses.
MAX_ORDENES = 400
#: A menos de esto del stop, la posición se pinta en ámbar.
CERCA_DEL_STOP_PCT = 2.0
#: Sobre cuántos días se mide la fiabilidad del broker. Siete: suficiente para
#: que una racha mala se vea y corto para que una semana buena la borre.
DIAS_FIABILIDAD = 7
#: Por debajo de esta tasa de confirmación, el broker deja de ser de fiar.
FIABILIDAD_MINIMA_PCT = 90.0
#: Con menos intentos que esto, el porcentaje no dice nada: una sola ambigua
#: sobre dos órdenes daría un 50 % que no significa que el broker esté roto.
MINIMO_PARA_JUZGAR = 4


def _r(x, dec=2):
    """Redondea y convierte a float nativo, o None. El JSON no lleva NaN."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else round(v, dec)


def enmascarar_cuenta(numero) -> str:
    """22770385 -> '•••385'. Esta página es pública."""
    s = str(numero or "")
    return f"•••{s[-3:]}" if len(s) >= 3 else "•••"


# --------------------------------------------------------------------------- #
# Componentes y su próxima ejecución
# --------------------------------------------------------------------------- #
def _proxima_sesion(desde: datetime) -> str | None:
    try:
        s = calendario.sesion_n_despues(desde.date().isoformat(), 1)
        return pd.Timestamp(s).date().isoformat()
    except Exception:  # noqa: BLE001 — el calendario no puede tumbar la página
        return None


def bloque_componentes(datos_salud: dict, ahora: datetime) -> list[dict]:
    """Una fila por componente: cuándo corrió, cómo acabó y cuándo toca.

    La hora se publica en ISO con su zona; el HTML la pasa a hora de Ecuador.
    Convertirla aquí obligaría a que el JSON supiera dónde se va a leer.
    """
    hoy_es_sesion = calendario.es_dia_de_mercado(ahora.date())
    proxima = (ahora.date().isoformat() if hoy_es_sesion
               else _proxima_sesion(ahora))

    filas = []
    for clave, (nombre, cada) in salud.COMPONENTES.items():
        r = datos_salud.get("runs", {}).get(clave, {})
        filas.append({
            "id": clave,
            "nombre": nombre,
            "cadencia": cada,
            "cuando": r.get("cuando"),
            "resultado": r.get("resultado"),
            # El ejecutor junta varias diferencias con "|", que es cómo las
            # escribe; aquí esto se LEE, así que se separan a la vista.
            "detalle": (str(r["detalle"]).replace("|", " · ")
                        if r.get("detalle") else None),
            "url": r.get("url"),
            "proxima_sesion": proxima,
        })
    return filas


# --------------------------------------------------------------------------- #
# La cuenta y sus posiciones
# --------------------------------------------------------------------------- #
def bloque_cuenta_broker(estado_broker: dict | None) -> dict:
    """Lo que dice XTB de la cuenta. None cuando aún no se ha leído nunca."""
    base = {
        "cuenta": enmascarar_cuenta(config.CUENTA_DEMO),
        "tipo": config.TIPO_CUENTA_BROKER,
        "candado_ok": None,
        "saldo": None, "equity": None, "invertido": None,
        "efectivo": None, "pnl_abierto": None, "pnl_abierto_pct": None,
        "leido": None,
    }
    if not estado_broker:
        return base

    saldo = _r(estado_broker.get("saldo"))
    equity = _r(estado_broker.get("equity"))
    invertido = _r(sum(p.get("coste", 0.0) or 0.0
                       for p in estado_broker.get("posiciones", [])))
    pnl = _r(sum(p.get("pnl", 0.0) or 0.0
                 for p in estado_broker.get("posiciones", [])))
    base.update({
        "candado_ok": bool(estado_broker.get("candado_ok")),
        "saldo": saldo, "equity": equity, "invertido": invertido,
        "efectivo": _r((equity or 0.0) - (invertido or 0.0)),
        "pnl_abierto": pnl,
        "pnl_abierto_pct": _r(100.0 * pnl / invertido) if invertido else None,
        "leido": estado_broker.get("leido"),
    })
    return base


def bloque_posiciones(estado_broker: dict | None, estado_sim: dict,
                      ahora: datetime) -> list[dict]:
    """Las posiciones vivas en XTB, cruzadas con lo que el simulador sabe.

    El broker conoce el precio y las acciones; el simulador conoce el objetivo,
    el stop y la fecha de salida por tiempo. Ninguno de los dos lo sabe todo, y
    lo que hace falta mirar por la mañana es la unión.
    """
    if not estado_broker:
        return []
    cartera = config.CARTERA_BROKER
    por_ticker = {p["ticker"]: p
                  for p in estado_sim.get("posiciones", {}).get(cartera, [])}
    manana = _proxima_sesion(ahora)

    filas = []
    for p in estado_broker.get("posiciones", []):
        tk = str(p.get("ticker", "")).replace(".US", "").replace("-", ".")
        sim = por_ticker.get(tk, {})
        entrada = _r(p.get("precio_entrada"))
        actual = _r(p.get("precio_actual")) or entrada
        objetivo = _r(sim.get("objetivo"))
        stop = _r(sim.get("stop"))
        limite = sim.get("dia_limite")
        dias = None
        if sim.get("fecha_entrada"):
            try:
                dias = len(calendario.sesiones_en_rango(
                    sim["fecha_entrada"], ahora.date().isoformat()))
            except Exception:  # noqa: BLE001
                dias = None

        filas.append({
            "ticker": tk,
            "acciones": _r(p.get("acciones"), 4),
            "precio_entrada": entrada,
            "fecha_entrada": sim.get("fecha_entrada"),
            "precio_actual": actual,
            "objetivo": objetivo,
            "stop": stop,
            "dist_objetivo_pct": (_r(100.0 * (objetivo / actual - 1.0))
                                  if objetivo and actual else None),
            "dist_stop_pct": (_r(100.0 * (actual / stop - 1.0))
                              if stop and actual else None),
            "dias_en_posicion": dias,
            "fecha_limite": limite,
            "pnl": _r(p.get("pnl")),
            "pnl_pct": (_r(100.0 * (actual / entrada - 1.0))
                        if entrada and actual else None),
            # Lo que hay que mirar hoy: sale mañana, o está pegada al stop.
            "sale_manana": bool(limite and manana and limite <= manana),
            "cerca_del_stop": bool(
                stop and actual and (actual / stop - 1.0) * 100.0 <= CERCA_DEL_STOP_PCT),
        })
    filas.sort(key=lambda f: (not f["sale_manana"], not f["cerca_del_stop"],
                              f["ticker"]))
    return filas


# --------------------------------------------------------------------------- #
# Historial de órdenes
# --------------------------------------------------------------------------- #
#: Cómo se llama cada tipo en pantalla. El vocabulario interno es cerrado, así
#: que un tipo sin traducir aquí es un despiste que conviene ver.
NOMBRE_TIPO = {
    ords.COMPRA: "Compra",
    ords.VENTA_TIEMPO: "Venta por tiempo",
    ords.VENTA_TIEMPO_DIFERIDO: "Venta diferida",
    ords.VENTA_STOP: "Venta por stop",
    ords.VENTA_OBJETIVO: "Venta por objetivo",
    # En vivo, disparadas por el vigilante de precios en el momento del cruce.
    # Se distinguen de las de arriba a propósito: comparar unas con otras es lo
    # que mide si vigilar tick a tick acerca la ejecución al simulador.
    ords.VENTA_STOP_INTRADIA: "Stop en vivo",
    ords.VENTA_OBJETIVO_INTRADIA: "Objetivo en vivo",
}
#: Grupos de los chips de filtro.
GRUPO_TIPO = {
    ords.COMPRA: "compras",
    ords.VENTA_TIEMPO: "ventas", ords.VENTA_TIEMPO_DIFERIDO: "ventas",
    ords.VENTA_STOP: "ventas", ords.VENTA_OBJETIVO: "ventas",
    ords.VENTA_STOP_INTRADIA: "ventas", ords.VENTA_OBJETIVO_INTRADIA: "ventas",
}


def bloque_ordenes() -> list[dict]:
    ruta = ords.ARCHIVO_BITACORA_BROKER
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8") as f:
        filas = list(csv.DictReader(f))

    salida = []
    for f in filas:
        tipo = f.get("tipo", "")
        precio = _r(f.get("precio"))
        pedido = _r(f.get("precio_simulador"))
        slip_pct = _r(f.get("slippage_pct"), 4)
        acciones = _r(f.get("acciones"), 4) or 0
        salida.append({
            "cuando": f.get("cuando_et"),
            "sesion": f.get("sesion"),
            "ticker": f.get("ticker"),
            "tipo": tipo,
            "tipo_nombre": NOMBRE_TIPO.get(tipo, tipo),
            "grupo": GRUPO_TIPO.get(tipo, "otras"),
            "acciones": acciones,
            "precio_pedido": pedido,
            "precio_ejecutado": precio,
            "precio_disparo": _r(f.get("precio_disparo")),
            "slippage_pct": slip_pct,
            "slippage": (_r(acciones * precio * slip_pct / 100.0)
                         if precio and slip_pct is not None else None),
            "estado": f.get("estado"),
            "error": f.get("error") or None,
            "orden_xtb": f.get("orden_xtb") or None,
            "id_interno": f.get("id"),
        })
    salida.reverse()                      # la más reciente primero
    return salida[:MAX_ORDENES]


# --------------------------------------------------------------------------- #
# Historial de objetivos y stops
# --------------------------------------------------------------------------- #
def bloque_niveles(estado_sim: dict) -> list[dict]:
    """Cada cambio de objetivo de las posiciones vivas, con su motivo.

    El stop no cambia nunca por diseño (se fija al comprar), así que aquí solo
    hay movimientos de objetivo; se publica igualmente el stop vigente para
    poder mirarlos juntos.
    """
    cartera = config.CARTERA_BROKER
    filas = []
    for pos in estado_sim.get("posiciones", {}).get(cartera, []):
        historial = pos.get("historial_objetivos") or []
        anterior = None
        cambios = []
        for h in historial:
            nuevo = _r(h.get("objetivo"))
            cambios.append({
                "fecha": h.get("fecha"),
                "anterior": anterior,
                "nuevo": nuevo,
                "motivo": h.get("motivo"),
            })
            anterior = nuevo
        if cambios:
            filas.append({
                "ticker": pos.get("ticker"),
                "stop": _r(pos.get("stop")),
                "objetivo_vigente": _r(pos.get("objetivo")),
                "cambios": cambios,
            })
    return filas


# --------------------------------------------------------------------------- #
# La línea "Hoy": del modelo al broker, y dónde se cae cada una
# --------------------------------------------------------------------------- #
def bloque_hoy(ordenes: list[dict], ahora: datetime) -> dict:
    """Señales -> decididas -> enviadas -> ejecutadas, con el motivo de cada salto.

    POR QUÉ ESTAS CUATRO CIFRAS Y NO UNA (fallo del 2026-09-29)
    -----------------------------------------------------------
    Ese día la página decía "Compras en XTB: ok" y el historial tenía cero
    órdenes, y las dos cosas eran ciertas: el modelo dio UNA señal (COHR,
    prob 0.808) y se descartó porque ya había posición abierta de ese ticker en
    el simulador. Pero para saberlo había que abrir Actions y leer 187 líneas de
    log. Un "ok" que obliga a eso no está informando de nada.

    Cada número de esta fila es una etapa del camino, y entre etapa y etapa se
    escribe QUIÉN se quedó por el camino y por qué. Un cero con motivo es
    información; un cero sin motivo es una pregunta.
    """
    hoy = ahora.date().isoformat()
    ruta = config.LOGS_DIR / f"decisiones-{hoy}.log"
    senales, decididas, descartes = 0, 0, []
    if ruta.exists():
        try:
            lineas = ruta.read_text(encoding="utf-8").splitlines()
        except OSError:
            lineas = []
        for ln in lineas:
            if "| ENTRAR |" in ln:
                senales += 1
            if "ENTRADA DESCARTADA" in ln:
                descartes.append({
                    "ticker": ln.split("|")[0].strip(),
                    "motivo": ln.split("ENTRADA DESCARTADA:")[-1].strip(),
                })
            m = re.search(r"DECIDIDAS PARA ENTRAR HOY \((\d+)\)", ln)
            if m:
                decididas = int(m.group(1))

    del_dia = [o for o in ordenes
               if o.get("sesion") == hoy and o.get("tipo") == ords.COMPRA]
    enviadas = [o for o in del_dia if o.get("estado") != "rechazada"]
    ejecutadas = [o for o in del_dia if o.get("estado") == "ejecutada"]
    rechazadas = [o for o in del_dia if o.get("estado") == "rechazada"]

    # Entre etapa y etapa, el porqué. Sin esto la fila sería cuatro números que
    # no cuadran y ninguna explicación, que es de donde venimos.
    huecos = []
    if senales > decididas:
        for d in descartes:
            huecos.append(f"{d['ticker']}: {d['motivo']}")
        if not descartes:
            huecos.append(f"{senales - decididas} señal(es) no llegaron a "
                          f"decisión y el log no dice por qué.")
    if decididas > len(del_dia):
        huecos.append(f"{decididas - len(del_dia)} decisión(es) sin orden en la "
                      f"bitácora: el ejecutor no llegó a enviarlas.")
    for o in rechazadas:
        huecos.append(f"{o.get('ticker')}: orden rechazada — "
                      f"{o.get('error') or 'sin motivo'}.")
    pendientes_de_ejecutar = len(enviadas) - len(ejecutadas)
    if pendientes_de_ejecutar > 0:
        huecos.append(f"{pendientes_de_ejecutar} orden(es) enviada(s) que XTB "
                      f"todavía no ha ejecutado.")

    return {
        "fecha": hoy,
        "es_sesion": calendario.es_dia_de_mercado(ahora.date()),
        "hubo_escaneo": ruta.exists(),
        "senales": senales,
        "decididas": decididas,
        "enviadas": len(enviadas),
        "ejecutadas": len(ejecutadas),
        "huecos": huecos,
    }


# --------------------------------------------------------------------------- #
# El vigilante de precios
# --------------------------------------------------------------------------- #
def bloque_vigilante_precios(ahora: datetime, posiciones: list[dict]) -> dict:
    """De dónde sacar el latido y cuándo exigirlo.

    El latido NO viaja en este JSON. Se publica en una rama aparte que se
    reescribe cada dos minutos, y la página lo pide en vivo al cargarse: si
    viniera aquí dentro, tendría la antigüedad de la última vez que se regeneró
    la página —horas— y la regla de "más de 10 minutos = rojo" sería imposible
    de cumplir. Aquí solo va la dirección y cuándo hay que exigirlo.
    """
    hoy = ahora.date()
    es_sesion = calendario.es_dia_de_mercado(hoy)
    apertura = cierre = None
    if es_sesion:
        ac = calendario.apertura_cierre_et(hoy.isoformat())
        if ac:
            apertura, cierre = ac[0].isoformat(), ac[1].isoformat()
    return {
        "url_latido": lat.url_publica(),
        "muerto_minutos": lat.MUERTO_MINUTOS,
        "cada_segundos": lat.CADA_SEGUNDOS,
        # El navegador compara su reloj con estas dos: fuera de sesión, que no
        # haya latido es lo normal y no puede pintar nada de rojo.
        "sesion_abre": apertura,
        "sesion_cierra": cierre,
        "es_sesion": es_sesion,
        # CUÁNTAS POSICIONES HAY QUE VIGILAR DE VERDAD. Sin esto, el navegador
        # daba por caído a un vigilante que había terminado porque no había
        # nada que vigilar: 62 minutos sin latir con la cuenta vacía se leía
        # igual que 62 minutos sin latir con tres posiciones y sus stops al
        # aire, y no son lo mismo ni de lejos.
        "posiciones_a_vigilar": sum(
            1 for p in posiciones
            if p.get("stop") is not None or p.get("objetivo") is not None),
    }


# --------------------------------------------------------------------------- #
# Reconciliación y alertas
# --------------------------------------------------------------------------- #
def bloque_reconciliacion(datos_salud: dict) -> dict:
    r = datos_salud.get("runs", {}).get("reconcilia", {})
    detalle = r.get("detalle") or ""
    diferencias = [d for d in detalle.split("|") if d.strip()] if detalle else []
    return {
        "cuando": r.get("cuando"),
        "coincide": (r.get("resultado") == "ok") if r.get("resultado") else None,
        "diferencias": diferencias,
        "url": r.get("url"),
    }


def bloque_alertas() -> list[dict]:
    """Los últimos avisos del Vigilante y del ejecutor, de los logs del repo."""
    alertas = []
    for ruta in sorted(config.LOGS_DIR.glob("decisiones-*.log"), reverse=True)[:10]:
        fecha = re.search(r"decisiones-(\d{4}-\d{2}-\d{2})", ruta.name)
        try:
            texto = ruta.read_text(encoding="utf-8")
        except OSError:
            continue
        for linea in texto.splitlines():
            if linea.startswith("::error::") or "EMERGENCIA" in linea:
                alertas.append({
                    "fecha": fecha.group(1) if fecha else None,
                    "texto": linea.replace("::error::", "").strip()[:300],
                })
    return alertas[:MAX_ALERTAS]


# --------------------------------------------------------------------------- #
def construir(ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(config.TZ_ET)
    datos_salud = salud.cargar()
    estado_sim = json.loads(
        (config.ESTADO_DIR / "estado.json").read_text(encoding="utf-8"))

    ruta_broker = config.ESTADO_DIR / "broker.json"
    estado_broker = (json.loads(ruta_broker.read_text(encoding="utf-8"))
                     if ruta_broker.exists() else None)

    horas_sesion = _horas_de_sesion()
    ordenes = bloque_ordenes()
    posiciones = bloque_posiciones(estado_broker, estado_sim, ahora)
    return {
        "generado": ahora.isoformat(),
        "hoy": bloque_hoy(ordenes, ahora),
        "vigilante_precios": bloque_vigilante_precios(ahora, posiciones),
        "fiabilidad": fiabilidad.resumen(DIAS_FIABILIDAD, ahora=ahora),
        # Para que el navegador sepa si los datos deberían haberse refrescado.
        "hoy_es_sesion": bool(calendario.es_dia_de_mercado(ahora.date())),
        "componentes": bloque_componentes(datos_salud, ahora),
        # Informativo y sin color: cuánto le queda a la sesión de XTB. No es un
        # aviso, es un dato — se renueva sola.
        "sesion_xtb": {
            "horas": _r(horas_sesion, 1),
            "caducada": bool(horas_sesion is not None and horas_sesion <= 0),
        } if horas_sesion is not None else None,
        "broker": bloque_cuenta_broker(estado_broker),
        "posiciones": posiciones,
        "ordenes": ordenes,
        "niveles": bloque_niveles(estado_sim),
        "reconciliacion": bloque_reconciliacion(datos_salud),
        "alertas": bloque_alertas(),
        "semaforo": semaforo(datos_salud, estado_broker, posiciones, ordenes,
                             ahora),
        "meta": {
            "repo": "https://github.com/sebas1331/centinela-sp500",
            "cartera_broker": config.CARTERA_BROKER,
            "marca_sesion_caducada": bx.MARCA_SESION_CADUCADA,
        },
    }


# --------------------------------------------------------------------------- #
# El semáforo
# --------------------------------------------------------------------------- #
#: Componentes cuyo fallo es un problema de verdad: si uno de estos está en
#: rojo, el sistema no está haciendo su trabajo. El reentrenamiento no está
#: porque corre una vez al mes y su fallo no impide operar ese día.
CRITICOS = ("preapertura", "postcierre", "compras", "apertura", "ventas",
            "reconcilia", "vigilante")
#: Cuántas horas de retraso sobre lo esperado se toleran antes de avisar.
RETRASO_AMBAR_HORAS = 30
#: Cuántos días atrás se miran las órdenes rechazadas.
DIAS_RECHAZOS = 3


def semaforo(datos_salud: dict, estado_broker: dict | None,
             posiciones: list[dict], ordenes: list[dict],
             ahora: datetime) -> dict:
    """Verde, ámbar o rojo, con los motivos escritos.

    Las reglas están aquí y no en el HTML por la misma razón que el resto de
    los cálculos: para poder probarlas sin abrir un navegador, y para que no
    haya dos versiones de "cuándo está esto mal".

    El orden importa: se recogen TODOS los motivos, y el color es el del peor.
    Un rojo no oculta los ámbares, porque cuando alguien entra a arreglar algo
    quiere ver todo lo que hay, no solo lo más grave.
    """
    rojos: list[str] = []
    ambares: list[str] = []
    runs = datos_salud.get("runs", {})

    # --- ROJO: algún componente crítico acabó mal -------------------------
    for clave in CRITICOS:
        r = runs.get(clave)
        if not r:
            continue
        resultado = str(r.get("resultado", ""))
        if resultado.startswith("fallo") or resultado in ("failure", "error"):
            nombre = salud.COMPONENTES[clave][0]
            rojos.append(f"{nombre} terminó en rojo ({resultado}).")

    # --- ROJO: la sesión de XTB caducó ------------------------------------
    for clave, r in runs.items():
        if bx.MARCA_SESION_CADUCADA in str(r.get("detalle", "")) or \
           bx.MARCA_SESION_CADUCADA in str(r.get("resultado", "")):
            rojos.append(
                f"La sesión de XTB caducó ({bx.MARCA_SESION_CADUCADA}). "
                f"Lanza el workflow «Renovar sesión XTB» con el código que "
                f"te llegó por correo.")
            break

    # --- ROJO: el candado de demo saltó -----------------------------------
    if estado_broker and estado_broker.get("candado_ok") is False:
        rojos.append(
            "El candado de cuenta demo se activó: el sistema NO envió nada "
            "porque la sesión no apuntaba a la cuenta permitida.")

    # --- ROJO: la reconciliación encontró diferencias ---------------------
    # Solo cuando la reconciliación CORRIÓ y no cuadró. Un "omitido:" significa
    # que no llegó a correr —el escaneo era idempotente y su ejecutor se
    # saltó—, y leerlo como discrepancia pintaba un rojo falso con un motivo
    # que no se sostenía: "encontró diferencias: ver el run".
    rec = runs.get("reconcilia", {})
    resultado_rec = str(rec.get("resultado", ""))
    if resultado_rec and not resultado_rec.startswith("omitido") \
            and resultado_rec != "ok":
        # El detalle viene con las diferencias separadas por "|", que es cómo
        # las junta el ejecutor. Aquí se leen, así que se separan con puntos.
        detalle = "; ".join(d.strip() for d in
                            str(rec.get("detalle", "")).split("|") if d.strip())
        rojos.append(
            f"La reconciliación con XTB encontró diferencias: "
            f"{detalle or 'ver el run'}.")

    # --- ROJO: una venta del vigilante de precios que no llegó al broker --
    # Es el peor fallo posible de todo el sistema y por eso va aparte. Una
    # compra que no entra cuesta una oportunidad; una VENTA por nivel que no
    # entra deja una posición con su stop cruzado y a merced del mercado, que
    # es exactamente lo que el stop existe para impedir.
    for o in ordenes:
        if o.get("tipo") not in (ords.VENTA_STOP_INTRADIA,
                                 ords.VENTA_OBJETIVO_INTRADIA):
            continue
        if o.get("estado") in ("ejecutada", "en_cola"):
            continue
        if not _reciente(o.get("sesion"), ahora, dias=DIAS_FIABILIDAD):
            continue
        rojos.append(
            f"{o.get('ticker')}: la venta por {o.get('tipo_nombre')} NO llegó "
            f"al broker el {o.get('sesion')} ({o.get('estado')}"
            + (f" — {o.get('error')}" if o.get("error") else "")
            + "). La posición sigue abierta con su nivel cruzado.")

    # --- ROJO: una posición pasada de su fecha de salida ------------------
    hoy = ahora.date().isoformat()
    for p in posiciones:
        if p.get("fecha_limite") and p["fecha_limite"] < hoy:
            rojos.append(
                f"{p['ticker']} debía salir por tiempo el {p['fecha_limite']} "
                f"y sigue abierta en XTB.")

    # --- ÁMBAR: salidas por tiempo que hubo que diferir -------------------
    diferidas = [o for o in ordenes
                 if o.get("tipo") == ords.VENTA_TIEMPO_DIFERIDO
                 and _reciente(o.get("sesion"), ahora, dias=7)]
    for o in diferidas:
        ambares.append(
            f"{o['ticker']} se cerró con el plan B el {o.get('sesion')}: la "
            f"ventana de ventas de ese día se perdió.")

    # La sesión de XTB NO aparece aquí a propósito. Este aviso existía cuando se
    # creía que renovarla exigía a una persona; el 2026-09-29 se midió que no:
    # el TGT caduca de madrugada todas las noches y el login en frío de la
    # mañana entra solo con la cookie de dispositivo de confianza. Avisar de
    # algo que se arregla solo es ruido, y el ruido se come la señal.
    #
    # Lo único que de verdad exige a una persona es que un login FALLE, y eso
    # ya está arriba: cualquier componente que muera con XTB_REQUIERE_CODIGO
    # pinta rojo. La caducidad se informa en Componentes, sin color.

    # --- ÁMBAR: algún componente crítico no ha reportado NUNCA ------------
    # Sin esta regla un componente que nunca escribe es invisible: las reglas de
    # abajo solo miran a los que tienen fecha, así que un workflow mal cableado
    # dejaría la página en verde para siempre. No es rojo porque al desplegar
    # esto es indistinguible de "todavía no le ha tocado correr", y se apaga
    # solo en cuanto cada uno corre una vez.
    for clave in CRITICOS:
        if not runs.get(clave, {}).get("cuando"):
            ambares.append(
                f"{salud.COMPONENTES[clave][0]} no ha reportado nunca. O acaba "
                f"de desplegarse, o nadie registra su resultado.")

    # --- ÁMBAR: algún componente lleva demasiado sin aparecer -------------
    for clave in CRITICOS:
        r = runs.get(clave)
        if not r or not r.get("cuando"):
            continue
        try:
            visto = datetime.fromisoformat(r["cuando"])
        except ValueError:
            continue
        retraso = (ahora - visto).total_seconds() / 3600
        if retraso > RETRASO_AMBAR_HORAS:
            ambares.append(
                f"{salud.COMPONENTES[clave][0]} no corre desde hace "
                f"{retraso:.0f} h.")

    # --- ÁMBAR: el broker no está aceptando órdenes -----------------------
    # El 2026-09-29 el endpoint de trading devolvió cuerpo vacío en 7 de 8
    # compras y el sistema no lo midió: se supo porque alguien estaba mirando.
    fia = fiabilidad.resumen(DIAS_FIABILIDAD, ahora=ahora)
    if fia["enviadas"] >= MINIMO_PARA_JUZGAR and \
            fia["fiabilidad_pct"] is not None and \
            fia["fiabilidad_pct"] < FIABILIDAD_MINIMA_PCT:
        ambares.append(
            f"XTB solo confirmó {fia['confirmadas']} de {fia['enviadas']} "
            f"órdenes en {DIAS_FIABILIDAD} días ({fia['fiabilidad_pct']:.0f} %): "
            f"{fia['ambiguas']} ambiguas y {fia['fallidas']} fallidas.")

    # --- ÁMBAR: órdenes rechazadas hace poco ------------------------------
    rechazos = [o for o in ordenes
                if o.get("estado") == "rechazada"
                and _reciente(o.get("sesion"), ahora, dias=DIAS_RECHAZOS)]
    for o in rechazos:
        ambares.append(
            f"Orden rechazada el {o.get('sesion')}: {o.get('tipo_nombre')} de "
            f"{o.get('ticker')} — {o.get('error') or 'sin motivo'}.")

    color = "rojo" if rojos else ("ambar" if ambares else "verde")
    titulo = {"rojo": "Problema", "ambar": "Atención",
              "verde": "Todo en orden"}[color]
    return {"color": color, "titulo": titulo,
            "motivos": rojos + ambares, "n_rojos": len(rojos),
            "n_ambares": len(ambares)}


def _reciente(sesion: str | None, ahora: datetime, dias: int) -> bool:
    if not sesion:
        return False
    corte = (ahora.date() - timedelta(days=dias)).isoformat()
    return str(sesion) >= corte


def _horas_de_sesion() -> float | None:
    """Horas que le quedan a la sesión de XTB, o None si no se puede saber.

    NO se lee nada del contenido: solo la fecha de caducidad. El TGT no sale de
    aquí ni en un cálculo intermedio.
    """
    if not bx.ARCHIVO_SESION.exists():
        return None
    try:
        datos = json.loads(bx.ARCHIVO_SESION.read_text(encoding="utf-8"))
        expira = datetime.fromisoformat(str(datos["expires_at"]))
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None
    return (expira - datetime.now(expira.tzinfo)).total_seconds() / 3600


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #
def _escribir_si_cambia(ruta: Path, contenido: str) -> bool:
    if ruta.exists() and ruta.read_text(encoding="utf-8") == contenido:
        return False
    ruta.write_text(contenido, encoding="utf-8")
    return True


def generar(destino: Path = DOCS, ahora: datetime | None = None) -> tuple[dict, bool]:
    if not PLANTILLA.exists():
        raise FileNotFoundError(f"Falta la plantilla: {PLANTILLA}")
    destino.mkdir(parents=True, exist_ok=True)
    datos = construir(ahora)

    txt = json.dumps(datos, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")) + "\n"
    cambios = []
    if _escribir_si_cambia(destino / "operativa.json", txt):
        cambios.append("operativa.json")
    if _escribir_si_cambia(destino / "operativa.html",
                           PLANTILLA.read_text(encoding="utf-8")):
        cambios.append("operativa.html")

    tam_json = len(txt.encode("utf-8"))
    tam_html = (destino / "operativa.html").stat().st_size
    print(f"operativa.json: {tam_json / 1024:.1f} KB | "
          f"operativa.html: {tam_html / 1024:.1f} KB")
    print(f"semáforo={datos['semaforo']['color']} "
          f"posiciones={len(datos['posiciones'])} "
          f"ordenes={len(datos['ordenes'])}")
    print("cambios: " + (", ".join(cambios) if cambios else "ninguno"), flush=True)

    print(presupuesto.exigir(destino / "operativa.html"), flush=True)
    if tam_json > presupuesto.TECHO_JSON:
        raise RuntimeError(f"docs/operativa.json pesa {tam_json / 1024:.0f} KB "
                           f"y el techo son "
                           f"{presupuesto.TECHO_JSON / 1024:.0f} KB.")
    return datos, bool(cambios)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--destino", type=Path, default=DOCS)
    args = ap.parse_args()
    _, hubo = generar(args.destino)
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as fh:
            fh.write(f"cambios={'si' if hubo else 'no'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
