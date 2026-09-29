"""Unofficial Python client for XTB xStation5 trading platform."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from xtb_api.auth.auth_manager import AuthManager as XTBAuth
from xtb_api.auth.auth_manager import SessionSource
from xtb_api.client import XTBClient
from xtb_api.exceptions import (
    AccountBlockedError,
    AmbiguousOutcomeError,
    AuthenticationError,
    CASError,
    InstrumentNotFoundError,
    InvalidCredentialsError,
    ProtocolError,
    RateLimitedError,
    RateLimitError,
    ReconnectionError,
    TradeError,
    TwoFactorRequiredError,
    XTBConnectionError,
    XTBError,
    XTBTimeoutError,
)
from xtb_api.instruments import InstrumentRegistry
from xtb_api.types.enums import (
    SocketStatus,
    SubscriptionEid,
    Xs6Side,
    XTBEnvironment,
)
from xtb_api.types.instrument import InstrumentSearchResult, Quote
from xtb_api.types.trading import (
    AccountBalance,
    CancelOutcome,
    CancelResult,
    PendingOrder,
    Position,
    TradeOptions,
    TradeOutcome,
    TradeResult,
)

try:
    __version__ = _pkg_version("xtb-api-python")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = [
    # Client
    "XTBClient",
    "XTBAuth",
    "SessionSource",
    "InstrumentRegistry",
    # Exceptions
    "XTBError",
    "XTBConnectionError",
    "AuthenticationError",
    "CASError",
    "InvalidCredentialsError",
    "AccountBlockedError",
    "RateLimitedError",
    "TwoFactorRequiredError",
    "ReconnectionError",
    "TradeError",
    "AmbiguousOutcomeError",
    "InstrumentNotFoundError",
    "RateLimitError",
    "XTBTimeoutError",
    "ProtocolError",
    # Data models
    "Position",
    "PendingOrder",
    "AccountBalance",
    "CancelResult",
    "CancelOutcome",
    "TradeResult",
    "TradeOutcome",
    "TradeOptions",
    "Quote",
    "InstrumentSearchResult",
    # Enums
    "Xs6Side",
    "SocketStatus",
    "XTBEnvironment",
    "SubscriptionEid",
]
