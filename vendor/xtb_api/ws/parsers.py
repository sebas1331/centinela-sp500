"""Pure parser functions for XTB WebSocket API responses.

Each function takes raw elements (list of dicts from the CoreAPI subscription
response) and returns typed Pydantic models.  No I/O, no side effects —
easy to unit-test with fixture data.
"""

from __future__ import annotations

from typing import Any

from xtb_api.types.enums import Xs6Side
from xtb_api.types.instrument import InstrumentSearchResult, Quote
from xtb_api.types.trading import AccountBalance, PendingOrder, Position


def parse_balance(
    elements: list[dict[str, Any]],
    currency: str,
    account_number: int,
) -> AccountBalance:
    """Parse balance from subscription elements.

    Args:
        elements: Raw elements from getAndSubscribeElement response.
        currency: Account currency code.
        account_number: Account number.

    Returns:
        Parsed AccountBalance (zeros if data is missing).
    """
    if elements:
        balance_data = (elements[0] or {}).get("value", {}).get("xtotalbalance")
        if balance_data:
            return AccountBalance(
                balance=float(balance_data.get("balance", 0)),
                equity=float(balance_data.get("equity", 0)),
                free_margin=float(balance_data.get("freeMargin", 0)),
                currency=currency,
                account_number=account_number,
            )

    return AccountBalance(
        balance=0.0,
        equity=0.0,
        free_margin=0.0,
        currency=currency,
        account_number=account_number,
    )


def parse_position_trade(trade: dict[str, Any]) -> Position:
    """Convert a single `xcfdtrade` dict (as pushed on the POSITIONS
    subscription) into a `Position`.

    Callers that receive the wrapped `{value: {xcfdtrade: ...}}` shape
    should use `parse_positions()` instead.
    """
    side_val = int(trade.get("side", 0))
    return Position(
        symbol=str(trade.get("symbol", "")),
        instrument_id=int(trade["idQuote"]) if trade.get("idQuote") is not None else None,
        volume=float(trade.get("volume", 0)),
        current_price=0.0,
        open_price=float(trade.get("openPrice", 0)),
        stop_loss=float(trade["sl"]) if trade.get("sl") and trade["sl"] != 0 else None,
        take_profit=float(trade["tp"]) if trade.get("tp") and trade["tp"] != 0 else None,
        profit_percent=0.0,
        profit_net=0.0,
        swap=float(trade["swap"]) if trade.get("swap") is not None else None,
        side="buy" if side_val == Xs6Side.BUY else "sell",
        order_id=str(trade["positionId"]) if trade.get("positionId") is not None else None,
        commission=float(trade["commission"]) if trade.get("commission") is not None else None,
        margin=float(trade["margin"]) if trade.get("margin") is not None else None,
        open_time=int(trade["openTime"]) if trade.get("openTime") is not None else None,
    )


def parse_positions(elements: list[dict[str, Any]]) -> list[Position]:
    """Parse open trading positions from subscription elements."""
    positions: list[Position] = []
    for el in elements:
        trade = (el or {}).get("value", {}).get("xcfdtrade")
        if not trade:
            continue
        positions.append(parse_position_trade(trade))
    return positions


def parse_orders(elements: list[dict[str, Any]]) -> list[PendingOrder]:
    """Parse pending (limit/stop) orders from subscription elements."""
    orders: list[PendingOrder] = []

    for el in elements:
        trade = (el or {}).get("value", {}).get("xcfdtrade")
        if not trade:
            continue

        side_val = int(trade.get("side", 0))
        orders.append(
            PendingOrder(
                symbol=str(trade.get("symbol", "")),
                instrument_id=int(trade["idQuote"]) if trade.get("idQuote") is not None else None,
                volume=float(trade.get("volume", 0)),
                price=float(trade.get("openPrice", 0)),
                stop_loss=float(trade["sl"]) if trade.get("sl") and trade["sl"] != 0 else None,
                take_profit=float(trade["tp"]) if trade.get("tp") and trade["tp"] != 0 else None,
                side="buy" if side_val == Xs6Side.BUY else "sell",
                order_id=str(trade["positionId"]) if trade.get("positionId") is not None else None,
                order_type=str(trade.get("orderType", "")),
                expiration=int(trade["expiration"]) if trade.get("expiration") is not None else None,
                open_time=int(trade["openTime"]) if trade.get("openTime") is not None else None,
            )
        )

    return orders


def parse_instruments(elements: list[dict[str, Any]]) -> list[InstrumentSearchResult]:
    """Parse instrument symbols from subscription elements."""
    symbols: list[InstrumentSearchResult] = []

    for el in elements:
        sym = (el or {}).get("value", {}).get("xcfdsymbol")
        if not sym:
            continue
        symbols.append(
            InstrumentSearchResult(
                symbol=str(sym.get("name", "")),
                instrument_id=int(sym.get("instrumentId", sym.get("quoteId", 0))),
                name=str(sym.get("description", sym.get("name", ""))),
                description=str(sym.get("description", "")),
                asset_class=str(sym.get("idAssetClass", "")),
                symbol_key=f"{sym.get('idAssetClass')}_{sym.get('name')}_{sym.get('groupId', sym.get('quoteId'))}",
            )
        )

    return symbols


def parse_quote(elements: list[dict[str, Any]], symbol: str) -> Quote | None:
    """Parse a quote (bid/ask) from subscription elements.

    Args:
        elements: Raw elements from tick subscription response.
        symbol: Fallback symbol name if not present in data.

    Returns:
        Parsed Quote, or None if no tick data found.
    """
    if not elements:
        return None

    tick = (elements[0] or {}).get("value", {}).get("xcfdtick")
    if not tick:
        return None

    return Quote(
        symbol=str(tick.get("symbol", symbol)),
        ask=float(tick.get("ask", 0)),
        bid=float(tick.get("bid", 0)),
        spread=float(tick.get("ask", 0)) - float(tick.get("bid", 0)),
        high=float(tick["high"]) if tick.get("high") is not None else None,
        low=float(tick["low"]) if tick.get("low") is not None else None,
        time=int(tick["timestamp"]) if tick.get("timestamp") is not None else None,
    )
