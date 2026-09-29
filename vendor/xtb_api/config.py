"""Env-driven configuration for XTB account type (real vs demo).

Pure resolver functions that convert the `account_type` kwarg + environment
variables into a concrete `ws_url` and `account_server` pair. Keeping these
separate from `client.py` makes them unit-testable without constructing a
client or touching the network.
"""

from __future__ import annotations

import os
from typing import Literal, TypedDict, cast

AccountType = Literal["real", "demo"]


class _Preset(TypedDict):
    ws_url: str
    account_server: str


PRESETS: dict[AccountType, _Preset] = {
    "real": {
        "ws_url": "wss://api5reala.x-station.eu/v1/xstation",
        "account_server": "XS-real1",
    },
    "demo": {
        "ws_url": "wss://api5demoa.x-station.eu/v1/xstation",
        "account_server": "XS-demo1",
    },
}


def resolve_account_type(explicit: AccountType | None) -> AccountType:
    """Resolve account type from explicit kwarg → env → default 'real'.

    Validates both the kwarg and the env var against PRESETS so a typo
    in either path raises ValueError instead of silently falling through.
    """
    if explicit is not None:
        raw = str(explicit).strip().lower()
    else:
        raw = os.environ.get("XTB_ACCOUNT_TYPE", "").strip().lower()
    if not raw:
        return "real"
    if raw not in PRESETS:
        raise ValueError(f"Unknown account_type {raw!r}. Expected 'real' or 'demo'.")
    return cast(AccountType, raw)


def resolve_ws_url(explicit: str | None, account_type: AccountType) -> str:
    """Resolve ws_url from explicit kwarg → XTB_WS_URL env → preset."""
    if explicit is not None:
        return explicit
    env = os.environ.get("XTB_WS_URL", "").strip()
    if env:
        return env
    return PRESETS[account_type]["ws_url"]


def resolve_account_server(explicit: str | None, account_type: AccountType) -> str:
    """Resolve account_server from explicit kwarg → XTB_ACCOUNT_SERVER env → preset."""
    if explicit is not None:
        return explicit
    env = os.environ.get("XTB_ACCOUNT_SERVER", "").strip()
    if env:
        return env
    return PRESETS[account_type]["account_server"]
