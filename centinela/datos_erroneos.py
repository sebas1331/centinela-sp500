"""Operaciones decididas sobre una serie de precios que no era real.

EL CASO (2026-10-02)
--------------------
`centinela/continuidad.py` cuenta el hallazgo: dos de los 28 tickers del
historial tenían una acción corporativa sin ajustar en la serie de yfinance.
CTVA pasó de 77,65 a 12,57 el 01/10 (escisión) y MRNA de 62,96 a 174,38 el
19/08 (contrasplit). Desde entonces el screener los deja fuera del universo.

Eso arregla el futuro. Este módulo es lo otro: lo que YA se decidió mirando
esas series. El drawdown del 86 % de CTVA no existía, y el ATR de MRNA
calculado a través de un salto de ×2,77 tampoco. Las entradas que salieron de
ahí no son operaciones de la estrategia: son operaciones de un dato roto.

QUÉ HACE Y QUÉ NO
-----------------
Marca esas operaciones y las deja fuera de las ESTADÍSTICAS. No las borra de la
bitácora: ocurrieron, el dinero de la demo se movió de verdad y esconderlas
sería exactamente el silencio que este repositorio lleva tres incidentes
intentando hacer imposible. Siguen en la tabla de operaciones, marcadas, y
siguen en `bitacora.csv` con su P&L. Lo único que cambia es que no cuentan en
el win rate, ni en la expectancy, ni en la cuenta simulada, porque mezclar una
decisión tomada sobre un artefacto con las demás no mide la estrategia: la
ensucia.

POR QUÉ UN REGISTRO DECLARADO Y NO UNA DETECCIÓN AUTOMÁTICA
-----------------------------------------------------------
Se podría recorrer la bitácora llamando a `continuidad.esta_rota` sobre la
serie de cada ticker. Y la cifra publicada cambiaría sola el día que yfinance
reajuste una serie o el salto salga de la ventana de 260 sesiones: el win rate
del panel se movería hacia atrás, en operaciones de hace dos meses, sin que
nadie hubiera decidido nada. Una exclusión que reescribe el pasado por su
cuenta no es auditable.

Así que las ventanas se declaran aquí, a mano, con su causa y su evidencia —el
mismo patrón que `incidentes.py`—. `continuidad.py` las ENCUENTRA; este fichero
las DECLARA. Son dos trabajos distintos a propósito.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config

ARCHIVO = config.ESTADO_DIR / "datos_erroneos.json"

#: Motivo de salida de una posición que se cierra porque su serie no es de fiar.
#: Entra en el vocabulario CERRADO de motivos (ver `scripts/generar_dashboard.py`
#: y `centinela/cuenta.py`): es una venta a mercado, decidida fuera de la
#: estrategia, y tenía que tener nombre propio para no esconderse dentro de
#: "tiempo" ni de "stop", que son salidas que el sistema sí decidió.
MOTIVO_SALIDA = "dato_erroneo"
ETIQUETA = "salida por dato erróneo"

#: Lo que una ventana tiene que traer. Sin evidencia no es una exclusión: es un
#: resultado que no gustó. `hasta` puede faltar (ventana abierta: la serie sigue
#: sin ser de fiar), y por eso no está aquí.
OBLIGATORIOS = ("ticker", "desde", "causa", "evidencia", "detectado")


def cargar(ruta: Path | None = None) -> list[dict]:
    ruta = ruta or ARCHIVO
    if not ruta.exists():
        return []
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("[datos_erroneos] fichero ilegible; se ignora.", flush=True)
        return []
    return datos.get("series", []) if isinstance(datos, dict) else []


def guardar(series: list[dict], ruta: Path | None = None) -> None:
    ruta = ruta or ARCHIVO
    for s in series:
        faltan = [c for c in OBLIGATORIOS if not s.get(c)]
        if faltan:
            raise ValueError(
                f"Serie sin {', '.join(faltan)}: {s.get('ticker') or s}. Una "
                f"exclusión sin causa y sin evidencia no es una exclusión, es "
                f"un resultado que no gustó.")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps({"actualizado": datetime.now(config.TZ_ET).isoformat(),
                    "series": series},
                   ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")


def ventanas(ruta: Path | None = None) -> dict[str, list[dict]]:
    """{ticker: [ventanas]}, para no releer el fichero por cada fila."""
    fuera: dict[str, list[dict]] = {}
    for s in cargar(ruta):
        fuera.setdefault(str(s["ticker"]).upper(), []).append(s)
    return fuera


def tickers(ruta: Path | None = None) -> set[str]:
    return set(ventanas(ruta))


def afectada(ticker: str, fecha_entrada: str, fecha_salida: str | None = None,
             ruta: Path | None = None) -> dict | None:
    """La ventana que ensucia esa operación, si alguna.

    SOLAPAMIENTO, no "entró después". Una posición abierta antes del salto y
    cerrada después tiene el P&L calculado a caballo de la discontinuidad, que
    es el caso más sucio de todos; y una cerrada antes del salto no la toca
    nadie, aunque el ticker esté en el registro. De ahí que haga falta el rango
    de la operación y no solo su fecha de entrada.
    """
    return _afectada_en(ventanas(ruta).get(str(ticker).upper(), []),
                        fecha_entrada, fecha_salida)


def _afectada_en(lista: list[dict], fecha_entrada, fecha_salida) -> dict | None:
    entrada = "" if fecha_entrada is None or pd.isna(fecha_entrada) else str(fecha_entrada)
    # Una posición ABIERTA llega hasta hoy, así que cualquier ventana viva la
    # alcanza: su salida se evalúa con la serie rota que haya en ese momento.
    salida = ("9999-12-31" if fecha_salida is None or pd.isna(fecha_salida)
              else str(fecha_salida))
    for v in lista:
        desde = str(v.get("desde", ""))
        hasta = str(v.get("hasta") or "9999-12-31")
        if salida >= desde and entrada <= hasta:
            return v
    return None


def marcar(bit: pd.DataFrame, ruta: Path | None = None) -> pd.Series:
    """True en cada fila de la bitácora decidida sobre una serie no fiable."""
    vs = ventanas(ruta)
    if bit.empty or not vs:
        return pd.Series(False, index=bit.index, dtype=bool)
    return pd.Series(
        [bool(_afectada_en(vs.get(str(t).upper(), []), fe, fs))
         for t, fe, fs in zip(bit["ticker"], bit["fecha_entrada"],
                              bit["fecha_salida"])],
        index=bit.index, dtype=bool)


def resumen(bit: pd.DataFrame, ruta: Path | None = None) -> dict:
    """Lo que el panel necesita para explicar la exclusión sin adivinar nada."""
    marcadas = marcar(bit, ruta)
    series = []
    for s in sorted(cargar(ruta), key=lambda x: (x["ticker"], x["desde"])):
        de_este = marcadas & (bit["ticker"].astype(str).str.upper()
                              == str(s["ticker"]).upper())
        series.append({
            "ticker": s["ticker"], "desde": s["desde"],
            "hasta": s.get("hasta"), "causa": s["causa"],
            "evidencia": s["evidencia"], "detectado": s["detectado"],
            "operaciones": int(de_este.sum()),
        })
    return {"operaciones": int(marcadas.sum()), "series": series}
