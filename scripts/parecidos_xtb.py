#!/usr/bin/env python
"""Para cada ticker del S&P 500 que XTB no ofrece como acción al contado, enseña
los símbolos PARECIDOS que XTB sí tiene.

Así se separan tres cosas que el conteo mezcla:
  - ausencia real (XTB no lista la empresa de ninguna forma),
  - clase de acción con otro separador (BRK.B -> BRK-B.US en el sistema, pero
    XTB puede llamarla BRK.B.US o BRKB.US),
  - ticker renombrado (BNY era BK; XTB puede seguir con el símbolo viejo).

Busca por formato (variantes del símbolo) y por NOMBRE de la empresa (tokens del
nombre del S&P 500 contra el nombre/descripción de cada instrumento de XTB).

READ-ONLY: no manda ninguna orden.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import broker_xtb as bx, config, ordenes as ords, universo  # noqa: E402

#: Palabras de los nombres de empresa que no distinguen nada (no sirven para
#: emparejar por nombre).
VACIAS = {"inc", "inc.", "corp", "corp.", "corporation", "company", "co",
          "co.", "the", "group", "holdings", "ltd", "plc", "sa", "class",
          "&", "of", "and", "", "international", "technologies", "systems"}


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _empresas() -> dict[str, str]:
    """ticker canónico -> nombre de empresa, del respaldo del universo."""
    out: dict[str, str] = {}
    with open(config.ARCHIVO_UNIVERSO, encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            out[fila["ticker"]] = fila.get("empresa", "")
    return out


def _variantes(ticker: str) -> list[str]:
    """Formas en que XTB podría nombrar el símbolo al contado del ticker."""
    base = ticker.strip().upper()
    bases = {base, base.replace(".", "-"), base.replace(".", ""),
             base.replace("-", "."), base.replace("-", "")}
    return sorted({f"{b}.US" for b in bases})


def _tokens(nombre: str) -> set[str]:
    palabras = re.split(r"[^A-Za-z0-9]+", nombre.lower())
    return {p for p in palabras if p and p not in VACIAS and len(p) > 2}


def main() -> int:
    empresas = _empresas()
    tickers = universo.tickers_sp500()

    with bx.BrokerXTB(bx.credenciales_del_entorno_o_llavero(), demo=True) as b:
        log(f"cuenta DEMO verificada: saldo {b.saldo()['saldo']:,.2f}")
        catalogo = b.catalogo_contado()
        contado = {d["symbol"].upper() for d in catalogo}
        log(f"XTB: {len(catalogo)} acciones al contado en el catálogo.")

        faltan = [t for t in tickers if ords.simbolo_xtb(t).upper() not in contado]
        log(f"\n{len(faltan)} tickers del S&P 500 no existen con el símbolo que "
            f"usa el sistema (simbolo_xtb). Parecidos en XTB:\n")

        for t in sorted(faltan):
            nombre = empresas.get(t, "")
            log(f"=== {t}  ({nombre or 'sin nombre'})  "
                f"[sistema manda: {ords.simbolo_xtb(t)}] ===")

            # 1) Por formato: ¿existe alguna variante del símbolo?
            formato = [v for v in _variantes(t) if v.upper() in contado]
            if formato:
                log(f"  FORMATO: XTB sí lo tiene como {', '.join(formato)}")

            # 2) Por nombre: instrumentos de EE.UU. cuyo nombre comparte tokens
            #    con el de la empresa (para cazar renombrados).
            toks = _tokens(nombre)
            por_nombre = []
            if toks:
                for d in catalogo:
                    if not d["symbol"].upper().endswith(".US"):
                        continue
                    if toks & _tokens(d["name"] + " " + d["description"]):
                        por_nombre.append(d)
            # No repetir los que ya salieron por formato.
            ya = set(formato)
            por_nombre = [d for d in por_nombre if d["symbol"].upper() not in ya]
            if por_nombre:
                log("  NOMBRE:")
                for d in por_nombre[:8]:
                    log(f"    {d['symbol']:12} {d['name']}")
            if not formato and not por_nombre:
                log("  (sin parecidos: ausencia real probable)")
            log("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
