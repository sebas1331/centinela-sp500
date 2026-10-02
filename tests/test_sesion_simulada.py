"""Una sesión entera, de la compra al cierre, contra un XTB de mentira.

No es un test de una función: es el día completo, con el reloj avanzando y las
piezas reales hablando entre sí — el ejecutor de compras, el vigilante de
precios con su bucle, el diario, la reconciliación. Lo único falso es XTB (un
doble que mueve posiciones y saldo como el de verdad, sin devolver el precio
al ejecutar) y el reloj.

  09:31  compra de AAA y BBB tras la apertura (CCC ya estaba de antes)
  09:35  arranca el vigilante: AAA y BBB con niveles, CCC sale HOY por tiempo
  10:05  AAA cruza su objetivo -> venta, y JUSTO al publicarla otro workflow
         empuja al mismo CSV (el choque de push de siempre)
  10:30  el vigilante muere; 15 minutos sin latir; se relanza
  11:00  BBB cruza su stop -> venta
  15:45  CCC sale por tiempo, 15 minutos antes del cierre
  16:00  cierre: latido en reposo, aviso externo pausado
  después  la reconciliación cuadra XTB contra lo registrado
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import (ambiguas as amb, aviso, broker_xtb as bx, config,  # noqa: E402
                       diario, estado as est_mod, estado_broker, latido as lat,
                       ordenes as ords, salud)
import ejecutor_xtb as ej  # noqa: E402
import hace_falta_vigilante as hf  # noqa: E402
import vigilante_precios as vp  # noqa: E402

from test_ventas_y_diario import XTBDeMentira, _git  # noqa: E402

HOY = "2026-10-05"                     # lunes
APERTURA = datetime(2026, 10, 5, 9, 30, tzinfo=config.TZ_ET)
CIERRE = datetime(2026, 10, 5, 16, 0, tzinfo=config.TZ_ET)


class Reloj:
    """El reloj de pared y el monotónico, avanzando juntos y a voluntad."""

    def __init__(self, inicio: datetime):
        self.t = inicio
        self.m = 1000.0

    def ahora(self, tz=None):
        return self.t

    def mono(self):
        return self.m

    def avanzar(self, segundos: float):
        self.t += timedelta(seconds=segundos)
        self.m += segundos

    def hasta(self, h: int, mi: int):
        self.avanzar((self.t.replace(hour=h, minute=mi) - self.t).total_seconds())


class XTBEnVivo(XTBDeMentira):
    """El de mentira, más lo que el vigilante necesita: ticks y conexión."""

    def __init__(self, **k):
        super().__init__(**k)
        self._tick = None
        self.suscritos = []
        self._precios = None

    def al_recibir_tick(self, cb):
        self._tick = cb

    def al_perder_conexion(self, cb):
        pass

    def al_recuperar_conexion(self, cb):
        pass

    def suscribir_ticks(self, s):
        self.suscritos.append(s)

    def bombear(self, _seg):
        pass

    def cotizacion(self, s):
        return {"ticker": s, "bid": self.bid[s], "ask": self.bid[s] + 0.01}

    @property
    def conectado(self):
        return True

    def tick(self, simbolo, bid):
        self.bid[simbolo] = bid
        if self._tick:
            self._tick({"symbol": simbolo, "bid": bid})


@pytest.fixture
def dia(tmp_path, monkeypatch):
    """El mundo del día: estado del simulador, órdenes, reloj, remoto git."""
    reloj = Reloj(datetime(2026, 10, 5, 9, 31, tzinfo=config.TZ_ET))

    class R(datetime):
        @classmethod
        def now(cls, tz=None):
            return reloj.ahora()

    for mod in (vp, ej, ords, salud, amb, lat, estado_broker):
        if hasattr(mod, "datetime"):
            monkeypatch.setattr(mod, "datetime", R)
    monkeypatch.setattr(amb, "ESPERA_CONFIRMACION", 0.0)
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-09-29")

    # CCC: abierta el 21/09 en el simulador, sale HOY por tiempo.
    estado = {"posiciones": {"A": [{
        "ticker": "CCC", "fecha_entrada": "2026-09-30", "entrada": 50.0,
        "objetivo": 70.0, "stop": 30.0, "id": 300, "dia_limite": HOY}], "B": []},
        "entradas_pendientes": []}
    monkeypatch.setattr(est_mod, "cargar", lambda: estado)

    # Las órdenes del día: dos compras (pre-apertura) y la salida por tiempo
    # de CCC (post-cierre de la víspera).
    ordenes = [
        {"id": f"{HOY}|A|AAA|compra", "tipo": ords.COMPRA, "cartera": "A",
         "ticker": "AAA", "acciones": 10, "sesion": HOY,
         "objetivo": 110.0, "stop": 90.0},
        {"id": f"{HOY}|A|BBB|compra", "tipo": ords.COMPRA, "cartera": "A",
         "ticker": "BBB", "acciones": 20, "sesion": HOY,
         "objetivo": 230.0, "stop": 190.0},
        {"id": f"{HOY}|A|CCC|venta_tiempo", "tipo": ords.VENTA_TIEMPO,
         "cartera": "A", "ticker": "CCC", "acciones": 30, "sesion": HOY,
         "fecha_limite": HOY, "id_operacion": 300},
    ]
    ords.ARCHIVO_PENDIENTES.write_text(json.dumps(
        {"sesion": HOY, "cartera_broker": "A", "ordenes": ordenes}),
        encoding="utf-8")

    # XTB: CCC ya estaba, comprada (y REGISTRADA) el 30/09.
    xtb = XTBEnVivo(saldo=30000.0, modelo="caja")
    xtb.bid.update({"AAA.US": 100.0, "BBB.US": 200.0, "CCC.US": 55.0})
    xtb.abrir("CCC.US", 30, 50.0)
    ords.registrar_ejecucion(
        ords.Orden(id="2026-09-30|A|CCC|compra", tipo=ords.COMPRA, cartera="A",
                   ticker="CCC", acciones=30, sesion="2026-09-30"),
        bx.Ejecucion(ticker="CCC.US", lado="compra", acciones=30,
                     estado="ejecutada", precio=50.0,
                     cuando="2026-09-30T09:31:00-04:00"))

    # Un remoto de verdad para publicar el diario, y "otro" que empuja.
    bare = tmp_path / "remoto.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    job, otro = tmp_path / "job", tmp_path / "otro"
    for c in (job, otro):
        _git("clone", "-q", str(bare), str(c), cwd=tmp_path)
        _git("config", "user.name", "t", cwd=c)
        _git("config", "user.email", "t@t", cwd=c)
    _git("checkout", "-q", "-b", "main", cwd=job)
    with open(job / diario.RUTA_BITACORA, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=ords.COLUMNAS_BROKER).writeheader()
    _git("add", ".", cwd=job)
    _git("commit", "-q", "-m", "inicio", cwd=job)
    _git("push", "-q", "origin", "main", cwd=job)
    _git("fetch", "-q", "origin", cwd=otro)
    _git("checkout", "-q", "-b", "main", "origin/main", cwd=otro)

    publicados = []
    monkeypatch.setattr(lat, "publicar", lambda d, _t: publicados.append(d))
    pings = []
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/uuid-de-prueba")
    monkeypatch.setenv("HEALTHCHECKS_API_KEY", "clave")

    def abrir(req):
        pings.append((reloj.ahora(), req.full_url))
        return 200
    monkeypatch.setattr(aviso, "_abrir", abrir)
    monkeypatch.setattr(aviso.ping, "__defaults__", ("", abrir))
    monkeypatch.setattr(aviso.pausar, "__defaults__", (abrir,))
    return {"reloj": reloj, "xtb": xtb, "bare": bare, "job": job, "otro": otro,
            "latidos": publicados, "pings": pings, "tmp": tmp_path}


def _filas():
    with open(ords.ARCHIVO_BITACORA_BROKER, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _sesion(d, historial=None):
    s = vp.Sesion(d["xtb"], ords.cargar_enviadas(), trabajo=d["tmp"] / "lat",
                  arrancado=d["reloj"].ahora().isoformat(), cierre=CIERRE,
                  reloj=d["reloj"].ahora, mono=d["reloj"].mono,
                  historial=historial)
    d["xtb"].al_recibir_tick(s.precios.encajar)
    return s


def _correr(d, s, hasta: datetime, cada: float = 30.0):
    while d["reloj"].ahora() < hasta and s.sigue():
        s.paso()
        d["reloj"].avanzar(cada)


def test_una_sesion_entera_de_la_compra_al_cierre(dia, monkeypatch):
    d, xtb, reloj = dia, dia["xtb"], dia["reloj"]

    # --- 09:31 compras tras la apertura -------------------------------------
    ok, _ = ej.en_ventana("compras", reloj.ahora())
    assert ok is True, "a las 09:31 la ventana de compras tiene que estar abierta"
    registro = ords.cargar_enviadas()
    hechas = ej.enviar(xtb, ords.cargar_pendientes(), "compras", registro)
    ords.guardar_enviadas(registro)
    assert [e.estado for _, e in hechas] == ["ejecutada", "ejecutada"]
    compras = {f["ticker"]: f for f in _filas() if f["sesion"] == HOY}
    # XTB no devolvió precio: sale de la posición nueva, y queda dicho.
    assert compras["AAA"]["precio"] == "100.01"
    assert compras["AAA"]["precio_fuente"] == "posicion"

    # --- 09:35 arranca el vigilante -----------------------------------------
    reloj.hasta(9, 35)
    s = _sesion(d)
    s.arrancar()
    vigiladas = {v["ticker"]: v for v in s.vigiladas}
    assert set(vigiladas) == {"AAA", "BBB", "CCC"}
    assert vigiladas["CCC"]["sale_hoy"] and not vigiladas["AAA"]["sale_hoy"]
    assert vigiladas["AAA"]["objetivo"] == 110.0, "niveles de la compra de hoy"
    assert d["pings"], "con posiciones abiertas tiene que hacer ping"

    # --- 10:05 AAA cruza el objetivo; al publicar, CHOQUE de push ------------
    _correr(d, s, datetime(2026, 10, 5, 10, 5, tzinfo=config.TZ_ET))
    git_real = diario._git
    chocado = {"n": 0}

    def git_con_choque(*args, cwd):
        if args and args[0] == "push" and chocado["n"] == 0:
            chocado["n"] += 1
            _git("pull", "-q", "origin", "main", cwd=d["otro"])
            with open(d["otro"] / diario.RUTA_BITACORA, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=ords.COLUMNAS_BROKER).writerow(
                    {c: "" for c in ords.COLUMNAS_BROKER} | {
                        "id": "otro-workflow", "cuando_et": "x", "tipo": ords.COMPRA,
                        "estado": "ejecutada"})
            _git("commit", "-qam", "otro workflow", cwd=d["otro"])
            _git("push", "-q", "origin", "main", cwd=d["otro"])
        return git_real(*args, cwd=cwd)

    monkeypatch.setattr(diario, "_git", git_con_choque)
    monkeypatch.setattr(diario, "publicar",
                        lambda m, **k: diario.publicar_de_verdad(
                            m, repo=d["job"], dormir=lambda _s: None,
                            **{x: y for x, y in k.items() if x != "dormir"}))
    xtb.tick("AAA.US", 110.5)
    s.paso()
    venta = [f for f in _filas() if f["tipo"] == ords.VENTA_OBJETIVO_INTRADIA]
    assert len(venta) == 1 and venta[0]["estado"] == "ejecutada", venta
    assert float(venta[0]["precio"]) == pytest.approx(110.5)
    assert venta[0]["precio_fuente"] == "saldo-caja"
    assert chocado["n"] == 1, "el choque no llegó a producirse"
    assert not vp.PENDIENTE["si"], "la venta debía haber subido al segundo intento"
    # El remoto tiene LAS DOS cosas: la venta y lo del otro workflow.
    _git("pull", "-q", "origin", "main", cwd=d["otro"])
    ids = [f["id"] for f in csv.DictReader(open(d["otro"] / diario.RUTA_BITACORA))]
    assert "otro-workflow" in ids and f"{HOY}|A|AAA|objetivo_intradia" in ids
    # Y AAA deja de anunciarse en cuanto se vende (relectura tras la venta).
    assert "AAA" not in {v["ticker"] for v in s.vigiladas}

    # --- 10:30 el vigilante muere; 15 min sin latir; se relanza --------------
    _correr(d, s, datetime(2026, 10, 5, 10, 30, tzinfo=config.TZ_ET))
    ultimo = d["latidos"][-1]
    assert "AAA" not in {v["ticker"] for v in ultimo["vigiladas"]}, \
        "el latido anuncia una posición que ya no existe"
    pings_antes = len(d["pings"])
    reloj.hasta(10, 45)                      # silencio: no hay paso() en 15 min
    arrancar, motivo = hf.decidir(ultimo, reloj.ahora(), forzado=False)
    assert arrancar and "muerto" in motivo
    s2 = _sesion(d, historial=lat.historial_de_hoy(ultimo, HOY))
    s2.arrancar()
    cortes = lat.huecos(s2.historial)
    assert len(cortes) == 1 and cortes[0]["minutos"] >= 15
    assert len(d["pings"]) > pings_antes, "el relevo vuelve a hacer ping"

    # --- 11:00 BBB cruza su stop ---------------------------------------------
    reloj.hasta(11, 0)
    xtb.tick("BBB.US", 189.0)
    s2.paso()
    stop = [f for f in _filas() if f["tipo"] == ords.VENTA_STOP_INTRADIA]
    assert len(stop) == 1 and stop[0]["estado"] == "ejecutada"
    assert {v["ticker"] for v in s2.vigiladas} == {"CCC"}

    # --- hasta las 15:45: CCC sale por tiempo --------------------------------
    _correr(d, s2, datetime(2026, 10, 5, 15, 50, tzinfo=config.TZ_ET), cada=60)
    tiempo = [f for f in _filas() if f["tipo"] == ords.VENTA_TIEMPO]
    assert len(tiempo) == 1 and tiempo[0]["estado"] == "ejecutada"
    hora_venta = datetime.fromisoformat(tiempo[0]["cuando_et"])
    assert (CIERRE - hora_venta).total_seconds() / 60 <= \
        config.VIGILANTE_TIEMPO_MIN_ANTES_CIERRE
    r = salud.cargar()["runs"]["ventas"]
    assert r["resultado"] == "ok: 1 vendidas" and "vigilante" in r["detalle"]
    assert not s2.sigue(), "sin posiciones, el vigilante termina"

    # El respaldo por cron, si llegara ahora, no repite nada.
    ej.ENVIADAS.clear()
    ej.enviar(xtb, ords.cargar_pendientes(), "ventas", ords.cargar_enviadas())
    assert ej.ENVIADAS[ords.VENTA_TIEMPO]["enviadas"] == 0
    assert len([f for f in _filas() if f["tipo"] == ords.VENTA_TIEMPO]) == 1

    # --- cierre: reposo y aviso pausado --------------------------------------
    motivo = s2.terminar()
    assert d["latidos"][-1]["estado"] == lat.EN_REPOSO
    assert d["pings"][-1][1].endswith("/pause"), "el aviso externo no se pausó"
    assert "no queda nada" in motivo or "cerró" in motivo

    # --- la reconciliación: XTB cuadra con lo registrado ---------------------
    assert xtb.posiciones() == []
    assert ej.cuadrar_libro(xtb) == []
    # La foto de la cuenta se volcó y dice 0 posiciones.
    foto = json.loads(estado_broker.ARCHIVO.read_text(encoding="utf-8"))
    assert foto["posiciones"] == []
    # Saldo final (modelo caja): 30.000 menos lo que costó CCC el 30/09, más
    # el resultado de AAA y BBB, más lo que se cobró por CCC hoy.
    assert xtb.saldo()["saldo"] == pytest.approx(
        30000 - 30 * 50.0 + 10 * (110.5 - 100.01) + 20 * (189.0 - 200.01)
        + 30 * 55.0, abs=0.02)


def test_EL_MODO_PRUEBA_no_revienta_tras_vender(dia, monkeypatch):
    """La prueba real (probar_vigilante_precios.yml): compra 1 acción, el
    vigilante la vende por un nivel artificial y tiene que terminar en VERDE.
    Antes, la relectura tras la venta buscaba la posición de prueba, no la
    encontraba —se acababa de vender— y reventaba."""
    d, xtb, reloj = dia, dia["xtb"], dia["reloj"]
    monkeypatch.setenv("CENTINELA_PRUEBA", "1")
    xtb.bid["F.US"] = 12.30
    xtb.abrir("F.US", 1, 12.31)
    s = vp.Sesion(xtb, {"enviadas": {}}, trabajo=d["tmp"] / "lat",
                  arrancado=reloj.ahora().isoformat(), cierre=CIERRE,
                  prueba="F:12.25:", reloj=reloj.ahora, mono=reloj.mono)
    xtb.al_recibir_tick(s.precios.encajar)
    s.arrancar()
    xtb.tick("F.US", 12.30)                  # bid >= objetivo 12.25: dispara
    s.paso()
    venta = [f for f in _filas() if f["tipo"] == ords.VENTA_OBJETIVO_INTRADIA]
    assert len(venta) == 1 and venta[0]["estado"] == "ejecutada"
    assert s.vigiladas == [] and not s.sigue()
