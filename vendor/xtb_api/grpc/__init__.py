"""gRPC-web trading module for XTB xStation5."""

from xtb_api.grpc.client import GrpcClient
from xtb_api.grpc.proto import (
    GRPC_AUTH_ENDPOINT,
    GRPC_BASE_URL,
    GRPC_CLOSE_POSITION_ENDPOINT,
    GRPC_CONFIRM_ENDPOINT,
    GRPC_NEW_ORDER_ENDPOINT,
    SIDE_BUY,
    SIDE_SELL,
)
from xtb_api.grpc.types import GrpcTradeResult

__all__ = [
    "GrpcClient",
    "GrpcTradeResult",
    "SIDE_BUY",
    "SIDE_SELL",
    "GRPC_BASE_URL",
    "GRPC_AUTH_ENDPOINT",
    "GRPC_NEW_ORDER_ENDPOINT",
    "GRPC_CONFIRM_ENDPOINT",
    "GRPC_CLOSE_POSITION_ENDPOINT",
]
