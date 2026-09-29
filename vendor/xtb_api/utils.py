"""Utility functions for price/volume conversion and helpers."""

from __future__ import annotations

import asyncio
import random
import time

from pydantic import BaseModel

from xtb_api.types.trading import IPrice, IVolume


def price_from_decimal(price: float, precision: int) -> IPrice:
    """Create IPrice from decimal: price_from_decimal(2.62, 2) → IPrice(value=262, scale=2)"""
    value = round(price * (10**precision))
    return IPrice(value=value, scale=precision)


def price_to_decimal(price: IPrice) -> float:
    """Convert IPrice to decimal: price_to_decimal(IPrice(value=262, scale=2)) → 2.62"""
    return float(price.value) * float(10 ** (-price.scale))


def volume_from(qty: int, scale: int = 0) -> IVolume:
    """Create IVolume: volume_from(19) → IVolume(value=19, scale=0)"""
    return IVolume(value=qty, scale=scale)


def generate_req_id(prefix: str) -> str:
    """Generate unique request ID."""
    return f"{prefix}_{int(time.time() * 1000)}_{random.randint(0, 999)}"


def build_account_id(account_number: int, endpoint: str = "meta1") -> str:
    """Build accountId: 'meta1_{accountNumber}'"""
    return f"{endpoint}_{account_number}"


class ParsedSymbolKey(BaseModel):
    """Parsed symbol key components."""

    asset_class_id: int
    symbol_name: str
    group_id: int


def parse_symbol_key(key: str) -> ParsedSymbolKey | None:
    """Parse symbol key: '9_CIG.PL_6' → ParsedSymbolKey(9, 'CIG.PL', 6)"""
    parts = key.split("_")
    if len(parts) < 3:
        return None
    return ParsedSymbolKey(
        asset_class_id=int(parts[0]),
        symbol_name="_".join(parts[1:-1]),
        group_id=int(parts[-1]),
    )


async def sleep(ms: int) -> None:
    """Sleep helper (milliseconds)."""
    await asyncio.sleep(ms / 1000)
