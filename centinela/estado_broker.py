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


def precio_de_mercado(p: dict, precios: dict | None, broker=None,
                      cotizar: bool = False) -> tuple[float | None, str]:
    """El precio actual de una posición, y de dónde sale. Nunca el de entrada.

    XTB devuelve `current_price` a 0 en acciones al contado (visto con CTVA el
    02/10 y con WDC el 05/10), y la página caía al precio de ENTRADA: P&L cero y
    una posición que parecía no moverse. Por orden:
      xtb        el que da XTB, si es positivo;
      bid        el último bid que vio el vigilante de precios (en vivo);
      cotizacion una cotización pedida ahora (solo fuera del vigilante: dentro,
                 pedirla rompería sus suscripciones de ticks);
      None       si no hay nada: un hueco honesto, no un número inventado.
    """
    actual = float(p.get("precio_actual") or 0.0)
    if actual > 0:
        return actual, "xtb"
    if precios and float(precios.get(p["ticker"]) or 0.0) > 0:
        return float(precios[p["ticker"]]), "bid"
    if cotizar and broker is not None:
        try:
            bid = float(broker.cotizacion(p["ticker"])["bid"])
        except Exception as exc:  # noqa: BLE001 — se dice y queda el hueco
            print(f"[broker] sin cotización de {p['ticker']} ({exc!r}): su valor "
                  f"de mercado queda vacío.", flush=True)
            return None, "sin-precio"
        if bid > 0:
            return bid, "cotizacion"
    return None, "sin-precio"


def _stops(broker) -> dict:
    """Sin la lista de contado (dobles de tests antiguos) no hay stops que ver;
    si la lista falla, se dice con None y no con {} ("no hay ninguna")."""
    if not hasattr(broker, "ordenes_contado"):
        return {}
    from . import proteccion
    try:
        return proteccion.stops_por_simbolo(broker)
    except Exception as exc:  # noqa: BLE001 — la foto sale igual, sin stops
        print(f"::warning::no se pudo leer las órdenes de contado ({exc!r})", flush=True)
        return None


def volcar(broker, candado_ok: bool = True, precios: dict | None = None,
           cotizar: bool = False) -> dict:
    """Deja en estado/broker.json lo que XTB dice de la cuenta ahora mismo.

    Lo consume la página de operativa, que es estática y no puede preguntarle
    nada a nadie. Se escribe SIN un solo dato de sesión: ni TGT, ni cookies, ni
    credenciales — ese fichero acaba en una página pública.

    EQUITY = SALDO + VALOR DE MERCADO DE LAS POSICIONES (2026-10-05). XTB
    contabiliza las acciones al contado en modo CAJA —medido: al comprar 1 F a
    12,18 el saldo bajó 12,18— y su campo `equity` no suma lo que valen las
    acciones: devolvía lo mismo que el saldo. Así que la página enseñaba un
    equity 1.758,84 $ más bajo de lo que era, con WDC abierta. Se calcula aquí,
    con el precio de mercado de `precio_de_mercado`; el de XTB queda guardado
    como `equity_xtb` por si algún día empieza a contarlo.

    `precios` son los bids que conoce el vigilante (símbolo -> bid); `cotizar`
    permite pedir cotización a quien no tiene ticks suscritos (el ejecutor).
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

    filas, valor_total, precios_completos = [], 0.0, True
    for p in posiciones:
        if p["lado"] != "buy":
            continue
        actual, fuente = precio_de_mercado(p, precios, broker, cotizar)
        acciones = float(p["acciones"])
        entrada = float(p["precio_entrada"])
        valor = None if actual is None else round(acciones * actual, 2)
        if valor is None:
            precios_completos = False
        else:
            valor_total += valor
        filas.append({
            "ticker": p["ticker"], "acciones": acciones,
            "precio_entrada": entrada,
            "precio_actual": actual, "precio_fuente": fuente,
            "invertido": round(acciones * entrada, 2),
            "valor": valor,
            # El P&L de XTB también viene a 0: se calcula con el precio real.
            "pnl": (None if actual is None
                    else round(acciones * (actual - entrada), 2)),
            "coste": coste.get(p["ticker"].replace(".US", "").replace("-", ".")),
        })

    datos = {
        "leido": datetime.now(config.TZ_ET).isoformat(),
        "candado_ok": bool(candado_ok),
        "saldo": saldo["saldo"],
        "equity_xtb": saldo["equity"],
        "valor_posiciones": round(valor_total, 2),
        # None si falta el precio de alguna: un equity a medias sería falso.
        "equity": (round(float(saldo["saldo"]) + valor_total, 2)
                   if precios_completos else None),
        "divisa": saldo["divisa"],
        "posiciones": filas,
        # Las stops vivas en XTB (centinela/proteccion.py): la página dice si
        # cada stop está "en XTB" o "solo vigilante", y la siguiente vuelta las
        # usa para saber que una stop que desaparece EXISTÍA.
        "stops": _stops(broker),
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
