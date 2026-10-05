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
from conftest import CUENTA_PRUEBA  # noqa: E402
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
        str(CUENTA_PRUEBA),               # el número entero de la cuenta
        "•••",                            # ni siquiera sus últimos dígitos
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
    # Dos descargas, y las dos son ficheros estáticos públicos sin token: su
    # propio JSON, y el latido del vigilante de precios, que tiene que pedirse
    # EN VIVO porque su edad es justo el dato.
    assert html.count("fetch(") == 2
    assert 'fetch("operativa.json"' in html
    assert "V.url_latido" in html


def test_el_latido_se_pide_en_vivo_y_no_viaja_en_el_json(datos):
    """Si el latido viniera dentro de operativa.json tendría la edad de la
    última vez que se regeneró la página —horas— y la regla de "más de 10
    minutos = rojo" sería imposible de cumplir sin falsos rojos."""
    v = datos["vigilante_precios"]
    assert set(v) == {"url_latido", "muerto_minutos", "cada_segundos",
                      "sesion_abre", "sesion_cierra", "es_sesion",
                      "posiciones_a_vigilar", "corte_minutos"}
    assert "cuando" not in v, "el latido no puede viajar aquí dentro"
    assert v["url_latido"].startswith("https://raw.githubusercontent.com/")
    assert "token" not in v["url_latido"]


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
                          "reconciliacion", "alertas", "semaforo", "meta",
                          "vigilante_precios", "fiabilidad", "sesion_xtb",
                          "incidentes", "coste_ejecucion"}
    assert set(datos["coste_ejecucion"]) == {"umbral_pct", "ultimas_n", "compras",
                                             "entradas_tardias"}
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


def test_que_la_sesion_caduque_pronto_NO_es_un_aviso(tmp_path, monkeypatch):
    """Este aviso existía cuando se creía que renovar la sesión exigía a una
    persona. El 2026-09-29 se midió que no: el TGT caduca de madrugada todas
    las noches y el login en frío de la mañana entra solo con la cookie de
    dispositivo de confianza.

    Avisar de algo que se arregla solo es ruido, y el ruido se come la señal.
    Lo único que exige a una persona es que un login FALLE, y eso ya pinta rojo
    por otra regla.
    """
    ruta = tmp_path / "sesion.json"
    expira = datetime.now(config.TZ_ET) + timedelta(hours=1)
    ruta.write_text(json.dumps({"tgt": "x", "expires_at": expira.isoformat()}),
                    encoding="utf-8")
    monkeypatch.setattr(bx, "ARCHIVO_SESION", ruta)
    s = _sem()
    assert s["color"] == "verde", s["motivos"]
    assert not any("caduca" in m for m in s["motivos"])


def test_una_sesion_ya_caducada_tampoco(tmp_path, monkeypatch):
    ruta = tmp_path / "sesion.json"
    expira = datetime.now(config.TZ_ET) - timedelta(hours=4)
    ruta.write_text(json.dumps({"tgt": "x", "expires_at": expira.isoformat()}),
                    encoding="utf-8")
    monkeypatch.setattr(bx, "ARCHIVO_SESION", ruta)
    assert _sem()["color"] == "verde"


def test_pero_un_login_que_pide_codigo_sigue_siendo_ROJO():
    """Es lo único que de verdad exige a una persona."""
    s = _sem(_salud(compras={"resultado": bx.MARCA_SESION_CADUCADA,
                             "detalle": "la sesión caducó"}))
    assert s["color"] == "rojo"
    assert any("Renovar sesión XTB" in m for m in s["motivos"])


def test_la_caducidad_se_informa_sin_color(datos):
    """Como mucho, un dato en la página. No un aviso."""
    assert "sesion_xtb" in datos
    if datos["sesion_xtb"] is not None:
        assert set(datos["sesion_xtb"]) == {"horas", "caducada"}


def test_ambar_si_un_componente_se_salta_una_sesion_entera():
    """Martes 20/10: la última vez fue el viernes 16 -> se saltó el lunes 19."""
    viejo = datetime(2026, 10, 16, 18, 0, tzinfo=config.TZ_ET).isoformat()
    s = _sem({"runs": {"vigilante": {"cuando": viejo, "resultado": "ok"}}})
    assert s["color"] == "ambar"
    assert any("no corre desde hace 1 sesión" in m for m in s["motivos"])


def test_un_fin_de_semana_NO_es_retraso():
    """El lunes por la mañana, lo último del viernes no ha faltado a nada: antes
    salía «no corre desde hace 60 h» en cuatro componentes."""
    lunes = datetime(2026, 10, 19, 9, 40, tzinfo=config.TZ_ET)
    viernes = datetime(2026, 10, 16, 20, 0, tzinfo=config.TZ_ET)
    assert go.sesiones_sin_correr(viernes, lunes) == 0
    assert go.sesiones_sin_correr(viernes, AHORA) == 1


def test_una_orden_rechazada_es_ROJA():
    """Era ámbar hasta el 2026-09-30, y ese día quedó claro que no basta: dos
    compras rechazadas y la página en verde con 'fiabilidad 100 %'. Una orden
    rechazada es una decisión del sistema que NO ocurrió."""
    ordenes = [{"tipo": ords.COMPRA, "ticker": "SNDK", "sesion": "2026-10-19",
                "estado": "rechazada", "error": "sin fondos",
                "tipo_nombre": "Compra"}]
    s = _sem(ordenes=ordenes)
    assert s["color"] == "rojo"
    assert any("RECHAZÓ" in m and "SNDK" in m and "sin fondos" in m
               for m in s["motivos"])


def test_un_rechazo_viejo_ya_no_pinta_nada():
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
    """Se mide lo COMPRIMIDO, que es lo que el teléfono siente. El techo en
    crudo obligaba a borrar comentarios para añadir función, y en estas páginas
    los comentarios son el 20 % del fichero."""
    from centinela import presupuesto
    crudo, comprimido = presupuesto.medir(
        RAIZ / "scripts" / "plantilla_operativa.html")
    assert comprimido <= presupuesto.TECHO_COMPRIMIDO, \
        f"operativa.html viaja {comprimido / 1024:.1f} KB comprimidos"
    assert crudo <= presupuesto.TECHO_CRUDO


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
    """Por el workflow reutilizable, que es el ÚNICO que regenera la página y
    lo hace en un solo grupo de concurrencia para todos (incidente de los
    "could not apply ... operativa", 2026-10-02)."""
    jobs = _wf(fichero)["jobs"]
    assert "operativa" in jobs, f"{fichero} no republica la operativa"
    assert jobs["operativa"].get("uses") == "./.github/workflows/pagina_operativa.yml"
    # Y la salud se publica antes, por el diario, en un job sin grupo.
    assert "salud" in jobs and "salud" in jobs["operativa"]["needs"]
    pasos = " ".join(str(p.get("run", "")) for p in jobs["salud"]["steps"])
    assert "publicar_diario.py" in pasos and "commit_y_push" not in pasos


def test_la_pagina_se_regenera_en_un_solo_grupo_y_sin_rebase():
    pagina = _wf("pagina_operativa.yml")["jobs"]["pagina"]
    assert pagina["concurrency"]["group"] == "centinela-operativa"
    pasos = " ".join(str(p.get("run", "")) for p in pagina["steps"])
    assert "publicar_operativa.py" in pasos
    assert "commit_y_push" not in pasos
    # Ningún otro workflow regenera la página por su cuenta.
    for f in WORKFLOWS.glob("*.yml"):
        if f.name == "pagina_operativa.yml":
            continue
        assert "generar_operativa.py" not in f.read_text(encoding="utf-8"), f.name


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
    for paso in _wf("pagina_operativa.yml")["jobs"]["pagina"]["steps"]:
        usa = str(paso.get("uses", ""))
        if "actions/cache" in usa:
            assert usa.startswith("actions/cache/restore@"), \
                f"la página usa {usa}, que además GUARDA"


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
                assert guarda == (nombre not in ("operativa", "pagina")), \
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


def test_la_sesion_se_informa_en_componentes_y_sin_color():
    """Como mucho un dato, nunca un aviso: caduca todas las noches y el login
    en frío de la mañana la renueva solo."""
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    assert 'id="sesion-xtb"' in html and "pintarSesionXTB" in html
    # Sin clase de aviso ni de error: es tenue y punto.
    assert ".nota-sesion{" in html and "var(--tenue)" in html
    assert "empeorarSemaforo" not in html.split("function pintarSesionXTB")[1] \
        .split("function pintarComponentes")[0]


def test_el_reentrenamiento_lee_el_historial_de_git():
    """Corrió el 02/08, el 01/09 y el 01/10, y la página decía "nunca ha
    corrido" porque el registro de salud es posterior y solo guarda la última
    vez. Si git tiene la huella, gana git."""
    import generar_operativa as go
    assert "reentrenamiento" in go.HUELLA_EN_GIT
    visto = go._ultima_vez_en_git(go.HUELLA_EN_GIT["reentrenamiento"])
    assert visto and visto.startswith("20"), "no encuentra el commit"

    # Y con el registro de salud vacío, la fila sale con esa fecha.
    ahora = datetime(2026, 10, 2, 10, 0, tzinfo=config.TZ_ET)
    filas = {f["id"]: f for f in go.bloque_componentes({"runs": {}}, ahora)}
    assert filas["reentrenamiento"]["cuando"] == visto
    assert "historial del repositorio" in filas["reentrenamiento"]["detalle"]


def test_pero_el_registro_de_salud_manda_cuando_existe():
    """Git es el respaldo, no la fuente: el registro sabe CÓMO acabó."""
    import generar_operativa as go
    ahora = datetime(2026, 10, 2, 10, 0, tzinfo=config.TZ_ET)
    salud_datos = {"runs": {"reentrenamiento": {
        "cuando": "2026-10-01T09:05:00-04:00", "resultado": "procesado",
        "detalle": "umbral sin cambios"}}}
    fila = {f["id"]: f for f in go.bloque_componentes(salud_datos, ahora)}
    assert fila["reentrenamiento"]["detalle"] == "umbral sin cambios"


def test_una_orden_que_acaba_el_dia_en_cola_es_ROJA():
    """"en_cola" es legítimo mientras la sesión está abierta. Si el día acaba
    así, la orden no se ejecutó: es lo que pasó con MRNA y FICO."""
    import generar_operativa as go
    ahora = datetime(2026, 10, 1, 18, 0, tzinfo=config.TZ_ET)   # tras el cierre
    orden = {"tipo": ords.COMPRA, "tipo_nombre": "Compra", "ticker": "MRNA",
             "sesion": "2026-10-01", "estado": "en_cola",
             "id_interno": "2026-10-01|A|MRNA|compra"}
    salud_ok = {"runs": {c: {"cuando": ahora.isoformat(), "resultado": "ok"}
                         for c in go.CRITICOS}}
    s = go.semaforo(salud_ok, None, [], [orden], ahora)
    assert s["color"] == "rojo"
    assert any("EN COLA" in m and "MRNA" in m for m in s["motivos"])


@pytest.mark.parametrize("fichero", PUBLICAN)
def test_el_job_de_salud_puede_escribir(fichero):
    """Publica por el diario (un push): sin permiso de escritura moriría en
    rojo cada vez. vigilante.yml es de solo lectura arriba y se le olvidó."""
    wf = _wf(fichero)
    permisos = wf["jobs"]["salud"].get("permissions") or wf.get("permissions") or {}
    assert permisos.get("contents") == "write", f"{fichero}: salud sin escritura"


def test_una_compra_de_hoy_cuenta_como_posicion_que_vigilar():
    """05/10: WDC comprada a las 09:58 contaba 0 posiciones que vigilar, porque
    el simulador no le pone niveles hasta el post-cierre. Si el vigilante
    muriera, la página no se habría puesto roja."""
    from centinela import broker_xtb as bx2, ordenes as o2
    hoy = AHORA.date().isoformat()
    o2.registrar_ejecucion(
        o2.Orden(id=f"{hoy}|A|WDC|entrada_tardia", tipo=o2.ENTRADA_TARDIA,
                 cartera="A", ticker="WDC", acciones=4, sesion=hoy),
        bx2.Ejecucion(ticker="WDC.US", lado="compra", acciones=4,
                      estado="ejecutada", precio=439.71,
                      cuando=AHORA.isoformat()))
    v = go.bloque_vigilante_precios(AHORA, [{"ticker": "WDC", "stop": None,
                                             "objetivo": None}])
    assert v["posiciones_a_vigilar"] == 1



# --------------------------------------------------------------------------- #
# Coste de ejecución de compras
# --------------------------------------------------------------------------- #
def _compra(tipo, ticker, sesion, pagado, apertura=None, estado="ejecutada"):
    from centinela import broker_xtb as bx2, ordenes as o2
    o2.registrar_ejecucion(
        o2.Orden(id=f"{sesion}|A|{ticker}|{tipo}", tipo=tipo, cartera="A",
                 ticker=ticker, acciones=1, sesion=sesion,
                 precio_simulador=apertura),
        bx2.Ejecucion(ticker=f"{ticker}.US", lado="compra", acciones=1,
                      estado=estado, precio=pagado,
                      cuando=f"{sesion}T09:36:00-04:00"))


def test_el_coste_separa_compras_normales_de_entradas_tardias(monkeypatch):
    from centinela import ordenes as o2
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    _compra(o2.COMPRA, "AAA", "2026-10-06", 100.2, apertura=100.0)
    _compra(o2.ENTRADA_TARDIA, "WDC", "2026-10-05", 439.71, apertura=429.935)
    _compra(o2.COMPRA, "BBB", "2026-10-06", 50.0, apertura=40.0, estado="rechazada")
    c = go.bloque_coste_ejecucion()
    assert c["compras"]["n"] == 1 and c["compras"]["media_ultimas"] == 0.2
    assert not c["compras"]["ambar"]
    assert c["entradas_tardias"]["media_ultimas"] == pytest.approx(2.274, abs=0.001)
    assert c["entradas_tardias"]["ambar"]


def test_el_coste_promedia_solo_las_ultimas_diez(monkeypatch):
    from centinela import ordenes as o2
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    _compra(o2.COMPRA, "VIEJA", "2026-10-01", 110.0, apertura=100.0)   # +10 %
    for i in range(10):
        _compra(o2.COMPRA, f"T{i}", f"2026-10-{10 + i:02d}", 100.1, apertura=100.0)
    c = go.bloque_coste_ejecucion()["compras"]
    assert c["n"] == 11 and len(c["ultimas"]) == 10
    assert c["media_ultimas"] == pytest.approx(0.1) and not c["ambar"]


def test_un_coste_alto_pinta_ambar_en_el_semaforo(monkeypatch):
    from centinela import ordenes as o2
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    _compra(o2.COMPRA, "AAA", "2026-10-06", 101.0, apertura=100.0)     # +1 %
    s = _sem({"runs": {}})
    assert any("Coste de ejecución de las compras normales" in m
               for m in s["motivos"])


def test_el_coste_excluye_las_series_rotas(monkeypatch):
    from centinela import datos_erroneos, ordenes as o2
    monkeypatch.setattr(config, "EJECUCION_DESDE", "2026-10-01")
    monkeypatch.setattr(datos_erroneos, "tickers", lambda *_a: {"CTVA"})
    _compra(o2.ENTRADA_TARDIA, "CTVA", "2026-10-02", 12.71, apertura=12.385)
    assert go.bloque_coste_ejecucion()["entradas_tardias"]["n"] == 0


# --------------------------------------------------------------------------- #
# Errores de la página del 05/10: equity, posiciones, notas
# --------------------------------------------------------------------------- #
def test_el_equity_suma_el_valor_de_mercado_y_el_efectivo_es_el_saldo():
    """Modelo de caja (medido): XTB devolvía equity == saldo con WDC abierta."""
    b = go.bloque_cuenta_broker({
        "saldo": 28133.03, "equity": 28133.03, "candado_ok": True, "leido": "x",
        "posiciones": [{"ticker": "WDC.US", "acciones": 4, "precio_entrada": 439.71,
                        "precio_actual": 444.0}]})
    assert b["efectivo"] == 28133.03
    assert b["equity"] == 29909.03
    assert b["pnl_abierto"] == 17.16 and b["invertido"] == 1758.84


def test_sin_precio_de_mercado_no_hay_equity_inventado():
    """XTB da precio 0: ni equity a medias ni el precio de entrada como actual."""
    b = go.bloque_cuenta_broker({
        "saldo": 28133.03, "equity": 28133.03, "candado_ok": True, "leido": "x",
        "posiciones": [{"ticker": "WDC.US", "acciones": 4, "precio_entrada": 439.71,
                        "precio_actual": 0.0}]})
    assert b["equity"] is None and b["pnl_abierto"] is None


def test_la_tabla_usa_los_niveles_de_la_compra_de_hoy_y_no_finge_precio():
    """WDC salía sin objetivo ni stop y con el precio actual igual al de entrada."""
    from centinela import broker_xtb as bx2, ordenes as o2
    hoy = AHORA.date().isoformat()
    o2.registrar_ejecucion(
        o2.Orden(id=f"{hoy}|A|WDC|entrada_tardia", tipo=o2.ENTRADA_TARDIA,
                 cartera="A", ticker="WDC", acciones=4, sesion=hoy,
                 objetivo=495.49, stop=365.46),
        bx2.Ejecucion(ticker="WDC.US", lado="compra", acciones=4,
                      estado="ejecutada", precio=439.71, cuando=AHORA.isoformat()))
    filas = go.bloque_posiciones(
        {"posiciones": [{"ticker": "WDC.US", "acciones": 4, "precio_entrada": 439.71,
                         "precio_actual": 0.0}]}, {"posiciones": {"A": []}}, AHORA)
    assert filas[0]["objetivo"] == 495.49 and filas[0]["stop"] == 365.46
    assert filas[0]["precio_actual"] is None and filas[0]["pnl"] is None


def test_las_notas_largas_no_se_cortan_a_mitad_de_frase():
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    assert "o.error.slice(0,80)" not in html and "detalle.slice(0,90)" not in html
    assert "function textoLargo" in html and "ver más" in html


def test_la_pagina_usa_el_precio_y_los_niveles_del_vigilante():
    html = (RAIZ / "scripts" / "plantilla_operativa.html").read_text(encoding="utf-8")
    assert "function enVivo" in html and "enVivo(L)" in html
