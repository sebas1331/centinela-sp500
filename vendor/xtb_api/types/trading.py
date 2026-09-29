"""Trading type definitions."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from xtb_api.types.enums import Xs6Side


class IPrice(BaseModel):
    """Price representation with value and scale.

    Actual price = value × 10^(-scale)
    """

    value: int
    scale: int


class IVolume(BaseModel):
    """Volume representation with value and scale."""

    value: int
    scale: int = 0


class ISize(BaseModel):
    """Trade size specification — either volume or amount."""

    volume: IVolume | None = None
    amount: float | None = None


class IStopLossInput(BaseModel):
    """Stop loss configuration."""

    price: IPrice | None = None
    trailingstopinput: dict | None = None


class ITakeProfitInput(BaseModel):
    """Take profit configuration."""

    price: IPrice | None = None


class INewMarketOrder(BaseModel):
    """Market order definition for WebSocket trading."""

    instrumentid: int
    size: ISize
    side: Xs6Side
    stoploss: IStopLossInput | None = None
    takeprofit: ITakeProfitInput | None = None


class IXs6AuthAccount(BaseModel):
    """Account information for trade events."""

    number: int
    server: str
    currency: str


class INewMarketOrderEvent(BaseModel):
    """Complete market order event for WebSocket API."""

    order: INewMarketOrder
    uiTrackingId: str | None = None
    account: IXs6AuthAccount


class TradeOptions(BaseModel):
    """Simplified trade options for high-level API."""

    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    amount: float | None = None


class Position(BaseModel):
    """Open trading position information."""

    symbol: str
    instrument_id: int | None = None
    volume: float
    current_price: float = 0.0
    open_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    profit_percent: float = 0.0
    profit_net: float = 0.0
    swap: float | None = None
    side: Literal["buy", "sell"]
    order_id: str | None = None
    commission: float | None = None
    margin: float | None = None
    open_time: int | None = None


class PendingOrder(BaseModel):
    """Pending (limit/stop) order information."""

    symbol: str
    instrument_id: int | None = None
    volume: float
    price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    side: Literal["buy", "sell"]
    order_id: str | None = None
    order_type: str | None = None
    expiration: int | None = None
    open_time: int | None = None


class AccountBalance(BaseModel):
    """Account balance and equity information."""

    balance: float
    equity: float
    free_margin: float
    currency: str
    account_number: int


class TradeOutcome(StrEnum):
    """Typed outcome of a trade request.

    Values:
    - ``FILLED`` — broker confirmed the order, position is open.
    - ``QUEUED`` — broker accepted the order but did not fill (typically
      because the instrument's market is closed). The order is live on
      the broker side until market open or an explicit cancel. Use
      ``XTBClient.cancel_order(result.order_number)`` to kill it.
    - ``REJECTED`` — broker refused (bad symbol, insufficient funds, etc.).
    - ``AMBIGUOUS`` — network or protocol failure after the send; the trade
      may or may not have been placed. Caller must reconcile via
      ``get_positions()`` / ``get_orders()``.
    - ``INSUFFICIENT_VOLUME`` — local pre-check: volume rounds to < 1.
    - ``AUTH_EXPIRED`` — JWT/TGT rejected (RBAC). Should be retried by the
      library; only surfaced if retry also fails.
    - ``RATE_LIMITED`` — broker throttled the request.
    - ``TIMEOUT`` — request exceeded its deadline.
    """

    FILLED = "FILLED"
    QUEUED = "QUEUED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_VOLUME = "INSUFFICIENT_VOLUME"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"


class TradeResult(BaseModel):
    """Trade execution result.

    ``status`` is the authoritative field. ``success`` is a convenience
    property equivalent to ``status is TradeOutcome.FILLED`` and is kept
    for one-line checks.

    Fields:
        status: TradeOutcome — the typed result category.
        order_id: broker-assigned UUID string, if known.
        order_number: broker-assigned integer order number, if known.
            Feed into ``XTBClient.cancel_order`` to cancel a queued order.
        symbol: the symbol traded.
        side: "buy" or "sell".
        volume: requested volume (post-rounding for the < 1 check).
        price: fill price, if observable via a position poll.
        error: free-text error message from the broker (if any).
        error_code: stable short code for the outcome flavor. Examples:
            "INSUFFICIENT_VOLUME", "RBAC_DENIED", "AMBIGUOUS_NO_RESPONSE",
            "FILL_PRICE_UNKNOWN", "FILL_STATE_UNKNOWN", "NETWORK_ERROR".
            May also carry the raw broker code when one is surfaced.
    """

    model_config = {"extra": "forbid"}

    status: TradeOutcome
    symbol: str
    side: Literal["buy", "sell"]
    volume: float | None = None
    price: float | None = None
    order_id: str | None = None
    order_number: int | None = None
    error: str | None = None
    error_code: str | None = None

    @property
    def success(self) -> bool:
        """True iff ``status is TradeOutcome.FILLED``."""
        return self.status is TradeOutcome.FILLED


class CancelOutcome(StrEnum):
    """Typed outcome of a cancel request.

    Values:
    - ``CANCELLED`` — broker accepted the cancel (grpc-status 0).
    - ``REJECTED`` — broker refused. Common cases: the order already
      filled between the trade request and the cancel, or the order
      number is unknown.
    - ``AMBIGUOUS`` — network or protocol failure; caller should
      reconcile via ``get_orders()``.
    """

    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


class CancelResult(BaseModel):
    """Cancel execution result.

    ``status`` is the authoritative field. ``success`` is a convenience
    property equivalent to ``status is CancelOutcome.CANCELLED``.
    """

    model_config = {"extra": "forbid"}

    status: CancelOutcome
    order_number: int
    cancellation_id: str | None = None
    error: str | None = None
    error_code: str | None = None

    @property
    def success(self) -> bool:
        """True iff ``status is CancelOutcome.CANCELLED``."""
        return self.status is CancelOutcome.CANCELLED
