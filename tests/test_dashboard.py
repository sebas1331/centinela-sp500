"""Tests del dashboard estático de docs/ (sin red y sin navegador).

Cubren las tres cosas que pueden romperse en silencio:

  1. El CONTRATO de docs/datos.json. El HTML no calcula nada, solo pinta lo que
     encuentra en ese fichero: si una clave cambia de nombre o de tipo, el
     dashboard se queda mudo sin que ningún workflow se ponga rojo.
  2. La ARITMÉTICA (win rate, expectancy, profit factor, P&L acumulado) contra
     una bitácora sintética cuyos resultados están calculados a mano aquí abajo.
  3. Que las posiciones ABIERTAS se marquen como tales y NO contaminen las
     estadísticas de cerradas. Su P&L es una marca a mercado, no un resultado.
"""
from __future__ import annotations

import html.parser
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

import generar_dashboard as gd  # noqa: E402


# --------------------------------------------------------------------------- #
# Bitácora sintética
# --------------------------------------------------------------------------- #
# Ocho operaciones con los números elegidos para que las cuentas salgan redondas
# y se puedan verificar mentalmente. `pnl_pct` va en FRACCIÓN, como en el CSV de
# verdad (-0.12 = -12%).
#
#   Cartera A cerradas: +20%, -10%, +5%, -15%   -> 2 de 4 ganadoras = 50%
#       expectancy = (20 - 10 + 5 - 15) / 4 = 0.0
#       profit factor = (20 + 5) / |−10 − 15| = 25 / 25 = 1.0
#   Cartera B cerradas: +30%, -10%              -> 1 de 2 ganadoras = 50%
#       expectancy = (30 - 10) / 2 = +10.0
#       profit factor = 30 / 10 = 3.0
#   Global: 6 cerradas, 3 ganadoras = 50%, P&L acumulado = 20-10+5-15+30-10 = +20
#   Y DOS ABIERTAS (una por cartera) que no deben entrar en nada de lo anterior.
CABECERA = ("id,grupo,ticker,portafolio,sector,fecha_entrada,hora_entrada_et,"
            "hora_entrada_utc,timestamp_escaneo,precio_entrada,probabilidad,"
            "score_fundamental,objetivo_inicial,objetivo_actual,historial_objetivos,"
            "stop,fecha_salida,hora_salida_et,precio_salida,motivo_salida,pnl_pct,"
            "dias_habiles,estado,notas")

FILAS = [
    # id, ticker, cartera, f_entrada, precio_ent, f_salida, precio_sal, motivo, pnl, estado
    (1, "AAA", "A", "2026-07-01", 100.0, "2026-07-08", 120.0, "objetivo", 0.20, "cerrada"),
    (2, "BBB", "A", "2026-07-02", 200.0, "2026-07-09", 180.0, "tiempo", -0.10, "cerrada"),
    (3, "CCC", "A", "2026-07-03", 50.0, "2026-07-10", 52.5, "tiempo", 0.05, "cerrada"),
    (4, "DDD", "A", "2026-07-06", 80.0, "2026-07-13", 68.0, "stop", -0.15, "cerrada"),
    (5, "AAA", "B", "2026-07-01", 100.0, "2026-07-14", 130.0, "objetivo", 0.30, "cerrada"),
    (6, "BBB", "B", "2026-07-02", 200.0, "2026-07-15", 180.0, "stop", -0.10, "cerrada"),
    (7, "EEE", "A", "2026-07-20", 10.0, None, None, None, None, "abierta"),
    (8, "EEE", "B", "2026-07-20", 10.0, None, None, None, None, "abierta"),
]

MFE_SINTETICO = """# Análisis MFE/MAE de posiciones

_Generado 2026-07-21 18:00 ET_

## Posiciones abiertas

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EEE | A | 2026-07-20 | 10.00 | +12.00% | 2026-07-21 | -3.00% | 2026-07-20 | +8.00% | ✅ sí | no | 14.00 |
| EEE | B | 2026-07-20 | 10.00 | +12.00% | 2026-07-21 | -3.00% | 2026-07-20 | +8.00% | ✅ sí | no | 14.00 |

## Posiciones cerradas en los últimos 30 días

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | P&L al cierre real | Motivo salida | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAA | A | 2026-07-01 | 100.00 | +25.00% | 2026-07-08 | -2.00% | 2026-07-02 | +20.00% | +20.00% | objetivo | ✅ sí | ✅ sí | 120.00 |

## Resumen

- **Posiciones abiertas:** 2 (2 con datos de precio).
"""

ESTADO_SINTETICO = {
    "actualizado": "2026-07-21T18:00:00-04:00",
    "ultima_preapertura": "2026-07-21",
    "ultima_postcierre": "2026-07-20",
}


def _csv_sintetico() -> str:
    lineas = [CABECERA]
    for (oid, tk, cart, fe, pe, fs, ps, motivo, pnl, estado) in FILAS:
        lineas.append(",".join([
            str(oid), "1", tk, cart, "Information Technology", fe,
            "", "", "", f"{pe}", "0.9", "60.0", "", "", '""', "",
            fs or "", "", "" if ps is None else f"{ps}", motivo or "",
            "" if pnl is None else f"{pnl}", "", estado, "",
        ]))
    return "\n".join(lineas) + "\n"


@pytest.fixture
def datos(tmp_path, monkeypatch) -> dict:
    """datos.json construido a partir de la bitácora sintética de arriba."""
    bit = tmp_path / "bitacora.csv"
    bit.write_text(_csv_sintetico(), encoding="utf-8")
    mfe = tmp_path / "mfe_actual.md"
    mfe.write_text(MFE_SINTETICO, encoding="utf-8")
    est = tmp_path / "estado.json"
    est.write_text(json.dumps(ESTADO_SINTETICO), encoding="utf-8")

    monkeypatch.setattr(gd, "RUTA_BITACORA", bit)
    monkeypatch.setattr(gd, "RUTA_MFE", mfe)
    monkeypatch.setattr(gd, "RUTA_ESTADO", est)
    return gd.construir_datos()


# --------------------------------------------------------------------------- #
# 1. Contrato de datos.json
# --------------------------------------------------------------------------- #
def test_schema_datos_json(datos):
    assert set(datos) == {"resumen", "carteras", "operaciones", "mfe", "meta"}

    r = datos["resumen"]
    assert set(r) == {"cerradas", "abiertas", "win_rate", "pnl_acumulado", "ultima_cerrada"}
    assert isinstance(r["cerradas"], int) and isinstance(r["abiertas"], int)
    assert isinstance(r["win_rate"], float) and isinstance(r["pnl_acumulado"], float)
    assert isinstance(r["ultima_cerrada"], str)

    assert set(datos["carteras"]) == {"A", "B"}
    for c in datos["carteras"].values():
        assert set(c) == {"cerradas", "win_rate", "expectancy", "profit_factor",
                          "mejor", "peor", "abiertas"}
        assert isinstance(c["cerradas"], int) and isinstance(c["abiertas"], int)
        for extremo in (c["mejor"], c["peor"]):
            assert set(extremo) == {"ticker", "pnl_pct", "fecha_salida"}
            assert isinstance(extremo["pnl_pct"], float)

    for o in datos["operaciones"]:
        assert set(o) == {"id", "ticker", "cartera", "estado", "fecha_entrada",
                          "precio_entrada", "fecha_salida", "precio_salida",
                          "pnl_pct", "no_realizado", "motivo"}
        assert isinstance(o["id"], int)
        assert o["estado"] in ("abierta", "cerrada")
        assert o["cartera"] in ("A", "B")
        assert o["motivo"] in ("Objetivo", "Stop", "Tiempo", "Abierta")
        assert isinstance(o["no_realizado"], bool)
        assert isinstance(o["precio_entrada"], float)

    for p in datos["mfe"]:
        assert set(p) == {"ticker", "cartera", "fecha_entrada", "precio_entrada",
                          "mfe_pct", "mae_pct", "pnl_actual_pct", "toco_5"}
        assert isinstance(p["toco_5"], bool)

    assert set(datos["meta"]) == {"actualizado", "mfe_generado", "ultima_preapertura",
                                  "ultima_postcierre", "repo"}


def test_datos_json_es_serializable_y_sin_nan(datos):
    """json.dumps con allow_nan=False falla si se cuela un NaN de pandas.

    Importa porque JSON.parse() del navegador no entiende `NaN`: el dashboard
    entero se quedaría en blanco por un solo valor ausente mal convertido.
    """
    texto = json.dumps(datos, allow_nan=False, ensure_ascii=False)
    assert "NaN" not in texto and "Infinity" not in texto


# --------------------------------------------------------------------------- #
# 2. Aritmética
# --------------------------------------------------------------------------- #
def test_win_rate_expectancy_y_profit_factor_por_cartera(datos):
    a = datos["carteras"]["A"]
    assert a["cerradas"] == 4
    assert a["win_rate"] == pytest.approx(50.0)
    assert a["expectancy"] == pytest.approx(0.0)
    assert a["profit_factor"] == pytest.approx(1.0)      # (20+5) / |-10-15|
    assert a["mejor"]["ticker"] == "AAA" and a["mejor"]["pnl_pct"] == pytest.approx(20.0)
    assert a["peor"]["ticker"] == "DDD" and a["peor"]["pnl_pct"] == pytest.approx(-15.0)

    b = datos["carteras"]["B"]
    assert b["cerradas"] == 2
    assert b["win_rate"] == pytest.approx(50.0)
    assert b["expectancy"] == pytest.approx(10.0)
    assert b["profit_factor"] == pytest.approx(3.0)      # 30 / |-10|


def test_agregados_globales(datos):
    r = datos["resumen"]
    assert r["cerradas"] == 6
    assert r["abiertas"] == 2
    assert r["win_rate"] == pytest.approx(50.0)          # 3 de 6
    assert r["pnl_acumulado"] == pytest.approx(20.0)     # 20-10+5-15+30-10
    assert r["ultima_cerrada"] == "2026-07-15"


def test_pnl_va_en_puntos_porcentuales_no_en_fraccion(datos):
    """La bitácora guarda -0.12; el dashboard tiene que enseñar -12."""
    op = [o for o in datos["operaciones"] if o["id"] == 4][0]
    assert op["pnl_pct"] == pytest.approx(-15.0)
    assert op["motivo"] == "Stop"


def test_profit_factor_es_none_si_no_hubo_perdidas(tmp_path, monkeypatch):
    """Sin pérdidas el cociente no existe: null, no un número inventado."""
    import pandas as pd
    solo_ganadoras = pd.DataFrame({
        "ticker": ["AAA", "BBB"], "pnl_pct_pp": [10.0, 20.0],
        "fecha_salida": ["2026-07-08", "2026-07-09"],
    })
    m = gd.metricas_cartera(solo_ganadoras)
    assert m["profit_factor"] is None
    assert m["win_rate"] == pytest.approx(100.0)


def test_cartera_sin_operaciones_cerradas_no_revienta():
    import pandas as pd
    m = gd.metricas_cartera(pd.DataFrame(columns=["ticker", "pnl_pct_pp", "fecha_salida"]))
    assert m == {"cerradas": 0, "win_rate": None, "expectancy": None,
                 "profit_factor": None, "mejor": None, "peor": None}


# --------------------------------------------------------------------------- #
# 3. Abiertas vs cerradas
# --------------------------------------------------------------------------- #
def test_abiertas_marcadas_y_fuera_de_las_estadisticas(datos):
    abiertas = [o for o in datos["operaciones"] if o["estado"] == "abierta"]
    assert len(abiertas) == 2
    for o in abiertas:
        assert o["no_realizado"] is True
        assert o["motivo"] == "Abierta"
        assert o["fecha_salida"] is None
        assert o["precio_salida"] is None
        # P&L a mercado tomado del informe MFE, no de la bitácora.
        assert o["pnl_pct"] == pytest.approx(8.0)

    # Las dos abiertas están en ganancia (+8%). Si contaran, el win rate global
    # subiría de 50% (3/6) a 62.5% (5/8) y el P&L acumulado de +20 a +36.
    assert datos["resumen"]["win_rate"] == pytest.approx(50.0)
    assert datos["resumen"]["pnl_acumulado"] == pytest.approx(20.0)
    assert datos["carteras"]["A"]["cerradas"] == 4     # no 5
    assert datos["carteras"]["B"]["cerradas"] == 2     # no 3
    assert datos["carteras"]["A"]["abiertas"] == 1
    assert datos["carteras"]["B"]["abiertas"] == 1


def test_cerradas_no_se_marcan_como_no_realizadas(datos):
    for o in datos["operaciones"]:
        if o["estado"] == "cerrada":
            assert o["no_realizado"] is False
            assert o["fecha_salida"] is not None
            assert o["pnl_pct"] is not None


def test_informe_mfe_parseado_por_nombre_de_columna(datos):
    """La tabla de abiertas se lee entera y con los signos correctos."""
    assert len(datos["mfe"]) == 2
    p = datos["mfe"][0]
    assert p["ticker"] == "EEE"
    assert p["mfe_pct"] == pytest.approx(12.0)
    assert p["mae_pct"] == pytest.approx(-3.0)
    assert p["pnl_actual_pct"] == pytest.approx(8.0)
    assert p["toco_5"] is True
    # La segunda tabla del informe (cerradas) NO debe colarse en esta lista.
    assert all(x["ticker"] == "EEE" for x in datos["mfe"])
    assert datos["meta"]["mfe_generado"] == "2026-07-21 18:00 ET"


# --------------------------------------------------------------------------- #
# 4. HTML generado
# --------------------------------------------------------------------------- #
class _Validador(html.parser.HTMLParser):
    """Comprueba que las etiquetas abren y cierran bien anidadas."""

    VACIAS = {"meta", "link", "br", "hr", "img", "input", "source", "col"}

    def __init__(self):
        super().__init__()
        self.pila: list[str] = []
        self.errores: list[str] = []
        self.vistas: set[str] = set()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.vistas.add(tag)
        for k, v in attrs:
            if k == "id":
                self.ids.add(v)
        if tag not in self.VACIAS:
            self.pila.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VACIAS:
            return
        if not self.pila:
            self.errores.append(f"</{tag}> sin apertura")
        elif self.pila[-1] != tag:
            self.errores.append(f"</{tag}> cierra un <{self.pila[-1]}> abierto")
        else:
            self.pila.pop()


@pytest.fixture(scope="module")
def html_generado() -> str:
    return gd.PLANTILLA.read_text(encoding="utf-8")


def test_html_bien_formado(html_generado):
    v = _Validador()
    v.feed(html_generado)
    assert v.errores == [], v.errores
    assert v.pila == [], f"etiquetas sin cerrar: {v.pila}"
    for tag in ("html", "head", "body", "header", "main", "footer", "table",
                "thead", "tbody", "details", "summary", "script", "style"):
        assert tag in v.vistas, f"falta <{tag}>"


def test_html_tiene_los_anclajes_que_el_script_rellena(html_generado):
    """Si alguien renombra un id en el HTML, el JS deja de encontrarlo y la
    sección se queda vacía sin ningún error visible. Aquí sale rojo."""
    v = _Validador()
    v.feed(html_generado)
    for ident in ("sello", "tema", "kpis", "carteras", "buscar", "chips", "cuenta",
                  "cabecera", "cuerpo", "vacio", "cuerpo-mfe", "mfe-sello",
                  "repo", "ult-cerrada", "det-mfe", "tabla"):
        assert ident in v.ids, f"falta id={ident}"


def test_html_es_autocontenido_y_responsive(html_generado):
    """Ni CDNs ni frameworks: el dashboard tiene que abrir sin red externa."""
    for prohibido in ("http://", "cdn.", "react", "tailwind", "jquery", "unpkg",
                      "jsdelivr", "googleapis"):
        assert prohibido not in html_generado.lower(), f"referencia externa: {prohibido}"
    # El único origen que se contacta es el propio datos.json, mismo directorio.
    assert 'fetch("datos.json"' in html_generado
    assert html_generado.count("<script") == 1
    assert "viewport" in html_generado
    assert "@media (max-width:719px)" in html_generado       # layout de tarjetas
    assert "prefers-color-scheme" in html_generado
    assert "localStorage" in html_generado
    assert "tabular-nums" in html_generado


def test_html_contiene_las_secciones_del_diseno(html_generado):
    for texto in ("Centinela SP500", "Comparativa A vs B", "Operaciones",
                  "Posiciones abiertas — MFE/MAE", "Paper trading — sin dinero real",
                  "Buscar ticker"):
        assert texto in html_generado, f"falta la sección/rótulo: {texto}"
    # Columnas de la tabla principal, en el orden exacto del diseño.
    columnas = ["Ticker", "Cartera", "Estado", "Fecha entrada", "Precio entrada",
                "Fecha salida", "Precio salida", "P&L %", "Motivo"]
    bloque = html_generado[html_generado.index("var COLS = ["):]
    posiciones = [bloque.index(f'"{c}"') for c in columnas]
    assert posiciones == sorted(posiciones), "las columnas no van en el orden pedido"
    # Chips de filtro combinables.
    for chip in ("Todas", "Abiertas", "Cerradas", "Cartera A", "Cartera B",
                 "Ganadoras", "Perdedoras"):
        assert f'txt:"{chip}"' in html_generado, f"falta el chip {chip}"
    # Paleta exacta del diseño, en sus dos temas.
    for color in ("#0a7d3b", "#4ade80", "#c2410c", "#f87171", "#0369a1", "#60a5fa"):
        assert color in html_generado, f"falta el color {color}"
    assert "details" in html_generado and "open" not in html_generado.split("<details")[1][:40]


def test_el_html_publicado_es_la_plantilla(tmp_path, monkeypatch, html_generado):
    """generar() copia la plantilla tal cual y deja el JSON al lado."""
    bit = tmp_path / "bitacora.csv"
    bit.write_text(_csv_sintetico(), encoding="utf-8")
    mfe = tmp_path / "mfe_actual.md"
    mfe.write_text(MFE_SINTETICO, encoding="utf-8")
    est = tmp_path / "estado.json"
    est.write_text(json.dumps(ESTADO_SINTETICO), encoding="utf-8")
    monkeypatch.setattr(gd, "RUTA_BITACORA", bit)
    monkeypatch.setattr(gd, "RUTA_MFE", mfe)
    monkeypatch.setattr(gd, "RUTA_ESTADO", est)

    destino = tmp_path / "docs"
    gd.generar(destino)
    assert (destino / "index.html").read_text(encoding="utf-8") == html_generado
    assert (destino / ".nojekyll").exists()
    publicado = json.loads((destino / "datos.json").read_text(encoding="utf-8"))
    assert publicado["resumen"]["cerradas"] == 6

    # Idempotencia: una segunda pasada no cambia un solo byte, así que el
    # workflow no genera un commit de ruido cuando no ha pasado nada.
    antes = {f.name: f.read_bytes() for f in destino.iterdir()}
    gd.generar(destino)
    assert {f.name: f.read_bytes() for f in destino.iterdir()} == antes


# --------------------------------------------------------------------------- #
# 5. Integración con el pipeline
# --------------------------------------------------------------------------- #
def test_el_dashboard_es_un_job_aparte_y_el_ultimo():
    """La publicación no puede poner en riesgo la persistencia de la bitácora.

    Si el dashboard fuera un paso más del job `postcierre`, un fallo suyo
    (yfinance caído, un KeyError en el markdown del informe) tumbaría el job que
    guarda la bitácora y el estado. Va aparte y detrás, como ya hace `mfe`.
    """
    import yaml
    wf = yaml.safe_load((RAIZ / ".github/workflows/postcierre.yml").read_text())
    job = wf["jobs"]["dashboard"]
    assert set(job["needs"]) == {"postcierre", "mfe"}
    # `mfe` puede quedar en skipped, y sin always() arrastraría al dashboard.
    assert "always()" in job["if"]
    assert "needs.postcierre.outputs.resultado == 'procesado'" in job["if"]
    assert isinstance(job.get("timeout-minutes"), int)

    pasos = " ".join(str(p.get("run", "")) for p in job["steps"])
    assert "scripts/generar_dashboard.py" in pasos
    # La persistencia se sigue exigiendo con el mismo script que el resto.
    assert "commit_y_push.sh" in pasos and "procesado" in pasos

    # El job del escaneo, que es el crítico, no depende del dashboard.
    assert "dashboard" not in wf["jobs"]["postcierre"].get("needs", [])
    assert "dashboard" not in wf["jobs"]["verificar"].get("needs", [])


def test_sin_silenciadores_de_errores_en_lo_nuevo():
    """Ni `|| true`, ni continue-on-error, ni 2>/dev/null, ni `set +e`.

    Un fallo tiene que verse rojo. Estos cuatro patrones son las cuatro formas
    habituales de convertir un fallo en un verde mentiroso.
    """
    ficheros = [RAIZ / "scripts/generar_dashboard.py",
                RAIZ / "scripts/plantilla_dashboard.html",
                RAIZ / "scripts/vigilante.py",
                RAIZ / ".github/workflows/vigilante.yml"]
    for f in ficheros:
        texto = f.read_text(encoding="utf-8")
        for patron in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
            assert patron not in texto, f"{f.name} contiene '{patron}'"

    # En postcierre.yml solo se mira el job nuevo: el resto del fichero es
    # anterior y tiene sus propias razones documentadas.
    texto = (RAIZ / ".github/workflows/postcierre.yml").read_text()
    bloque = texto[texto.index("  dashboard:"):]
    for patron in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
        assert patron not in bloque, f"el job dashboard contiene '{patron}'"


def test_el_vigilante_tiene_timeout_corto_y_cotas():
    """Regresión del 2026-08-06: un Vigilante que tarda minutos está roto."""
    import yaml
    wf = yaml.safe_load((RAIZ / ".github/workflows/vigilante.yml").read_text())
    assert wf["jobs"]["vigilar"]["timeout-minutes"] <= 5

    import vigilante
    assert vigilante.MAX_SESIONES > 0
    assert vigilante.MAX_COMMITS_LISTADOS > 0
    assert vigilante.TIMEOUT_GIT_S > 0
    # Una petición pedida a lo bruto no puede quedarse esperando para siempre.
    import socket
    assert socket.getdefaulttimeout() == vigilante.TIMEOUT_RED_S
    # El techo de sesiones se aplica de verdad, no es solo una constante.
    assert len(vigilante.sesiones_a_exigir(10 ** 6)) <= vigilante.MAX_SESIONES


def test_presupuestos_de_tamano():
    """Los techos del diseño, medidos sobre lo que hay publicado de verdad."""
    docs = gd.DOCS_DIR
    if not (docs / "index.html").exists():
        pytest.skip("docs/ aún no generado en este árbol")
    assert (docs / "index.html").stat().st_size < 50 * 1024
    assert (docs / "datos.json").stat().st_size < 500 * 1024
