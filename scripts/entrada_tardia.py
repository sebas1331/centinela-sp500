#!/usr/bin/env python
"""Qué pasó con las órdenes de hoy en XTB, y —si toca— reenviarlas tarde.

EL CASO (2026-09-30)
--------------------
El sistema mandó dos compras a las 08:47 ET, **43 minutos antes de la
apertura**. XTB las aceptó en el momento y el sistema las anotó `en_cola`. En
xStation 5 figuran como RECHAZADO. La página decía "todo en orden" porque nunca
volvió a preguntar: "aceptada por el servidor" se tomó por "ejecutada".

Este script pregunta lo único que vale, que es qué dice XTB ahora:

  * ¿Hay posición del símbolo?      -> se ejecutó.
  * ¿Sigue la orden en cola?        -> está viva, esperando.
  * ¿Ni una cosa ni la otra?        -> no existe: se rechazó.

Con `--enviar` vuelve a mandarlas a mercado, marcadas como `entrada_tardia` y
guardando el precio de apertura de hoy junto al ejecutado, que es lo que mide
cuánto costó el retraso. Solo dentro de la primera hora de sesión: después, una
compra "al open" ya no es al open y es mejor perder el día.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (ambiguas as amb, broker_xtb as bx, calendario,  # noqa: E402
                       config, fiabilidad, ordenes as ords)

#: Hasta cuándo tiene sentido una entrada tardía. Después, comprar "al open" ya
#: no es comprar al open y el simulador mediría otra cosa.
MINUTOS_MAXIMO = 60


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%H:%M:%S ET}] {msg}", flush=True)


def estado_real(broker, simbolo: str, numero_orden) -> tuple[str, str]:
    """(estado, explicación) según XTB, no según lo que creímos enviar."""
    posiciones = [p for p in broker.posiciones()
                  if p["lado"] == "buy" and p["ticker"] == simbolo]
    if posiciones:
        acciones = sum(p["acciones"] for p in posiciones)
        return "ejecutada", (f"hay posición de {simbolo}: {acciones:g} "
                             f"acción(es) a {posiciones[0]['precio_entrada']}")

    cola = [o for o in broker.ordenes_pendientes()
            if str(o.get("orden")) == str(numero_orden)
            or o.get("ticker") == simbolo]
    if cola:
        return "en_cola", (f"la orden {cola[0].get('orden')} sigue en cola "
                           f"esperando al mercado")

    return "rechazada", (f"XTB no tiene ni posición ni orden de {simbolo}: la "
                         f"orden {numero_orden} no existe, se rechazó")


def apertura_de_hoy(tickers: list[str]) -> dict:
    """El precio de apertura de hoy, para medir el coste del retraso."""
    if not tickers:
        return {}
    from centinela import datos
    import pandas as pd
    hoy = pd.Timestamp(datetime.now(config.TZ_ET).date())
    fuera = {}
    try:
        series = datos.actualizar_precios(sorted(set(tickers)))
    except Exception as exc:  # noqa: BLE001
        log(f"no se pudieron traer las aperturas: {exc!r}")
        return {}
    for tk, df in series.items():
        if df is not None and len(df) and hoy in df.index:
            try:
                fuera[tk] = float(df.loc[hoy, "Open"])
            except (KeyError, TypeError, ValueError):
                continue
    return fuera


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enviar", action="store_true",
                    help="reenviar a mercado lo que XTB rechazó")
    ap.add_argument("--sesion", default=None, help="sesión a revisar (hoy)")
    args = ap.parse_args()

    hoy = args.sesion or datetime.now(config.TZ_ET).date().isoformat()
    filas = [f for f in ords.filas_de_sesion(hoy) if f.get("tipo") == ords.COMPRA]
    if not filas:
        log(f"no hay compras registradas para {hoy}.")
        return 0

    ahora = datetime.now(config.TZ_ET)
    ac = calendario.apertura_cierre_et(hoy)
    if ac is None:
        log(f"{hoy} no es día de mercado.")
        return 0
    apertura, _ = ac
    minutos = (ahora - apertura).total_seconds() / 60

    problemas: list[str] = []
    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        saldo = b.saldo()
        log(f"cuenta {saldo['cuenta']} (DEMO): saldo {saldo['saldo']:,.2f}")
        log(f"apertura de hoy: {apertura:%H:%M ET} | ahora: {ahora:%H:%M ET} "
            f"({minutos:+.0f} min)")
        log("")

        rechazadas = []
        for f in filas:
            real, porque = estado_real(b, f["simbolo_xtb"], f.get("orden_xtb"))
            anotado = f.get("estado")
            marca = "" if real == anotado else f"  ⚠ estaba anotada '{anotado}'"
            log(f"  {f['ticker']:6} orden {f.get('orden_xtb'):>12} -> "
                f"{real.upper():10} {porque}{marca}")

            if real != anotado:
                # El estado sale de XTB, no de lo que creímos enviar.
                ords.actualizar_ejecucion(
                    f["id"], estado=real,
                    error=("XTB rechazó la orden (mandada "
                           f"{(apertura - datetime.fromisoformat(f['cuando_et'])).total_seconds() / 60:.0f}"
                           " min antes de la apertura)" if real == "rechazada"
                           else ""))
                problemas.append(
                    f"{f['ticker']}: la bitácora decía '{anotado}' y XTB dice "
                    f"'{real}'. {porque}.")
            if real == "rechazada":
                rechazadas.append(f)
                fiabilidad.anotar(fiabilidad.FALLIDA, f["simbolo_xtb"],
                                  ords.COMPRA, porque, id_orden=f["id"])

        if not rechazadas:
            log("")
            log("✅ ninguna orden rechazada.")
            return 1 if problemas else 0

        log("")
        if not args.enviar:
            log(f"{len(rechazadas)} orden(es) rechazada(s). Con --enviar se "
                f"reenvían como entrada tardía.")
            return 1

        if minutos < 0:
            log("el mercado todavía no ha abierto: no se manda nada.")
            return 1
        if minutos > MINUTOS_MAXIMO:
            log(f"han pasado {minutos:.0f} min de la apertura (más de "
                f"{MINUTOS_MAXIMO}). Una compra ahora ya no sería al open: "
                f"NO se envía y la sesión queda perdida.")
            return 1

        aperturas = apertura_de_hoy([f["ticker"] for f in rechazadas])
        registro = ords.cargar_enviadas()
        for f in rechazadas:
            acciones = int(float(f["acciones"]))
            tipo = ords.ENTRADA_TARDIA
            id_orden = ords.identificador(hoy, f["cartera"], f["ticker"], tipo)
            if ords.ya_enviada(registro, id_orden):
                log(f"  {f['ticker']}: la entrada tardía ya se envió hoy.")
                continue

            abre = aperturas.get(f["ticker"])
            orden = ords.Orden(
                id=id_orden, tipo=tipo, cartera=f["cartera"],
                ticker=f["ticker"], acciones=acciones, sesion=hoy,
                objetivo=float(f["objetivo"]) if f.get("objetivo") else None,
                stop=float(f["stop"]) if f.get("stop") else None,
                # Contra la apertura: es lo que el simulador supone que se pagó,
                # así que la diferencia ES el coste del retraso.
                precio_simulador=abre)
            log(f"  enviando {f['ticker']} x{acciones} como entrada tardía "
                f"(apertura {abre})...")
            e = amb.enviar_resolviendo(
                b, orden.simbolo, tipo,
                lambda o=orden: b.comprar(o.simbolo, o.acciones,
                                          objetivo=o.objetivo, stop=o.stop),
                id_orden=orden.id)
            log(f"    -> {e.estado}" + (f" a {e.precio}" if e.precio else "")
                + (f" (orden {e.orden})" if e.orden else "")
                + (f" ERROR: {e.error}" if e.error else ""))

            # Y se confirma contra XTB, que es de lo que iba todo esto.
            real, porque = estado_real(b, orden.simbolo, e.orden)
            log(f"    confirmado en XTB: {real.upper()} — {porque}")
            e.estado = real
            ords.registrar_ejecucion(orden, e)
            # El diario de fiabilidad ya lo anotó `enviar_resolviendo`: un
            # envío deja UNA fila. Anotarlo otra vez aquí contaba la misma
            # orden dos veces y desvirtuaba el porcentaje.
            if real == "ejecutada":
                ords.marcar_enviada(registro, id_orden,
                                    {"estado": real, "orden": e.orden})
            else:
                problemas.append(f"{f['ticker']}: la entrada tardía acabó "
                                 f"'{real}'. {porque}.")
        ords.guardar_enviadas(registro)

    if problemas:
        for p in problemas:
            print(f"::error::{p}", flush=True)
        log(f"❌ {len(problemas)} problema(s).")
        return 1
    log("✅ entradas tardías enviadas y confirmadas en XTB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
