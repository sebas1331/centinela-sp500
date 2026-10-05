"""gRPC-web client for XTB xStation5 trading.

Uses native HTTP POST via httpx for gRPC-web calls to ipax.xtb.com.
Requires a valid TGT (obtained via AuthManager) to create JWT tokens.

Flow:
1. Build CreateAccessTokenRequest protobuf (TGT + Account)
2. Send auth request → get JWT with account scope (acn/acs)
3. Send trade requests with JWT
"""

from __future__ import annotations

import base64
import contextlib
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from xtb_api.auth.auth_manager import AuthManager

from xtb_api.exceptions import (
    AmbiguousOutcomeError,
    AuthenticationError,
)
from xtb_api.grpc.proto import (
    GRPC_AUTH_ENDPOINT,
    GRPC_DELETE_ORDERS_ENDPOINT,
    GRPC_NEW_ORDER_ENDPOINT,
    GRPC_WEB_TEXT_CONTENT_TYPE,
    SIDE_BUY,
    SIDE_SELL,
    build_create_access_token_request,
    build_delete_orders_request,
    build_grpc_web_text_body,
    build_new_market_order,
    extract_jwt,
    parse_delete_orders_response,
    parse_new_market_order_response,
)
from xtb_api.grpc.proto import (  # CENTINELA (CAMBIOS.md, parche 3)
    ERRORES_MODIFICAR,
    ERRORES_NUEVA,
    GRPC_MODIFY_LIMIT_ORDER_ENDPOINT,
    GRPC_MODIFY_STOP_ORDER_ENDPOINT,
    GRPC_NEW_LIMIT_ORDER_ENDPOINT,
    GRPC_NEW_STOP_ORDER_ENDPOINT,
    GRPC_SUBSCRIBE_ORDER_GROUPS_ENDPOINT,
    build_modify_pending_order,
    build_new_pending_order,
    parse_delete_orders_full,
    parse_order_groups,
    parse_pending_order_response,
    split_grpc_web,
)
from xtb_api.grpc.types import GrpcCancelResult, GrpcPendingOrderResult, GrpcTradeResult

logger = logging.getLogger(__name__)

# JWT cache lifetime
_JWT_VALIDITY_SEC = 300  # 5 minutes


#: CENTINELA (CAMBIOS.md, parche 2). El último error de gRPC leído de las
#: cabeceras, para que quien traduzca la respuesta pueda decir POR QUÉ falló en
#: vez de "respuesta vacía".
CENTINELA_ULTIMO_ERROR: dict = {}

#: Cómo llama gRPC a cada código, para no tener que buscarlo.
#: https://grpc.io/docs/guides/status-codes/
CENTINELA_CODIGOS = {
    "0": "OK", "1": "CANCELLED", "2": "UNKNOWN", "3": "INVALID_ARGUMENT",
    "4": "DEADLINE_EXCEEDED", "5": "NOT_FOUND", "6": "ALREADY_EXISTS",
    "7": "PERMISSION_DENIED", "8": "RESOURCE_EXHAUSTED",
    "9": "FAILED_PRECONDITION", "10": "ABORTED", "11": "OUT_OF_RANGE",
    "12": "UNIMPLEMENTED", "13": "INTERNAL", "14": "UNAVAILABLE",
    "15": "DATA_LOSS", "16": "UNAUTHENTICATED",
}


def _centinela_motivo(headers) -> str | None:
    """'INVALID_ARGUMENT (3): Could not find instrument for id: 335'."""
    estado = headers.get("grpc-status")
    if estado is None:
        return None
    nombre = CENTINELA_CODIGOS.get(str(estado), f"código {estado}")
    return f"{nombre} ({estado}): {headers.get('grpc-message') or 'sin mensaje'}"


class GrpcClient:
    """gRPC-web client for XTB xStation5 trading.

    When an AuthManager is provided, JWT tokens are automatically
    refreshed from the shared TGT — no manual token management needed.
    """

    def __init__(
        self,
        account_number: str,
        account_server: str = "XS-real1",
        auth: AuthManager | None = None,
    ) -> None:
        self._account_number = account_number
        self._account_server = account_server
        self._auth = auth
        self._jwt: str | None = None
        self._jwt_timestamp: float = 0.0
        self._http: httpx.AsyncClient | None = None

    def invalidate_jwt(self) -> None:
        """Clear the cached JWT so the next call fetches a fresh one."""
        self._jwt = None
        self._jwt_timestamp = 0.0

    async def _ensure_http(self) -> httpx.AsyncClient:
        """Get or create the long-lived httpx client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=20.0)
        return self._http

    async def _grpc_call(
        self,
        endpoint: str,
        body_b64: str,
        jwt: str | None = None,
    ) -> bytes:
        """Make a gRPC-web call via httpx.

        Args:
            endpoint: Full gRPC-web endpoint URL.
            body_b64: Base64-encoded protobuf body.
            jwt: Optional JWT bearer token.

        Returns:
            Decoded protobuf response bytes.
        """
        headers = {
            "Content-Type": GRPC_WEB_TEXT_CONTENT_TYPE,
            "Accept": GRPC_WEB_TEXT_CONTENT_TYPE,
            "X-Grpc-Web": "1",
            "x-user-agent": "grpc-web-javascript/0.1",
        }
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        client = await self._ensure_http()
        resp = await client.post(endpoint, content=body_b64, headers=headers)
        resp.raise_for_status()

        if not resp.text:
            # CENTINELA (ver CAMBIOS.md, parche 2). En gRPC-web los errores
            # llegan con HTTP 200 y el motivo en las cabeceras: una respuesta
            # "trailers-only" tiene el cuerpo vacío y el porqué arriba. El
            # original devolvía b"" sin mirarlas, así que TODO error de trading
            # subía como "respuesta vacía; resultado ambiguo" y no decía nada.
            #
            # El 2026-09-29 eso costó una tarde: ocho compras rechazadas sin
            # una sola pista. Al leer las cabeceras, el motivo apareció al
            # primer intento: "INVALID_ARGUMENT (3): Could not find instrument
            # for id: 335".
            CENTINELA_ULTIMO_ERROR.clear()
            motivo = _centinela_motivo(resp.headers)
            if motivo:
                CENTINELA_ULTIMO_ERROR["motivo"] = motivo
                CENTINELA_ULTIMO_ERROR["endpoint"] = endpoint.rsplit("/", 1)[-1]
                logger.warning("gRPC %s: %s",
                               CENTINELA_ULTIMO_ERROR["endpoint"], motivo)
            return b""

        CENTINELA_ULTIMO_ERROR.clear()
        return base64.b64decode(resp.text)

    async def get_jwt(self, tgt: str | None = None) -> str:
        """Get JWT with account scope via CreateAccessToken gRPC call.

        If an AuthManager is configured, the TGT is obtained automatically.
        Otherwise, a TGT must be passed explicitly.

        Args:
            tgt: TGT string. If None, uses AuthManager to get one.

        Returns:
            JWT string with acn/acs fields for trading.
        """
        now = time.monotonic()
        if self._jwt and (now - self._jwt_timestamp) < _JWT_VALIDITY_SEC:
            return self._jwt

        if tgt is None:
            if self._auth is None:
                raise AuthenticationError("No TGT provided and no AuthManager configured")
            tgt = await self._auth.get_tgt()

        logger.info("Requesting new JWT via CreateAccessToken...")

        proto_msg = build_create_access_token_request(
            tgt=tgt,
            account_number=self._account_number,
            account_server=self._account_server,
        )
        body_b64 = build_grpc_web_text_body(proto_msg)

        response_bytes = await self._grpc_call(GRPC_AUTH_ENDPOINT, body_b64, jwt=None)

        if not response_bytes:
            raise AuthenticationError("CreateAccessToken returned an empty response — TGT may be invalid")

        jwt = extract_jwt(response_bytes)
        if not jwt:
            raise AuthenticationError(
                "Failed to extract JWT from CreateAccessToken response "
                f"({len(response_bytes)} bytes). "
                "Check that TGT is valid and account info is correct."
            )

        self._jwt = jwt
        self._jwt_timestamp = now
        logger.info("JWT obtained (with account scope)")
        return jwt

    async def _ensure_jwt(self) -> str:
        """Ensure a valid JWT is available, refreshing if needed."""
        now = time.monotonic()
        if self._jwt and (now - self._jwt_timestamp) < _JWT_VALIDITY_SEC:
            return self._jwt
        return await self.get_jwt()

    async def buy(
        self,
        instrument_id: int,
        volume: int,
        *,
        stop_loss_value: int | None = None,
        stop_loss_scale: int | None = None,
        take_profit_value: int | None = None,
        take_profit_scale: int | None = None,
    ) -> GrpcTradeResult:
        """Execute BUY market order."""
        return await self.execute_order(
            instrument_id,
            volume,
            SIDE_BUY,
            stop_loss_value=stop_loss_value,
            stop_loss_scale=stop_loss_scale,
            take_profit_value=take_profit_value,
            take_profit_scale=take_profit_scale,
        )

    async def sell(
        self,
        instrument_id: int,
        volume: int,
        *,
        stop_loss_value: int | None = None,
        stop_loss_scale: int | None = None,
        take_profit_value: int | None = None,
        take_profit_scale: int | None = None,
    ) -> GrpcTradeResult:
        """Execute SELL market order."""
        return await self.execute_order(
            instrument_id,
            volume,
            SIDE_SELL,
            stop_loss_value=stop_loss_value,
            stop_loss_scale=stop_loss_scale,
            take_profit_value=take_profit_value,
            take_profit_scale=take_profit_scale,
        )

    async def execute_order(
        self,
        instrument_id: int,
        volume: int,
        side: int,
        *,
        stop_loss_value: int | None = None,
        stop_loss_scale: int | None = None,
        take_profit_value: int | None = None,
        take_profit_scale: int | None = None,
    ) -> GrpcTradeResult:
        """Execute market order via gRPC-web NewMarketOrder.

        Args:
            instrument_id: gRPC instrument ID (e.g., 9438 for CIG.PL)
            volume: Number of shares
            side: SIDE_BUY (1) or SIDE_SELL (2)
            stop_loss_value: SL price as integer (e.g., 10850 for 1.0850 with scale=4)
            stop_loss_scale: SL price scale (decimal places)
            take_profit_value: TP price as integer
            take_profit_scale: TP price scale (decimal places)

        Returns:
            GrpcTradeResult with success status and order details.
        """
        jwt = await self._ensure_jwt()

        side_name = "BUY" if side == SIDE_BUY else "SELL"
        logger.info("gRPC trade: %s instrument=%d volume=%d", side_name, instrument_id, volume)

        proto_msg = build_new_market_order(
            instrument_id,
            volume,
            side,
            stop_loss_value=stop_loss_value,
            stop_loss_scale=stop_loss_scale,
            take_profit_value=take_profit_value,
            take_profit_scale=take_profit_scale,
        )
        body_b64 = build_grpc_web_text_body(proto_msg)

        try:
            response_bytes = await self._grpc_call(GRPC_NEW_ORDER_ENDPOINT, body_b64, jwt=jwt)
        except httpx.HTTPError as e:
            # Network / HTTP errors are surfaced as failed trades. Unexpected
            # errors (e.g. ValueError from a logic bug, AssertionError) are
            # propagated so they stop execution and hit logging with a full
            # traceback.
            logger.warning("gRPC trade network error: %s", e, exc_info=True)
            return GrpcTradeResult(success=False, error=str(e))

        if not response_bytes:
            # HTTP POST succeeded but the gRPC body is empty. The order may
            # or may not have been placed; the caller must reconcile.
            raise AmbiguousOutcomeError("gRPC trade endpoint returned an empty response; outcome ambiguous")

        logger.debug(
            "gRPC response: %d bytes — %s",
            len(response_bytes),
            response_bytes[:50].hex(),
        )

        return self._parse_trade_response(response_bytes)

    async def cancel_orders(self, order_numbers: list[int]) -> list[GrpcCancelResult]:
        """Cancel one or more broker orders via DeleteOrders gRPC.

        Input order numbers are sent as a packed repeated uint64 in a single
        wire call. Returns one ``GrpcCancelResult`` per input order number,
        in input order. On network failure every order reports the same
        underlying error string.

        Duplicate order numbers in the input list produce undefined
        per-row results: the server returns one acknowledgement per unique
        order_number and duplicates in the input get their cancellation_id
        copied from the first occurrence. Callers should pass unique order
        numbers.
        """
        jwt = await self._ensure_jwt()
        logger.info("gRPC cancel: order_numbers=%s", order_numbers)

        proto_msg = build_delete_orders_request(order_numbers)
        body_b64 = build_grpc_web_text_body(proto_msg)

        try:
            response_bytes = await self._grpc_call(GRPC_DELETE_ORDERS_ENDPOINT, body_b64, jwt=jwt)
        except httpx.HTTPError as e:
            logger.warning("gRPC cancel network error: %s", e, exc_info=True)
            return [GrpcCancelResult(success=False, order_number=n, error=str(e)) for n in order_numbers]

        return self._parse_cancel_response(response_bytes, order_numbers)

    # ------------------------------------------------------------------ #
    # CENTINELA (CAMBIOS.md, parche 3) — órdenes pendientes al contado
    # ------------------------------------------------------------------ #
    async def _pending_call(self, endpoint: str, proto_msg: bytes,
                            nombres_error: dict[int, str]) -> GrpcPendingOrderResult:
        """Una llamada de orden pendiente, con TODO lo que XTB contesta.

        A diferencia de `execute_order`, un cuerpo vacío no lanza: se devuelve
        `ambiguous=True` con el motivo de las cabeceras, y quien llama decide
        mirando la lista de órdenes. Para una orden pendiente eso basta —no hay
        dinero moviéndose hasta que se ejecute— y evita duplicarla a ciegas.
        """
        jwt = await self._ensure_jwt()
        nombre = endpoint.rsplit("/", 1)[-1]
        body_b64 = build_grpc_web_text_body(proto_msg)
        try:
            raw = await self._grpc_call(endpoint, body_b64, jwt=jwt)
        except httpx.HTTPError as e:
            logger.warning("gRPC %s network error: %s", nombre, e)
            return GrpcPendingOrderResult(success=False, ambiguous=True, rpc=nombre,
                                          error=f"red: {e}")
        if not raw:
            motivo = CENTINELA_ULTIMO_ERROR.get("motivo") or "respuesta vacía"
            # Un trailers-only con grpc-status != 0 es un RECHAZO, no una duda:
            # XTB ha contestado y ha dicho que no.
            rechazo = "(0)" not in motivo and motivo != "respuesta vacía"
            return GrpcPendingOrderResult(success=False, ambiguous=not rechazo, rpc=nombre,
                                          error=motivo)
        datos, status, message = split_grpc_web(raw)
        if status not in (0, None) and not datos:
            return GrpcPendingOrderResult(success=False, rpc=nombre, grpc_status=status,
                                          error=f"grpc-status {status}: {message or ''}")
        r = parse_pending_order_response(datos[0] if datos else b"", nombres_error)
        if r["order_id"] is not None and r["error"] is None:
            logger.info("gRPC %s OK: order_id=%s", nombre, r["order_id"])
            return GrpcPendingOrderResult(success=True, rpc=nombre, order_number=r["order_id"],
                                          trace_id=r["trace_id"], grpc_status=status or 0)
        detalle = r["error"] or "sin resultado"
        if r["error_message"] or r["error_code"] is not None:
            detalle += f" (code={r['error_code']}: {r['error_message']})"
        logger.warning("gRPC %s rechazada: %s", nombre, detalle)
        return GrpcPendingOrderResult(success=False, rpc=nombre, trace_id=r["trace_id"],
                                      grpc_status=status or 0, error=detalle,
                                      error_kind=r["error"])

    async def new_limit_order(self, instrument_id: int, volume: int, side: int,
                              price: float) -> GrpcPendingOrderResult:
        """Orden limitada (sin vencimiento). Venta: se ejecuta a `price` o mejor."""
        return await self._pending_call(
            GRPC_NEW_LIMIT_ORDER_ENDPOINT,
            build_new_pending_order(instrument_id, volume, side, price), ERRORES_NUEVA)

    async def new_stop_order(self, instrument_id: int, volume: int, side: int,
                             price: float) -> GrpcPendingOrderResult:
        """Orden stop (sin vencimiento). Venta: al tocar `price` sale a mercado."""
        return await self._pending_call(
            GRPC_NEW_STOP_ORDER_ENDPOINT,
            build_new_pending_order(instrument_id, volume, side, price), ERRORES_NUEVA)

    async def modify_limit_order(self, order_id: int, price: float) -> GrpcPendingOrderResult:
        return await self._pending_call(
            GRPC_MODIFY_LIMIT_ORDER_ENDPOINT,
            build_modify_pending_order(order_id, price), ERRORES_MODIFICAR)

    async def modify_stop_order(self, order_id: int, price: float) -> GrpcPendingOrderResult:
        return await self._pending_call(
            GRPC_MODIFY_STOP_ORDER_ENDPOINT,
            build_modify_pending_order(order_id, price), ERRORES_MODIFICAR)

    async def delete_orders_checked(self, order_numbers: list[int]) -> dict[int, GrpcCancelResult]:
        """DeleteOrders leyendo la rama success/error de CADA orden.

        `cancel_orders` (original) da por cancelada una orden con que su número
        vuelva en la respuesta, y vuelve también cuando XTB contesta
        ERROR_CODE_CANNOT_FIND_ORDER. Para emular un OCO eso no vale: hay que
        saber si la otra pata se canceló de verdad.
        """
        jwt = await self._ensure_jwt()
        body_b64 = build_grpc_web_text_body(build_delete_orders_request(order_numbers))
        try:
            raw = await self._grpc_call(GRPC_DELETE_ORDERS_ENDPOINT, body_b64, jwt=jwt)
        except httpx.HTTPError as e:
            return {n: GrpcCancelResult(success=False, order_number=n, error=f"red: {e}")
                    for n in order_numbers}
        if not raw:
            motivo = CENTINELA_ULTIMO_ERROR.get("motivo") or "respuesta vacía"
            return {n: GrpcCancelResult(success=False, order_number=n, error=motivo)
                    for n in order_numbers}
        datos, status, message = split_grpc_web(raw)
        res: dict[int, tuple] = {}
        for d in datos:
            res.update(parse_delete_orders_full(d)["resultados"])
        out: dict[int, GrpcCancelResult] = {}
        for n in order_numbers:
            ok, codigo, msg = res.get(n, (False, f"sin_resultado (grpc-status {status}: {message})", None))
            out[n] = GrpcCancelResult(success=ok, order_number=n, grpc_status=status or 0,
                                      error=None if ok else f"{codigo}{': ' + msg if msg else ''}")
        return out

    async def cash_orders_snapshot(self, timeout: float = 20.0) -> dict:
        """La foto de TODAS las órdenes de contado (OrderService/SubscribeOrderGroups).

        Es un stream de servidor: se abre, se lee hasta el primer mensaje
        completo —la foto, eventType SNAPSHOT— y se cierra. En gRPC-web-text
        cada mensaje llega como un trozo base64 independiente (con su propio
        relleno), así que se decodifica trozo a trozo y no todo de golpe.

        Lanza si no llega la foto: una lista vacía por un fallo sería decirle a
        la reconciliación "no hay órdenes", que es justo lo que no se puede
        confundir con "no lo sé".
        """
        import asyncio
        import re

        jwt = await self._ensure_jwt()
        headers = {
            "Content-Type": GRPC_WEB_TEXT_CONTENT_TYPE,
            "Accept": GRPC_WEB_TEXT_CONTENT_TYPE,
            "X-Grpc-Web": "1",
            "x-user-agent": "grpc-web-javascript/0.1",
            "Authorization": f"Bearer {jwt}",
        }
        body = build_grpc_web_text_body(b"")
        client = await self._ensure_http()
        texto, binario = "", b""

        async def leer() -> dict:
            nonlocal texto, binario
            async with client.stream("POST", GRPC_SUBSCRIBE_ORDER_GROUPS_ENDPOINT,
                                     content=body, headers=headers) as resp:
                resp.raise_for_status()
                if "grpc-status" in resp.headers and resp.headers.get("grpc-status") != "0":
                    raise RuntimeError(f"SubscribeOrderGroups: {_centinela_motivo(resp.headers)}")
                async for trozo in resp.aiter_text():
                    texto += trozo
                    # Trozos base64 completos: hasta un relleno '=' o múltiplo de 4.
                    partes = re.split(r"(?<==)(?=[^=])", texto)
                    completos, texto = partes[:-1], partes[-1]
                    if len(texto) % 4 == 0 and texto:
                        completos.append(texto)
                        texto = ""
                    for c in completos:
                        binario += base64.b64decode(c)
                    datos, status, message = split_grpc_web(binario)
                    if status not in (None, 0):
                        raise RuntimeError(f"SubscribeOrderGroups grpc-status {status}: {message}")
                    for d in datos:
                        foto = parse_order_groups(d)
                        if foto["event"] == "SNAPSHOT":
                            return foto
                    if status == 0:
                        break
            raise RuntimeError("SubscribeOrderGroups terminó sin enviar la foto (SNAPSHOT)")

        return await asyncio.wait_for(leer(), timeout=timeout)

    def _parse_cancel_response(self, response_bytes: bytes, order_numbers: list[int]) -> list[GrpcCancelResult]:
        """Parse a DeleteOrders response into one result per requested order.

        The wire carries one data frame per cancelled order plus one trailer
        frame. A non-zero grpc-status in the trailer applies to every
        requested order (broker-level rejection). An unpaired data frame
        (e.g. partial success) propagates fields from the frame's UUID+number
        onto the matching input order; any unmatched input orders get a
        ``grpc_status``-based failure result.
        """
        import struct

        grpc_status: int | None = None
        grpc_message: str | None = None
        data_frames: list[bytes] = []

        pos = 0
        while pos + 5 <= len(response_bytes):
            flag = response_bytes[pos]
            length = struct.unpack(">I", response_bytes[pos + 1 : pos + 5])[0]
            pos += 5
            if pos + length > len(response_bytes):
                break
            frame_data = response_bytes[pos : pos + length]
            pos += length

            if flag & 0x80:
                trailer_text = frame_data.decode("latin-1", errors="replace")
                for line in trailer_text.split("\r\n"):
                    if line.startswith("grpc-status:"):
                        with contextlib.suppress(ValueError):
                            grpc_status = int(line.split(":", 1)[1].strip())
                    elif line.startswith("grpc-message:"):
                        grpc_message = line.split(":", 1)[1].strip()
            else:
                data_frames.append(frame_data)

        # Build a lookup of parsed data frames by order_number
        parsed: dict[int, str | None] = {}
        for frame in data_frames:
            cancellation_id, order_number = parse_delete_orders_response(frame)
            if order_number is not None:
                parsed[order_number] = cancellation_id

        status = grpc_status if grpc_status is not None else 0
        results: list[GrpcCancelResult] = []
        for n in order_numbers:
            if status == 0 and n in parsed:
                results.append(
                    GrpcCancelResult(
                        success=True,
                        order_number=n,
                        cancellation_id=parsed[n],
                        grpc_status=0,
                    )
                )
            else:
                error_msg = grpc_message or f"gRPC cancel failed (status={status})"
                results.append(
                    GrpcCancelResult(
                        success=False,
                        order_number=n,
                        grpc_status=status,
                        error=error_msg,
                    )
                )
        return results

    def _parse_trade_response(self, response_bytes: bytes) -> GrpcTradeResult:
        """Parse gRPC-web trade response into GrpcTradeResult.

        Uses proper gRPC frame parsing instead of string matching to avoid
        false-positive success on rejected trades (e.g. 'grpc-status: 16'
        containing '0' as a substring in error details).
        """
        # Parse gRPC frames: flag 0x00 = data, flag 0x80 = trailers
        grpc_status: int | None = None
        grpc_message: str | None = None
        data_payload: bytes = b""

        pos = 0
        while pos + 5 <= len(response_bytes):
            flag = response_bytes[pos]
            import struct

            length = struct.unpack(">I", response_bytes[pos + 1 : pos + 5])[0]
            pos += 5
            if pos + length > len(response_bytes):
                break
            frame_data = response_bytes[pos : pos + length]
            pos += length

            if flag & 0x80:
                # Trailer frame — parse as HTTP/2 headers (key: value\r\n)
                trailer_text = frame_data.decode("latin-1", errors="replace")
                for line in trailer_text.split("\r\n"):
                    if line.startswith("grpc-status:"):
                        with contextlib.suppress(ValueError):
                            grpc_status = int(line.split(":", 1)[1].strip())
                    elif line.startswith("grpc-message:"):
                        grpc_message = line.split(":", 1)[1].strip()
            else:
                # Data frame
                data_payload = frame_data

        # Success requires explicit grpc-status 0 from trailer
        if grpc_status == 0:
            order_id, order_number = parse_new_market_order_response(data_payload)
            logger.info("Trade executed successfully via gRPC")
            return GrpcTradeResult(
                success=True,
                order_id=order_id,
                order_number=order_number,
                grpc_status=0,
            )

        # Error cases
        status = grpc_status if grpc_status is not None else 0
        response_text = response_bytes.decode("latin-1", errors="replace")
        if status == 7:
            detail = grpc_message or response_text
            error_msg = f"gRPC RBAC/auth denied: {detail}"
        elif grpc_message:
            error_msg = f"gRPC error: grpc-message: {grpc_message}"
        else:
            error_msg = f"gRPC order rejected: {response_text}"

        logger.error(error_msg)
        return GrpcTradeResult(success=False, grpc_status=status, error=error_msg)

    async def disconnect(self) -> None:
        """Clean up resources."""
        self._jwt = None
        self._jwt_timestamp = 0.0
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None
