"""Low-level WebSocket client for xStation5.

Implements the CoreAPI protocol with full CAS authentication support.
Provides real-time data subscriptions and trading capabilities via WebSocket.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast

import websockets
import websockets.asyncio.client

if TYPE_CHECKING:
    from xtb_api.auth.auth_manager import AuthManager

from xtb_api.auth.cas_client import CASClient
from xtb_api.exceptions import (
    AuthenticationError,
    ProtocolError,
    ReconnectionError,
    XTBConnectionError,
    XTBTimeoutError,
)
from xtb_api.types.enums import SocketStatus, SubscriptionEid, Xs6Side
from xtb_api.types.instrument import InstrumentSearchResult, Quote
from xtb_api.types.trading import (
    AccountBalance,
    PendingOrder,
    Position,
    TradeOptions,
    TradeOutcome,
    TradeResult,
)
from xtb_api.types.websocket import (
    CASLoginTwoFactorRequired,
    ClientInfo,
    WSClientConfig,
    WSPushMessage,
    WSResponse,
    XLoginAccountInfo,
    XLoginResult,
)
from xtb_api.utils import build_account_id, price_from_decimal, volume_from
from xtb_api.ws.parsers import (
    parse_balance,
    parse_instruments,
    parse_orders,
    parse_positions,
    parse_quote,
)

logger = logging.getLogger(__name__)

_BALANCE_SNAPSHOT_POLL_MS = 200
_BALANCE_SNAPSHOT_MAX_WAIT_MS = 3000

# Type alias for event callbacks
EventCallback = Callable[..., Any]


class XTBWebSocketClient:
    """Low-level WebSocket client for xStation5.

    Features:
    - Full CAS authentication flow (credentials → TGT → Service Ticket → WebSocket auth)
    - Real-time subscriptions (ticks, positions, request status)
    - Symbol cache for fast instrument search (11,888+ instruments)
    - Auto-reconnection with exponential backoff
    - Direct trading via tradeTransaction commands
    """

    def __init__(
        self,
        config: WSClientConfig,
        auth_manager: AuthManager | None = None,
    ) -> None:
        self._config = config
        self._auth_manager = auth_manager
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._status = SocketStatus.CLOSED
        self._pending_requests: dict[str, asyncio.Future[WSResponse]] = {}
        self._req_sequence = 0
        self._ping_task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._reconnect_delay = 1.0
        self._reconnecting = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._intentional_disconnect = False
        self._cas_client: CASClient | None = None
        self._login_result: XLoginResult | None = None
        self._authenticated = False
        self._symbols_cache: list[InstrumentSearchResult] | None = None
        self._symbols_lock = asyncio.Lock()

        # Event handlers
        self._event_handlers: dict[str, list[EventCallback]] = {}

        # Initialize CAS client if auth credentials provided
        if config.auth and config.auth.credentials:
            self._cas_client = CASClient()

    # ─── Properties ───

    @property
    def account_id(self) -> str:
        """Account ID in format 'meta1_12345678'."""
        return build_account_id(self._config.account_number, self._config.endpoint)

    @property
    def connection_status(self) -> SocketStatus:
        """Current WebSocket connection status."""
        return self._status

    @property
    def is_connected(self) -> bool:
        """Whether WebSocket is connected."""
        return self._status == SocketStatus.CONNECTED

    @property
    def is_authenticated(self) -> bool:
        """Whether authenticated with XTB servers."""
        return self._authenticated

    @property
    def account_info(self) -> XLoginResult | None:
        """Account information from login result."""
        return self._login_result

    # ─── Event System ───

    def on(self, event: str, callback: EventCallback) -> None:
        """Register event handler.

        Events:
        - 'connected' - WebSocket connection established
        - 'authenticated' - CAS authentication successful (XLoginResult)
        - 'disconnected' - Connection closed (code, reason)
        - 'error' - Error occurred (Exception)
        - 'status_update' - Status changed (SocketStatus)
        - 'push' - Generic push message (WSPushMessage)
        - 'message' - Any WebSocket message (WSResponse)
        - 'tick' - Real-time tick data (dict)
        - 'position' - Position update (dict)
        - 'symbol' - Symbol data update (dict)
        - 'requires_2fa' - Two-factor auth required (dict)
        """
        self._event_handlers.setdefault(event, []).append(callback)

    def off(self, event: str, callback: EventCallback) -> None:
        """Remove event handler."""
        handlers = self._event_handlers.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def _emit(self, event: str, *args: Any) -> None:
        """Emit event to all registered handlers.

        Supports both sync and async callbacks. Async callbacks are
        scheduled on the running event loop.
        """
        for handler in self._event_handlers.get(event, []):
            try:
                result = handler(*args)
                if inspect.iscoroutine(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        result.close()  # Prevent "coroutine never awaited" warning
            except Exception:
                logger.error("Error in event handler for '%s'", event, exc_info=True)

    # ─── Connection ───

    async def connect(self) -> None:
        """Connect to WebSocket server and perform authentication if configured.

        Raises:
            RuntimeError: If already connected
            Exception: If connection or authentication fails
        """
        if self._ws is not None:
            raise XTBConnectionError("Already connected or connecting")

        await self._establish_connection()

        if self._config.auth:
            await self._perform_authentication()

    async def _establish_connection(self) -> None:
        """Establish WebSocket connection."""
        self._update_status(SocketStatus.CONNECTING)

        try:
            self._ws = await websockets.asyncio.client.connect(
                self._config.url,
                max_size=20 * 1024 * 1024,  # 20MB for large symbol lists
            )
        except Exception:
            self._update_status(SocketStatus.ERROR)
            raise

        self._update_status(SocketStatus.CONNECTED)
        self._reconnect_delay = 1.0
        self._reconnecting = False
        self._reconnect_attempts = 0
        self._start_ping()
        self._start_listen()
        self._emit("connected")

    async def _perform_authentication(self) -> None:
        """Perform CAS authentication flow."""
        auth = self._config.auth
        if auth is None:
            return

        service_ticket: str | None = None

        if auth.service_ticket:
            service_ticket = auth.service_ticket
        elif auth.tgt:
            if not self._cas_client:
                self._cas_client = CASClient()
            result = await self._cas_client.get_service_ticket(auth.tgt, "xapi5")
            service_ticket = result.service_ticket
        elif auth.credentials:
            if not self._cas_client:
                self._cas_client = CASClient()

            if auth.browser_auth:
                self._browser_auth_active = True
                login_result = await self._cas_client.login_with_browser(
                    auth.credentials.email, auth.credentials.password
                )
            else:
                login_result = await self._cas_client.login(auth.credentials.email, auth.credentials.password)

            if isinstance(login_result, CASLoginTwoFactorRequired):
                self._emit(
                    "requires_2fa",
                    {
                        "login_ticket": login_result.login_ticket,
                        "session_id": login_result.session_id,  # backward compat
                        "two_factor_auth_type": login_result.two_factor_auth_type,
                        "methods": login_result.methods,
                        "expires_at": login_result.expires_at,
                    },
                )
                return  # Wait for 2FA completion

            ticket_result = await self._cas_client.get_service_ticket(login_result.tgt, "xapi5")
            service_ticket = ticket_result.service_ticket
        else:
            raise AuthenticationError("No valid authentication method provided")

        # Register client info then login
        await self.register_client_info()
        await self.login_with_service_ticket(service_ticket)

    def disconnect(self) -> None:
        """Disconnect from the WebSocket server.

        Prefers async close when a running loop is available.
        Falls back to cleanup-only when called outside async context.
        """
        self._intentional_disconnect = True
        ws = self._ws  # Capture before cleanup nulls it
        if ws:
            self._update_status(SocketStatus.DISCONNECTING)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._close_ws_ref(ws))
            except RuntimeError:
                pass  # No running loop — ws will be cleaned up below
        self._cleanup()

    async def _close_ws_ref(self, ws: Any) -> None:
        """Close a specific WebSocket connection reference."""
        with contextlib.suppress(Exception):
            await ws.close()

    async def _close_ws(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()

    async def disconnect_async(self) -> None:
        """Async disconnect from the WebSocket server."""
        self._intentional_disconnect = True
        if self._ws:
            self._update_status(SocketStatus.DISCONNECTING)
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._cleanup()

    # ─── Send Commands ───

    async def send(self, command_name: str, payload: dict[str, Any], timeout_ms: int = 10000) -> WSResponse:
        """Send a raw CoreAPI command and wait for response.

        Args:
            command_name: Command name for request ID generation
            payload: CoreAPI command payload
            timeout_ms: Request timeout in milliseconds

        Returns:
            Command response

        Raises:
            RuntimeError: If not connected
            TimeoutError: If request times out
        """
        if not self.is_connected or not self._ws:
            raise XTBConnectionError("Not connected")

        req_id = self._next_req_id(command_name)

        core_api: dict[str, Any] = {
            "endpoint": self._config.endpoint,
            **payload,
        }

        # Only add accountId for non-auth commands
        if "registerClientInfo" not in payload and "logonWithServiceTicket" not in payload:
            core_api["accountId"] = self.account_id

        request = {
            "reqId": req_id,
            "command": [{"CoreAPI": core_api}],
        }

        loop = asyncio.get_running_loop()
        future: asyncio.Future[WSResponse] = loop.create_future()
        self._pending_requests[req_id] = future

        try:
            await self._ws.send(json.dumps(request))
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        except TimeoutError as e:
            self._pending_requests.pop(req_id, None)
            raise XTBTimeoutError(f"Request {req_id} timed out") from e

    # ─── Subscriptions ───

    async def subscribe_ticks(self, symbol_key: str) -> WSResponse:
        """Subscribe to real-time tick/quote data for a symbol.

        Args:
            symbol_key: Symbol key in format {assetClassId}_{symbolName}_{groupId}
        """
        return await self.send(
            "getAndSubscribeTicks",
            {"getAndSubscribeElement": {"eid": SubscriptionEid.TICKS, "keys": [symbol_key]}},
        )

    async def unsubscribe_ticks(self, symbol_key: str) -> WSResponse:
        """Unsubscribe from tick data for a symbol."""
        return await self.send(
            "unsubscribeTicks",
            {"unsubscribeElement": {"eid": SubscriptionEid.TICKS, "keys": [symbol_key]}},
        )

    async def subscribe_request_status(self) -> WSResponse:
        """Subscribe to request status updates for trade confirmations."""
        return await self.send(
            "subscribeRequestStatus",
            {"subscribeElement": {"eid": SubscriptionEid.REQUEST_STATUS}},
        )

    async def ping(self) -> int:
        """Ping the server and return latency in milliseconds."""
        start = time.monotonic()
        await self.send("ping", {"ping": {}})
        return int((time.monotonic() - start) * 1000)

    # ─── Authentication ───

    async def register_client_info(self) -> WSResponse:
        """Register client info — first step in authentication flow."""
        client_info = ClientInfo(
            appName=self._config.app_name,
            appVersion=self._config.app_version,
            appBuildNumber="0",
            device=self._config.device,
            osVersion="",
            comment="Python",
            apiVersion="2.73.0",
            osType=0,
            deviceType=1,
        )

        return await self.send(
            "registerClientInfo",
            {"registerClientInfo": {"clientInfo": client_info.model_dump()}},
        )

    async def login_with_service_ticket(self, service_ticket: str) -> XLoginResult:
        """Login with service ticket — second step in authentication flow.

        Args:
            service_ticket: Service ticket from CAS (format: ST-...)

        Returns:
            Login result with account list and user data

        Raises:
            RuntimeError: If login fails
        """
        response = await self.send(
            "loginWithServiceTicket",
            {"logonWithServiceTicket": {"serviceTicket": service_ticket}},
        )

        # Parse login result
        resp_list = response.response or []
        if not resp_list:
            raise AuthenticationError("Login failed: empty response")

        first = resp_list[0] if resp_list else {}
        if not isinstance(first, dict):
            raise ProtocolError("Login failed: unexpected response format")

        login_data = first.get("xloginresult")
        if not login_data:
            exception = first.get("exception", {})
            error_msg = exception.get("message", "") if isinstance(exception, dict) else str(exception)
            raise AuthenticationError(f"Login failed: {error_msg or 'Unknown error'}")

        # Parse accountList
        account_list = []
        for acc in login_data.get("accountList", []):
            wt_account_id = acc.get("wtAccountId", {})
            account_no = int(wt_account_id.get("accountNo", acc.get("accountNo", 0)))
            endpoint_type = acc.get("endpointType", {})
            if isinstance(endpoint_type, dict):
                endpoint_type = endpoint_type.get("name", "")
            account_list.append(
                XLoginAccountInfo(
                    accountNo=account_no,
                    currency=str(acc.get("currency", "")),
                    endpointType=str(endpoint_type),
                )
            )

        user_data = login_data.get("userData", {})
        self._login_result = XLoginResult(
            accountList=account_list,
            endpointList=login_data.get("endpointList", []),
            userData={
                "name": str(user_data.get("name", "")),
                "surname": str(user_data.get("surname", "")),
            },
        )

        self._authenticated = True
        self._emit("authenticated", self._login_result)
        return self._login_result

    async def submit_two_factor_code(
        self,
        login_ticket: str,
        code: str,
        two_factor_auth_type: str = "SMS",
        *,
        session_id: str | None = None,
    ) -> None:
        """Submit 2FA code to complete login.

        Args:
            login_ticket: Login ticket from 'requires_2fa' event (MID-xxx).
                          For backward compat, ``session_id`` kwarg is also accepted.
            code: 6-digit OTP code
            two_factor_auth_type: Auth method, default ``"SMS"``
            session_id: **Deprecated** — alias for ``login_ticket``

        Raises:
            RuntimeError: If CAS client not available
        """
        if not self._cas_client:
            raise AuthenticationError("No CAS client available - authentication not started")

        # Route OTP to browser auth if active
        if getattr(self, "_browser_auth_active", False):
            two_factor_result = await self._cas_client.submit_browser_otp(code)
            self._browser_auth_active = False
        else:
            ticket = login_ticket or session_id or ""
            two_factor_result = await self._cas_client.login_with_two_factor(ticket, code, two_factor_auth_type)

        if isinstance(two_factor_result, CASLoginTwoFactorRequired):
            self._emit(
                "requires_2fa",
                {
                    "login_ticket": two_factor_result.login_ticket,
                    "session_id": two_factor_result.session_id,
                    "two_factor_auth_type": two_factor_result.two_factor_auth_type,
                    "methods": two_factor_result.methods,
                    "expires_at": two_factor_result.expires_at,
                },
            )
            return

        ticket_result = await self._cas_client.get_service_ticket(two_factor_result.tgt, "xapi5")
        await self.register_client_info()
        await self.login_with_service_ticket(ticket_result.service_ticket)

    # ─── High-level API ───

    async def get_balance(self) -> AccountBalance:
        """Get account balance and equity information.

        XTB acks the TOTAL_BALANCE subscribe immediately with an empty
        element list; the populated `xtotalbalance` snapshot arrives on
        the subscription a moment later. To avoid silently returning
        zeros, this method polls up to
        :data:`_BALANCE_SNAPSHOT_MAX_WAIT_MS` for the first populated
        snapshot, then falls back to whatever the last response held.
        """
        if not self._authenticated or not self._login_result:
            raise AuthenticationError("Must be authenticated to get balance")

        account = None
        for acc in self._login_result.accountList:
            if acc.accountNo == self._config.account_number:
                account = acc
                break
        if not account and self._login_result.accountList:
            account = self._login_result.accountList[0]
        if not account:
            raise ProtocolError("Account not found in login result")

        deadline = time.monotonic() + _BALANCE_SNAPSHOT_MAX_WAIT_MS / 1000
        elements: list[dict[str, Any]] = []
        first = True
        while True:
            res = await self.send(
                "getBalance",
                {"getAndSubscribeElement": {"eid": SubscriptionEid.TOTAL_BALANCE}},
            )
            elements = self._extract_elements(res)
            if elements and (elements[0] or {}).get("value", {}).get("xtotalbalance"):
                break
            if time.monotonic() >= deadline:
                if first:
                    logger.warning(
                        "TOTAL_BALANCE subscription never produced a snapshot within %dms; returning zeros",
                        _BALANCE_SNAPSHOT_MAX_WAIT_MS,
                    )
                break
            first = False
            await asyncio.sleep(_BALANCE_SNAPSHOT_POLL_MS / 1000)

        return parse_balance(elements, account.currency, account.accountNo)

    async def get_positions(self) -> list[Position]:
        """Get all open trading positions.

        XTB's xStation5 CoreAPI echoes the position snapshot on the normal
        reqId-correlated response channel (not the push channel, despite the
        `getAndSubscribeElement` keyword). The subscription it sets up also
        emits per-position updates via `_emit("position", ...)`; those are
        orthogonal to the snapshot returned here.
        """
        res = await self.send(
            "getPositions",
            {"getAndSubscribeElement": {"eid": SubscriptionEid.POSITIONS}},
            timeout_ms=30000,
        )
        return parse_positions(self._extract_elements(res))

    async def get_orders(self) -> list[PendingOrder]:
        """Get all pending (limit/stop) orders."""
        res = await self.send(
            "getAllOrders",
            {"getAndSubscribeElement": {"eid": SubscriptionEid.ORDERS}},
        )

        return parse_orders(self._extract_elements(res))

    async def buy(self, symbol: str, volume: int, options: TradeOptions | None = None) -> TradeResult:
        """Execute a BUY order via WebSocket using ``Xs6Side.BUY`` (value 0).

        ⚠️ WARNING: This executes real trades. Always test on demo accounts first.

        Note: This uses the WebSocket protocol side constant (``Xs6Side.BUY=0``),
        which differs from the gRPC constant (``SIDE_BUY=1``). Do not mix.
        """
        return await self._execute_trade(symbol, volume, Xs6Side.BUY, options)

    async def sell(self, symbol: str, volume: int, options: TradeOptions | None = None) -> TradeResult:
        """Execute a SELL order via WebSocket using ``Xs6Side.SELL`` (value 1).

        ⚠️ WARNING: This executes real trades. Always test on demo accounts first.

        Note: This uses the WebSocket protocol side constant (``Xs6Side.SELL=1``),
        which differs from the gRPC constant (``SIDE_SELL=2``). Do not mix.
        """
        return await self._execute_trade(symbol, volume, Xs6Side.SELL, options)

    def _filter_cached_symbols(self, query: str) -> list[InstrumentSearchResult]:
        """Filter the cached symbols list by substring match (symbol/name/description)."""
        if self._symbols_cache is None:
            return []
        query_lower = query.lower()
        return [
            s
            for s in self._symbols_cache
            if query_lower in s.symbol.lower() or query_lower in s.name.lower() or query_lower in s.description.lower()
        ][:100]

    async def search_instrument(self, query: str) -> list[InstrumentSearchResult]:
        """Search for financial instruments with caching.

        First call downloads all 11,888+ instruments and caches them.
        Subsequent searches are instant from cache. Uses a lock to
        prevent concurrent callers from downloading the list multiple times.
        """
        # Fast path: cache already populated (no lock needed)
        if self._symbols_cache is not None:
            return self._filter_cached_symbols(query)

        async with self._symbols_lock:
            # Re-check after acquiring lock (another coroutine may have populated it)
            if self._symbols_cache is not None:
                return self._filter_cached_symbols(query)

            res = await self.send(
                "searchInstruments",
                {"getAndSubscribeElement": {"eid": SubscriptionEid.SYMBOLS}},
                timeout_ms=30000,
            )

            self._symbols_cache = parse_instruments(self._extract_elements(res))
            logger.info("Cached %d instruments for instant search", len(self._symbols_cache))

        return self._filter_cached_symbols(query)

    def get_account_number(self) -> int:
        """Get the account number for this WebSocket session."""
        if self._login_result and self._login_result.accountList:
            for acc in self._login_result.accountList:
                if acc.accountNo == self._config.account_number:
                    return acc.accountNo
            return self._login_result.accountList[0].accountNo
        return self._config.account_number

    async def get_quote(self, symbol: str) -> Quote | None:
        """Get current quote (bid/ask prices) for a symbol.

        Args:
            symbol: Symbol name or full symbol key
        """
        is_key = "_" in symbol
        keys_to_try = [symbol] if is_key else [f"9_{symbol}_6", symbol]

        for key in keys_to_try:
            try:
                res = await self.subscribe_ticks(key)
                try:
                    quote = parse_quote(self._extract_elements(res), symbol)
                finally:
                    # Always unsubscribe to avoid leaking subscriptions
                    with contextlib.suppress(Exception):
                        await self.unsubscribe_ticks(key)
                if quote:
                    return quote
            except Exception:
                continue

        return None

    # ─── Private helpers ───

    async def _execute_trade(
        self,
        symbol: str,
        volume: int,
        side: Xs6Side,
        options: TradeOptions | None = None,
    ) -> TradeResult:
        """Execute a trade order."""
        results = await self.search_instrument(symbol)
        instrument = None
        for r in results:
            if r.symbol.upper() == symbol.upper():
                instrument = r
                break
        if not instrument and results:
            instrument = results[0]

        side_str = "buy" if side == Xs6Side.BUY else "sell"
        if not instrument:
            return TradeResult(
                status=TradeOutcome.REJECTED,
                symbol=symbol,
                side=cast("Literal['buy', 'sell']", side_str),
                error=f"Instrument not found: {symbol}",
                error_code="INSTRUMENT_NOT_FOUND",
            )

        size: dict[str, Any]
        if options and options.amount is not None:
            size = {"amount": options.amount}
        else:
            vol = volume_from(volume)
            size = {"volume": {"value": vol.value, "scale": vol.scale}}

        order: dict[str, Any] = {
            "instrumentid": instrument.instrument_id,
            "size": size,
            "side": side.value,
        }

        if options and options.stop_loss is not None:
            if options.trailing_stop is not None:
                order["stoploss"] = {"trailingstopinput": {"pips": options.trailing_stop}}
            else:
                p = price_from_decimal(options.stop_loss, 2)
                order["stoploss"] = {"price": {"value": p.value, "scale": p.scale}}
        if options and options.take_profit is not None:
            p = price_from_decimal(options.take_profit, 2)
            order["takeprofit"] = {"price": {"value": p.value, "scale": p.scale}}

        order_event = {
            "order": order,
            "uiTrackingId": f"ws_{int(time.time() * 1000)}",
            "account": {
                "number": self._config.account_number,
                "server": self._config.endpoint,
                "currency": "",
            },
        }

        await self.subscribe_request_status()

        res = await self.send(
            "tradeTransaction",
            {"tradeTransaction": {"newMarketOrder": order_event}},
            timeout_ms=15000,
        )

        if res.error:
            return TradeResult(
                status=TradeOutcome.REJECTED,
                symbol=symbol,
                side=cast("Literal['buy', 'sell']", side_str),
                error=res.error.get("message", "Unknown error"),
            )

        data = self._extract_response_data(res)
        return TradeResult(
            status=TradeOutcome.FILLED,
            order_id=str(data.get("orderId")) if data and data.get("orderId") is not None else None,
            symbol=symbol,
            side=cast("Literal['buy', 'sell']", side_str),
            volume=float(volume),
            price=float(data["price"]) if data and data.get("price") is not None else None,
        )

    def _extract_response_data(self, res: WSResponse) -> dict[str, Any] | None:
        """Extract response data from WSResponse."""
        resp_list = res.response
        if resp_list and len(resp_list) > 0:
            first = resp_list[0]
            if isinstance(first, dict):
                return first
        if res.data and isinstance(res.data, dict):
            return cast("dict[str, Any] | None", res.data)
        return None

    def _extract_elements(self, res: WSResponse) -> list[dict[str, Any]]:
        """Extract all elements from a subscription response."""
        resp_list = res.response
        if not resp_list:
            return []
        first = resp_list[0] if resp_list else None
        if not isinstance(first, dict):
            return []
        element = first.get("element", {})
        if isinstance(element, dict) and isinstance(element.get("elements"), list):
            return cast("list[dict[str, Any]]", element["elements"])
        return []

    def _next_req_id(self, prefix: str) -> str:
        """Generate next request ID."""
        self._req_sequence += 1
        return f"{prefix}_{int(time.time() * 1000)}_{self._req_sequence}"

    def _handle_message(self, raw: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            self._emit("error", ProtocolError(f"Failed to parse message: {e}"))
            return

        req_id = msg.get("reqId", "")

        # Handle request responses
        if req_id and req_id in self._pending_requests:
            future = self._pending_requests.pop(req_id)
            if not future.done():
                response = WSResponse(**msg) if isinstance(msg, dict) else WSResponse(reqId=req_id)
                future.set_result(response)
            return

        # Handle push messages (status=1)
        if msg.get("status") == 1 and msg.get("events"):
            push_msg = WSPushMessage(**msg) if isinstance(msg, dict) else WSPushMessage()
            self._emit("push", push_msg)

            for event in msg.get("events", []):
                eid = event.get("eid")
                row = event.get("row", {})
                value = row.get("value", {})

                if eid == SubscriptionEid.TICKS and value.get("xcfdtick"):
                    self._emit("tick", value["xcfdtick"])
                elif eid == SubscriptionEid.POSITIONS and value.get("xcfdtrade"):
                    self._emit("position", value["xcfdtrade"])
                elif eid == SubscriptionEid.SYMBOLS and value.get("xcfdsymbol"):
                    self._emit("symbol", value["xcfdsymbol"])
            return

        # Generic message
        response = WSResponse(**msg) if isinstance(msg, dict) else WSResponse()
        self._emit("message", response)

    def _start_ping(self) -> None:
        """Start ping keepalive task."""
        self._stop_ping()

        async def ping_loop() -> None:
            while self.is_connected:
                try:
                    await asyncio.sleep(self._config.ping_interval / 1000)
                    if self.is_connected:
                        await self.ping()
                except Exception:
                    pass

        self._ping_task = asyncio.get_running_loop().create_task(ping_loop())

    def _stop_ping(self) -> None:
        """Stop ping keepalive task."""
        if self._ping_task:
            self._ping_task.cancel()
            self._ping_task = None

    def _start_listen(self) -> None:
        """Start listening for incoming messages."""
        if self._listen_task:
            self._listen_task.cancel()

        async def listen_loop() -> None:
            try:
                assert self._ws is not None
                async for message in self._ws:
                    if isinstance(message, (str, bytes)):
                        self._handle_message(message if isinstance(message, str) else message.decode())
            except websockets.exceptions.ConnectionClosed as e:
                self._cleanup()
                self._update_status(SocketStatus.CLOSED)
                self._emit("disconnected", e.code, str(e.reason))
                if self._config.auto_reconnect and not self._reconnecting and not self._intentional_disconnect:
                    asyncio.get_running_loop().create_task(self._schedule_reconnect())
            except Exception as e:
                self._emit("error", e)

        self._listen_task = asyncio.get_running_loop().create_task(listen_loop())

    async def _schedule_reconnect(self) -> None:
        """Schedule reconnection with exponential backoff.

        If an AuthManager is available, obtains a fresh service ticket
        for re-authentication instead of reusing the (possibly stale) original.

        Raises ReconnectionError after max_reconnect_attempts failures.
        """
        self._reconnecting = True
        self._reconnect_attempts += 1

        if self._reconnect_attempts > self._max_reconnect_attempts:
            self._reconnecting = False
            error = ReconnectionError(f"Exhausted {self._max_reconnect_attempts} reconnection attempts")
            self._emit("error", error)
            return

        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(
            self._reconnect_delay * 1.5,
            self._config.max_reconnect_delay / 1000,
        )
        try:
            if self._auth_manager:
                # Get fresh ST from AuthManager (handles TGT refresh if needed)
                fresh_st = await self._auth_manager.get_service_ticket()
                await self._establish_connection()
                await self.register_client_info()
                await self.login_with_service_ticket(fresh_st)
            else:
                await self.connect()
        except Exception as e:
            logger.warning("Reconnection attempt %d failed: %s", self._reconnect_attempts, e)
            self._reconnecting = False
            if self._config.auto_reconnect and self._reconnect_attempts < self._max_reconnect_attempts:
                asyncio.get_running_loop().create_task(self._schedule_reconnect())
            elif self._reconnect_attempts >= self._max_reconnect_attempts:
                error = ReconnectionError(f"Exhausted {self._max_reconnect_attempts} reconnection attempts")
                self._emit("error", error)

    def _cleanup(self) -> None:
        """Clean up connection resources."""
        self._stop_ping()
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None

        for _req_id, future in self._pending_requests.items():
            if not future.done():
                future.set_exception(XTBConnectionError("Connection closed"))
        self._pending_requests.clear()
        self._ws = None
        self._authenticated = False
        self._login_result = None
        self._symbols_cache = None

    def _update_status(self, status: SocketStatus) -> None:
        """Update connection status and emit event."""
        self._status = status
        self._emit("status_update", status)
