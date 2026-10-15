#!/usr/bin/env python
"""¿La diferencia entre las compras que entran y las que no está en el CÓDIGO?

Manda 1 acción de F por los DOS caminos, uno detrás de otro, con el mercado
abierto, y compara el resultado:

  A) EJECUTOR AUTOMÁTICO: exactamente lo que hace `enviar()` en el ejecutor,
     `ambiguas.enviar_resolviendo(... broker.comprar(...))`.
  B) SCRIPT QUE SÍ FUNCIONÓ: `broker.comprar(...)` directo y una lectura de
     confirmación, como el diagnóstico y la entrada tardía manual.

Las dos llaman al final a la misma `comprar` -> `_cliente.buy` -> NewMarketOrder
con (instrument_id, volumen, lado) y nada más, así que si una entra y otra no,
la diferencia está en la envoltura, no en la orden. Mientras tanto escucha el
stream de OrderService y anota el estado y el MOTIVO LITERAL que XTB devuelva.

LIMPIEZA: F NO es un ticker de la estrategia; cualquier posición suya es residuo
de esta prueba y se vende entera al terminar. READ/One-shot: no entra en el
bucle de reposición ni toca la bitácora del ejecutor.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from centinela import ambiguas as amb, broker_xtb as bx, ordenes as ords  # noqa: E402

SIMBOLO = "F.US"


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _motivo_stream(broker, numero, segundos=20.0) -> str:
    """Escucha el stream de OrderService y devuelve el último estado + motivo
    literal de la orden `numero` (o '' si no aparece)."""
    out = []
    try:
        for ev in broker.eventos_contado(segundos):
            for o in ev.get("orders", []):
                if str(o.get("order_id")) == str(numero):
                    out.append(f"{ev.get('event')}:{o.get('status')}")
            if ev.get("event") == "CIERRE" and ev.get("grpc_message"):
                out.append(f"grpc:{ev.get('grpc_message')}")
    except Exception as exc:  # noqa: BLE001
        return f"(no se pudo escuchar el stream: {exc!r})"
    return " -> ".join(out) or "(sin eventos para esa orden)"


def _cerrar_todo(broker) -> None:
    ahora = [p for p in broker.posiciones()
             if p["lado"] == "buy" and p["ticker"].replace(".US", "") == "F"]
    total = int(sum(p["acciones"] for p in ahora))
    if total >= 1:
        log(f"limpieza: vendiendo {total} F (residuo de la prueba)...")
        c = broker.vender(SIMBOLO, total)
        log(f"  venta de limpieza: {c.estado}" + (f" ERROR: {c.error}" if c.error else ""))


def main() -> int:
    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        log(f"cuenta DEMO verificada: saldo {b.saldo()['saldo']:,.2f}")
        q = b.cotizacion(SIMBOLO)
        log(f"{SIMBOLO}: bid {q['bid']} / ask {q['ask']}")

        # El criterio de ÉXITO es el mismo para los dos: ¿acabó XTB con la
        # posición? (no la cadena de estado, que A promueve a 'ejecutada' con su
        # confirmación y B deja en 'en_cola' por usar una más simple).
        def _tiene_F() -> int:
            return int(sum(p["acciones"] for p in b.posiciones()
                           if p["lado"] == "buy" and p["ticker"].replace(".US", "") == "F"))

        # --- Camino A: el del ejecutor automático --------------------------
        log("\n=== A) EJECUTOR AUTOMÁTICO (enviar_resolviendo + comprar) ===")
        ea = amb.enviar_resolviendo(
            b, SIMBOLO, ords.COMPRA, lambda: b.comprar(SIMBOLO, 1),
            id_orden="prueba_dos|ejecutor")
        log(f"A resultado: estado={ea.estado} orden={ea.orden} precio={ea.precio}"
            + (f" ERROR: {ea.error}" if ea.error else ""))
        log(f"A stream OrderService: {_motivo_stream(b, ea.orden)}")
        time.sleep(2)
        a_entro = ea.estado == "ejecutada" or _tiene_F() >= 1
        log(f"A entró en XTB: {'sí' if a_entro else 'no'}")
        _cerrar_todo(b)
        time.sleep(3)

        # --- Camino B: el del script que sí funcionó -----------------------
        log("\n=== B) SCRIPT DIRECTO (comprar + confirmación simple) ===")
        eb = b.comprar(SIMBOLO, 1)
        log(f"B respuesta inmediata: estado={eb.estado} orden={eb.orden} "
            f"precio={eb.precio}" + (f" ERROR: {eb.error}" if eb.error else ""))
        log(f"B stream OrderService: {_motivo_stream(b, eb.orden)}")
        time.sleep(2)
        b_entro = _tiene_F() >= 1
        log(f"B entró en XTB: {'sí' if b_entro else 'no'}")
        _cerrar_todo(b)

        log("\n=== VEREDICTO ===")
        log(f"A (ejecutor) -> entró: {'sí' if a_entro else 'no'}")
        log(f"B (script)   -> entró: {'sí' if b_entro else 'no'}")
        if a_entro == b_entro:
            log("Mismo resultado por los dos caminos: la diferencia NO está en el "
                "código del envío.")
        else:
            log("Resultado DISTINTO: la diferencia está en la envoltura del envío.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
