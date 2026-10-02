"""Lo que XTB dice de la cuenta, volcado a estado/broker.json.

POR QUÉ ESTÁ AQUÍ Y NO EN EL EJECUTOR
--------------------------------------
Lo necesitan tres caminos distintos —el ejecutor, la entrada tardía y el
vigilante de precios— y vivía dentro de uno. El 2026-10-02 eso se notó: CTVA se
compró por la entrada tardía, que no lo llamaba, y la página estuvo media sesión
diciendo "0 posiciones" con 134 acciones abiertas en XTB.

Regla: se vuelca tras cada compra, cada venta, cada entrada tardía y en cada
latido del vigilante de precios. Y si no se puede leer, REVIENTA: una página que
enseña la cuenta de ayer sin decirlo es peor que una página en rojo.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from . import config, cuenta

ARCHIVO = config.ESTADO_DIR / "broker.json"


def volcar(broker, candado_ok: bool = True) -> dict:
    """Deja en estado/broker.json lo que XTB dice de la cuenta ahora mismo.

    Lo consume la página de operativa, que es estática y no puede preguntarle
    nada a nadie. Se escribe SIN un solo dato de sesión: ni TGT, ni cookies, ni
    credenciales — ese fichero acaba en una página pública.

    El coste de las posiciones sale de la cuenta simulada, porque XTB no lo
    devuelve: el broker da precio de entrada y volumen, y multiplicar los dos
    ignoraría las fricciones que la cuenta sí modela.
    """
    # SIN try. Antes un fallo aquí se imprimía y se seguía, y la página se
    # quedaba enseñando la cuenta de ayer como si fuera la de ahora: el
    # 2026-10-02 dijo "0 posiciones" durante toda la vida de CTVA. Quien llama
    # decide qué hacer con el error; lo que no puede pasar es que nadie lo vea.
    saldo = broker.saldo()
    posiciones = broker.posiciones()

    # La misma vista limpia que el panel: sin duplicadas y sin las decididas
    # sobre una serie rota. Si cada página se filtrara a su manera, el coste de
    # una posición saldría distinto aquí y allí sin que nadie se enterara.
    limpias = cuenta.vista_limpia(pd.read_csv(config.BASE_DIR / "bitacora.csv"))
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
    ruta = ARCHIVO
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    if ruta == config.ESTADO_DIR / "broker.json":
        from . import diario
        diario.anotar("broker", datos=datos)
    print(f"estado del broker volcado: {len(datos['posiciones'])} posiciones",
          flush=True)
    return datos
