"""Series de precios rotas por una acción corporativa sin ajustar.

EL CASO (2026-10-02)
--------------------
CTVA pasó de 77,65 $ a 12,57 $ de un día para otro, con 88 millones de volumen
—22 veces lo normal—. No fue un desplome: fue una escisión, y yfinance no
reajustó el histórico. El sistema vio esto:

    CTVA | dd=86.2% | prob=1.000 | ENTRAR

Un drawdown del 86 % que **no existe**: la acción venía de 80 $ y cotiza a 12,9
solo porque repartió el resto en otra compañía. El drawdown real es ~0 %. El
modelo le dio confianza máxima a un artefacto, y de paso el ATR(14) calculado a
través del salto salió al 51 % del precio, lo que puso el objetivo en +103 % y
el stop en su suelo.

Esto no es un caso raro: barriendo los 28 tickers del historial apareció otro
—MRNA, ×2,77 el 19/08, probable contrasplit—. Dos de 28.

POR QUÉ SE EXCLUYE Y NO SE CORRIGE
----------------------------------
Se podría intentar reconstruir el factor de ajuste y reescalar la serie. Pero
adivinar el factor de una escisión a partir del salto es justo eso, adivinar, y
un error ahí no se ve: produce una serie plausible y mal. Mientras la serie sea
dudosa, el ticker sale del universo. Cuando el salto queda fuera de la ventana
que el modelo mira, vuelve solo.

Es la decisión conservadora: deja de operar algo que no se entiende, en vez de
operarlo con un número inventado.
"""
from __future__ import annotations

import pandas as pd

from . import config

#: Fuera de esta horquilla, el salto de un día a otro no es un movimiento de
#: mercado. El límite inferior (0,60) deja pasar un desplome del 40 %, que es
#: brutal pero ocurre; el superior (1,70) deja pasar un +70 % en un día, que
#: también. Un split o una escisión se van muy por encima de eso.
CAIDA_MAXIMA = 0.60
SUBIDA_MAXIMA = 1.70

#: Cuántas sesiones hacia atrás importa. Es la ventana que el modelo mira: si
#: el salto ya salió de ahí, no contamina ni las features ni el ATR, y el
#: ticker vuelve al universo por su cuenta.
VENTANA = 260


def saltos(df: pd.DataFrame, ventana: int = VENTANA) -> list[dict]:
    """Los saltos sospechosos de la serie, del más reciente al más antiguo."""
    if df is None or len(df) < 3:
        return []
    cierres = df["Close"].tail(ventana)
    ratio = cierres / cierres.shift(1)
    malos = ratio[(ratio < CAIDA_MAXIMA) | (ratio > SUBIDA_MAXIMA)].dropna()
    fuera = []
    for fecha, r in malos.items():
        fuera.append({
            "fecha": pd.Timestamp(fecha).date().isoformat(),
            "ratio": round(float(r), 4),
            "antes": round(float(cierres.shift(1)[fecha]), 2),
            "despues": round(float(cierres[fecha]), 2),
        })
    return sorted(fuera, key=lambda s: s["fecha"], reverse=True)


def esta_rota(df: pd.DataFrame, ventana: int = VENTANA) -> dict | None:
    """El salto más reciente que inhabilita la serie, o None si está limpia."""
    encontrados = saltos(df, ventana)
    return encontrados[0] if encontrados else None


def motivo(ticker: str, salto: dict) -> str:
    """La línea que va al log, para que se vea por qué se descartó."""
    direccion = "cayó" if salto["ratio"] < 1 else "subió"
    return (f"{ticker} | SERIE ROTA: el {salto['fecha']} {direccion} de "
            f"{salto['antes']} a {salto['despues']} (×{salto['ratio']}). "
            f"Parece una acción corporativa sin ajustar, no un movimiento de "
            f"mercado: el drawdown y el ATR que saldrían de esta serie no son "
            f"reales. Fuera del universo hasta que el salto salga de la ventana.")
