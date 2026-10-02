"""Lo que XTB dice de la cuenta, volcado a estado/broker.json.

POR QUÉ ESTÁ AQUÍ Y NO EN EL EJECUTOR
--------------------------------------
Lo necesitan tres caminos distintos —el ejecutor, la entrada tardía y el
vigilante de precios— y vivía dentro de uno. El 2026-10-02 eso se notó: CTVA se
compró por la entrada tardía, que no lo llamaba, y la página estuvo media sesión
diciendo "0 posiciones" con 134 acciones abiertas en XTB.

Regla simple: todo camino que cambie posiciones vuelca el estado al terminar.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from . import config, cuenta


def volcar(broker, candado_ok: bool = True) -> None:
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
        print(f"no se pudo volcar el estado del broker: {exc!r}")
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
    print(f"estado del broker volcado: {len(datos['posiciones'])} posiciones",
          flush=True)
