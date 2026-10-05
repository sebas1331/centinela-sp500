"""Result types for gRPC-web trading."""

from __future__ import annotations

from pydantic import BaseModel


class GrpcTradeResult(BaseModel):
    """Result of a gRPC-web trade execution."""

    success: bool
    order_id: str | None = None
    order_number: int | None = None
    grpc_status: int = 0
    error: str | None = None


class GrpcCancelResult(BaseModel):
    """Result of a gRPC-web DeleteOrders call for a single order number."""

    success: bool
    order_number: int
    cancellation_id: str | None = None
    grpc_status: int = 0
    error: str | None = None


class GrpcPendingOrderResult(BaseModel):
    """CENTINELA (CAMBIOS.md, parche 3). Resultado de New/Modify Limit/Stop.

    ``ambiguous=True``: XTB no contestó nada legible; la orden puede existir o
    no y hay que mirarlo en la lista de órdenes antes de repetirla.
    """

    success: bool
    rpc: str = ""
    order_number: int | None = None
    trace_id: str | None = None
    grpc_status: int = 0
    error: str | None = None
    error_kind: str | None = None
    ambiguous: bool = False
