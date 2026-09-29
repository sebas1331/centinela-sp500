"""La página de operativa: schema, semáforo, datos viejos y privacidad.

Esta página es PÚBLICA en GitHub Pages y describe el estado de una cuenta de
broker. Dos cosas no pueden fallar nunca: que no se escape ningún secreto, y
que el semáforo no se ponga verde con información caducada — un verde mentiroso
da tranquilidad sin haberla comprobado, y eso es peor que no tener página.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from centinela import config, salud, broker_xtb as bx, ordenes as ords  # noqa: E402
import generar_operativa as go  # noqa: E402


AHORA = datetime(2026, 10, 20, 18, 0, tzinfo=config.TZ_ET)   # martes, tras el cierre


def _salud(**runs):
    """Un sistema sano, con los cambios que pida cada test.

    Parte de TODOS los componentes críticos reportando 'ok' hace un momento,
    que es el estado normal, para que cada test solo tenga que describir lo que
    quiere probar. Un salud vacío es otra cosa —nadie ha reportado nunca— y
    tiene su propio test.
    """
    base = {"runs": {}, "actualizado": AHORA.isoformat()}
    for clave in go.CRITICOS:
        base["runs"][clave] = {"cuando": AHORA.isoformat(), "resultado": "ok"}
    for k, v in runs.items():
        base["runs"][k] = {"cuando": AHORA.isoformat(), **v}
    return base


def _broker(posiciones=(), candado_ok=True, saldo=30000.0):
    return {"leido": AHORA.isoformat(), "candado_ok": candado_ok,
            "saldo": saldo, "equity": saldo, "divisa": "USD",
            "posiciones": list(posiciones)}


# --------------------------------------------------------------------------- #
# 1. Privacidad — lo primero, porque la página es pública
# --------------------------------------------------------------------------- #
def test_el_numero_de_cuenta_sale_enmascarado():
    m = go.enmascarar_cuenta(22770385)
    assert m == "•••385"
    assert "22770385" not in m


@pytest.mark.parametrize("numero,esperado", [
    (22770385, "•••385"), ("987", "•••987"), ("12", "•••"), (None, "•••"),
])
def test_el_enmascarado_nunca_deja_ver_de_mas(numero, esperado):
    assert go.enmascarar_cuenta(numero) == esperado


def test_el_JSON_publicado_no_contiene_NINGUN_secreto(tmp_path, monkeypatch):
    """La comprobación que importa: se busca cada secreto conocido en el JSON.

    Un descuido aquí publica credenciales en una página indexable. Se mira el
    texto entero, no campo a campo, para que un campo nuevo no se escape.
    """
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    (tmp_path / "estado.json").write_text(json.dumps(
        {"posiciones": {"A": [], "B": []}}), encoding="utf-8")
    monkeypatch.setattr(config, "ESTADO_DIR", tmp_path)
    monkeypatch.setattr(bx, "ARCHIVO_SESION", tmp_path / "no-hay.json")

    datos = go.construir(AHORA)
    texto = json.dumps(datos, ensure_ascii=False)

    prohibidos = [
        str(config.CUENTA_DEMO),          # el número entero de la cuenta
        "tgt", "TGT", "CASTGC",           # la sesión
        "password", "contrasena", "secret", "token", "cookie",
        "XTB_EMAIL", "XTB_PASSWORD", "xtb_password",
    ]
    for p in prohibidos:
        assert p not in texto, f"el JSON público contiene {p!r}"


def test_la_pagina_no_llama_a_la_API_de_GitHub():
    """Llamarla exigiría un token en una página pública. El estado lo escriben
    los propios workflows en salud.json."""
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    assert "api.github.com" not in html
    # Lo único que se descarga es su propio JSON.
    assert html.count('fetch(') == 1 and 'fetch("operativa.json"' in html


# --------------------------------------------------------------------------- #
# 2. Schema
# --------------------------------------------------------------------------- #
@pytest.fixture
def datos(tmp_path, monkeypatch):
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    (tmp_path / "estado.json").write_text(json.dumps(
        {"posiciones": {"A": [], "B": []}}), encoding="utf-8")
    monkeypatch.setattr(config, "ESTADO_DIR", tmp_path)
    monkeypatch.setattr(bx, "ARCHIVO_SESION", tmp_path / "no-hay.json")
    return go.construir(AHORA)


def test_schema_de_operativa_json(datos):
    assert set(datos) == {"generado", "hoy", "hoy_es_sesion", "componentes",
                          "broker", "posiciones", "ordenes", "niveles",
                          "reconciliacion", "alertas", "semaforo", "meta"}
    assert set(datos["hoy"]) == {"fecha", "es_sesion", "hubo_escaneo", "senales",
                                 "decididas", "enviadas", "ejecutadas", "huecos"}
    assert set(datos["semaforo"]) == {"color", "titulo", "motivos", "n_rojos",
                                      "n_ambares"}
    assert datos["semaforo"]["color"] in ("verde", "ambar", "rojo")

    # Un componente por cada cosa que puede fallar, ni uno menos.
    ids = {c["id"] for c in datos["componentes"]}
    assert ids == set(salud.COMPONENTES)
    for c in datos["componentes"]:
        assert set(c) == {"id", "nombre", "cadencia", "cuando", "resultado",
                          "detalle", "url", "proxima_sesion"}

    json.dumps(datos, allow_nan=False)    # sin NaN: rompería JSON.parse


def test_el_JSON_es_serializable_sin_NaN(datos):
    """pandas mete NaN donde falta un dato y JSON.parse revienta con eso."""
    json.dumps(datos, allow_nan=False)


# --------------------------------------------------------------------------- #
# 3. El semáforo, regla por regla
# --------------------------------------------------------------------------- #
def _sem(salud_datos=None, broker=None, posiciones=(), ordenes=(), ahora=AHORA):
    return go.semaforo(salud_datos or _salud(), broker, list(posiciones),
                       list(ordenes), ahora)


def test_verde_cuando_no_hay_nada_que_decir():
    s = _sem(_salud(preapertura={"resultado": "procesado"},
                    reconcilia={"resultado": "ok"}))
    assert s["color"] == "verde" and s["motivos"] == []


def test_rojo_si_un_componente_critico_termino_mal():
    s = _sem(_salud(preapertura={"resultado": "fallo:ventana-perdida"}))
    assert s["color"] == "rojo"
    assert any("pre-apertura" in m for m in s["motivos"])


def test_rojo_si_la_reconciliacion_encontro_diferencias():
    s = _sem(_salud(reconcilia={"resultado": "diferencias",
                                "detalle": "MRNA: en el simulador y no en XTB"}))
    assert s["color"] == "rojo"
    assert any("MRNA" in m for m in s["motivos"])


def test_rojo_si_la_sesion_de_XTB_caduco():
    s = _sem(_salud(compras={"resultado": bx.MARCA_SESION_CADUCADA,
                             "detalle": "la sesión caducó"}))
    assert s["color"] == "rojo"
    assert any("Renovar sesión XTB" in m for m in s["motivos"])


def test_rojo_si_el_candado_de_demo_se_activo():
    s = _sem(_salud(), broker=_broker(candado_ok=False))
    assert s["color"] == "rojo"
    assert any("candado" in m.lower() for m in s["motivos"])


def test_rojo_si_una_posicion_pasa_de_su_fecha_de_salida():
    """Una posición pasada de fecha deja de ser la estrategia que se simula."""
    pos = [{"ticker": "MRNA", "fecha_limite": "2026-10-15"}]   # AHORA es el 20
    s = _sem(posiciones=pos)
    assert s["color"] == "rojo"
    assert any("MRNA" in m and "sigue abierta" in m for m in s["motivos"])


def test_ambar_si_hubo_una_salida_diferida():
    ordenes = [{"tipo": ords.VENTA_TIEMPO_DIFERIDO, "ticker": "GLW",
                "sesion": "2026-10-19", "estado": "ejecutada"}]
    s = _sem(ordenes=ordenes)
    assert s["color"] == "ambar"
    assert any("plan B" in m for m in s["motivos"])


def test_ambar_si_la_sesion_esta_a_punto_de_caducar(tmp_path, monkeypatch):
    ruta = tmp_path / "sesion.json"
    expira = datetime.now(config.TZ_ET) + timedelta(hours=1)
    ruta.write_text(json.dumps({"tgt": "x", "expires_at": expira.isoformat()}),
                    encoding="utf-8")
    monkeypatch.setattr(bx, "ARCHIVO_SESION", ruta)
    s = _sem()
    assert s["color"] == "ambar"
    assert any("caduca en" in m for m in s["motivos"])


def test_ambar_si_un_componente_lleva_demasiado_sin_correr():
    viejo = (AHORA - timedelta(hours=40)).isoformat()
    s = _sem({"runs": {"vigilante": {"cuando": viejo, "resultado": "ok"}}})
    assert s["color"] == "ambar"
    assert any("no corre desde hace" in m for m in s["motivos"])


def test_ambar_si_hubo_una_orden_rechazada_hace_poco():
    ordenes = [{"tipo": ords.COMPRA, "ticker": "SNDK", "sesion": "2026-10-19",
                "estado": "rechazada", "error": "sin fondos",
                "tipo_nombre": "Compra"}]
    s = _sem(ordenes=ordenes)
    assert s["color"] == "ambar"
    assert any("rechazada" in m and "SNDK" in m for m in s["motivos"])


def test_un_rechazo_viejo_ya_no_pinta_ambar():
    ordenes = [{"tipo": ords.COMPRA, "ticker": "SNDK", "sesion": "2026-09-01",
                "estado": "rechazada", "error": "x", "tipo_nombre": "Compra"}]
    assert _sem(ordenes=ordenes)["color"] == "verde"


def test_el_rojo_no_oculta_los_ambares():
    """Quien entra a arreglar algo quiere ver TODO lo que hay, no solo lo peor."""
    ordenes = [{"tipo": ords.VENTA_TIEMPO_DIFERIDO, "ticker": "GLW",
                "sesion": "2026-10-19", "estado": "ejecutada"}]
    s = _sem(_salud(postcierre={"resultado": "fallo:sesion-pendiente"}),
             ordenes=ordenes)
    assert s["color"] == "rojo"
    assert s["n_rojos"] >= 1 and s["n_ambares"] >= 1
    assert any("plan B" in m for m in s["motivos"])


# --------------------------------------------------------------------------- #
# 4. Datos viejos (la regla vive en el JS; aquí se fija el contrato)
# --------------------------------------------------------------------------- #
def test_el_html_detecta_datos_viejos_y_solo_puede_EMPEORAR_el_color():
    """La comprobación del navegador nunca puede pintar de verde algo que el
    generador marcó en rojo: solo añade motivos y sube la gravedad."""
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    assert "function frescura(" in html
    assert "Los datos no se actualizan desde hace" in html
    # Umbrales explícitos, no números sueltos por el código.
    assert "HORAS_AMBAR" in html and "HORAS_ROJO" in html
    # Y la regla de que solo empeora.
    assert 'if(f.nivel === "rojo" || color === "verde") color = f.nivel;' in html


def test_el_json_dice_si_hoy_habia_mercado(datos):
    """El navegador lo necesita para saber si la página DEBÍA refrescarse."""
    assert isinstance(datos["hoy_es_sesion"], bool)


# --------------------------------------------------------------------------- #
# 5. Presupuesto y enlaces
# --------------------------------------------------------------------------- #
def test_la_pagina_cabe_en_el_presupuesto():
    tam = (RAIZ / "scripts" / "plantilla_operativa.html").stat().st_size
    assert tam <= 50 * 1024, f"operativa.html pesa {tam / 1024:.1f} KB"


def test_sin_frameworks_ni_CDNs():
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    for prohibido in ("http://", "cdn.", "unpkg", "jsdelivr", "googleapis",
                      "<script src", "<link rel=\"stylesheet\""):
        assert prohibido not in html, f"referencia externa: {prohibido}"


def test_las_dos_paginas_se_enlazan_entre_si():
    op = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    dash = (RAIZ / "scripts" / "plantilla_dashboard.html").read_text(encoding="utf-8")
    assert 'href="index.html"' in op
    assert 'href="operativa.html"' in dash


def test_la_hora_se_muestra_en_hora_de_Ecuador():
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    assert "America/Guayaquil" in html


# --------------------------------------------------------------------------- #
# 6. El cableado en los workflows
# --------------------------------------------------------------------------- #
import yaml  # noqa: E402

WORKFLOWS = RAIZ / ".github" / "workflows"
#: Los workflows que tienen que republicar la página, y de qué job cuelgan.
PUBLICAN = ["preapertura.yml", "postcierre.yml", "ventas.yml",
            "vigilante.yml", "reentrenamiento.yml",
            "verificacion_apertura.yml"]


def _wf(nombre: str) -> dict:
    return yaml.safe_load((WORKFLOWS / nombre).read_text(encoding="utf-8"))


@pytest.mark.parametrize("fichero", PUBLICAN)
def test_cada_workflow_que_toca_XTB_republica_la_pagina(fichero):
    jobs = _wf(fichero)["jobs"]
    assert "operativa" in jobs, f"{fichero} no republica la operativa"
    pasos = " ".join(str(p.get("run", "")) for p in jobs["operativa"]["steps"])
    assert "scripts/generar_operativa.py" in pasos
    assert "commit_y_push.sh" in pasos


@pytest.mark.parametrize("fichero", PUBLICAN)
def test_la_publicacion_va_en_job_aparte_y_nunca_bloquea_al_trabajo(fichero):
    """`needs` para no poder tocar nada ya persistido; `always()` para que los
    días malos también se publiquen."""
    op = _wf(fichero)["jobs"]["operativa"]
    assert op.get("needs"), f"{fichero}: operativa sin needs"
    assert "always()" in str(op.get("if", "")), f"{fichero}: operativa sin always()"
    # Y nadie puede depender de ella: si la página revienta, se queda sola.
    for nombre, job in _wf(fichero)["jobs"].items():
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        assert "operativa" not in needs, f"{fichero}: {nombre} depende de operativa"


@pytest.mark.parametrize("fichero", PUBLICAN)
def test_la_publicacion_no_silencia_errores(fichero):
    texto = (WORKFLOWS / fichero).read_text(encoding="utf-8")
    for prohibido in ("|| true", "continue-on-error", "2>/dev/null", "set +e"):
        assert prohibido not in texto, f"{fichero} silencia errores: {prohibido}"


def test_el_job_que_publica_no_puede_sobrescribir_la_sesion_de_XTB():
    """Lee la caducidad con `cache/restore`, nunca con `cache`: un job que no
    habla con el broker no debe poder pisar su sesión al terminar."""
    for fichero in PUBLICAN:
        for paso in _wf(fichero)["jobs"]["operativa"]["steps"]:
            usa = str(paso.get("uses", ""))
            if "actions/cache" in usa:
                assert usa.startswith("actions/cache/restore@"), \
                    f"{fichero}: la operativa usa {usa}, que además GUARDA"


def test_los_componentes_criticos_los_registra_alguien():
    """Cada componente crítico tiene quien anote cómo acabó; si no, la página
    lo daría por 'nunca visto' para siempre."""
    registrados = set()
    for fichero in PUBLICAN:
        for job in _wf(fichero)["jobs"].values():
            for paso in job.get("steps", []):
                run = str(paso.get("run", ""))
                if "registrar_salud.py" in run:
                    registrados |= {c for c in salud.COMPONENTES
                                    if f"registrar_salud.py {c}" in run}
    # El ejecutor se registra a sí mismo desde dentro, con más detalle.
    ejecutor = (RAIZ / "scripts" / "ejecutor_xtb.py").read_text(encoding="utf-8")
    assert "salud.registrar" in ejecutor
    faltan = set(go.CRITICOS) - registrados
    assert not faltan, f"nadie registra: {sorted(faltan)}"


def test_el_vigilante_publica_todos_los_dias():
    """Es el único que corre en fin de semana: sin él, un puente largo dejaría
    la página vieja y el navegador la pintaría de ámbar sin motivo."""
    wf = _wf("vigilante.yml")
    crons = [c["cron"] for c in wf[True]["schedule"]]
    assert any(c.split()[-1] == "*" for c in crons), \
        "el vigilante no corre todos los días"
    assert "operativa" in wf["jobs"]


def test_el_job_que_escribe_del_vigilante_es_el_unico_con_permiso():
    """El vigilar es de solo lectura por diseño; el permiso de escritura vive
    en el job de la página y en ningún otro sitio."""
    wf = _wf("vigilante.yml")
    assert wf["permissions"]["contents"] == "read"
    assert wf["jobs"]["operativa"]["permissions"]["contents"] == "write"
    assert "vigilar" not in wf["jobs"]["operativa"].get("permissions", {})
    assert "concurrency" not in wf


# --------------------------------------------------------------------------- #
# 7. Traducción del result de un job al vocabulario de salud
# --------------------------------------------------------------------------- #
sys.path.insert(0, str(RAIZ / "scripts"))
import registrar_salud as rs  # noqa: E402


@pytest.mark.parametrize("salida,job,esperado", [
    ("procesado", "success", "procesado"),
    ("omitido:no-es-sesion", "success", "omitido:no-es-sesion"),
    ("", "success", "ok"),
    ("", "failure", "fallo:job-en-rojo"),
    ("", "cancelled", "fallo:job-cancelado"),
    ("", "skipped", "omitido:job-saltado"),
    ("", "una-cosa-nueva", "fallo:job-una-cosa-nueva"),
])
def test_el_result_del_job_se_traduce_al_vocabulario_de_salud(salida, job, esperado):
    assert rs.resolver(salida, job) == esperado


def test_un_job_en_rojo_manda_sobre_lo_que_diga_el_componente():
    """Trabajo hecho y job muerto después es rojo igual: la página tiene que
    enseñarlo, no fiarse del 'procesado' que se alcanzó a escribir."""
    r = rs.resolver("procesado", "failure")
    assert r.startswith("fallo:") and "procesado" in r


def test_no_se_pisa_lo_que_el_propio_componente_ya_conto(tmp_path, monkeypatch):
    """El ejecutor distingue candado, sesión caducada y diferencias; el job solo
    ve 'failure'. Gana el que sabe más."""
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    salud.registrar("compras", "candado", "la sesión apunta a otra cuenta")
    assert salud.hablo_en_este_run("compras")

    monkeypatch.setattr(sys, "argv",
                        ["registrar_salud.py", "compras", "--job", "failure",
                         "--solo-si-falta"])
    assert rs.main() == 0
    assert salud.cargar()["runs"]["compras"]["resultado"] == "candado"


def test_pero_si_el_componente_murio_antes_de_hablar_manda_el_job(tmp_path, monkeypatch):
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setattr(sys, "argv",
                        ["registrar_salud.py", "compras", "--job", "failure",
                         "--solo-si-falta"])
    assert rs.main() == 0
    assert salud.cargar()["runs"]["compras"]["resultado"] == "fallo:job-en-rojo"


def test_un_run_anterior_no_cuenta_como_haber_hablado(tmp_path, monkeypatch):
    """Si no, un fallo de hoy se escondería detrás del 'ok' de ayer."""
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    salud.registrar("ventas", "ok")
    monkeypatch.setenv("GITHUB_RUN_ID", "2")
    assert not salud.hablo_en_este_run("ventas")


def test_ambar_si_un_componente_critico_no_reporto_nunca():
    """Sin esta regla, un workflow mal cableado deja la página verde para
    siempre: las demás reglas solo miran a los componentes que tienen fecha."""
    s = _sem({"runs": {}})
    assert s["color"] == "ambar"
    assert s["n_ambares"] == len(go.CRITICOS)
    assert all("no ha reportado nunca" in m for m in s["motivos"])


def test_y_se_apaga_en_cuanto_cada_uno_corre_una_vez():
    todos = {c: {"resultado": "ok"} for c in go.CRITICOS}
    assert _sem(_salud(**todos))["color"] == "verde"


# --------------------------------------------------------------------------- #
# 8. Higiene de cachés
#
# Van aquí porque es la página de operativa la que se queda muda cuando esto se
# rompe: sin caché de sesión no hay fecha de caducidad que avisar, y la caché de
# sesión desaparece cuando la de precios se come los 10 GB del repositorio.
# --------------------------------------------------------------------------- #
def _pasos_de_cache_xtb():
    """Todos los pasos de caché de sesión del repositorio, con sus rutas."""
    encontrados = []
    for fichero in sorted(WORKFLOWS.glob("*.yml")):
        for nombre, job in (_wf(fichero.name).get("jobs") or {}).items():
            for paso in job.get("steps", []):
                if not str(paso.get("uses", "")).startswith("actions/cache"):
                    continue
                con = paso.get("with", {})
                if not str(con.get("key", "")).startswith("xtb-sesion-"):
                    continue
                rutas = con.get("path", "")
                rutas = tuple(sorted(r.strip() for r in str(rutas).split("\n")
                                     if r.strip()))
                encontrados.append((fichero.name, nombre, rutas))
    return encontrados


def test_todos_los_pasos_de_cache_piden_las_mismas_rutas():
    """GitHub calcula la VERSIÓN de una caché a partir de su lista de rutas, y
    una clave solo casa dentro de su versión. Dos listas distintas para la misma
    clave = la caché nunca se restaura, y no hay ningún error que lo delate:
    el paso sale en verde diciendo «Cache not found».

    Pasó de verdad el 2026-09-28: el job que lee la caducidad de la sesión pedía
    solo el fichero de sesión y los demás pedían tres ficheros.
    """
    pasos = _pasos_de_cache_xtb()
    assert pasos, "nadie cachea la sesión de XTB"
    rutas = {p[2] for p in pasos}
    assert len(rutas) == 1, (
        "listas de rutas distintas para la misma clave:\n  "
        + "\n  ".join(f"{f}/{j}: {list(r)}" for f, j, r in pasos))
    assert rutas.pop() == (
        "~/.centinela_xtb_cookies.json",
        "~/.centinela_xtb_session",
        "~/.centinela_xtb_ticket.json",
    )


def test_solo_guardan_cache_los_que_hablan_con_el_broker():
    """Quien solo lee la caducidad usa cache/restore: un job que no toca XTB no
    puede sobrescribir la sesión al terminar."""
    for fichero in sorted(WORKFLOWS.glob("*.yml")):
        for nombre, job in (_wf(fichero.name).get("jobs") or {}).items():
            for paso in job.get("steps", []):
                usa = str(paso.get("uses", ""))
                if not usa.startswith("actions/cache"):
                    continue
                if not str(paso.get("with", {}).get("key", "")).startswith("xtb-"):
                    continue
                guarda = not usa.startswith("actions/cache/restore@")
                assert guarda == (nombre != "operativa"), \
                    f"{fichero.name}/{nombre} usa {usa}"


def _pasos_de_cache_precios():
    hallados = []
    for fichero in sorted(WORKFLOWS.glob("*.yml")):
        for nombre, job in (_wf(fichero.name).get("jobs") or {}).items():
            for paso in job.get("steps", []):
                usa = str(paso.get("uses", ""))
                clave = str(paso.get("with", {}).get("key", ""))
                if usa.startswith("actions/cache") and clave.startswith("precios"):
                    hallados.append((fichero.name, nombre, usa, clave))
    return hallados


def test_la_cache_de_precios_se_guarda_una_vez_al_dia_y_no_una_por_run():
    """El 2026-09-28 el repositorio estaba al 99 % de sus 10 GB de caché: 74
    cachés de precios de 135 MB, porque la clave llevaba el id del run y por
    tanto NUNCA acertaba en la primaria, así que cada run guardaba una nueva.
    La escalera de la pre-apertura sola dejaba ~17 al día.

    Eso no es solo desperdicio: al pasar de 10 GB GitHub desaloja por orden de
    último uso, y lo que desaloja es la sesión de XTB (359 bytes), que es lo que
    evita tener que renovarla a mano.
    """
    for fichero, job, usa, clave in _pasos_de_cache_precios():
        if usa.startswith("actions/cache/restore@"):
            continue                       # solo lee: no guarda nada
        assert "github.run_id" not in clave, (
            f"{fichero}/{job}: la clave lleva el id del run, así que este job "
            f"guardará una caché de 135 MB en CADA ejecución")
        assert "steps.dia.outputs.fecha" in clave, \
            f"{fichero}/{job}: la clave debería llevar la fecha"


def test_solo_guardan_precios_los_jobs_que_amplian_la_cache():
    """Los demás (dashboard, órdenes) solo leen: guardar desde ellos duplicaba
    la misma caché dos y tres veces por run."""
    guardan = {(f, j) for f, j, u, _ in _pasos_de_cache_precios()
               if not u.startswith("actions/cache/restore@")}
    assert guardan == {("preapertura.yml", "preapertura"),
                       ("postcierre.yml", "postcierre"),
                       ("reentrenamiento.yml", "reentrenar")}


def test_publicar_la_pagina_no_compite_con_los_escaneos():
    """El 2026-09-28 el job de la página del Vigilante estaba en el grupo de
    escritura de los escaneos. El post-cierre tomó el turno, este se puso en
    cola, llegó un segundo post-cierre y GitHub canceló el más viejo: solo
    guarda UN run en cola por grupo.

    Publicar una página no puede depender de un turno que un escaneo puede
    retener 160 minutos. Los ficheros que toca no los toca ningún escaneo, así
    que el rebase de commit_y_push.sh basta.
    """
    for fichero in PUBLICAN:
        grupo = (_wf(fichero)["jobs"]["operativa"]
                 .get("concurrency", {}).get("group", ""))
        assert "centinela-escritura" not in grupo, (
            f"{fichero}: la página compite por el turno de los escaneos")


def test_el_vigilante_revisa_la_sesion_antes_de_la_ventana_de_compras():
    """La sesión de XTB caduca de madrugada (8 h desde el último login, que es
    el post-cierre de la tarde anterior). Un único disparo a las 14:37 UTC
    avisaba SIEMPRE tarde: la ventana de compras es 09:30-13:10 UTC en verano y
    10:30-14:10 en invierno, así que para cuando avisaba el run de compras ya se
    había encontrado la sesión cerrada.
    """
    crons = [c["cron"] for c in _wf("vigilante.yml")[True]["schedule"]]
    minutos = []
    for c in crons:
        m, h = c.split()[0], c.split()[1]
        if "," in m or "," in h or "*" in h:
            continue
        minutos.append(int(h) * 60 + int(m))
    APERTURA_VENTANA_VERANO = 9 * 60 + 30
    assert any(m < APERTURA_VENTANA_VERANO for m in minutos), (
        f"ningún disparo cae antes de la ventana de compras: {crons}")
    # Y alguno tiene que seguir corriendo TODOS los días: es el que mantiene
    # viva la caché de la sesión los fines de semana.
    assert any(c.split()[-1] == "*" for c in crons)


# --------------------------------------------------------------------------- #
# 9. "No corrió" no es "salió mal"
# --------------------------------------------------------------------------- #
def test_una_reconciliacion_que_no_corrio_no_es_una_discrepancia():
    """El 2026-09-28 el segundo post-cierre del día fue idempotente, su ejecutor
    se saltó, y la página lo pintó de rojo diciendo «la reconciliación encontró
    diferencias: ver el run». No había encontrado nada: no había corrido.
    """
    s = _sem(_salud(reconcilia={"resultado": "omitido:job-saltado"}))
    assert s["color"] == "verde", s["motivos"]


def test_pero_una_reconciliacion_que_si_corrio_y_no_cuadro_sigue_siendo_roja():
    s = _sem(_salud(reconcilia={"resultado": "diferencias",
                                "detalle": "MRNA: en el simulador y no en XTB"}))
    assert s["color"] == "rojo"
    assert any("MRNA" in m for m in s["motivos"])


def test_un_job_saltado_no_pisa_el_resultado_real_de_antes(tmp_path, monkeypatch):
    """Registrar «no corrí» por encima del «ok» de hace tres horas cuenta menos
    y confunde más. Si un componente deja de correr de verdad, lo denuncia la
    regla de «lleva X h sin aparecer»."""
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    salud.registrar("reconcilia", "ok", "todo cuadra")

    monkeypatch.setenv("GITHUB_RUN_ID", "2")
    monkeypatch.setattr(sys, "argv",
                        ["registrar_salud.py", "reconcilia", "--job", "skipped",
                         "--solo-si-falta"])
    assert rs.main() == 0
    guardado = salud.cargar()["runs"]["reconcilia"]
    assert guardado["resultado"] == "ok" and guardado["run"] == "1"


def test_pero_un_omitido_que_publica_el_propio_componente_si_se_guarda(
        tmp_path, monkeypatch):
    """`omitido:ya-procesado` lo dice el escaneo, no el job: es información real
    sobre una idempotencia que funcionó."""
    monkeypatch.setattr(salud, "ARCHIVO", tmp_path / "salud.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setattr(sys, "argv",
                        ["registrar_salud.py", "postcierre",
                         "--salida", "omitido:ya-procesado", "--job", "success"])
    assert rs.main() == 0
    assert salud.cargar()["runs"]["postcierre"]["resultado"] == "omitido:ya-procesado"
