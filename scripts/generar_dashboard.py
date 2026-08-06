#!/usr/bin/env python
"""Genera el dashboard estático de docs/ que sirve GitHub Pages.

QUÉ HACE Y QUÉ NO
-----------------
Este script es SOLO LECTURA sobre el sistema de trading. No toca el modelo, ni el
umbral, ni las features, ni los objetivos, ni los stops, ni la bitácora. Lee lo
que el pipeline ya dejó escrito (bitacora.csv, reportes/mfe_actual.md,
estado/estado.json), lo agrega y lo publica en docs/.

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

from centinela import config  # noqa: E402

DOCS_DIR = config.BASE_DIR / "docs"
PLANTILLA = Path(__file__).resolve().parent / "plantilla_dashboard.html"
RUTA_MFE = config.REPORTES_DIR / "mfe_actual.md"
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
# Lectura del informe MFE/MAE (markdown)
# --------------------------------------------------------------------------- #
def _num(celda: str) -> float | None:
    """'+33.72%' -> 33.72 ; '1.510,26' no aplica ; '' / 'n/d' -> None."""
    if celda is None:
        return None
    limpio = celda.replace("%", "").replace("+", "").replace(",", "").strip()
    if not limpio or limpio.lower() in {"n/d", "nan", "-", "—"}:
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


def _si_no(celda: str) -> bool:
    """'✅ sí' -> True ; 'no' -> False. El emoji es del informe, no del dashboard."""
    return "sí" in (celda or "").lower()


def _tabla_tras(seccion: str, texto: str) -> list[dict]:
    """Primera tabla markdown que aparece bajo `seccion`, como lista de dicts.

    Se indexa POR NOMBRE de columna, no por posición: las dos tablas del informe
    (abiertas y cerradas) tienen distinto número de columnas, y así añadir una
    columna al informe no descoloca silenciosamente el dashboard.
    """
    idx = texto.find(seccion)
    if idx == -1:
        return []
    lineas = texto[idx:].splitlines()[1:]
    filas: list[dict] = []
    cabecera: list[str] | None = None
    for linea in lineas:
        linea = linea.strip()
        if linea.startswith("##"):
            break
        if not linea.startswith("|"):
            if cabecera is not None and filas:
                break
            continue
        celdas = [c.strip() for c in linea.strip("|").split("|")]
        if cabecera is None:
            cabecera = celdas
            continue
        if all(set(c) <= set("-: ") for c in celdas):  # separador |---|---|
            continue
        filas.append(dict(zip(cabecera, celdas)))
    return filas


def leer_mfe(ruta: Path = RUTA_MFE) -> tuple[list[dict], str | None]:
    """Posiciones abiertas del informe MFE/MAE + su fecha de generación."""
    if not ruta.exists():
        raise FileNotFoundError(
            f"No existe {ruta}. El dashboard necesita el informe MFE/MAE para el "
            f"P&L no realizado de las posiciones abiertas.")
    texto = ruta.read_text(encoding="utf-8")

    m = re.search(r"_Generado (.+?)_", texto)
    generado = m.group(1).strip() if m else None

    posiciones = []
    for f in _tabla_tras("## Posiciones abiertas", texto):
        posiciones.append({
            "ticker": f.get("Ticker", ""),
            "cartera": f.get("Cartera", ""),
            "fecha_entrada": f.get("Entrada", ""),
            "precio_entrada": _num(f.get("Precio entrada")),
            "mfe_pct": _num(f.get("MFE %")),
            "mae_pct": _num(f.get("MAE %")),
            "pnl_actual_pct": _num(f.get("P&L actual %")),
            "toco_5": _si_no(f.get("¿Tocó +5%?", "")),
        })
    return posiciones, generado


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


def pnl_por_cartera(cerradas: pd.DataFrame, abiertas: pd.DataFrame) -> dict:
    """P&L acumulado de una cartera, separando lo realizado de lo que no lo es.

      - realizado  = suma del P&L de las operaciones YA CERRADAS. Es dinero
        (simulado) hecho: no puede cambiar.
      - total      = realizado + suma del P&L actual de las ABIERTAS. Ese segundo
        sumando es una marca a mercado contra el último cierre disponible y se
        mueve cada día, así que el total es una foto, no un resultado.

    Las dos cifras son SUMAS de retornos de posiciones equiponderadas, no una
    curva de capital compuesta (ver la nota que el dashboard enseña debajo).

    `abiertas_sin_pnl` cuenta las posiciones abiertas para las que el informe
    MFE/MAE todavía no tiene fila —típicamente una entrada de hoy—, porque si no
    el total saldría corto sin que nada lo dijera.
    """
    realizado = float(cerradas["pnl_pct_pp"].sum()) if len(cerradas) else 0.0
    no_realizado = abiertas["pnl_abierta_pp"].dropna()
    return {
        "pnl_realizado": _redondear(realizado),
        "pnl_total": _redondear(realizado + float(no_realizado.sum())),
        "abiertas_sin_pnl": int(abiertas["pnl_abierta_pp"].isna().sum()),
    }


def curva_equity(cerradas: pd.DataFrame) -> list[dict]:
    """Evolución del P&L acumulado REALIZADO de A y B, punto por fecha de salida.

    Solo entran operaciones cerradas: la curva es de resultado hecho. Lo no
    realizado de las abiertas está en las tarjetas de arriba, que sí avisan de
    que se mueve; mezclarlo aquí convertiría el histórico en algo que cambia de
    forma cada día hacia atrás, que es justo lo que una curva no debe hacer.

    Todas las operaciones que cierran el mismo día se agregan en un solo punto.
    En cada fecha se registra el acumulado de AMBAS carteras, aunque ese día solo
    haya cerrado una: así la otra serie queda plana en su último valor y el HTML
    puede dibujar las dos sobre el mismo eje sin interpolar nada por su cuenta.
    """
    if cerradas.empty:
        return []
    df = cerradas.dropna(subset=["fecha_salida"])
    if df.empty:
        return []

    acumulado = {"A": 0.0, "B": 0.0}
    n = {"A": 0, "B": 0}
    puntos = []
    for fecha in sorted(df["fecha_salida"].unique()):
        del_dia = df[df["fecha_salida"] == fecha]
        for c in ("A", "B"):
            de_la_cartera = del_dia[del_dia["portafolio"] == c]
            acumulado[c] += float(de_la_cartera["pnl_pct_pp"].sum())
            n[c] += int(len(de_la_cartera))
        puntos.append({
            "fecha": str(fecha),
            "pl_acumulado_a": _redondear(acumulado["A"]),
            "pl_acumulado_b": _redondear(acumulado["B"]),
            "n_cerradas_a": n["A"],
            "n_cerradas_b": n["B"],
        })
    return puntos


def marcar_duplicadas(bit: pd.DataFrame) -> pd.Series:
    """True en cada entrada que se abrió teniendo ya ese ticker vivo en su cartera.

    Es la cicatriz del bug corregido el 2026-08-06 (ver CHANGELOG): el filtro de
    tickers ocupados miraba siempre la cartera A, así que cuando el stop cerraba
    la posición de A pero la de B seguía abierta, el ticker volvía a entrar y B
    acababa con dos posiciones del mismo valor a la vez.

    Se marca la SEGUNDA y siguientes, nunca la primera: la primera entrada era
    legítima. Una posición que cierra el mismo día en que se abre la siguiente
    cuenta como solape, porque durante esa sesión las dos estuvieron vivas.
    """
    dup = pd.Series(False, index=bit.index)
    entrada = pd.to_datetime(bit["fecha_entrada"])
    salida = pd.to_datetime(bit["fecha_salida"])
    # Una posición abierta ocupa hasta hoy; se usa el máximo del fichero como
    # "hoy" para que el cálculo no dependa de cuándo se ejecute este script.
    fin = salida.fillna(max(entrada.max(), salida.max()))

    for _, grupo in bit.groupby([bit["ticker"], bit["portafolio"]], sort=False):
        orden = grupo.sort_values(["fecha_entrada", "id"]).index
        for pos, idx in enumerate(orden):
            if any(fin[previo] >= entrada[idx] for previo in orden[:pos]):
                dup[idx] = True
    return dup


def _vista(cerradas: pd.DataFrame, abiertas: pd.DataFrame) -> dict:
    """Los cuatro bloques agregados a partir de un subconjunto de operaciones.

    Se calcula dos veces: con todo, y solo con las operaciones limpias. Que sea
    la MISMA función garantiza que las dos vistas no puedan divergir en la
    definición de win rate, expectancy o P&L; si una cambia, cambian las dos.
    """
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
        "pnl_por_cartera": {
            c: pnl_por_cartera(cerradas[cerradas["portafolio"] == c],
                               abiertas[abiertas["portafolio"] == c])
            for c in ("A", "B")
        },
        "curva": curva_equity(cerradas),
    }


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


def construir_datos() -> dict:
    """Todo lo que el dashboard necesita, ya calculado."""
    bit = pd.read_csv(RUTA_BITACORA)
    if len(bit) > MAX_OPERACIONES:
        raise RuntimeError(
            f"bitacora.csv tiene {len(bit)} operaciones y el techo del dashboard "
            f"es {MAX_OPERACIONES}. Hay que paginar o recortar antes de seguir "
            f"publicando un JSON que ya no cabe en el presupuesto de 500 KB.")

    estado = json.loads(RUTA_ESTADO.read_text(encoding="utf-8"))
    # Ruta explícita, no el valor por defecto de leer_mfe: los defaults se
    # congelan al definir la función y los tests no podrían redirigirla.
    mfe, mfe_generado = leer_mfe(RUTA_MFE)

    # P&L no realizado de las abiertas, indexado por la clave que las distingue.
    # Un mismo ticker puede estar abierto dos veces en la misma cartera con
    # fechas de entrada distintas (p.ej. LITE B el 28 y el 30 de julio), así que
    # la fecha forma parte de la clave.
    pnl_abiertas = {(p["ticker"], p["cartera"], p["fecha_entrada"]): p["pnl_actual_pct"]
                    for p in mfe}

    bit["pnl_pct_pp"] = bit["pnl_pct"] * 100.0  # fracción -> puntos porcentuales
    # El P&L no realizado, como columna: así las tarjetas de "P&L por cartera" y
    # la tabla de operaciones leen exactamente el mismo dato en vez de repetir
    # cada una su propio cruce contra el informe MFE.
    bit["pnl_abierta_pp"] = [
        pnl_abiertas.get(clave)
        for clave in zip(bit["ticker"], bit["portafolio"], bit["fecha_entrada"])
    ]
    bit["duplicada"] = marcar_duplicadas(bit)
    cerradas = bit[bit["estado"] == "cerrada"].copy()
    abiertas = bit[bit["estado"] != "cerrada"].copy()
    limpias = bit[~bit["duplicada"]]
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
            "duplicada": bool(r["duplicada"]),
            "motivo": (MOTIVOS.get(r["motivo_salida"], r["motivo_salida"])
                       if es_cerrada else "Abierta"),
        })

    # P&L acumulado = SUMA de los retornos de cada operación cerrada. Es la
    # lectura correcta para carteras de posiciones equiponderadas e
    # independientes como estas, donde cada entrada arriesga el mismo tamaño;
    # NO es un retorno compuesto de una curva de capital, que exigiría un modelo
    # de asignación de capital que este experimento no tiene.
    todo = _vista(cerradas, abiertas)
    # Vista paralela sin las entradas duplicadas por el bug. Se publica aparte y
    # NO sustituye a la de arriba: la bitácora es el registro de lo que pasó de
    # verdad, cicatrices incluidas, y borrarla de la vista por defecto sería
    # maquillar el histórico en vez de explicarlo.
    limpio = _vista(cerradas_ok, abiertas_ok)

    return {
        "resumen": todo["resumen"],
        "carteras": {c: {**todo["comparativa"][c], **todo["pnl_por_cartera"][c]}
                     for c in ("A", "B")},
        "curva_equity": todo["curva"],
        "resumen_limpio": limpio["resumen"],
        "comparativa_ab_limpia": limpio["comparativa"],
        "pnl_por_cartera_limpio": limpio["pnl_por_cartera"],
        "curva_equity_limpia": limpio["curva"],
        "operaciones": operaciones,
        "mfe": mfe,
        "meta": {
            "actualizado": _ultimo_commit_de_datos() or estado.get("actualizado"),
            "mfe_generado": mfe_generado,
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


def generar(destino: Path = DOCS_DIR) -> tuple[dict, bool]:
    """Escribe docs/datos.json y docs/index.html.

    Devuelve (datos publicados, si se tocó algún fichero). El segundo valor es lo
    que decide si hay que commitear: sin él, o commiteamos siempre (ruido diario)
    o dejamos de exigir el commit cuando sí toca (que es justo el silencio que
    este repositorio lleva dos incidentes intentando hacer imposible).
    """
    if not PLANTILLA.exists():
        raise FileNotFoundError(f"Falta la plantilla del dashboard: {PLANTILLA}")

    destino.mkdir(parents=True, exist_ok=True)
    datos = construir_datos()

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
          f"abiertas={datos['resumen']['abiertas']} "
          f"mfe={len(datos['mfe'])}")
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
