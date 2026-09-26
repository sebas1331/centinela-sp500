#!/usr/bin/env python
"""Login guiado a XTB: se resuelve el 2FA UNA vez y se guarda la sesión.

POR QUÉ EXISTE
--------------
XTB no ofrece TOTP como segundo factor: sus métodos son SMS, notificación push
y correo electrónico, y los tres necesitan que un humano lea un código. Eso es
incompatible con un ejecutor desatendido, que es justo lo que queremos montar.

La salida es resolver el 2FA una sola vez, a mano, y quedarse con:

  * el TGT (ticket de 8 horas) en `~/.centinela_xtb_session`;
  * las cookies de CAS —CASTGC y la huella de dispositivo— en
    `~/.centinela_xtb_cookies.json`.

Con esas cookies el navegador deja de ser "nuevo" para XTB, que es lo que
dispara la petición de 2FA. Si XTB respeta su propia lista de dispositivos de
confianza, los siguientes logins entran solos. Si no la respeta, se verá aquí
mismo la próxima vez y habrá que replantear la ejecución automática — no es
algo que se pueda dar por hecho, y por eso este script imprime al final qué
comprobar.

CÓMO SE PASA EL CÓDIGO
----------------------
El navegador se queda abierto unos cinco minutos esperando el código. Como el
script corre desatendido, el código NO se teclea: se escribe en el fichero que
indique `--codigo-en` y el script lo recoge en cuanto aparece. Ese fichero se
borra en cuanto se lee: un código de un solo uso no tiene por qué sobrevivir.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, parche_otp  # noqa: E402

SESION = Path.home() / ".centinela_xtb_session"
COOKIES = Path.home() / ".centinela_xtb_cookies.json"
#: Margen de seguridad frente a los 5 min que el navegador mantiene la página
#: del OTP abierta. Si se agota, se dice claramente en vez de morir con un
#: timeout de Playwright que no explica nada.
ESPERA_CODIGO_SEG = 280


def log(msg: str) -> None:
    print(f"[login] {msg}", flush=True)


async def _esperar_codigo(ruta: Path) -> str:
    """Espera a que aparezca el fichero con el código. Lo borra al leerlo."""
    log(f"ESPERANDO EL CÓDIGO. Escríbelo en: {ruta}")
    t0 = time.time()
    while time.time() - t0 < ESPERA_CODIGO_SEG:
        if ruta.exists():
            codigo = ruta.read_text(encoding="utf-8").strip()
            ruta.unlink(missing_ok=True)   # de un solo uso: no sobrevive
            if codigo:
                log(f"código recibido ({len(codigo)} dígitos)")
                return codigo
        await asyncio.sleep(2)
    raise TimeoutError(
        f"No llegó ningún código en {ESPERA_CODIGO_SEG} s. El código de XTB "
        f"caduca y el navegador cierra su página, así que hay que repetir el "
        f"login entero.")


async def _login(cred: bx.Credenciales, ruta_codigo: Path) -> None:
    from xtb_api.auth.cas_client import CASClient, CASClientConfig
    from xtb_api.types.websocket import CASLoginTwoFactorRequired

    cas = CASClient(CASClientConfig(cookies_file=COOKIES))

    log("abriendo el navegador y enviando las credenciales...")
    resultado = await cas.login_with_browser(cred.email, cred.password,
                                             headless=True)

    if isinstance(resultado, CASLoginTwoFactorRequired):
        log(f"XTB pide segundo factor por {resultado.two_factor_auth_type!r} "
            f"(métodos: {', '.join(resultado.methods)})")
        log("Revisa tu CORREO: XTB acaba de enviarte un código de 6 dígitos.")
        codigo = await _esperar_codigo(ruta_codigo)
        log("enviando el código...")
        resultado = await cas.submit_browser_otp(codigo)
        if isinstance(resultado, CASLoginTwoFactorRequired):
            raise RuntimeError("XTB volvió a pedir 2FA tras enviar el código.")
    else:
        log("XTB NO pidió segundo factor: el dispositivo ya era de confianza.")

    tgt = resultado.tgt
    expira = getattr(resultado, "expires_at", time.time() + 8 * 3600)
    SESION.write_text(json.dumps({"tgt": tgt, "expires_at": expira}),
                      encoding="utf-8")
    SESION.chmod(0o600)
    log(f"sesión guardada en {SESION} (caduca en "
        f"{(expira - time.time()) / 3600:.1f} h)")
    log(f"cookies guardadas en {COOKIES}: "
        f"{'sí' if COOKIES.exists() else 'NO — XTB no devolvió ninguna'}")
    await cas.close() if hasattr(cas, "close") else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codigo-en", type=Path,
                    default=Path("/tmp/centinela_codigo_xtb.txt"),
                    help="fichero del que leer el código de 6 dígitos")
    args = ap.parse_args()

    cred = bx.Credenciales.del_llavero()
    log(f"cuenta {cred.cuenta} (DEMO)")
    # Sin esto el login muere buscando el campo del código por su nombre en
    # polaco. Ver centinela/parche_otp.py.
    log("parche del formulario 2FA: "
        + ("aplicado" if parche_otp.aplicar() else "NO se pudo aplicar"))
    args.codigo_en.unlink(missing_ok=True)   # que no quede uno viejo

    try:
        asyncio.run(_login(cred, args.codigo_en))
    except Exception as exc:
        log(f"FALLO: {type(exc).__name__}: {exc}")
        return 1

    log("")
    log("LISTO. Lo que hay que comprobar ahora es si XTB respeta el")
    log("dispositivo: vuelve a conectar y mira si pide 2FA otra vez.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
