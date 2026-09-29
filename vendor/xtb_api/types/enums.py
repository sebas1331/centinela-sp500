"""Enumerations for XTB API protocol."""

from enum import IntEnum, StrEnum


class Xs6Side(IntEnum):
    """Trade side enumeration for buy/sell operations (WebSocket protocol).

    WARNING: These values (BUY=0, SELL=1) differ from the gRPC protocol
    constants in ``grpc.proto`` (SIDE_BUY=1, SIDE_SELL=2). Do NOT pass
    ``Xs6Side`` values to gRPC functions or vice-versa.
    """

    BUY = 0  # WebSocket only — gRPC uses SIDE_BUY=1
    SELL = 1  # WebSocket only — gRPC uses SIDE_SELL=2


class TradeCommand(IntEnum):
    """Trade command types for different order types."""

    BUY = 0
    SELL = 1
    BUY_LIMIT = 2
    SELL_LIMIT = 3
    BUY_STOP = 4
    SELL_STOP = 5


class TradeType(IntEnum):
    """Trade type classification for order execution."""

    MARKET = 0
    LIMIT = 1
    STOP = 2


class RequestTradeData(IntEnum):
    """Field identifiers for trade request data."""

    TYPE = 1
    TRADE_TYPE = 2
    SIDE = 3
    VOLUME = 4
    AMOUNT = 5
    SL = 6
    TP = 7
    OFFSET = 8
    PRICE = 9
    EXPIRATION = 10
    ORDER_ID = 11
    INSTRUMENT_ID = 12
    SL_AMOUNT = 13
    TP_AMOUNT = 14
    SYMBOL_KEY = 15


class SymbolSessionType(IntEnum):
    """Symbol trading session status."""

    CLOSED = 0
    OPEN = 1
    LOBBY = 2


class SocketStatus(StrEnum):
    """WebSocket connection status."""

    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"
    CLOSED = "CLOSED"
    ERROR = "SOCKET_ERROR"


class XTBEnvironment(StrEnum):
    """XTB trading environment type."""

    REAL = "real"
    DEMO = "demo"


class SubscriptionEid(IntEnum):
    """Element IDs for WebSocket data subscriptions."""

    POSITIONS = 1
    TICKS = 2
    SYMBOLS = 3
    SYMBOL_GROUPS = 4
    GROUP_SETTINGS = 5
    REQUEST_STATUS = 6
    ORDERS = 7
    TOTAL_BALANCE = 1043
