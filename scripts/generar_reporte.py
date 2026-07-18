#!/usr/bin/env python
"""Genera reportes (semanal/mensual) manualmente.

Uso:
    python scripts/generar_reporte.py semanal
    python scripts/generar_reporte.py mensual [--fecha YYYY-MM-DD]
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from centinela import reportes  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tipo", choices=["semanal", "mensual"])
    ap.add_argument("--fecha", default=None)
    args = ap.parse_args()
    if args.tipo == "semanal":
        print("Generado:", reportes.generar_semanal(args.fecha))
    else:
        print("Generado:", reportes.generar_mensual(args.fecha))


if __name__ == "__main__":
    main()
