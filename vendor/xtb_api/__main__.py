"""CLI entry point for xtb-api-python.

Usage:
    python -m xtb_api doctor   # Verify installation state
    xtb-api doctor             # Same, if the entry point script is on PATH
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _ok(label: str, detail: str = "") -> str:
    return f"  [OK]   {label}" + (f" — {detail}" if detail else "")


def _fail(label: str, detail: str = "") -> str:
    return f"  [FAIL] {label}" + (f" — {detail}" if detail else "")


def _info(label: str, detail: str = "") -> str:
    return f"  [--]   {label}" + (f" — {detail}" if detail else "")


def _check_python_version() -> tuple[bool, str]:
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}.{sys.version_info[2]}"
    ok = (major, minor) >= (3, 12)
    return ok, version_str


def _check_package_version() -> tuple[bool, str]:
    try:
        return True, _pkg_version("xtb-api-python")
    except PackageNotFoundError:
        return False, "not installed (are you running from a non-installed checkout?)"


def _check_playwright_package() -> tuple[bool, str]:
    spec = importlib.util.find_spec("playwright")
    if spec is None:
        return False, "playwright Python package not installed"
    try:
        ver = _pkg_version("playwright")
    except PackageNotFoundError:
        ver = "unknown"
    return True, f"playwright {ver}"


def _check_chromium_binary() -> tuple[bool, str]:
    """Attempt to locate the Chromium executable without launching it."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright not importable"

    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            # executable_path is a property that returns a string path; does not
            # guarantee the file exists. Probe it.
            from pathlib import Path

            if exe and Path(exe).exists():
                return True, exe
            return False, f"expected binary at {exe!r} not found"
    except Exception as e:  # noqa: BLE001
        return False, f"playwright check failed: {e}"


def _check_pyotp_optional() -> tuple[bool, str]:
    spec = importlib.util.find_spec("pyotp")
    if spec is None:
        return False, "pyotp not installed (install 'xtb-api-python[totp]' for auto-2FA)"
    try:
        ver = _pkg_version("pyotp")
    except PackageNotFoundError:
        ver = "unknown"
    return True, f"pyotp {ver}"


def run_doctor() -> int:
    """Run environment checks and print a status report. Returns 0 on success."""
    print(f"xtb-api-python doctor — {platform.platform()}")
    print()

    all_ok = True

    # Required checks
    ok, detail = _check_python_version()
    print(_ok("Python >= 3.12", detail) if ok else _fail("Python >= 3.12", detail))
    all_ok &= ok

    ok, detail = _check_package_version()
    print(_ok("xtb-api-python", detail) if ok else _fail("xtb-api-python", detail))
    all_ok &= ok

    ok, detail = _check_playwright_package()
    print(_ok("playwright package", detail) if ok else _fail("playwright package", detail))
    all_ok &= ok

    ok, detail = _check_chromium_binary()
    if ok:
        print(_ok("Chromium binary", detail))
    else:
        print(_fail("Chromium binary", detail))
        print()
        print("  To install Chromium, run:")
        print("      playwright install chromium")
        print()
        all_ok = False

    # Optional checks
    ok, detail = _check_pyotp_optional()
    print(_ok("pyotp (optional 2FA)", detail) if ok else _info("pyotp (optional 2FA)", detail))

    print()
    if all_ok:
        print("All required checks passed.")
        return 0
    print("Some required checks failed. See above for fix instructions.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="xtb-api",
        description="xtb-api-python CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Verify the library's installation state")

    try:
        args = parser.parse_args()
    except SystemExit as e:
        # argparse raises SystemExit on --help or parse errors. Convert to
        # a return value so main() stays testable and honors its -> int contract.
        code = e.code
        if isinstance(code, int):
            return code
        return 2

    if args.command == "doctor":
        return run_doctor()
    return 2


if __name__ == "__main__":
    sys.exit(main())
