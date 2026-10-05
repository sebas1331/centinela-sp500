"""High-level XTB trading client.

Provides a dead-simple, single-client API that handles all auth lifecycle,
transport selection, and token refresh transparently.

⚠️ Warning: This is an unofficial library. Use at your own risk.
Always test thoroughly on demo accounts before using with real money.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from xtb_api.auth.auth_manager import AuthManager, SessionSource
from xtb_api.auth.cas_client import CASClientConfig
from xtb_api.config import resolve_account_server, resolve_account_type, resolve_ws_url
from xtb_api.exceptions import AmbiguousOutcomeError, InstrumentNotFoundError
from xtb_api.grpc.client import GrpcClient
from xtb_api.grpc.proto import SIDE_BUY, SIDE_SELL
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
from xtb_api.types.websocket import WSClientConfig
from xtb_api.utils import price_from_decimal
from xtb_api.ws.ws_client import XTBWebSocketClient

logger = logging.getLogger(__name__)


#: CENTINELA (CAMBIOS.md, parche 1). Prefijo de clave de las acciones al
#: contado. El 4 es el de los CFD, y la clave es estructura: el nombre también
#: lo dice ("CFD", "CLOSE ONLY") pero es texto para humanos y puede cambiar.
CENTINELA_PREFIJO_CONTADO = "9_"
CENTINELA_SENALES_CFD = ("cfd", "close only")


def _is_cash_instrument(result) -> bool:
    """¿Es la acción al contado y no su CFD? (CENTINELA, parche 1)"""
    key = str(getattr(result, "symbol_key", "") or "")
    if key:
        return key.startswith(CENTINELA_PREFIJO_CONTADO)
    text = f"{getattr(result, 'name', '')} {getattr(result, 'description', '')}".lower()
    return not any(s in text for s in CENTINELA_SENALES_CFD)


class XTBClient:
    """High-level XTB trading client.

    Handles all authentication, token refresh, and transport selection
    automatically. Users never need to understand the auth lifecycle.

    Read operations (balance, positions, quotes, instruments) go through
    WebSocket. Trading (buy/sell) goes through gRPC-web (lazy-initialized
    on first trade call).

    Example::

        client = XTBClient(
            email="user@example.com",
            password="secret",
            account_number=51984891,
            totp_secret="BASE32SECRET",      # optional, auto-handles 2FA
            session_file="~/.xtb_session",   # optional, persists auth
        )
        await client.connect()

        balance = await client.get_balance()
        positions = await client.get_positions()
        result = await client.buy("EURUSD", volume=1, stop_loss=1.0850)

        await client.disconnect()
    """

    def __init__(
        self,
        email: str,
        password: str,
        account_number: int,
        *,
        totp_secret: str = "",
        session_file: Path | str | None = None,
        account_type: Literal["real", "demo"] | None = None,
        ws_url: str | None = None,
        endpoint: str = "meta1",
        account_server: str | None = None,
        auto_reconnect: bool = True,
        cas_config: CASClientConfig | None = None,
    ) -> None:
        """
        Args:
            email: XTB account email.
            password: XTB account password.
            account_number: XTB account number.
            totp_secret: Base32 TOTP secret for automatic 2FA (optional).
            session_file: Path to cache TGT on disk (optional).
            account_type: 'real' (default) or 'demo'. Selects the matching
                ``ws_url`` + ``account_server`` pair. Falls back to the
                ``XTB_ACCOUNT_TYPE`` env var when unset.
            ws_url: WebSocket endpoint URL. Defaults to the preset for the
                resolved ``account_type``; can also be set via ``XTB_WS_URL``.
            endpoint: Server endpoint name (e.g., 'meta1').
            account_server: gRPC account server name. Defaults to the preset
                for the resolved ``account_type``; can also be set via
                ``XTB_ACCOUNT_SERVER``.
            auto_reconnect: Auto-reconnect WebSocket on disconnect.
            cas_config: Custom CAS client configuration (optional).
        """
        resolved_account_type = resolve_account_type(account_type)
        resolved_ws_url = resolve_ws_url(ws_url, resolved_account_type)
        resolved_account_server = resolve_account_server(account_server, resolved_account_type)

        self._account_number = account_number
        self._account_server = resolved_account_server

        # Auth manager — shared by WS and gRPC
        self._auth = AuthManager(
            email=email,
            password=password,
            totp_secret=totp_secret,
            session_file=session_file,
            cas_config=cas_config,
        )

        # WebSocket client — always created
        ws_config = WSClientConfig(
            url=resolved_ws_url,
            account_number=account_number,
            endpoint=endpoint,
            auto_reconnect=auto_reconnect,
        )
        self._ws = XTBWebSocketClient(ws_config, auth_manager=self._auth)

        # gRPC client — lazy-initialized on first trade
        self._grpc: GrpcClient | None = None

    async def connect(self) -> None:
        """Connect to XTB, authenticate, and start receiving data.

        Handles the full auth flow: TGT → Service Ticket → WebSocket login.
        """
        service_ticket = await self._auth.get_service_ticket()
        await self._ws._establish_connection()
        await self._ws.register_client_info()
        await self._ws.login_with_service_ticket(service_ticket)

    @property
    def session_source(self) -> SessionSource:
        """Where the currently-held TGT came from (see :class:`SessionSource`).

        Inspect after :meth:`connect` to verify whether session reuse actually
        happened. ``SESSION_FILE`` / ``MEMORY`` mean no fresh login occurred
        (no XTB "new login" email); ``CAS_LOGIN`` / ``BROWSER_LOGIN`` indicate
        that the cached TGT was missing or expired and a fresh login ran.
        """
        return self._auth.session_source

    @property
    def session_expires_at(self) -> float | None:
        """Unix timestamp at which the current TGT expires (``None`` if unset)."""
        return self._auth.session_expires_at

    async def disconnect(self) -> None:
        """Disconnect from XTB and clean up all resources."""
        await self._ws.disconnect_async()
        if self._grpc:
            await self._grpc.disconnect()
            self._grpc = None
        await self._auth.aclose()

    # ── Read Operations (WebSocket) ──────────────────────────────

    async def get_balance(self) -> AccountBalance:
        """Get account balance and equity information."""
        return await self._ws.get_balance()

    async def get_positions(self) -> list[Position]:
        """Get all open trading positions."""
        return await self._ws.get_positions()

    async def get_orders(self) -> list[PendingOrder]:
        """Get all pending (limit/stop) orders."""
        return await self._ws.get_orders()

    async def get_quote(self, symbol: str) -> Quote | None:
        """Get current quote (bid/ask prices) for a symbol.

        Args:
            symbol: Symbol name (e.g., 'EURUSD', 'CIG.PL')
        """
        return await self._ws.get_quote(symbol)

    async def search_instrument(self, query: str) -> list[InstrumentSearchResult]:
        """Search for financial instruments by name.

        First call downloads all instruments and caches them.
        Subsequent searches are instant.
        """
        return await self._ws.search_instrument(query)

    # ── Trading (gRPC, lazy-initialized) ─────────────────────────

    async def buy(
        self,
        symbol: str,
        volume: int,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        options: TradeOptions | None = None,
    ) -> TradeResult:
        """Execute a BUY market order via gRPC using ``SIDE_BUY`` (value 1).

        ⚠️ WARNING: This executes real trades. Use demo accounts for testing.

        Note: This uses the gRPC protocol side constant (``SIDE_BUY=1``),
        which differs from the WebSocket constant (``Xs6Side.BUY=0``). Do not mix.

        Args:
            symbol: Symbol name (e.g., 'EURUSD', 'CIG.PL')
            volume: Number of shares/lots
            stop_loss: Stop loss price (flat kwarg for simple use)
            take_profit: Take profit price (flat kwarg for simple use)
            options: Advanced trade options (overrides stop_loss/take_profit)
        """
        return await self._execute_trade(symbol, volume, SIDE_BUY, stop_loss, take_profit, options)

    async def sell(
        self,
        symbol: str,
        volume: int,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        options: TradeOptions | None = None,
    ) -> TradeResult:
        """Execute a SELL market order via gRPC using ``SIDE_SELL`` (value 2).

        ⚠️ WARNING: This executes real trades. Use demo accounts for testing.

        Note: This uses the gRPC protocol side constant (``SIDE_SELL=2``),
        which differs from the WebSocket constant (``Xs6Side.SELL=1``). Do not mix.

        Args:
            symbol: Symbol name (e.g., 'EURUSD', 'CIG.PL')
            volume: Number of shares/lots
            stop_loss: Stop loss price (flat kwarg for simple use)
            take_profit: Take profit price (flat kwarg for simple use)
            options: Advanced trade options (overrides stop_loss/take_profit)
        """
        return await self._execute_trade(symbol, volume, SIDE_SELL, stop_loss, take_profit, options)

    async def cancel_order(self, order_number: int) -> CancelResult:
        """Cancel a queued or pending broker order by its order number.

        Pass the ``order_number`` from a ``TradeResult`` (populated on
        both FILLED and QUEUED outcomes). Returns a typed
        :class:`CancelResult` whose ``status`` is a :class:`CancelOutcome`.

        ``CancelOutcome.REJECTED`` is a common and expected outcome — if
        the order filled between the trade request and the cancel, the
        broker has no queued order left to cancel.
        """
        grpc = self._ensure_grpc()
        grpc_results = await grpc.cancel_orders([order_number])
        grpc_result = grpc_results[0]

        if grpc_result.success:
            return CancelResult(
                status=CancelOutcome.CANCELLED,
                order_number=grpc_result.order_number,
                cancellation_id=grpc_result.cancellation_id,
            )

        # Network failures leave grpc_status=0 (no trailer observed); broker
        # rejections carry a non-zero grpc_status from the trailer.
        if grpc_result.grpc_status == 0:
            return CancelResult(
                status=CancelOutcome.AMBIGUOUS,
                order_number=grpc_result.order_number,
                error=grpc_result.error,
                error_code="AMBIGUOUS_NO_RESPONSE",
            )

        # Status 7 (PermissionDenied) is the only code observed from XTB so
        # far (e.g. cancelling an unknown order number). Other non-zero codes
        # fall through with error_code=None until we see them in the wild.
        error_code: str | None = None
        if grpc_result.grpc_status == 7:
            error_code = "RBAC_DENIED"

        return CancelResult(
            status=CancelOutcome.REJECTED,
            order_number=grpc_result.order_number,
            error=grpc_result.error,
            error_code=error_code,
        )

    # ── CENTINELA (CAMBIOS.md, parche 3): órdenes pendientes ─────

    async def place_pending_order(self, symbol: str, volume: int, kind: str,
                                  price: float, side: str = "sell"):
        """Orden pendiente sobre la ACCIÓN al contado, sin vencimiento.

        kind: "limit" (venta: a `price` o mejor, por encima del mercado) o
        "stop" (venta: al tocar `price`, por debajo del mercado). Devuelve un
        ``GrpcPendingOrderResult``; ``order_number`` es el número que luego
        aparece en ``get_orders()`` y el que piden modificar y cancelar.
        """
        if kind not in ("limit", "stop"):
            raise ValueError(f"kind debe ser 'limit' o 'stop', no {kind!r}")
        if not isinstance(volume, int) or volume < 1:
            raise ValueError(f"volumen entero >= 1, no {volume!r}")
        grpc = self._ensure_grpc()
        instrument_id = await self._resolve_instrument_id(symbol)
        lado = SIDE_SELL if side == "sell" else SIDE_BUY
        fn = grpc.new_limit_order if kind == "limit" else grpc.new_stop_order
        return await fn(instrument_id, volume, lado, price)

    async def modify_pending_order(self, order_number: int, kind: str, price: float):
        """Cambia el precio de una orden pendiente existente (no crea otra)."""
        grpc = self._ensure_grpc()
        if kind == "limit":
            return await grpc.modify_limit_order(order_number, price)
        if kind == "stop":
            return await grpc.modify_stop_order(order_number, price)
        raise ValueError(f"kind debe ser 'limit' o 'stop', no {kind!r}")

    async def cancel_pending_orders(self, order_numbers: list[int]):
        """Cancela y dice, orden a orden, si XTB la canceló DE VERDAD."""
        return await self._ensure_grpc().delete_orders_checked(order_numbers)

    # ── Real-time Events ─────────────────────────────────────────

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        """Register event handler.

        Events:
        - 'tick' — Real-time tick data
        - 'position' — Position update
        - 'symbol' — Symbol data update
        - 'connected' — WebSocket connected
        - 'disconnected' — Connection closed
        - 'error' — Error occurred
        """
        self._ws.on(event, callback)

    def off(self, event: str, callback: Callable[..., Any]) -> None:
        """Remove event handler."""
        self._ws.off(event, callback)

    async def subscribe_ticks(self, symbol: str) -> None:
        """Subscribe to real-time tick data for a symbol.

        Args:
            symbol: Symbol name (e.g., 'EURUSD'). Resolves to symbol key automatically.
        """
        symbol_key = await self._resolve_symbol_key(symbol)
        await self._ws.subscribe_ticks(symbol_key)

    async def unsubscribe_ticks(self, symbol: str) -> None:
        """Unsubscribe from tick data for a symbol."""
        symbol_key = await self._resolve_symbol_key(symbol)
        await self._ws.unsubscribe_ticks(symbol_key)

    # ── Properties ───────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """Whether WebSocket is connected."""
        return self._ws.is_connected

    @property
    def is_authenticated(self) -> bool:
        """Whether authenticated with XTB servers."""
        return self._ws.is_authenticated

    @property
    def account_number(self) -> int:
        """Account number."""
        return self._account_number

    @property
    def ws(self) -> XTBWebSocketClient:
        """Access underlying WebSocket client for advanced use.

        Warning: The WebSocket client's ``buy()``/``sell()`` methods use
        ``Xs6Side`` constants (BUY=0, SELL=1), which differ from the gRPC
        constants (SIDE_BUY=1, SIDE_SELL=2) used by ``XTBClient.buy()``/
        ``sell()``. Do not pass side values between protocols.
        """
        return self._ws

    @property
    def grpc_client(self) -> GrpcClient | None:
        """Access underlying gRPC client (None until first trade)."""
        return self._grpc

    @property
    def auth(self) -> AuthManager:
        """Access the auth manager."""
        return self._auth

    # ── Internal ─────────────────────────────────────────────────

    def _ensure_grpc(self) -> GrpcClient:
        """Lazy-initialize gRPC client on first trade."""
        if self._grpc is None:
            self._grpc = GrpcClient(
                account_number=str(self._account_number),
                account_server=self._account_server,
                auth=self._auth,
            )
        return self._grpc

    async def _resolve_symbol_key(self, symbol: str) -> str:
        """Resolve a symbol name to its internal symbol key.

        Uses the instrument cache (downloading it if needed).
        If the symbol already looks like a key (contains '_'), returns as-is.
        """
        if "_" in symbol:
            return symbol

        results = await self._ws.search_instrument(symbol)
        for r in results:
            if r.symbol.upper() == symbol.upper():
                return r.symbol_key
        if results:
            return results[0].symbol_key
        raise InstrumentNotFoundError(f"Symbol not found: {symbol}")

    async def _resolve_instrument_id(self, symbol: str) -> int:
        """Resolve a symbol name to its gRPC instrument ID.

        CENTINELA (ver CAMBIOS.md, parche 1). El original devolvía el PRIMER
        resultado cuyo símbolo coincidía, y XTB ofrece muchos símbolos por
        partida doble: la acción al contado y su CFD, con el mismo nombre. El
        orden de esa lista cambia entre sesiones, así que unas veces se mandaba
        la acción y otras el CFD — que el servicio de contado rechaza.

        Y si NINGUNO coincidía, devolvía `results[0]`: una orden sobre el primer
        instrumento que contuviera esas letras, que puede ser cualquier cosa.
        """
        results = await self._ws.search_instrument(symbol)
        exact = [r for r in results if r.symbol.upper() == symbol.upper()]
        if not exact:
            raise InstrumentNotFoundError(
                f"Symbol not found: {symbol} (la búsqueda devolvió "
                f"{len(results)} parecidos; operar 'el más parecido' es operar "
                f"otra cosa)")

        cash = [r for r in exact if _is_cash_instrument(r)]
        if not cash:
            detail = ", ".join(f"{r.symbol_key} (id {r.instrument_id})"
                               for r in exact)
            raise InstrumentNotFoundError(
                f"De los {len(exact)} instrumentos que XTB llama {symbol}, "
                f"ninguno es la acción al contado: {detail}. Probablemente solo "
                f"quede el CFD, que este sistema no opera.")

        if len(exact) > 1:
            logger.info(
                "%s: %d coincidencias; se opera id=%s (%s) y se descarta %s",
                symbol, len(exact), cash[0].instrument_id, cash[0].symbol_key,
                ", ".join(f"id={r.instrument_id} ({r.symbol_key})"
                          for r in exact if r is not cash[0]))
        return cash[0].instrument_id

    async def _execute_trade(
        self,
        symbol: str,
        volume: int,
        side: int,
        stop_loss: float | None,
        take_profit: float | None,
        options: TradeOptions | None,
    ) -> TradeResult:
        """Execute a trade via gRPC, resolving the symbol first."""
        side_str = cast("Literal['buy', 'sell']", "buy" if side == SIDE_BUY else "sell")

        # Volume validation: reject anything that rounds to less than 1 share.
        rounded = int(volume + 0.5)
        if rounded < 1:
            return TradeResult(
                status=TradeOutcome.INSUFFICIENT_VOLUME,
                symbol=symbol,
                side=side_str,
                volume=float(volume),
                order_id=None,
                error=f"{volume} rounds to {rounded} (need >= 1)",
                error_code="INSUFFICIENT_VOLUME",
            )

        grpc = self._ensure_grpc()
        try:
            instrument_id = await self._resolve_instrument_id(symbol)
        except InstrumentNotFoundError as exc:
            return TradeResult(
                status=TradeOutcome.REJECTED,
                symbol=symbol,
                side=side_str,
                volume=float(volume),
                order_id=None,
                error=str(exc),
                error_code="INSTRUMENT_NOT_FOUND",
            )

        # Merge flat kwargs into effective SL/TP (options take precedence)
        effective_sl = options.stop_loss if options and options.stop_loss is not None else stop_loss
        effective_tp = options.take_profit if options and options.take_profit is not None else take_profit

        sl_value = sl_scale = tp_value = tp_scale = None
        if effective_sl is not None:
            p = price_from_decimal(effective_sl, _decimal_places(effective_sl))
            sl_value, sl_scale = p.value, p.scale
        if effective_tp is not None:
            p = price_from_decimal(effective_tp, _decimal_places(effective_tp))
            tp_value, tp_scale = p.value, p.scale

        try:
            result = await grpc.execute_order(
                instrument_id,
                volume,
                side,
                stop_loss_value=sl_value,
                stop_loss_scale=sl_scale,
                take_profit_value=tp_value,
                take_profit_scale=tp_scale,
            )
        except AmbiguousOutcomeError as exc:
            return TradeResult(
                status=TradeOutcome.AMBIGUOUS,
                symbol=symbol,
                side=side_str,
                volume=float(volume),
                order_id=None,
                error=str(exc),
                error_code="AMBIGUOUS_NO_RESPONSE",
            )

        # F02/F13: detect RBAC/AUTH_EXPIRED via grpc_status 7 — reliable
        # regardless of the free-text error message.
        if not result.success and getattr(result, "grpc_status", 0) == 7:
            # Idempotency probe: did the first call actually fill despite
            # the RBAC error? Compare live positions against the request.
            existing = await self._find_matching_position(symbol, volume, side_str)
            if existing is not None:
                logger.info(
                    "RBAC returned but matching position %s already exists — skipping retry (idempotent short-circuit)",
                    existing.order_id,
                )
                return TradeResult(
                    status=TradeOutcome.FILLED,
                    symbol=symbol,
                    side=side_str,
                    volume=float(volume),
                    price=existing.open_price,
                    order_id=existing.order_id,
                    error=None,
                    error_code=None,
                )

            logger.info("RBAC error, refreshing JWT and retrying...")
            grpc.invalidate_jwt()
            try:
                result = await grpc.execute_order(
                    instrument_id,
                    volume,
                    side,
                    stop_loss_value=sl_value,
                    stop_loss_scale=sl_scale,
                    take_profit_value=tp_value,
                    take_profit_scale=tp_scale,
                )
            except AmbiguousOutcomeError as exc:
                return TradeResult(
                    status=TradeOutcome.AMBIGUOUS,
                    symbol=symbol,
                    side=side_str,
                    volume=float(volume),
                    order_id=None,
                    error=str(exc),
                    error_code="AMBIGUOUS_NO_RESPONSE",
                )

        return await self._build_trade_result(result, symbol, side_str, volume)

    async def _build_trade_result(
        self,
        grpc_result: Any,
        symbol: str,
        side_str: Literal["buy", "sell"],
        volume: int,
    ) -> TradeResult:
        """Map a GrpcTradeResult to a typed TradeResult.

        On gRPC success the wire is ambiguous (filled and queued orders return
        an identical shape), so we probe the WS-side `get_positions()` and
        `get_orders()` to classify the outcome. See spec §2.
        """
        order_number: int | None = getattr(grpc_result, "order_number", None)

        if grpc_result.success:
            return await self._classify_accepted_trade(
                symbol=symbol,
                side_str=side_str,
                volume=volume,
                order_id=grpc_result.order_id,
                order_number=order_number,
            )

        # Non-success: categorize by grpc_status / error text.
        status_code = getattr(grpc_result, "grpc_status", 0) or 0
        err_text = grpc_result.error or ""
        if status_code == 7:
            outcome = TradeOutcome.AUTH_EXPIRED
            error_code: str | None = "RBAC_DENIED"
        else:
            outcome = TradeOutcome.REJECTED
            error_code = None

        return TradeResult(
            status=outcome,
            symbol=symbol,
            side=side_str,
            volume=float(volume),
            order_id=grpc_result.order_id,
            order_number=order_number,
            error=err_text or None,
            error_code=error_code,
        )

    async def _classify_accepted_trade(
        self,
        *,
        symbol: str,
        side_str: Literal["buy", "sell"],
        volume: int,
        order_id: str | None,
        order_number: int | None,
    ) -> TradeResult:
        """Decide FILLED vs QUEUED vs AMBIGUOUS after a gRPC-accepted order.

        Probe 1: positions match by order_id / (symbol, side, volume) → FILLED.
        Probe 2: pending orders match by order_number → QUEUED.
        Retry both probes once after 500 ms.

        If both probes ran cleanly but surfaced nothing and we have an
        ``order_number``, classify as QUEUED: gRPC success plus an
        order_number is XTB's receipt that it holds the order, and
        ``getAllOrders`` does not surface queued market-closed orders in
        practice. AMBIGUOUS is reserved for the case where a probe
        actually raised and we genuinely cannot tell.
        """
        last_exc: str | None = None

        for attempt in range(2):
            if attempt == 1:
                await asyncio.sleep(0.5)

            try:
                positions = await self._ws.get_positions()
            except Exception as exc:  # noqa: BLE001
                logger.warning("get_positions probe failed: %s", exc)
                positions = None
                if last_exc is None:
                    last_exc = str(exc)

            if positions is not None:
                position = _match_position(positions, symbol, volume, side_str, order_id=order_id)
                if position is not None:
                    fill_price, fill_code = await self._poll_fill_price(symbol, order_id=order_id)
                    return TradeResult(
                        status=TradeOutcome.FILLED,
                        symbol=symbol,
                        side=side_str,
                        volume=float(volume),
                        price=fill_price,
                        order_id=order_id,
                        order_number=order_number,
                        error=None,
                        error_code=fill_code,
                    )

            if order_number is not None:
                try:
                    orders = await self._ws.get_orders()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("get_orders probe failed: %s", exc)
                    orders = []
                    if last_exc is None:
                        last_exc = str(exc)

                if any(_order_matches_number(o.order_id, order_number) for o in orders):
                    return TradeResult(
                        status=TradeOutcome.QUEUED,
                        symbol=symbol,
                        side=side_str,
                        volume=float(volume),
                        order_id=order_id,
                        order_number=order_number,
                    )

        if last_exc is None and order_number is not None:
            logger.info(
                "gRPC accepted order_number=%s but probes returned empty; "
                "classifying as QUEUED (getAllOrders does not list queued market orders)",
                order_number,
            )
            return TradeResult(
                status=TradeOutcome.QUEUED,
                symbol=symbol,
                side=side_str,
                volume=float(volume),
                order_id=order_id,
                order_number=order_number,
            )

        err_msg = (
            f"gRPC accepted order (order_id={order_id}, order_number={order_number}) "
            "but neither a matching position nor a pending order was found"
        )
        if last_exc:
            err_msg = f"{err_msg}; last probe error: {last_exc}"
        logger.warning(err_msg)
        return TradeResult(
            status=TradeOutcome.AMBIGUOUS,
            symbol=symbol,
            side=side_str,
            volume=float(volume),
            order_id=order_id,
            order_number=order_number,
            error=err_msg,
            error_code="FILL_STATE_UNKNOWN",
        )

    async def _find_matching_position(
        self,
        symbol: str,
        volume: int,
        side_str: Literal["buy", "sell"],
        *,
        order_id: str | None = None,
    ) -> Position | None:
        """Find a live position that plausibly corresponds to a just-sent trade.

        When ``order_id`` is known and the WS snapshot populates order_ids,
        the match is by exact order_id (disambiguates against a pre-existing
        identical position). Falls back to (symbol, side, volume) matching
        either when no ``order_id`` was provided (RBAC-retry idempotency
        probe) or when the WS snapshot has no order_ids populated yet.
        """
        try:
            positions = await self._ws.get_positions()
        except Exception as exc:
            logger.warning("Idempotency probe failed (get_positions): %s", exc)
            return None
        return _match_position(positions, symbol, volume, side_str, order_id=order_id)

    async def _poll_fill_price(
        self,
        symbol: str,
        attempts: int = 3,
        delay_sec: float = 1.0,
        *,
        order_id: str | None = None,
    ) -> tuple[float | None, str | None]:
        """Poll positions after a successful trade to determine the fill price.

        When ``order_id`` is provided and the snapshot populates order_ids,
        match by order_id to avoid reading the price of a pre-existing
        identical position. Otherwise fall back to the first symbol match.

        Returns ``(price, error_code)``. ``error_code`` is None when the
        price was observed, ``"FILL_PRICE_UNKNOWN"`` when the position did
        not appear within ``attempts`` tries. The trade still succeeded —
        the order ID is the authoritative record.
        """
        target = symbol.upper()
        for i in range(attempts):
            try:
                positions = await self._ws.get_positions()
                decided, match = _lookup_by_order_id(positions, order_id)
                if decided:
                    if match is not None:
                        return match.open_price, None
                else:
                    for p in positions:
                        if p.symbol.upper() == target:
                            return p.open_price, None
            except Exception as exc:
                logger.warning("Fill-price poll attempt %d/%d failed: %s", i + 1, attempts, exc)
            if i < attempts - 1:
                await asyncio.sleep(delay_sec)
        logger.warning(
            "Could not determine fill price for %s after %d attempts",
            symbol,
            attempts,
        )
        return None, "FILL_PRICE_UNKNOWN"


def _match_position(
    positions: list[Position],
    symbol: str,
    volume: int,
    side_str: Literal["buy", "sell"],
    *,
    order_id: str | None = None,
) -> Position | None:
    """Match a position from a snapshot. Prefer order_id; fall back to (symbol, side, volume)."""
    decided, match = _lookup_by_order_id(positions, order_id)
    if decided:
        return match
    target = symbol.upper()
    for p in positions:
        if p.symbol.upper() == target and p.side == side_str and abs(p.volume - float(volume)) < 1e-9:
            return p
    return None


def _lookup_by_order_id(positions: list[Position], order_id: str | None) -> tuple[bool, Position | None]:
    """Resolve a position by order_id when id-based lookup is meaningful.

    Returns ``(decided, match)``:

    - ``decided=True``  — an id-based lookup was performed. ``match`` is
      the hit or ``None`` if no id matched; caller MUST respect the
      decision (do not fall back to heuristic matching).
    - ``decided=False`` — id-based lookup was not meaningful (no
      ``order_id`` given, or no position in the snapshot has any
      ``order_id`` populated). Caller should use its own fallback.
    """
    if order_id is None or not any(p.order_id is not None for p in positions):
        return False, None
    for p in positions:
        if p.order_id == order_id:
            return True, p
    return True, None


def _order_matches_number(order_id: str | None, order_number: int) -> bool:
    """True if a PendingOrder.order_id refers to the given broker order_number.

    Accepts the empirically-observed ``str(order_number)`` form and also
    matches when ``order_id`` parses as an integer equal to ``order_number``
    (tolerates leading-zero / int-like drift in the wire format).
    """
    if order_id is None:
        return False
    if order_id == str(order_number):
        return True
    try:
        return int(order_id) == order_number
    except ValueError:
        return False


def _decimal_places(value: float, max_scale: int = 5) -> int:
    """Determine the number of decimal places in a float, up to max_scale."""
    text = f"{value:.{max_scale}f}"
    decimals = text.split(".")[1] if "." in text else ""
    # Strip trailing zeros
    stripped = decimals.rstrip("0")
    return max(len(stripped), 2)  # at least 2 for prices
