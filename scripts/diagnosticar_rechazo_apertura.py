#!/usr/bin/env python
"""Por qué XTB rechaza las compras a mercado en los primeros minutos de sesión.

EL CASO (medido). Las compras mandadas 0-5 min tras la apertura (CTVA 02/10,
WDC 05/10, STX 09/10, FICO 12/10) XTB las ACEPTA —devuelve número de orden,
grpc-status 0— y las descarta sin dejar posición; las mismas a los 20-36 min
entran. El motivo exacto nunca se capturó porque el sistema lo deducía de las
posiciones y de getAllOrders, y getAllOrders NO ve las órdenes de contado. La
única lista con el ESTADO real es OrderService, y XTB purga las órdenes muertas
de ella a los pocos minutos: para cazar el porqué hay que ESTAR ESCUCHANDO el
stream cuando el rechazo llega.

QUÉ HACE. Con el mercado abierto, manda UNA acción del ticker que se le diga y,
mientras, escucha el stream de OrderService y las cabeceras gRPC de la orden.
Imprime: el estado de la orden según XTB a lo largo del tiempo, el frame crudo
de la orden rechazada (por si el motivo viene en un campo que el parser aún no
decodifica) y el minuto de sesión. Al terminar deja la cuenta como estaba: si
la compra entró, la vende; si quedó una orden viva, la cancela.

Con `--espera-hasta HH:MM` duerme hasta esa hora ET antes de mandar, para poder
sondear a una hora concreta de la apertura (09:31, 09:35, 09:45, 10:00…).

Solo opera con `--operar`; sin él solo lee y escucha.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from centinela import broker_xtb as bx, calendario, config, ordenes as ords  # noqa: E402
from diagnosticar_trading_xtb import instalar_espia  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(config.TZ_ET):%H:%M:%S ET}] {msg}", flush=True)


def _dump_crudo(raw_hex: str, prof: int = 0) -> None:
    """Imprime el árbol de campos del proto en crudo, para ver los que el parser
    normal descarta (p. ej. un posible motivo de rechazo)."""
    from xtb_api.grpc.proto import parse_proto_fields
    try:
        campos = parse_proto_fields(bytes.fromhex(raw_hex))
    except Exception as exc:  # noqa: BLE001
        log(f"{'  ' * prof}(no se pudo parsear el crudo: {exc!r})")
        return
    for num, valores in sorted(campos.items()):
        for _, v in valores:
            if isinstance(v, bytes) and v and prof < 6:
                # ¿sub-mensaje? se intenta descender; si no, se enseña en hex/texto.
                try:
                    sub = parse_proto_fields(v)
                except Exception:  # noqa: BLE001
                    sub = None
                if sub:
                    log(f"{'  ' * prof}campo {num}: (mensaje)")
                    _dump_crudo(v.hex(), prof + 1)
                    continue
                texto = v.decode("utf-8", "replace")
                legible = texto if texto.isprintable() else v.hex()
                log(f"{'  ' * prof}campo {num}: {legible!r}")
            else:
                log(f"{'  ' * prof}campo {num}: {v!r}")


def esperar_hasta(hhmm: str) -> None:
    """Duerme hasta la hora ET `HH:MM` de hoy (máx. 6 h, por si algo va mal)."""
    objetivo = datetime.now(config.TZ_ET).replace(
        hour=int(hhmm.split(":")[0]), minute=int(hhmm.split(":")[1]),
        second=0, microsecond=0)
    espera = (objetivo - datetime.now(config.TZ_ET)).total_seconds()
    if espera <= 0:
        log(f"la hora objetivo {hhmm} ya pasó; se manda ya.")
        return
    espera = min(espera, 6 * 3600)
    log(f"esperando {espera / 60:.1f} min hasta las {hhmm} ET...")
    time.sleep(espera)


def _pos_de(broker, simbolo):
    return [p for p in broker.posiciones()
            if p["lado"] == "buy" and p["ticker"].replace(".US", "") == simbolo.replace(".US", "")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", default="F", help="ticker a sondear (barato)")
    ap.add_argument("--acciones", type=int, default=1)
    ap.add_argument("--operar", action="store_true", help="mandar la orden de verdad")
    ap.add_argument("--escuchar", type=float, default=35.0,
                    help="segundos escuchando el stream de OrderService tras mandar")
    ap.add_argument("--espera-hasta", default="", help="dormir hasta HH:MM ET antes de mandar")
    args = ap.parse_args()

    simbolo = ords.simbolo_xtb(args.ticker)
    visto = instalar_espia()                 # cabeceras gRPC de la NewMarketOrder

    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        log(f"cuenta DEMO verificada: saldo {b.saldo()['saldo']:,.2f}")

        hoy = datetime.now(config.TZ_ET).date().isoformat()
        ac = calendario.apertura_cierre_et(hoy)
        if ac:
            apertura, _ = ac
            log(f"apertura de hoy {apertura:%H:%M ET}; ahora "
                f"{(datetime.now(config.TZ_ET) - apertura).total_seconds() / 60:+.0f} min")

        q = b.cotizacion(simbolo)
        log(f"{simbolo}: bid {q['bid']} / ask {q['ask']}")

        if args.espera_hasta:
            esperar_hasta(args.espera_hasta)

        if not args.operar:
            log("(sin --operar: no se manda nada; solo se escucha 10 s el stream)")
            for ev in b.eventos_contado(10.0):
                log(f"  evento {ev['event']}: {len(ev['orders'])} orden(es)")
            return 0

        apertura_dt = ac[0] if ac else None
        min_tras = ((datetime.now(config.TZ_ET) - apertura_dt).total_seconds() / 60
                    if apertura_dt else None)
        log(f"MANDANDO compra {simbolo} x{args.acciones} "
            + (f"a los {min_tras:+.0f} min de la apertura..." if min_tras is not None else "..."))
        t_envio = time.time()
        e = b.comprar(simbolo, args.acciones)
        log(f"  respuesta inmediata: {e.estado}"
            + (f" a {e.precio}" if e.precio else "")
            + (f" (orden {e.orden})" if e.orden else "")
            + (f" ERROR: {e.error}" if e.error else ""))

        # EL NÚCLEO: escuchar el stream de OrderService y ver qué le pasa a la
        # orden, con el frame crudo de cuando quede REJECTED.
        log(f"escuchando OrderService {args.escuchar:.0f} s...")
        rechazos_crudos = []
        for ev in b.eventos_contado(args.escuchar):
            if ev["event"] == "CIERRE":
                log(f"  stream cerrado: grpc-status={ev.get('grpc_status')} "
                    f"{ev.get('grpc_message')}")
                continue
            # Los eventos del stream traen las claves CRUDAS de parse_order_groups
            # (symbol/order_id/type/side/status/volume), no las renombradas de
            # ordenes_contado() (ticker/orden/tipo/lado/estado).
            mias = [o for o in ev["orders"]
                    if str(o.get("symbol", "")).replace(".US", "")
                    == simbolo.replace(".US", "")]
            for o in mias:
                dt = time.time() - t_envio
                log(f"  +{dt:4.0f}s {ev['event']:8} orden {o.get('order_id')} "
                    f"{o.get('type')}/{o.get('side')} x{o.get('volume')} "
                    f"-> {o.get('status')}")
                if o.get("status") in ("REJECTED", "CANCELED", "EXPIRED"):
                    rechazos_crudos.append((o.get("order_id"), ev["raw_hex"]))

        # Estado final por el resumen (por si el stream no lo trajo).
        try:
            res = b.resumen_contado(simbolo, e.orden)
            log(f"resumen final de {simbolo}: estado de la orden {e.orden} = "
                f"{res['estado']}; vivas = {[o['orden'] for o in res['vivas']]}")
        except Exception as exc:  # noqa: BLE001
            log(f"no se pudo leer el resumen final: {exc!r}")

        if rechazos_crudos:
            log("")
            log("=== FRAME CRUDO DE LA ORDEN RECHAZADA (buscando el motivo) ===")
            _dump_crudo(rechazos_crudos[-1][1])
            log("=== fin del crudo ===")

        # Cabeceras gRPC de la NewMarketOrder (por si el rechazo fue síncrono).
        for ll in visto["llamadas"]:
            if "NewMarketOrder" in ll["endpoint"]:
                log(f"NewMarketOrder: HTTP {ll['http']}, {ll['cuerpo_bytes']} bytes de cuerpo"
                    + (f", grpc-status={ll['cabeceras'].get('grpc-status')}"
                       if ll['cabeceras'].get('grpc-status') else "")
                    + (f", {ll['cabeceras'].get('grpc-message')}"
                       if ll['cabeceras'].get('grpc-message') else ""))

        # LIMPIEZA: la cuenta tiene que quedar como estaba. El ticker de prueba
        # NUNCA es de la estrategia (F no está en la cartera), así que CUALQUIER
        # posición suya es residuo de la prueba y se cierra entera —esto limpia
        # también un residuo de un intento anterior que muriera antes de limpiar—.
        time.sleep(3)
        ahora = _pos_de(b, simbolo)
        total = int(sum(p["acciones"] for p in ahora))
        if total >= 1:
            log(f"cerrando {total} acción/es de {simbolo} (residuo de la prueba)...")
            c = b.vender(simbolo, total)
            log(f"  venta de limpieza: {c.estado}" + (f" ERROR: {c.error}" if c.error else ""))
        try:
            vivas = [o["orden"] for o in b.resumen_contado(simbolo, e.orden)["vivas"]]
            if vivas:
                log(f"cancelando órdenes vivas {vivas}: {b.cancelar_ordenes(vivas)}")
        except Exception as exc:  # noqa: BLE001
            log(f"no se pudo revisar/cancelar vivas: {exc!r}")

    log("")
    log("VEREDICTO: revisa arriba el estado de la orden a lo largo del tiempo y, "
        "si hubo rechazo, el frame crudo y las cabeceras.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
