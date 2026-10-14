#!/usr/bin/env python
"""Cuántos tickers del S&P 500 NO existen en XTB como acción al contado.

COHR el 2026-10-13 era un candidato del modelo que XTB no ofrece: la orden se
mandó cuatro veces y XTB la rechazó ('Symbol not found'). Antes de decidir una
entrada, el ejecutor comprueba que el símbolo existe (centinela/disponibilidad)
y descarta el que no. Este script mide el tamaño del problema: cuántos de los
~500 del universo no están, para saber si es un caso raro o uno que va a
repetirse.

READ-ONLY: solo busca instrumentos, no manda ninguna orden. La primera búsqueda
descarga la lista de instrumentos de XTB (~11.900) y la cachea, así que las ~500
comprobaciones siguientes son casi instantáneas.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, config, ordenes as ords, universo  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    tickers = universo.tickers_sp500()
    log(f"S&P 500: {len(tickers)} tickers en el universo.")
    faltan, erroneos = [], []
    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        log(f"cuenta DEMO verificada: saldo {b.saldo()['saldo']:,.2f}")
        for t in tickers:
            simbolo = ords.simbolo_xtb(t)
            try:
                if not b.existe_contado(simbolo):
                    faltan.append(t)
            except Exception as exc:  # noqa: BLE001 — un fallo suelto no para el barrido
                erroneos.append((t, repr(exc)))

    log("")
    log(f"=== RESULTADO: {len(faltan)} de {len(tickers)} tickers del S&P 500 NO "
        f"existen en XTB como acción al contado ===")
    if faltan:
        log("No disponibles: " + ", ".join(sorted(faltan)))
    if erroneos:
        log(f"\n{len(erroneos)} no se pudieron comprobar (errores):")
        for t, err in erroneos:
            log(f"  {t}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
