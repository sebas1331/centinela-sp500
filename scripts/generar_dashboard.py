#!/usr/bin/env python
"""Genera el dashboard estático de docs/ que sirve GitHub Pages.

QUÉ HACE Y QUÉ NO
-----------------
Este script es SOLO LECTURA sobre el sistema de trading. No toca el modelo, ni el
umbral, ni las features, ni los objetivos, ni los stops, ni la bitácora. Lee lo
que el pipeline ya dejó escrito (bitacora.csv, estado/estado.json), lo agrega,
lo valora a precios de mercado y lo publica en docs/.

DÓNDE SE CALCULA CADA COSA
--------------------------
Todos los agregados (win rate, expectancy, profit factor, P&L acumulado) se
calculan AQUÍ, en Python, y viajan ya cocinados dentro de docs/datos.json. El
HTML no calcula nada: solo pinta. Una sola fuente de verdad, y además comprobable
por los tests sin necesidad de un navegador.

IDEMPOTENCIA
------------
El script no estampa la hora de ejecución en ningún sitio. La marca temporal que
enseña el dashboard es la fecha del último commit que tocó DATOS reales
(bitacora.csv, estado/, reportes/), no la de este script. Así, si el post-cierre
no cambió nada, datos.json sale byte a byte idéntico, git no ve diff y no hay
commit de ruido. Y como los commits del propio dashboard solo tocan docs/,
tampoco mueven esa marca ni se realimentan.

CONVENCIONES DE UNIDADES
------------------------
En bitacora.csv, `pnl_pct` es una FRACCIÓN (-0.12 = -12%). En datos.json todos
los porcentajes van ya en PUNTOS PORCENTUALES (-12.0), que es lo que el HTML
pinta tal cual. La conversión ocurre una sola vez, aquí.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import calendario, config, cuenta, datos  # noqa: E402

DOCS_DIR = config.BASE_DIR / "docs"
PLANTILLA = Path(__file__).resolve().parent / "plantilla_dashboard.html"
RUTA_BITACORA = config.BASE_DIR / "bitacora.csv"
RUTA_ESTADO = config.ESTADO_DIR / "estado.json"

#: Ficheros cuyo último commit marca "última actualización" en la cabecera. Se
#: excluye docs/ a propósito: si el propio dashboard contara, cada publicación
#: cambiaría la marca y generaría el commit siguiente, y así sin fin.
RUTAS_DATOS = ("bitacora.csv", "estado", "reportes", "logs")

#: Techo de operaciones que se vuelcan al JSON. Hoy son decenas y el diseño pide
#: "sin paginación", pero un fichero que crece sin límite acabaría rompiendo el
#: presupuesto de 500 KB y la fluidez en móvil. Si algún día se supera, es mejor
#: enterarse por este error que servir un dashboard que tarda en abrir.
MAX_OPERACIONES = 5000

#: Etiquetas de motivo de salida tal y como se enseñan. La bitácora las guarda en
#: minúscula y con vocabulario cerrado (objetivo / stop / tiempo).
MOTIVOS = {"objetivo": "Objetivo", "stop": "Stop", "tiempo": "Tiempo"}

#: Día en que el simulador dejó de poder abrir dos posiciones del mismo ticker en
#: la misma cartera. Todo lo anterior puede llevar duplicados; lo posterior no.
FECHA_CORRECCION_DUPLICADOS = "2026-08-06"


# --------------------------------------------------------------------------- #
# Agregados
# --------------------------------------------------------------------------- #
def _redondear(x) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), 4)


def metricas_cartera(cerradas: pd.DataFrame) -> dict:
    """Métricas de una cartera sobre sus operaciones YA CERRADAS.

    Interpretaciones (explícitas para que no haya que adivinarlas):
      - Ganadora  = pnl > 0. Una operación exactamente plana no cuenta como
        ganadora ni como perdedora en el profit factor, pero sí en el
        denominador del win rate: "de cada 100 cerradas, cuántas ganaron".
      - Expectancy = media aritmética del P&L de las cerradas, en puntos
        porcentuales. Es la forma corta de win_rate*media_ganancia -
        loss_rate*media_pérdida, que da exactamente lo mismo.
      - Profit factor = suma de ganancias / |suma de pérdidas|. Sin ninguna
        pérdida el cociente no está definido: se devuelve null y el dashboard
        pinta "∞" en vez de inventarse un número.
    """
    n = int(len(cerradas))
    if n == 0:
        return {"cerradas": 0, "win_rate": None, "expectancy": None,
                "profit_factor": None, "mejor": None, "peor": None}

    pnl = cerradas["pnl_pct_pp"]
    ganancias = pnl[pnl > 0].sum()
    perdidas = pnl[pnl < 0].sum()

    def _extremo(fila) -> dict:
        salida = fila["fecha_salida"]
        return {"ticker": fila["ticker"], "pnl_pct": _redondear(fila["pnl_pct_pp"]),
                "fecha_salida": None if pd.isna(salida) else str(salida)}

    return {
        "cerradas": n,
        "win_rate": _redondear(100.0 * (pnl > 0).sum() / n),
        "expectancy": _redondear(pnl.mean()),
        "profit_factor": (None if perdidas == 0
                          else _redondear(ganancias / abs(perdidas))),
        "mejor": _extremo(cerradas.loc[pnl.idxmax()]),
        "peor": _extremo(cerradas.loc[pnl.idxmin()]),
    }


def curva_equity(cerradas: pd.DataFrame, sesiones: list[str],
                 cuentas: dict | None = None) -> list[dict]:
    """Evolución DIARIA del valor de cada cuenta, en dólares.

    Los puntos son SESIONES, no fechas de salida: el equity de una cuenta se
    mueve todos los días aunque no cierre nada, y una curva que solo tuviera
    puntos en los cierres escondería justo los tramos de caída, que es lo que
    más importa ver.

    `n_cerradas_a/b` acompaña a cada punto para que el globo del gráfico pueda
    decir cuántas operaciones llevaba cerradas la cartera ese día.
    """
    if not sesiones:
        return []
    cerr = cerradas.dropna(subset=["fecha_salida"]) if not cerradas.empty else cerradas

    equity = {}
    if cuentas:
        for c in ("A", "B"):
            curva = cuentas[c]["curva"]
            equity[c] = dict(zip(curva["fecha"], curva["equity"]))

    n = {"A": 0, "B": 0}
    puntos = []
    for fecha in sesiones:
        if not cerr.empty:
            del_dia = cerr[cerr["fecha_salida"] == fecha]
            for c in ("A", "B"):
                n[c] += int((del_dia["portafolio"] == c).sum())
        punto = {
            "fecha": str(fecha),
            "n_cerradas_a": n["A"],
            "n_cerradas_b": n["B"],
        }
        if equity:
            punto["equity_a"] = _redondear(equity["A"].get(fecha))
            punto["equity_b"] = _redondear(equity["B"].get(fecha))
        puntos.append(punto)
    return puntos


#: El criterio de "entrada duplicada por el bug" vive en el paquete, no aquí:
#: la auditoría de fiabilidad y las notificaciones necesitan exactamente el
#: mismo, y dos copias acabarían contando universos distintos sin que nadie se
#: entere. Se reexporta con este nombre porque es como lo llaman los tests.
marcar_duplicadas = cuenta.marcar_duplicadas


def _vista(cerradas: pd.DataFrame, abiertas: pd.DataFrame,
           sesiones: list[str], cuentas: dict | None = None) -> dict:
    """Los agregados a partir de un subconjunto de operaciones."""
    pnl = cerradas["pnl_pct_pp"]
    fechas = cerradas["fecha_salida"].dropna() if not cerradas.empty else []
    return {
        "resumen": {
            "cerradas": int(len(cerradas)),
            "abiertas": int(len(abiertas)),
            "win_rate": _redondear(100.0 * (pnl > 0).sum() / len(cerradas))
                        if len(cerradas) else None,
            "pnl_acumulado": _redondear(pnl.sum()) if len(cerradas) else None,
            "ultima_cerrada": str(max(fechas)) if len(fechas) else None,
        },
        "comparativa": {
            c: {**metricas_cartera(cerradas[cerradas["portafolio"] == c]),
                "abiertas": int((abiertas["portafolio"] == c).sum())}
            for c in ("A", "B")
        },
        "curva": curva_equity(cerradas, sesiones, cuentas),
    }


def sesiones_del_periodo(bit: pd.DataFrame) -> list[str]:
    """Sesiones de mercado desde la primera entrada hasta la última actividad."""
    if bit.empty:
        return []
    inicio = str(bit["fecha_entrada"].min())
    fin = str(max(bit["fecha_entrada"].max(),
                  bit["fecha_salida"].dropna().max() if bit["fecha_salida"].notna().any()
                  else bit["fecha_entrada"].max()))
    return [d.date().isoformat() for d in calendario.sesiones_en_rango(inicio, fin)]


def _exigir_precios(bit: pd.DataFrame, precios: dict) -> None:
    """Sin precios de las posiciones ABIERTAS no hay marca a mercado posible.

    `datos.descargar` no lanza cuando un ticker falla: lo deja fuera del dict.
    Si esto no lo comprobara, una descarga a medias valoraría las posiciones
    abiertas a su precio de coste y el dashboard publicaría un drawdown y un
    Sharpe distintos SIN QUE NADA LO DIJERA. Ese silencio es exactamente lo que
    este repositorio lleva dos incidentes intentando hacer imposible: mejor un
    dashboard rojo que un dashboard que miente en la cifra principal.
    """
    abiertos = set(bit[bit["estado"] != "cerrada"]["ticker"].dropna())
    faltan = sorted(t for t in abiertos if t not in precios)
    if faltan:
        raise RuntimeError(
            f"Faltan precios de {len(faltan)} ticker(s) con posición abierta "
            f"({', '.join(faltan)}). Sin ellos no se puede marcar la cuenta a "
            f"mercado y las métricas de riesgo saldrían falseadas.")


def cuentas_simuladas(bit: pd.DataFrame, sesiones: list[str],
                     precios: dict | None = None) -> dict:
    """La cuenta de cada cartera, en dólares y neta de fricciones.

    Esta es AHORA la cifra principal del dashboard. La suma de retornos que se
    publicaba antes sigue abajo, con su etiqueta: mide la estrategia, no el
    dinero, y las dos cosas no coinciden porque una cuenta compone al cerrar y
    solo tiene 20 slots.

    Necesita precios para marcar a mercado las posiciones abiertas, así que
    este generador ya no es puramente offline. Es un riesgo aceptado y acotado:
    el dashboard vive en su propio job (ver postcierre.yml) y si la descarga
    falla, lo único que ocurre es que la web se queda con los datos de ayer,
    con la bitácora y el estado ya cerrados y verificados varios jobs antes.
    """
    if precios is None:
        # Vía caché incremental, no descarga en bruto: el escaneo del día ya
        # dejó los parquet al día y aquí solo hace falta leerlos. Tres jobs
        # pidiendo lo mismo a yfinance por separado es como se consigue que
        # empiece a limitar.
        precios = (datos.actualizar_precios(sorted(bit["ticker"].dropna().unique()))
                   if sesiones else {})
    _exigir_precios(bit, precios)
    salida = {}
    for c in ("A", "B"):
        cta = cuenta.simular(bit[bit["portafolio"] == c], fricciones=True)
        curva = cuenta.curva_diaria(cta, precios, sesiones)
        m = cuenta.metricas(curva, cta["capital_inicial"])
        salida[c] = {"cta": cta, "curva": curva, "metricas": m}
    return salida


def bloque_cuenta(cuentas: dict) -> dict:
    """Lo que pinta la sección "Cuenta simulada", ya cocinado."""
    return {
        c: {
            "capital_inicial": _redondear(cuentas[c]["cta"]["capital_inicial"]),
            "capital_actual": cuentas[c]["metricas"]["equity_final"],
            "rentabilidad": cuentas[c]["metricas"]["rentabilidad_pct"],
            "cagr": cuentas[c]["metricas"]["cagr_pct"],
            "drawdown_max": cuentas[c]["metricas"]["drawdown_max_pct"],
            "sharpe": cuentas[c]["metricas"]["sharpe"],
            "slots": cuentas[c]["cta"]["slots"],
            "slots_usados": len(cuentas[c]["cta"]["abiertas"]),
            "caja": _redondear(cuentas[c]["cta"]["cash"]),
        }
        for c in ("A", "B")
    }


def ordenes_activas(bit: pd.DataFrame, estado: dict, cuentas: dict) -> list[dict]:
    """Lo que debería estar puesto HOY en el broker, posición a posición.

    Tres cosas y en este orden de urgencia:
      - venta al cierre programada: la posición cumple su décima sesión mañana
        (o ya la cumplió), así que sale market-on-close;
      - orden STOP: solo Cartera A;
      - orden LÍMITE de venta: el objetivo vigente de cada posición abierta.

    El día límite sale del estado, que es quien lo lleva; si una posición no lo
    trajera (estados anteriores a que existiera el campo) se recalcula con el
    calendario en vez de omitir la fila.
    """
    limites = {}
    for cart in ("A", "B"):
        for p in estado.get("posiciones", {}).get(cart, []):
            limites[int(p["id"])] = p.get("dia_limite")

    hoy = max(estado.get("ultima_postcierre") or "",
              estado.get("ultima_preapertura") or "")
    manana = calendario.sesion_n_despues(hoy, 1) if hoy else None
    manana_iso = pd.Timestamp(manana).date().isoformat() if manana is not None else None

    tamanos = {t["id"]: t for c in ("A", "B") for t in cuentas[c]["cta"]["trades"]}

    filas = []
    for _, r in bit[bit["estado"] != "cerrada"].iterrows():
        oid = int(r["id"])
        limite = limites.get(oid)
        if not limite:
            s = calendario.sesion_n_despues(str(r["fecha_entrada"]),
                                            config.HORIZONTE_DIAS_HABILES - 1)
            limite = pd.Timestamp(s).date().isoformat()
        t = tamanos.get(oid, {})
        filas.append({
            "id": oid,
            "ticker": r["ticker"],
            "cartera": r["portafolio"],
            "entrada": _redondear(r["precio_entrada"]),
            "objetivo": _redondear(r["objetivo_actual"]),
            "stop": None if pd.isna(r["stop"]) else _redondear(r["stop"]),
            "fecha_limite": limite,
            # Una venta al cierre se programa cuando el día límite ya llegó o
            # llega en la próxima sesión: es el aviso que da tiempo a actuar.
            "cierre_programado": bool(manana_iso and limite <= manana_iso),
            "acciones": _redondear(t.get("acciones")),
            "importe": _redondear(t.get("coste")),
        })
    # Lo urgente arriba: primero los cierres programados, luego por fecha límite.
    filas.sort(key=lambda f: (not f["cierre_programado"], f["fecha_limite"],
                              f["ticker"], f["cartera"]))
    return filas


def _pnl_no_realizado(bit: pd.DataFrame, precios: dict) -> list:
    """P&L en puntos porcentuales de cada posición abierta, a precio de mercado.

    Por RATIO contra el open del día de entrada de la misma serie descargada, no
    contra el precio_entrada de la bitácora: aquel se guardó ajustado en su día
    y esta serie está reajustada por los dividendos posteriores. Es el mismo
    criterio que usa `cuenta.curva_diaria`, y por la misma razón.

    None cuando no hay precio: el dashboard lo pinta como "—" en vez de
    inventarse un cero, que se leería como "esta posición no se ha movido".
    """
    salida = []
    for _, r in bit.iterrows():
        if r["estado"] == "cerrada":
            salida.append(None)
            continue
        df = precios.get(r["ticker"])
        ts = pd.Timestamp(r["fecha_entrada"])
        if df is None or ts not in df.index or df.empty:
            salida.append(None)
            continue
        base = float(df.loc[ts, "Open"])
        ultimo = float(df["Close"].iloc[-1])
        salida.append(None if base <= 0 else 100.0 * (ultimo / base - 1.0))
    return salida


def _ultimo_commit_de_datos() -> str | None:
    """Fecha ISO del último commit que tocó datos reales (no docs/).

    Es la marca de "última actualización" de la cabecera. Si no hay git (tests,
    tarball suelto) se devuelve None y el HTML cae en `estado.actualizado`.
    """
    try:
        salida = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", *RUTAS_DATOS],
            cwd=config.BASE_DIR, capture_output=True, text=True,
            check=True, timeout=60,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return salida or None


def construir_datos(precios: dict | None = None) -> dict:
    """Todo lo que el dashboard necesita, ya calculado.

    `precios` se inyecta en los tests para que no toquen la red; en producción
    se descarga aquí dentro (ver `cuentas_simuladas`).
    """
    bit = pd.read_csv(RUTA_BITACORA)
    if len(bit) > MAX_OPERACIONES:
        raise RuntimeError(
            f"bitacora.csv tiene {len(bit)} operaciones y el techo del dashboard "
            f"es {MAX_OPERACIONES}. Hay que paginar o recortar antes de seguir "
            f"publicando un JSON que ya no cabe en el presupuesto de 500 KB.")

    estado = json.loads(RUTA_ESTADO.read_text(encoding="utf-8"))

    bit["pnl_pct_pp"] = bit["pnl_pct"] * 100.0  # fracción -> puntos porcentuales
    bit["duplicada"] = marcar_duplicadas(bit)
    limpias = bit[~bit["duplicada"]]
    sesiones = sesiones_del_periodo(limpias)

    if precios is None:
        precios = (datos.actualizar_precios(sorted(bit["ticker"].dropna().unique()))
                   if sesiones else {})

    # El P&L no realizado de las abiertas se valora AQUÍ, contra el último
    # cierre descargado. Antes salía de reportes/mfe_actual.md; desde que el job
    # `mfe` dejó de ejecutarse (2026-09-25) ese informe se queda congelado, y
    # leerlo publicaría el P&L del día en que el análisis corrió por última vez
    # sin que nada lo dijera.
    bit["pnl_abierta_pp"] = _pnl_no_realizado(bit, precios)
    limpias = bit[~bit["duplicada"]]
    cerradas = bit[bit["estado"] == "cerrada"].copy()
    abiertas = bit[bit["estado"] != "cerrada"].copy()
    cerradas_ok = limpias[limpias["estado"] == "cerrada"].copy()
    abiertas_ok = limpias[limpias["estado"] != "cerrada"].copy()

    operaciones = []
    for _, r in bit.iterrows():
        es_cerrada = r["estado"] == "cerrada"
        pnl = (_redondear(r["pnl_pct_pp"]) if es_cerrada
               else _redondear(r["pnl_abierta_pp"]))
        operaciones.append({
            "id": int(r["id"]),
            "ticker": r["ticker"],
            "cartera": r["portafolio"],
            "estado": "cerrada" if es_cerrada else "abierta",
            "fecha_entrada": r["fecha_entrada"],
            "precio_entrada": _redondear(r["precio_entrada"]),
            "fecha_salida": None if pd.isna(r["fecha_salida"]) else r["fecha_salida"],
            "precio_salida": _redondear(r["precio_salida"]),
            "pnl_pct": pnl,
            # `no_realizado` es lo que hace que el HTML anteponga "~": ese P&L es
            # una marca a mercado contra el último cierre disponible, no dinero
            # realizado, y no entra en ninguna estadística de cerradas.
            "no_realizado": not es_cerrada,
            # Segunda (o siguiente) entrada del mismo ticker en la misma cartera
            # con la anterior aún viva: la huella del bug del 2026-08-06.
            "es_duplicada": bool(r["duplicada"]),
            "motivo": (MOTIVOS.get(r["motivo_salida"], r["motivo_salida"])
                       if es_cerrada else "Abierta"),
        })

    # La cuenta se calcula SIEMPRE sobre la vista limpia: las 13 entradas del
    # bug del 2026-08-06 llegaron a poner 30 posiciones vivas a la vez en la
    # Cartera B, diez más de las que caben en la cuenta. Incluirlas no daría una
    # cifra "con duplicados": daría una cifra imposible.
    ctas = cuentas_simuladas(limpias, sesiones, precios)

    limpio = _vista(cerradas_ok, abiertas_ok, sesiones, ctas)

    return {
        "cuenta": bloque_cuenta(ctas),
        "ordenes": ordenes_activas(limpias, estado, ctas),
        "resumen": limpio["resumen"],
        "carteras": limpio["comparativa"],
        "curva_equity": limpio["curva"],
        "operaciones": operaciones,
        "meta": {
            "actualizado": _ultimo_commit_de_datos() or estado.get("actualizado"),
            "ultima_preapertura": estado.get("ultima_preapertura"),
            "ultima_postcierre": estado.get("ultima_postcierre"),
            "repo": "https://github.com/sebas1331/centinela-sp500",
            # Lo que necesita el aviso de la cabecera: cuántas operaciones están
            # afectadas y desde cuándo el sistema ya no las puede crear.
            "duplicadas": int(bit["duplicada"].sum()),
            "corregido_el": FECHA_CORRECCION_DUPLICADOS,
        },
    }


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #
def _escribir_si_cambia(ruta: Path, contenido: str) -> bool:
    """Escribe solo si el contenido difiere. Devuelve True si tocó el fichero."""
    if ruta.exists() and ruta.read_text(encoding="utf-8") == contenido:
        return False
    ruta.write_text(contenido, encoding="utf-8")
    return True


def generar(destino: Path = DOCS_DIR,
            precios: dict | None = None) -> tuple[dict, bool]:
    """Escribe docs/datos.json y docs/index.html.

    Devuelve (datos publicados, si se tocó algún fichero). El segundo valor es lo
    que decide si hay que commitear: sin él, o commiteamos siempre (ruido diario)
    o dejamos de exigir el commit cuando sí toca (que es justo el silencio que
    este repositorio lleva dos incidentes intentando hacer imposible).
    """
    if not PLANTILLA.exists():
        raise FileNotFoundError(f"Falta la plantilla del dashboard: {PLANTILLA}")

    destino.mkdir(parents=True, exist_ok=True)
    datos = construir_datos(precios)

    # `sort_keys` + separadores fijos: dos ejecuciones con los mismos datos
    # producen el mismo byte, que es de lo que depende la idempotencia.
    json_txt = json.dumps(datos, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n"

    cambios = []
    if _escribir_si_cambia(destino / "datos.json", json_txt):
        cambios.append("datos.json")
    if _escribir_si_cambia(destino / "index.html",
                           PLANTILLA.read_text(encoding="utf-8")):
        cambios.append("index.html")
    # Sin .nojekyll, Pages pasa el sitio por Jekyll y se come cualquier fichero
    # que empiece por guion bajo. Aquí no hay ninguno, pero el fichero es gratis
    # y evita una sorpresa futura difícil de diagnosticar.
    if _escribir_si_cambia(destino / ".nojekyll", ""):
        cambios.append(".nojekyll")

    tam_json = len(json_txt.encode("utf-8"))
    tam_html = (destino / "index.html").stat().st_size
    print(f"datos.json: {tam_json / 1024:.1f} KB | index.html: {tam_html / 1024:.1f} KB")
    print(f"operaciones={len(datos['operaciones'])} "
          f"cerradas={datos['resumen']['cerradas']} "
          f"abiertas={datos['resumen']['abiertas']}")
    print("cambios: " + (", ".join(cambios) if cambios else
                         "ninguno (nada que commitear)"), flush=True)

    # Presupuestos del diseño. Se comprueban aquí y no en el test para que el
    # workflow también los haga cumplir cada día, según crezca la bitácora.
    if tam_json > 500 * 1024:
        raise RuntimeError(f"docs/datos.json pesa {tam_json / 1024:.0f} KB y el "
                           f"techo son 500 KB.")
    if tam_html > 50 * 1024:
        raise RuntimeError(f"docs/index.html pesa {tam_html / 1024:.0f} KB y el "
                           f"techo son 50 KB.")
    return datos, bool(cambios)


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera el dashboard estático de docs/")
    ap.add_argument("--destino", type=Path, default=DOCS_DIR)
    args = ap.parse_args()
    _, hubo_cambios = generar(args.destino)

    # El workflow usa esta salida para decidir si toca commitear. Es lo que
    # permite ser idempotente SIN aflojar la verificación de persistencia: si
    # aquí se dice "sí", el paso de commit exige commit, push y confirmación
    # contra el remoto, y se pone rojo si falta cualquiera de los tres.
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as fh:
            fh.write(f"cambios={'si' if hubo_cambios else 'no'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
