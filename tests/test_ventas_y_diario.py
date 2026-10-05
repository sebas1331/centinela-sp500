"""Las ventas se confirman bien, el precio no se inventa y nada se pierde al publicar.

EL CASO (2026-10-02)
--------------------
XTB vendió las 134 acciones de CTVA a 11,97 y el sistema lo anotó como
"rechazada": `ambiguas.confirmar()` solo sabía confirmar compras, porque daba
una orden por buena si la posición CRECÍA. Una venta hace justo lo contrario.

Y en la misma semana, 13 runs murieron con "could not apply ...": dos workflows
escribían la bitácora del broker o la salud a la vez y el rebase no sabía
juntarlas. El día que choque uno con una venta dentro, esa venta existe en XTB y
en ningún sitio más. Estos tests fijan las dos cosas.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import (ambiguas as amb, broker_xtb as bx, config,  # noqa: E402
                       diario, fiabilidad, latido as lat, ordenes as ords, salud)
import ejecutor_xtb as ej  # noqa: E402


def _sin_dormir(_s):
    return None


# --------------------------------------------------------------------------- #
# Un XTB que se comporta como el de verdad: el saldo se mueve al operar
# --------------------------------------------------------------------------- #
class XTBDeMentira:
    """Posiciones, saldo y precios coherentes entre sí.

    `modelo="caja"`: el saldo baja al comprar y sube al vender por el importe.
    `modelo="pnl"`: el saldo solo se mueve al cerrar, por el resultado.
    No devuelve el precio al ejecutar —como XTB, muchas veces—, así que el
    sistema tiene que deducirlo.
    """

    def __init__(self, saldo=30000.0, modelo="caja", parcial=None):
        self._saldo = saldo
        self.modelo = modelo
        self.parcial = parcial       # vender solo esta cantidad aunque se pida más
        self.pos: dict[str, dict] = {}
        self.bid: dict[str, float] = {}
        self.n = 900000

    def saldo(self):
        return {"saldo": round(self._saldo, 2), "equity": round(self._saldo, 2),
                "divisa": "USD", "cuenta": 0}

    def posiciones(self):
        return [{"ticker": s, "acciones": float(p["acciones"]), "lado": "buy",
                 "precio_entrada": p["entrada"], "precio_actual": self.bid.get(s, 0.0),
                 "orden": p["orden"], "stop": None, "objetivo": None, "pnl": 0.0}
                for s, p in self.pos.items() if p["acciones"] > 0]

    def ordenes_pendientes(self):
        return []

    def abrir(self, simbolo, acciones, entrada):
        self.n += 1
        self.pos[simbolo] = {"acciones": acciones, "entrada": entrada,
                             "orden": self.n}
        if self.modelo == "caja":
            self._saldo -= acciones * entrada

    def comprar(self, simbolo, acciones, **_k):
        self.abrir(simbolo, acciones, self.bid[simbolo] + 0.01)
        return bx.Ejecucion(ticker=simbolo, lado="compra", acciones=acciones,
                            estado="ejecutada", orden=self.n)

    def vender(self, simbolo, acciones):
        p = self.pos[simbolo]
        hechas = min(acciones, self.parcial or acciones, p["acciones"])
        precio = self.bid[simbolo]
        p["acciones"] -= hechas
        self._saldo += (hechas * precio if self.modelo == "caja"
                        else hechas * (precio - p["entrada"]))
        self.n += 1
        return bx.Ejecucion(ticker=simbolo, lado="venta", acciones=acciones,
                            estado="ejecutada", orden=self.n)


# --------------------------------------------------------------------------- #
# 1. Confirmar una venta
# --------------------------------------------------------------------------- #
def test_EL_CASO_una_venta_que_hace_desaparecer_la_posicion_es_EJECUTADA():
    """CTVA, 2026-10-02: 134 acciones vendidas a 11,97, anotadas 'rechazada'."""
    x = XTBDeMentira(saldo=28287.90)
    x.abrir("CTVA.US", 134, 12.71)
    x._saldo = 28287.90
    x.bid["CTVA.US"] = 11.97
    e = amb.enviar_resolviendo(x, "CTVA.US", ords.VENTA_DATO_ERRONEO,
                               lambda: x.vender("CTVA.US", 134),
                               dormir=_sin_dormir, referencia=11.97)
    assert e.estado == "ejecutada", e.error
    assert e.acciones_hechas == 134
    assert e.precio == pytest.approx(11.97) and e.precio_fuente == "saldo-caja"
    assert x.saldo()["saldo"] == pytest.approx(29891.88)


def test_vender_PARTE_de_una_posicion_tambien_es_ejecutada():
    x = XTBDeMentira()
    x.abrir("F.US", 134, 12.0)
    x.bid["F.US"] = 12.5
    e = amb.enviar_resolviendo(x, "F.US", ords.VENTA_TIEMPO,
                               lambda: x.vender("F.US", 50),
                               dormir=_sin_dormir, referencia=12.5)
    assert e.estado == "ejecutada" and e.acciones_hechas == 50
    assert x.posiciones()[0]["acciones"] == 84
    assert not (e.error or "").startswith("PARCIAL")


def test_una_venta_PARCIAL_es_ejecutada_con_lo_que_se_vendio_de_verdad(tmp_path):
    """Se pidieron 100 y XTB vendió 40: ejecutada, 40, y dicho en voz alta."""
    x = XTBDeMentira(parcial=40)
    x.abrir("F.US", 100, 12.0)
    x.bid["F.US"] = 12.5
    e = amb.enviar_resolviendo(x, "F.US", ords.VENTA_STOP_INTRADIA,
                               lambda: x.vender("F.US", 100),
                               dormir=_sin_dormir, referencia=12.5)
    assert e.estado == "ejecutada" and e.acciones_hechas == 40
    assert e.error.startswith("PARCIAL")
    # Y la bitácora apunta las 40, no las 100: la reconciliación cuadra con eso.
    o = ords.Orden(id="2026-10-05|A|F|stop_intradia", tipo=ords.VENTA_STOP_INTRADIA,
                   cartera="A", ticker="F", acciones=100, sesion="2026-10-05")
    ords.registrar_ejecucion(o, e)
    fila = ords.filas_de_sesion("2026-10-05")[0]
    assert fila["acciones"] == "40" and fila["estado"] == "ejecutada"


def test_una_venta_que_no_baja_la_posicion_es_RECHAZADA():
    x = XTBDeMentira()
    x.abrir("F.US", 10, 12.0)
    e = amb.enviar_resolviendo(
        x, "F.US", ords.VENTA_TIEMPO,
        lambda: bx.Ejecucion(ticker="F.US", lado="venta", acciones=10,
                             estado="ejecutada"),
        dormir=_sin_dormir)
    assert e.estado == "rechazada"
    assert "sigue con 10 acciones" in e.error


def test_una_venta_ambigua_que_si_entro_no_se_reintenta():
    """Respuesta vacía, pero la posición bajó: entró. Reintentar vendería dos
    veces (y con la posición ya cerrada, abriría una corta)."""
    x = XTBDeMentira()
    x.abrir("F.US", 10, 12.0)
    x.bid["F.US"] = 12.0
    envios = []

    def mandar():
        envios.append(1)
        x.vender("F.US", 10)
        return bx.Ejecucion(ticker="F.US", lado="venta", acciones=10,
                            estado="ambigua", error="cuerpo vacío")

    e = amb.enviar_resolviendo(x, "F.US", ords.VENTA_TIEMPO, mandar,
                               dormir=_sin_dormir, referencia=12.0)
    assert e.estado == "ejecutada" and len(envios) == 1


def test_una_compra_se_sigue_confirmando_porque_la_posicion_CRECE():
    x = XTBDeMentira()
    x.bid["F.US"] = 12.0
    e = amb.enviar_resolviendo(x, "F.US", ords.COMPRA,
                               lambda: x.comprar("F.US", 5), dormir=_sin_dormir)
    assert e.estado == "ejecutada" and e.acciones_hechas == 5
    # El precio sale de la posición nueva que enseña XTB, no del saldo.
    assert e.precio == pytest.approx(12.01) and e.precio_fuente == "posicion"


def test_el_lado_sale_del_tipo_y_un_tipo_inventado_no_cuela():
    assert ords.lado_de(ords.COMPRA) == "compra"
    assert ords.lado_de(ords.ENTRADA_TARDIA) == "compra"
    for t in (ords.VENTA_TIEMPO, ords.VENTA_STOP_INTRADIA, ords.VENTA_DATO_ERRONEO,
              ords.VENTA_TIEMPO_DIFERIDO, ords.VENTA_OBJETIVO_INTRADIA):
        assert ords.lado_de(t) == "venta"
    with pytest.raises(ValueError):
        ords.lado_de("vender_lo_que_sea")


# --------------------------------------------------------------------------- #
# 2. El precio, deducido del saldo sin adivinar el modelo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("modelo", ["caja", "pnl"])
def test_el_precio_de_una_venta_sale_del_saldo_en_los_dos_modelos(modelo):
    x = XTBDeMentira(saldo=29991.04, modelo=modelo)
    x.abrir("CTVA.US", 134, 12.71)
    x.bid["CTVA.US"] = 11.97
    e = amb.enviar_resolviendo(x, "CTVA.US", ords.VENTA_TIEMPO,
                               lambda: x.vender("CTVA.US", 134),
                               dormir=_sin_dormir, referencia=11.97)
    assert e.precio == pytest.approx(11.97)
    assert e.precio_fuente == f"saldo-{modelo}"


def test_sin_referencia_el_precio_no_se_inventa():
    p, motivo = amb.deducir_precio_venta(28287.90, 29891.88, 134, 12.71, None)
    assert p is None and "referencia" in motivo


def test_un_precio_que_no_cuadra_con_la_referencia_se_deja_vacio():
    p, motivo = amb.deducir_precio_venta(28287.90, 29891.88, 134, 12.71, 30.0)
    assert p is None and "aleja" in motivo


# --------------------------------------------------------------------------- #
# 3. El diario: idempotente, y publicado sin rebase aunque haya choque
# --------------------------------------------------------------------------- #
def _fila(id_, cuando, tipo=ords.VENTA_TIEMPO, acciones=10):
    return {c: "" for c in ords.COLUMNAS_BROKER} | {
        "id": id_, "sesion": cuando[:10], "cuando_et": cuando, "cartera": "A",
        "ticker": "F", "simbolo_xtb": "F.US", "tipo": tipo,
        "acciones": acciones, "estado": "ejecutada", "precio": 12.5}


def test_aplicar_el_diario_dos_veces_no_cambia_nada_la_segunda(tmp_path):
    entradas = [
        {"op": "orden", "anotado": "x", "fila": _fila("a", "2026-10-05T15:45:00")},
        {"op": "actualizar", "anotado": "x", "id": "a", "campos": {"precio": 12.6}},
        {"op": "enviada", "anotado": "2026-10-05T15:45:01", "id": "a",
         "detalle": {"estado": "ejecutada"}},
        {"op": "salud", "anotado": "x", "componente": "ventas",
         "entrada": {"cuando": "2026-10-05T15:45:02", "resultado": "ok: 1 vendidas"}},
    ]
    assert diario.aplicar(entradas, tmp_path)
    foto = {p: (tmp_path / p).read_text() for p in diario.RUTAS if (tmp_path / p).exists()}
    assert diario.aplicar(entradas, tmp_path) == []
    assert foto == {p: (tmp_path / p).read_text() for p in foto}
    filas = list(csv.DictReader(open(tmp_path / diario.RUTA_BITACORA)))
    assert len(filas) == 1 and filas[0]["precio"] == "12.6"


def test_una_linea_ilegible_del_diario_para_en_rojo(tmp_path, monkeypatch):
    monkeypatch.setattr(diario, "ARCHIVO", tmp_path / "d.jsonl")
    diario.ARCHIVO.write_text('{"op": "orden"}\n{roto\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="ilegible"):
        diario.leer()


def _git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def remoto(tmp_path):
    """Un remoto de verdad (bare) y dos clones: el del job y el de 'otro'."""
    bare = tmp_path / "remoto.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    clones = []
    for nombre in ("job", "otro"):
        c = tmp_path / nombre
        _git("clone", "-q", str(bare), str(c), cwd=tmp_path)
        _git("config", "user.name", "t", cwd=c)
        _git("config", "user.email", "t@t", cwd=c)
        clones.append(c)
    job, otro = clones
    _git("checkout", "-q", "-b", "main", cwd=job)
    with open(job / diario.RUTA_BITACORA, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=ords.COLUMNAS_BROKER).writeheader()
    _git("add", ".", cwd=job)
    _git("commit", "-q", "-m", "inicio", cwd=job)
    _git("push", "-q", "origin", "main", cwd=job)
    _git("fetch", "-q", "origin", cwd=otro)
    _git("checkout", "-q", "-b", "main", "origin/main", cwd=otro)
    return bare, job, otro


def _otro_empuja_una_fila(otro, id_):
    """Otro workflow añade una fila al MISMO CSV y empuja: el choque de siempre."""
    _git("pull", "-q", "origin", "main", cwd=otro)
    with open(otro / diario.RUTA_BITACORA, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=ords.COLUMNAS_BROKER).writerow(
            _fila(id_, "2026-10-05T15:44:00", tipo=ords.COMPRA))
    _git("commit", "-qam", f"otro: {id_}", cwd=otro)
    _git("push", "-q", "origin", "main", cwd=otro)


def test_un_CHOQUE_DE_PUSH_no_pierde_la_venta(remoto):
    """Otro workflow empuja una fila al mismo CSV entre que el job lee y el job
    empuja. Con rebase eso era 'could not apply' y la venta, perdida. Con el
    diario, el segundo intento la aplica sobre el origin nuevo y entran las dos.
    """
    bare, job, otro = remoto
    entradas = [{"op": "orden", "anotado": "x",
                 "fila": _fila("2026-10-05|A|F|venta_tiempo", "2026-10-05T15:45:00")}]
    llamadas = {"n": 0}
    git_real = diario._git

    def git_con_choque(*args, cwd):
        # Justo antes del PRIMER push del job, otro empuja.
        if args and args[0] == "push" and llamadas["n"] == 0:
            llamadas["n"] += 1
            _otro_empuja_una_fila(otro, "2026-10-05|A|G|compra")
        return git_real(*args, cwd=cwd)

    import centinela.diario as d
    original = d._git
    d._git = git_con_choque
    try:
        sha = d.publicar_de_verdad("venta [skip ci]", repo=job, entradas=entradas,
                                   dormir=_sin_dormir, log=lambda *_a: None)
    finally:
        d._git = original
    assert sha
    final = tmp = bare.parent / "comprobar"
    _git("clone", "-q", str(bare), str(final), cwd=bare.parent)
    ids = [f["id"] for f in csv.DictReader(open(tmp / diario.RUTA_BITACORA))]
    assert "2026-10-05|A|G|compra" in ids, "se perdió lo del otro workflow"
    assert "2026-10-05|A|F|venta_tiempo" in ids, "SE PERDIÓ LA VENTA"
    # Y el árbol del job no se ha tocado: el diario trabaja en uno aparte.
    assert _git("status", "--porcelain", cwd=job) == ""


def test_publicar_lo_ya_publicado_no_hace_commit(remoto):
    bare, job, _ = remoto
    entradas = [{"op": "orden", "anotado": "x",
                 "fila": _fila("x", "2026-10-05T15:45:00")}]
    assert diario.publicar_de_verdad("1", repo=job, entradas=entradas,
                                     dormir=_sin_dormir, log=lambda *_a: None)
    assert diario.publicar_de_verdad("2", repo=job, entradas=entradas,
                                     dormir=_sin_dormir, log=lambda *_a: None) is None


def test_lo_que_el_broker_hace_queda_en_el_diario_antes_que_en_ningun_sitio():
    o = ords.Orden(id="2026-10-05|A|F|venta_tiempo", tipo=ords.VENTA_TIEMPO,
                   cartera="A", ticker="F", acciones=10, sesion="2026-10-05")
    e = bx.Ejecucion(ticker="F.US", lado="venta", acciones=10, estado="ejecutada",
                     precio=12.5, orden=1, cuando="2026-10-05T15:45:00")
    ords.registrar_ejecucion(o, e)
    reg = {"enviadas": {}}
    ords.marcar_enviada(reg, o.id, {"estado": "ejecutada"})
    fiabilidad.anotar(fiabilidad.EJECUTADA, "F.US", o.tipo, "ok", id_orden=o.id,
                      ruta=fiabilidad.ARCHIVO)
    ops = [e["op"] for e in diario.leer()]
    assert ops[:2] == ["orden", "enviada"]


# --------------------------------------------------------------------------- #
# 4. La reconciliación detecta una venta de XTB que no está registrada
# --------------------------------------------------------------------------- #
def test_una_venta_en_XTB_que_no_esta_en_la_bitacora_es_ROJO(monkeypatch):
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    o = ords.Orden(id="2026-10-05|A|F|compra", tipo=ords.COMPRA, cartera="A",
                   ticker="F", acciones=10, sesion="2026-10-05")
    ords.registrar_ejecucion(o, bx.Ejecucion(
        ticker="F.US", lado="compra", acciones=10, estado="ejecutada",
        precio=12.0, cuando="2026-10-05T09:31:00"))
    x = XTBDeMentira()
    x.abrir("F.US", 10, 12.0)
    assert ej.cuadrar_libro(x) == []
    x.pos["F.US"]["acciones"] = 0           # alguien la vendió y nadie lo apuntó
    problemas = ej.cuadrar_libro(x)
    assert len(problemas) == 1 and "VENTA" in problemas[0]


def test_el_libro_cuadra_compras_y_ventas_ejecutadas(monkeypatch):
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    for i, (tipo, n) in enumerate([(ords.COMPRA, 10), (ords.VENTA_TIEMPO, 4)]):
        ords.registrar_ejecucion(
            ords.Orden(id=f"x{i}", tipo=tipo, cartera="A", ticker="F",
                       acciones=n, sesion="2026-10-05"),
            bx.Ejecucion(ticker="F.US", lado="x", acciones=n, estado="ejecutada",
                         cuando=f"2026-10-05T1{i}:00:00"))
    # Una rechazada no cuenta.
    ords.registrar_ejecucion(
        ords.Orden(id="x9", tipo=ords.VENTA_TIEMPO, cartera="A", ticker="F",
                   acciones=6, sesion="2026-10-05"),
        bx.Ejecucion(ticker="F.US", lado="venta", acciones=6, estado="rechazada",
                     cuando="2026-10-05T15:00:00"))
    assert ej.libro_de_acciones() == {"F.US": 6}


# --------------------------------------------------------------------------- #
# 5. Un "ok" de ventas no puede ocultar que se llegó tarde
# --------------------------------------------------------------------------- #
AHORA_TARDE = datetime(2026, 10, 5, 19, 30, tzinfo=config.TZ_ET)


def _pendientes_de_tiempo(*tickers):
    ords.ARCHIVO_PENDIENTES.write_text(json.dumps({
        "sesion": "2026-10-05", "cartera_broker": "A",
        "ordenes": [{"id": f"2026-10-05|A|{t}|venta_tiempo", "tipo": ords.VENTA_TIEMPO,
                     "cartera": "A", "ticker": t, "acciones": 5,
                     "sesion": "2026-10-05"} for t in tickers]}), encoding="utf-8")


def test_fuera_de_ventana_sin_nada_que_vender_lo_dice_y_no_es_rojo():
    _pendientes_de_tiempo()
    assert ej.ventas_fuera_de_ventana("faltan -210 min", ahora=AHORA_TARDE) == 0
    r = salud.cargar()["runs"]["ventas"]
    assert r["resultado"] == "fuera-de-ventana: nada que vender"
    assert "19:30" in r["detalle"]


def test_fuera_de_ventana_con_ventas_sin_hacer_es_ROJO():
    _pendientes_de_tiempo("F")
    assert ej.ventas_fuera_de_ventana("faltan -210 min", ahora=AHORA_TARDE) == 1
    r = salud.cargar()["runs"]["ventas"]
    assert r["resultado"].startswith("fallo:") and "F" in r["detalle"]


def test_si_el_vigilante_ya_vendio_el_respaldo_tardio_no_lo_pisa():
    _pendientes_de_tiempo("F")
    salud.registrar("ventas", "ok: 1 vendidas", "vigilante de precios, 15:45 ET")
    datos = salud.cargar()
    datos["runs"]["ventas"]["cuando"] = "2026-10-05T15:45:00-04:00"
    salud.guardar(datos)
    assert ej.ventas_fuera_de_ventana("faltan -210 min", ahora=AHORA_TARDE) == 0
    assert salud.cargar()["runs"]["ventas"]["resultado"] == "ok: 1 vendidas"


def test_el_resultado_de_ventas_distingue_las_tres_cosas():
    ej.ENVIADAS[ords.VENTA_TIEMPO] = {"decididas": 0, "enviadas": 0, "ya_estaban": 0}
    assert ej.resultado_explicito("ventas") == "ok: nada que vender"
    ej.ENVIADAS[ords.VENTA_TIEMPO] = {"decididas": 2, "enviadas": 2, "ya_estaban": 0}
    assert ej.resultado_explicito("ventas") == "ok: 2 vendidas"
    ej.ENVIADAS[ords.VENTA_TIEMPO] = {"decididas": 2, "enviadas": 0, "ya_estaban": 2}
    assert "vigilante" in ej.resultado_explicito("ventas")


# --------------------------------------------------------------------------- #
# 7. El latido con historial
# --------------------------------------------------------------------------- #
def test_el_historial_del_latido_crece_y_se_recorta():
    h = []
    for _ in range(lat.HISTORIAL_MAX + 5):
        h = lat.construir("2026-10-05T09:30:00-04:00", [], historial=h)["historial"]
    assert len(h) == lat.HISTORIAL_MAX


def test_un_corte_en_el_historial_se_detecta():
    h = [{"cuando": f"2026-10-05T10:{m:02d}:00-04:00", "estado": "vivo"}
         for m in (0, 2, 4, 21, 23)]
    cortes = lat.huecos(h)
    assert len(cortes) == 1 and cortes[0]["minutos"] == 17.0


def test_un_tramo_en_reposo_no_es_un_corte():
    h = [{"cuando": "2026-10-05T10:00:00-04:00", "estado": "en-reposo"},
         {"cuando": "2026-10-05T12:00:00-04:00", "estado": "vivo"}]
    assert lat.huecos(h) == []


def test_el_historial_de_un_relevo_sigue_el_de_hoy_y_no_el_de_ayer():
    previo = {"historial": [{"cuando": "2026-10-02T15:58:00-04:00"},
                            {"cuando": "2026-10-05T09:31:00-04:00"}]}
    assert len(lat.historial_de_hoy(previo, "2026-10-05")) == 1


def test_el_arranque_del_vigilante_no_se_cae_cuando_el_ejecutor_muere():
    """05/10: el ejecutor murió sin escribir `posiciones_xtb` y GitHub, que
    compara '' y '0' como el número 0, dejó el vigilante sin arrancar."""
    import yaml
    d = yaml.safe_load((RAIZ / ".github/workflows/preapertura.yml").read_text())
    cond = d["jobs"]["arrancar_vigilante"]["if"]
    assert "format(" in cond and "!= '0'" not in cond


def test_una_orden_rechazada_cuenta_como_reportada(monkeypatch):
    import vigilante
    o = ords.Orden(id="2026-10-02|A|CTVA|compra", tipo=ords.COMPRA, cartera="A",
                   ticker="CTVA", acciones=134, sesion="2026-10-02")
    ords.guardar_pendientes([o], "2026-10-02", "A")
    monkeypatch.setattr(vigilante, "sesiones_a_exigir", lambda *_a: ["2026-10-02"])
    assert len(vigilante.revisar_ejecutor(1)) == 1     # sin rastro: problema
    ords.registrar_ejecucion(o, bx.Ejecucion(
        ticker="CTVA.US", lado="compra", acciones=134, estado="rechazada",
        cuando="2026-10-02T09:30:19-04:00"))
    assert vigilante.revisar_ejecutor(1) == []          # rechazada, pero reportada


def test_las_compras_no_salen_en_los_primeros_cinco_minutos():
    """05/10: compras a las 09:30:19 y 09:30:31, aceptadas y descartadas."""
    apertura = datetime(2026, 10, 5, 9, 30, tzinfo=config.TZ_ET)
    from datetime import timedelta
    assert ej.en_ventana("compras", apertura + timedelta(seconds=31))[0] is ej.ESPERAR
    assert ej.en_ventana("compras", apertura + timedelta(minutes=4))[0] is ej.ESPERAR
    assert ej.en_ventana("compras", apertura + timedelta(minutes=5))[0] is True
