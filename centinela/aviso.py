"""Aviso externo: healthchecks.io avisa por email si el vigilante deja de latir.

POR QUÉ HACE FALTA ALGO FUERA DE GITHUB
---------------------------------------
Si el vigilante de precios muere con posiciones abiertas, las posiciones se
quedan sin stop: XTB no los acepta en acciones al contado. Hasta ahora lo único
que lo denunciaba era la propia página (en rojo a los 10-15 minutos) y el
Vigilante general, que corre por cron — y los crons de este repositorio llegan
con 4 a 7 horas de retraso. Nadie recibía nada.

healthchecks.io invierte la pregunta: no hay que acordarse de mirar, porque es
el SILENCIO lo que dispara el aviso. El vigilante hace ping cada minuto
mientras tiene posiciones abiertas con el mercado abierto; si los pings se
interrumpen más de 5 minutos, healthchecks manda un email.

FUERA DE HORARIO NO HAY QUE AVISAR
----------------------------------
Al terminar la sesión —o cuando no queda nada que vigilar— el vigilante PAUSA
el check por la API de gestión. Un check pausado no avisa por estar callado, y
vuelve solo al estado normal con el primer ping de la sesión siguiente. Así el
silencio de la noche y del fin de semana no manda emails.

CONFIGURACIÓN (secrets del repositorio)
---------------------------------------
  HEALTHCHECKS_PING_URL   https://hc-ping.com/<uuid> del check
  HEALTHCHECKS_API_KEY    clave de la API del proyecto (lectura y escritura),
                          solo para pausar

Sin la URL no hay aviso externo, y eso NO es silencioso: el vigilante lo dice
en el log y en el latido, y la página lo pinta en ámbar.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

#: Cada cuánto se hace ping. El check se configura con periodo de 1 minuto y
#: gracia de 4: avisa tras 5 minutos sin ping.
CADA_SEGUNDOS = 60
API = "https://healthchecks.io/api/v3/checks/{uuid}/pause"
TIMEOUT_SEG = 8


def url_ping() -> str:
    return os.environ.get("HEALTHCHECKS_PING_URL", "").strip()


def configurado() -> bool:
    return bool(url_ping())


def _uuid(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _abrir(req) -> int:
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:  # noqa: S310
        return r.status


def ping(detalle: str = "", abrir=_abrir) -> bool:
    """Un ping. True si healthchecks lo recibió.

    No revienta: un ping que no sale por un hipo de red no puede matar al
    vigilante, que es justo lo que este aviso vigila. Si los pings dejan de
    salir de verdad, healthchecks avisa — que es exactamente lo correcto.
    Lo que sí hace es decirlo en el log.
    """
    url = url_ping()
    if not url:
        return False
    req = urllib.request.Request(url, data=detalle[:1000].encode("utf-8"),
                                 method="POST")
    try:
        return abrir(req) == 200
    except (urllib.error.URLError, OSError) as exc:
        print(f"[aviso] el ping a healthchecks no salió ({exc!r}).", flush=True)
        return False


def pausar(motivo: str, abrir=_abrir) -> bool:
    """Pausa el check para que el silencio fuera de sesión no avise.

    True si quedó pausado. Si falla, se dice: lo peor que pasa es un email de
    más esta noche, que es mucho mejor que uno de menos durante la sesión.
    """
    url, clave = url_ping(), os.environ.get("HEALTHCHECKS_API_KEY", "").strip()
    if not url:
        return False
    if not clave:
        print("[aviso] sin HEALTHCHECKS_API_KEY: no se puede pausar el check; "
              "healthchecks avisará del silencio fuera de sesión.", flush=True)
        return False
    req = urllib.request.Request(
        API.format(uuid=_uuid(url)), data=json.dumps({}).encode(),
        headers={"X-Api-Key": clave, "Content-Type": "application/json"},
        method="POST")
    try:
        ok = abrir(req) == 200
    except (urllib.error.URLError, OSError) as exc:
        print(f"[aviso] no se pudo pausar el check ({exc!r}).", flush=True)
        return False
    print(f"[aviso] check pausado ({motivo}).", flush=True)
    return ok
