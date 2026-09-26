"""Parche del formulario de 2FA del cliente de xStation5.

EL PROBLEMA
-----------
`xtb_api.auth.browser_auth.BrowserCASAuth.submit_otp` localiza el campo del
código y el botón de envío por su texto EN POLACO:

    otp_input  = page.get_by_placeholder("Wprowadź kod tutaj")
    submit_btn = page.get_by_role("button", name="Weryfikacja")

XTB es una empresa polaca y el autor de la librería usa su interfaz en polaco.
Con la plataforma en otro idioma esos localizadores no encuentran nada y el
login muere con:

    TimeoutError: Locator.wait_for: Timeout 10000ms exceeded.
    waiting for get_by_placeholder("Wprowadź kod tutaj") to be visible

Pero el fallo es peor de lo que parece: volcando el DOM real de la cuenta el
2026-09-25, el placeholder del campo resultó ser **"Wpisz tutaj kod e-mail"**,
no "Wprowadź kod tutaj". El texto cambia según el método de 2FA (correo, SMS o
push), así que la librería falla INCLUSO en polaco cuando el segundo factor va
por email.

LO QUE HAY DE VERDAD EN LA PÁGINA
---------------------------------
Del volcado, dentro del Shadow DOM de `<xs6-two-factor-authentication>`:

    INPUT   id="otpCode"  name="otpCode"  type="text"
    BUTTON  type="submit"  texto="Weryfikacja"

El `id` es estable y no depende del idioma: es el ancla buena. Ojo con los
selectores genéricos, porque en la misma página hay un `input[type=text]`
INVISIBLE (el `xslogin` del formulario de acceso) que aparece antes en el DOM:
un `.first` sin filtrar por visibilidad se lo lleva y falla. Ese fue el primer
intento de este parche.

LA SOLUCIÓN
-----------
Se sustituye `submit_otp` por una versión que localiza los dos elementos por lo
que SON y no por cómo se llaman en un idioma concreto: el campo, por ser un
input de texto visible dentro del formulario; el botón, por ser el de envío.
Como red de seguridad se prueban también los textos conocidos en polaco,
español e inglés.

Este parche vive AQUÍ y no en un fork de la librería a propósito: es
exactamente para lo que existe `centinela/broker_xtb.py` como capa de
aislamiento. Cuando la librería lo arregle, se borra este fichero y no hay que
tocar nada más.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

#: Textos conocidos del campo y del botón, por si los selectores estructurales
#: fallan. No son la vía principal: son el último recurso.
PLACEHOLDERS = ("Wpisz tutaj kod e-mail",        # el real con 2FA por correo
                "Wprowadź kod tutaj",            # el que busca la librería
                "Introduce el código aquí", "Ingresa el código aquí",
                "Enter code here", "Enter the code here")
BOTONES = ("Weryfikacja", "Verificación", "Verificar", "Verification", "Verify",
           "Confirmar", "Confirm")

#: Selectores del campo del código, del más específico al más general. Los
#: locators de Playwright atraviesan el Shadow DOM del componente
#: XS6-TWO-FACTOR-AUTHENTICATION, así que no hace falta recorrerlo a mano.
SELECTORES_INPUT = (
    "#otpCode",                       # el id real, estable y sin idioma
    'input[name="otpCode"]',
    'xs6-two-factor-authentication input[type="text"]',
    'input[inputmode="numeric"]',
    'input[autocomplete="one-time-code"]',
    'input[type="tel"]',
    # Último recurso. `:visible` NO es decorativo: en esta página hay un
    # input[type=text] oculto (el login) que va antes en el DOM.
    'input[type="text"]:visible',
)

#: Selectores del botón de envío, acotados al componente del 2FA: fuera de él
#: hay otros `button[type=submit]` visibles (el del centro de ayuda).
SELECTORES_BOTON = (
    'xs6-two-factor-authentication button[type="submit"]',
    'button[type="submit"]:visible',
)


async def _localizar_input(page):
    """El campo del código, por lo que es y no por cómo se llama."""
    for selector in SELECTORES_INPUT:
        loc = page.locator(selector).first
        try:
            await loc.wait_for(state="visible", timeout=4000)
            return loc
        except Exception:  # noqa: BLE001 — se prueba el siguiente
            continue
    for texto in PLACEHOLDERS:
        loc = page.get_by_placeholder(texto)
        try:
            await loc.wait_for(state="visible", timeout=2000)
            return loc
        except Exception:  # noqa: BLE001
            continue
    await _volcar_diagnostico(page)
    raise RuntimeError(
        "No se encontró el campo del código 2FA en la página de XTB. Puede que "
        "hayan cambiado el formulario: mira la captura que deja este parche.")


async def _pulsar_enviar(page):
    """El botón de enviar. Si no aparece ninguno conocido, se usa Enter.

    Muchos formularios de OTP se envían solos al completar los seis dígitos, y
    los que no, responden a Enter. Así que no encontrar el botón no es motivo
    para abandonar.
    """
    for nombre in BOTONES:
        try:
            btn = page.get_by_role("button", name=nombre)
            await btn.wait_for(state="visible", timeout=1500)
            await btn.click()
            return f"botón {nombre!r}"
        except Exception:  # noqa: BLE001
            continue
    for selector in SELECTORES_BOTON:
        try:
            btn = page.locator(selector).first
            await btn.wait_for(state="visible", timeout=1500)
            await btn.click()
            return f"selector {selector!r}"
        except Exception:  # noqa: BLE001
            continue
    await page.keyboard.press("Enter")
    return "tecla Enter"


async def _volcar_diagnostico(page) -> None:
    """Deja en /tmp qué había en la página cuando no se encontró el campo.

    Sin esto, el siguiente intento vuelve a empezar a ciegas y cuesta otro
    código de un solo uso al usuario, que además caduca en minutos.
    """
    try:
        await page.screenshot(path="/tmp/centinela_otp_fallo.png", full_page=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from pathlib import Path
        Path("/tmp/centinela_otp_fallo.html").write_text(
            await page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def aplicar() -> bool:
    """Sustituye `submit_otp` por la versión multiidioma. True si se aplicó."""
    try:
        from xtb_api.auth import browser_auth
        from xtb_api.auth.cas_client import CASError
        from xtb_api.types.websocket import CASLoginSuccess
    except ImportError:
        return False

    async def submit_otp(self, code: str):
        if not self._page:
            raise CASError("BROWSER_AUTH_NO_PAGE",
                           "Browser page not available — call login() first")
        page = self._page
        try:
            await page.wait_for_timeout(2000)
            campo = await _localizar_input(page)
            await campo.fill(code)
            como = await _pulsar_enviar(page)
            logger.info("OTP enviado mediante %s; esperando TGT...", como)

            try:
                await asyncio.wait_for(self._tgt_event.wait(), timeout=30)
            except (TimeoutError, asyncio.TimeoutError) as e:
                # Una captura vale más que el mensaje de timeout: dice si el
                # código era incorrecto, si el formulario cambió o si XTB
                # respondió otra cosa.
                try:
                    await page.screenshot(path="/tmp/centinela_otp_fallo.png")
                except Exception:  # noqa: BLE001
                    pass
                raise CASError(
                    "BROWSER_AUTH_OTP_TIMEOUT",
                    "Se envió el código pero XTB no devolvió el TGT en 30 s. "
                    "Hay una captura de la página en /tmp/centinela_otp_fallo.png",
                ) from e

            if not self._tgt:
                raise CASError("BROWSER_AUTH_NO_TGT",
                               "OTP enviado pero XTB no devolvió ningún TGT")
            return CASLoginSuccess(tgt=self._tgt, expires_at=time.time() + 8 * 3600)
        finally:
            await self.close()

    browser_auth.BrowserCASAuth.submit_otp = submit_otp
    return True
