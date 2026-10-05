#!/usr/bin/env python
"""¿Arranca Chromium? Lo abre en headless, carga una página vacía y lo cierra.

Lo usa la acción `.github/actions/chromium` para saber si el navegador cacheado
funciona con las librerías que ya trae el runner, o si hace falta instalar las
dependencias del sistema con apt (que es lo que tardó 14 min el 2026-10-05).
Sale con código 1 si no arranca: la acción decide entonces.
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright


def main() -> int:
    t0 = time.monotonic()
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=True)
            pagina = navegador.new_page()
            pagina.set_content("<p>centinela</p>")
            assert pagina.inner_text("p") == "centinela"
            navegador.close()
    except Exception as exc:  # noqa: BLE001 — se dice y lo decide la acción
        print(f"Chromium NO arranca: {exc!r}", flush=True)
        return 1
    print(f"Chromium arranca en {time.monotonic() - t0:.1f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
