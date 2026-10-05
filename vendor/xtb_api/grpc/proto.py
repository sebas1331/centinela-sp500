"""Minimal protobuf encoder/decoder for XTB gRPC-web protocol.

No external dependencies — manual varint/length-delimited encoding
matching the wire format observed in HAR captures from xStation5.
"""

from __future__ import annotations

import base64
import re
import struct


def encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    parts: list[int] = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def decode_varint(data: bytes, pos: int = 0) -> tuple[int, int]:
    """Decode a varint at position. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Truncated varint")


def encode_field_varint(field_num: int, value: int) -> bytes:
    """Encode a varint field (wire type 0)."""
    tag = (field_num << 3) | 0  # wire type 0 = varint
    return encode_varint(tag) + encode_varint(value)


def encode_field_bytes(field_num: int, data: bytes) -> bytes:
    """Encode a length-delimited field (wire type 2)."""
    tag = (field_num << 3) | 2  # wire type 2 = length-delimited
    return encode_varint(tag) + encode_varint(len(data)) + data


def _encode_price(value: int, scale: int) -> bytes:
    """Encode a Price protobuf sub-message: { field 1: value, field 2: scale }."""
    return encode_field_varint(1, value) + encode_field_varint(2, scale)


def build_new_market_order(
    instrument_id: int,
    volume: int,
    side: int,
    *,
    stop_loss_value: int | None = None,
    stop_loss_scale: int | None = None,
    take_profit_value: int | None = None,
    take_profit_scale: int | None = None,
) -> bytes:
    """Build NewMarketOrder protobuf message.

    Args:
        instrument_id: gRPC instrument ID (e.g., 9438 for CIG.PL)
        volume: Number of shares
        side: 1=BUY, 2=SELL
        stop_loss_value: SL price as integer (e.g., 10850 for 1.0850 with scale=4)
        stop_loss_scale: SL price scale (decimal places)
        take_profit_value: TP price as integer
        take_profit_scale: TP price scale (decimal places)

    Returns:
        Serialized protobuf bytes

    Wire format (from HAR analysis):
        Field 1 (varint): instrument_id
        Field 2 (bytes):  order {
            Field 2 (bytes): volume { Field 1 (varint): value }
            Field 3 (bytes): stoploss { Field 1 (bytes): price { value, scale } }
            Field 4 (bytes): takeprofit { Field 1 (bytes): price { value, scale } }
        }
        Field 3 (varint): side
    """
    # Inner: volume message — field 1 = value
    volume_msg = encode_field_varint(1, volume)
    # Middle: order message — field 2 = volume
    order_msg = encode_field_bytes(2, volume_msg)

    # Optional SL: order field 3 = stoploss { field 1 = price { value, scale } }
    if stop_loss_value is not None and stop_loss_scale is not None:
        price_msg = _encode_price(stop_loss_value, stop_loss_scale)
        sl_msg = encode_field_bytes(1, price_msg)  # stoploss.price
        order_msg += encode_field_bytes(3, sl_msg)

    # Optional TP: order field 4 = takeprofit { field 1 = price { value, scale } }
    if take_profit_value is not None and take_profit_scale is not None:
        price_msg = _encode_price(take_profit_value, take_profit_scale)
        tp_msg = encode_field_bytes(1, price_msg)  # takeprofit.price
        order_msg += encode_field_bytes(4, tp_msg)

    # Outer: full message
    return encode_field_varint(1, instrument_id) + encode_field_bytes(2, order_msg) + encode_field_varint(3, side)


def build_grpc_frame(proto_msg: bytes) -> bytes:
    """Wrap protobuf message in a gRPC-web frame.

    Frame format: 1 byte flag + 4 bytes big-endian length + payload
    Flag 0 = data frame (uncompressed)
    """
    return struct.pack(">BI", 0, len(proto_msg)) + proto_msg


def build_grpc_web_text_body(proto_msg: bytes) -> str:
    """Build gRPC-web-text body (base64-encoded gRPC frame)."""
    frame = build_grpc_frame(proto_msg)
    return base64.b64encode(frame).decode("ascii")


def build_create_access_token_request(tgt: str, account_number: str, account_server: str) -> bytes:
    """Build CreateAccessTokenRequest protobuf.

    Proto structure (discovered via proto classes in xStation5):
      message CreateAccessTokenRequest {
          string tgt = 1;          // TGT/ST cookie value (optional if CASTGT cookie present)
          Account account = 2;     // Account info
      }
      message Account {
          uint64 number = 1;       // e.g. 51984891 (varint, NOT string)
          string server = 2;       // e.g. "XS-real1"
      }

    The JWT returned will contain:
      - pid: person ID
      - acn: account number (REQUIRED for trading!)
      - acs: account server (REQUIRED for trading!)
    """
    # Build inner Account message
    # account_number is varint-encoded (field type 0), not length-delimited
    account_msg = encode_field_varint(1, int(account_number)) + encode_field_bytes(2, account_server.encode("utf-8"))
    # Build outer CreateAccessTokenRequest
    return encode_field_bytes(1, tgt.encode("utf-8")) + encode_field_bytes(2, account_msg)


def build_delete_orders_request(order_numbers: list[int]) -> bytes:
    """Build DeleteOrders protobuf message.

    Wire format (from HAR analysis, single-cancel case):
        Field 1 (bytes, wire type 2): packed repeated uint64 — concatenated
            varints of the broker order numbers to cancel. No inner tags.

    For ``[872077045]`` this produces ``0a 05 f5 ad eb 9f 03`` (7 bytes).
    """
    packed = b"".join(encode_varint(n) for n in order_numbers)
    return encode_field_bytes(1, packed)


def parse_grpc_frames(data: bytes) -> list[bytes]:
    """Parse one or more gRPC-web frames from response data.

    Returns list of payload bytes (one per frame).
    """
    frames: list[bytes] = []
    pos = 0
    while pos + 5 <= len(data):
        _flag = data[pos]
        length = struct.unpack(">I", data[pos + 1 : pos + 5])[0]
        pos += 5
        if pos + length > len(data):
            break
        frames.append(data[pos : pos + length])
        pos += length
    return frames


def parse_proto_fields(data: bytes) -> dict[int, list[tuple[int, bytes | int]]]:
    """Parse protobuf fields into {field_num: [(wire_type, value), ...]}.

    Wire type 0 → value is int (varint)
    Wire type 2 → value is bytes (length-delimited)
    Wire type 5 → value is bytes (4 bytes, fixed32)
    Wire type 1 → value is bytes (8 bytes, fixed64)
    """
    fields: dict[int, list[tuple[int, bytes | int]]] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except ValueError:
            break
        wire_type = tag & 0x07
        field_num = tag >> 3

        if wire_type == 0:  # varint
            value, pos = decode_varint(data, pos)
            fields.setdefault(field_num, []).append((wire_type, value))
        elif wire_type == 2:  # length-delimited
            length, pos = decode_varint(data, pos)
            value_bytes = data[pos : pos + length]
            pos += length
            fields.setdefault(field_num, []).append((wire_type, value_bytes))
        elif wire_type == 5:  # fixed32
            value_bytes = data[pos : pos + 4]
            pos += 4
            fields.setdefault(field_num, []).append((wire_type, value_bytes))
        elif wire_type == 1:  # fixed64
            value_bytes = data[pos : pos + 8]
            pos += 8
            fields.setdefault(field_num, []).append((wire_type, value_bytes))
        else:
            break  # Unknown wire type

    return fields


def extract_jwt(data: bytes) -> str | None:
    """Extract JWT token from gRPC response bytes.

    Searches for the JWT pattern in the raw bytes (works regardless
    of protobuf nesting level).
    """
    text = data.decode("latin-1")
    match = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text)
    return match.group(0) if match else None


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _parse_uuid_and_order_number(payload: bytes) -> tuple[str | None, int | None]:
    """Shared parser for the (UUID, order_number) response shape.

    Used by both NewMarketOrder and DeleteOrders responses:

        { field 1: UUID string,
          field 2: { field 1: uint64 order_number, ... } }

    Field 1 is parsed as UTF-8 first; if it doesn't decode cleanly the
    helper falls back to a UUID regex sweep over the entire payload so
    wire-shape drift doesn't silently lose the order_id.
    """
    if not payload:
        return None, None

    fields = parse_proto_fields(payload)
    order_id: str | None = None
    order_number: int | None = None

    field1 = fields.get(1)
    if field1:
        _, raw = field1[0]
        if isinstance(raw, bytes):
            try:
                candidate = raw.decode("utf-8")
                # Accept it as the order_id regardless of format — XTB has used
                # UUIDs consistently so far, but the parser shouldn't reject
                # valid strings just because they don't match a pattern.
                order_id = candidate
            except UnicodeDecodeError:
                order_id = None

    field2 = fields.get(2)
    if field2:
        _, nested = field2[0]
        if isinstance(nested, bytes):
            inner = parse_proto_fields(nested)
            inner1 = inner.get(1)
            if inner1:
                _, v = inner1[0]
                if isinstance(v, int):
                    order_number = v

    # Regex fallback: if we failed to pull order_id from field 1 (wire drift),
    # scan the raw bytes for a UUID.
    if order_id is None:
        match = _UUID_RE.search(payload.decode("latin-1"))
        if match:
            order_id = match.group(0)

    return order_id, order_number


def parse_new_market_order_response(payload: bytes) -> tuple[str | None, int | None]:
    """Parse a NewMarketOrder data-frame payload to ``(order_id, order_number)``."""
    return _parse_uuid_and_order_number(payload)


def parse_delete_orders_response(payload: bytes) -> tuple[str | None, int | None]:
    """Parse a DeleteOrders data-frame payload to ``(cancellation_id, order_number)``."""
    return _parse_uuid_and_order_number(payload)


# Side constants for gRPC protocol.
# WARNING: These differ from WebSocket Xs6Side enum (BUY=0, SELL=1).
# Do NOT interchange with Xs6Side values — wrong side will be sent.
SIDE_BUY = 1  # gRPC only — WebSocket uses Xs6Side.BUY=0
SIDE_SELL = 2  # gRPC only — WebSocket uses Xs6Side.SELL=1

# Content type for gRPC-web-text (base64 encoded)
GRPC_WEB_TEXT_CONTENT_TYPE = "application/grpc-web-text"

# gRPC-web endpoints
GRPC_BASE_URL = "https://ipax.xtb.com"
GRPC_AUTH_ENDPOINT = f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.auth.v2.AuthService/CreateAccessToken"
GRPC_NEW_ORDER_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/NewMarketOrder"
)
GRPC_CONFIRM_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingconfirmation.v1"
    ".CashTradingConfirmationService/SubscribeNewMarketOrderConfirmation"
)
GRPC_CLOSE_POSITION_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/CloseSinglePosition"
)
GRPC_DELETE_ORDERS_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/DeleteOrders"
)


# --------------------------------------------------------------------------- #
# CENTINELA (CAMBIOS.md, parche 3) — órdenes pendientes de acciones al contado
# --------------------------------------------------------------------------- #
# Esquema sacado de los descriptores protobuf que la propia web de xStation 5
# lleva embebidos (microfrontend `trading-web-cmp`, fichero
# `cash-trading-neworder-service-proto/v1/*.proto`, paquete
# `pl.xtb.ipax.pub.grpc.cashtradingneworder.v1`). Mismo servicio y misma vía que
# `NewMarketOrder`, que es lo que este cliente ya usaba:
#
#   message NewLimitOrderRequest {          message NewStopOrderRequest {
#     int32  instrumentId   = 1;              int32  instrumentId    = 1;
#     Size   size           = 2;              Size   size            = 2;
#     Side   side           = 3;              Side   side            = 3;
#     Price  limitPrice     = 4;              Price  activationPrice = 4;
#     optional int64 expirationDate = 5;      optional int64 expirationDate = 5;
#     bool   process_eth    = 6;              bool   process_eth     = 6;
#   }                                       }
#   message ModifyLimitOrderRequest { int64 orderId = 1; Price limitPrice = 2;
#                                     optional int64 expirationDate = 3; ... }
#   message ModifyStopOrderRequest  { int64 orderId = 1; Price activationPrice = 2;
#                                     optional int64 expirationDate = 3; ... }
#   message Size   { oneof value { int64 amount = 1; Volume volume = 2; } }
#   message Volume { int64 value = 1; int32 scale = 2; }
#   message Price  { int64 value = 1; int32 scale = 2; }
#
#   Respuesta (las cuatro): { string traceId = 1;
#                             oneof result { Success success = 2; Error error = 3; } }
#     Success { int64 orderId = 1; }
#     Error   { oneof error { ... } }   (nombres por RPC: ERRORES_NUEVA / ERRORES_MODIFICAR)
#
# Sin `expirationDate` la orden no vence: es lo que hace la web con el
# interruptor "Vencimiento de la orden" apagado (el campo solo se rellena si
# viene una fecha).

GRPC_NEW_LIMIT_ORDER_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/NewLimitOrder"
)
GRPC_NEW_STOP_ORDER_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/NewStopOrder"
)
GRPC_MODIFY_LIMIT_ORDER_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/ModifyLimitOrder"
)
GRPC_MODIFY_STOP_ORDER_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService/ModifyStopOrder"
)

#: Nombres del `oneof error` de NewLimitOrderResponse / NewStopOrderResponse.
ERRORES_NUEVA = {1: "otherError", 2: "kidConsentRequiredForETF",
                 3: "invalidParameter", 4: "accountNotTradeable", 5: "noMoney",
                 6: "orderValueLowerThanMinimum"}
#: Nombres del `oneof error` de ModifyLimitOrderResponse / ModifyStopOrderResponse.
ERRORES_MODIFICAR = {1: "otherError", 2: "invalidParameter",
                     3: "accountNotTradeable", 4: "noMoney",
                     5: "orderValueLowerThanMinimum", 6: "orderNotExists"}
#: DeleteOrderResponse.Error.code
ERRORES_CANCELAR = {0: "ERROR_CODE_NOT_SET", 1: "ERROR_CODE_CANNOT_FIND_ORDER",
                    2: "ERROR_CODE_UNEXPECTED_ERROR", 3: "ERROR_CODE_UNKNOWN_RESPONSE",
                    4: "ERROR_CODE_INVALID_PARAMETER"}


def precio_a_proto(precio: float, max_decimales: int = 4) -> tuple[int, int]:
    """1.0850 -> (10850, 4); 495.49 -> (49549, 2); 12 -> (12, 0).

    Por la representación decimal y no por aritmética binaria: 0.1+0.2 no es
    0.3 en coma flotante, y un precio de orden no puede salir con un céntimo
    de más. Se quitan los ceros de cola para mandar la escala mínima, que es lo
    que hace la web.
    """
    from decimal import ROUND_HALF_UP, Decimal

    if precio is None or precio <= 0:
        raise ValueError(f"precio de orden no válido: {precio!r}")
    d = Decimal(str(precio)).quantize(Decimal(1).scaleb(-max_decimales), rounding=ROUND_HALF_UP)
    d = d.normalize()
    exp = d.as_tuple().exponent
    escala = max(0, -int(exp))
    valor = int(d.scaleb(escala))
    return valor, escala


def _encode_size_volume(volume: int) -> bytes:
    """Size { volume = 2: Volume { value = 1; scale = 2 (0: se omite) } }."""
    return encode_field_bytes(2, encode_field_varint(1, volume))


def build_new_pending_order(instrument_id: int, volume: int, side: int, precio: float) -> bytes:
    """NewLimitOrderRequest / NewStopOrderRequest: la forma es idéntica, solo
    cambia el nombre del campo 4 (limitPrice / activationPrice). Sin
    expirationDate: sin vencimiento."""
    valor, escala = precio_a_proto(precio)
    return (
        encode_field_varint(1, instrument_id)
        + encode_field_bytes(2, _encode_size_volume(volume))
        + encode_field_varint(3, side)
        + encode_field_bytes(4, _encode_price(valor, escala))
    )


def build_modify_pending_order(order_id: int, precio: float) -> bytes:
    """ModifyLimitOrderRequest / ModifyStopOrderRequest (mismo cableado)."""
    valor, escala = precio_a_proto(precio)
    return encode_field_varint(1, order_id) + encode_field_bytes(2, _encode_price(valor, escala))


def split_grpc_web(response_bytes: bytes) -> tuple[list[bytes], int | None, str | None]:
    """Separa una respuesta gRPC-web en (frames de datos, grpc-status, grpc-message)."""
    datos: list[bytes] = []
    status: int | None = None
    message: str | None = None
    pos = 0
    while pos + 5 <= len(response_bytes):
        flag = response_bytes[pos]
        length = struct.unpack(">I", response_bytes[pos + 1 : pos + 5])[0]
        pos += 5
        if pos + length > len(response_bytes):
            break
        frame = response_bytes[pos : pos + length]
        pos += length
        if flag & 0x80:
            for line in frame.decode("latin-1", errors="replace").split("\r\n"):
                if line.startswith("grpc-status:"):
                    try:
                        status = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("grpc-message:"):
                    message = line.split(":", 1)[1].strip()
        else:
            datos.append(frame)
    return datos, status, message


def _texto(b) -> str:
    return b.decode("utf-8", errors="replace") if isinstance(b, bytes) else str(b)


def parse_pending_order_response(payload: bytes, nombres_error: dict[int, str]) -> dict:
    """Respuesta de New/Modify Limit/Stop -> dict con lo que importa.

    {"trace_id", "order_id" (si success), "error" (nombre del oneof),
     "error_code", "error_message" (solo otherError)}
    """
    campos = parse_proto_fields(payload)
    out: dict = {"trace_id": None, "order_id": None, "error": None,
                 "error_code": None, "error_message": None}
    if 1 in campos:
        out["trace_id"] = _texto(campos[1][0][1])
    if 2 in campos:
        exito = parse_proto_fields(campos[2][0][1]) if isinstance(campos[2][0][1], bytes) else {}
        if 1 in exito and isinstance(exito[1][0][1], int):
            out["order_id"] = exito[1][0][1]
        else:
            out["order_id"] = 0  # éxito sin número: no debería pasar, pero es éxito
    elif 3 in campos:
        err = parse_proto_fields(campos[3][0][1]) if isinstance(campos[3][0][1], bytes) else {}
        if err:
            num = next(iter(err))
            out["error"] = nombres_error.get(num, f"error#{num}")
            interno = err[num][0][1]
            if num == 1 and isinstance(interno, bytes):  # OtherError {code, message}
                oe = parse_proto_fields(interno)
                if 1 in oe:
                    out["error_code"] = oe[1][0][1]
                if 2 in oe:
                    out["error_message"] = _texto(oe[2][0][1])
        else:
            out["error"] = "error"
    return out


def parse_delete_orders_full(payload: bytes) -> dict:
    """DeleteOrdersResponse completo -> {"trace_id", "resultados": {orderId: (ok, codigo, mensaje)}}.

    El parser original (`parse_delete_orders_response`) solo leía el PRIMER
    resultado y daba por buena la cancelación con que viniera el número de
    orden, que viene TAMBIÉN cuando XTB contesta "no encuentro esa orden" (la
    rama `error = 3`). Este distingue las dos ramas.
    """
    campos = parse_proto_fields(payload)
    out: dict = {"trace_id": None, "resultados": {}}
    if 1 in campos:
        out["trace_id"] = _texto(campos[1][0][1])
    for _, sub in campos.get(2, []):
        if not isinstance(sub, bytes):
            continue
        r = parse_proto_fields(sub)
        oid = r.get(1, [(0, None)])[0][1]
        if 2 in r:
            out["resultados"][oid] = (True, None, None)
        elif 3 in r:
            e = parse_proto_fields(r[3][0][1]) if isinstance(r[3][0][1], bytes) else {}
            codigo = e.get(1, [(0, 0)])[0][1]
            msg = _texto(e[2][0][1]) if 2 in e else None
            out["resultados"][oid] = (False, ERRORES_CANCELAR.get(codigo, str(codigo)), msg)
        else:
            out["resultados"][oid] = (False, "sin_resultado", None)
    return out


# --------------------------------------------------------------------------- #
# CENTINELA (CAMBIOS.md, parche 3) — la lista de órdenes de contado
# --------------------------------------------------------------------------- #
# `getAllOrders` del WebSocket (el que usaba el cliente) NO devuelve las órdenes
# de acciones al contado: medido el 2026-10-05, con una limitada y una stop
# aceptadas sobre F.US, devolvió cero. La web las lista con este servicio
# (microfrontend `portfolio`, `order-service-proto/v1`), que es un stream: el
# primer mensaje es la foto completa (eventType = SNAPSHOT) y luego llegan
# cambios. Aquí solo se lee la foto.
#
#   OrderGroupEvent { EventType eventType = 1; repeated {int32 key=1; OrderGroup value=2} orderGroups = 2; }
#   OrderGroup      { int32 instrumentId = 1; oneof { CashOrderGroup cash = 2; CfdOrderGroup cfd = 3; } }
#   CashOrderGroup  { Instrument instrument = 1; repeated {int64 key=1; CashOrder value=2} orders = 2; }
#   CashOrder       { int64 signed_order_id = 1; OrderBaseInfo baseInfo = 2;
#                     CashOrderDetails details = 3; int64 unsigned_order_id = 4; }
#   OrderBaseInfo   { OrderSide side=1; OrderType type=2; OrderName name=3;
#                     oneof { Volume volume=4; int64 amount=5; } string orderPrice=6;
#                     optional int64 expiration=7; optional double marketPrice=8; }
#   CashOrderDetails{ int64 nominalValue=1; int64 createTime=2; Origin origin=3; ...
#                     OrderStatus orderStatus=7; bool is_extended_trading_hours=8; }
#   Instrument      { int32 idInstrument=1; string ticker=2; ... TradingRules tradingRules=12; }
#   TradingRules    { ...; LimitOrder limitOrder=5; StopOrder stopOrder=6; }
#   LimitOrder/StopOrder { bool isDeleteAllowed=6; bool isModifyAllowed=7; }

GRPC_SUBSCRIBE_ORDER_GROUPS_ENDPOINT = (
    f"{GRPC_BASE_URL}/pl.xtb.ipax.pub.grpc.order.v1.OrderService/SubscribeOrderGroups"
)

ORDER_SIDE = {0: "not_set", 1: "buy", 2: "sell"}
ORDER_TYPE = {0: "not_set", 1: "market", 2: "limit", 3: "stop"}
ORDER_NAME = {0: "NOT_SET", 1: "BUY", 2: "SELL", 3: "BUY_STOP", 4: "SELL_STOP",
              5: "BUY_LIMIT", 6: "SELL_LIMIT"}
ORDER_STATUS = {0: "NOT_SET", 1: "PENDING_NEW", 2: "NEW", 3: "ACCEPTED", 4: "REJECTED",
                5: "PENDING_CANCEL", 6: "CANCELED", 7: "PENDING_MODIFY", 8: "EXPIRED",
                9: "FILLED", 10: "PARTIAL_FILLED"}
#: Estados en los que la orden sigue viva en el servidor de XTB.
ORDER_STATUS_VIVA = {"PENDING_NEW", "NEW", "ACCEPTED", "PENDING_MODIFY", "PARTIAL_FILLED"}


def _uno(campos: dict, n: int, defecto=None):
    v = campos.get(n)
    return v[0][1] if v else defecto


def _sub(campos: dict, n: int) -> dict:
    v = _uno(campos, n)
    return parse_proto_fields(v) if isinstance(v, bytes) else {}


def _double(b) -> float | None:
    return struct.unpack("<d", b)[0] if isinstance(b, bytes) and len(b) == 8 else None


def _signed64(v: int | None) -> int | None:
    if v is None:
        return None
    return v - (1 << 64) if v >= (1 << 63) else v


def parse_order_groups(payload: bytes) -> dict:
    """OrderGroupEvent -> {"event": "SNAPSHOT"|"UPDATE"|..., "orders": [dict], "rules": {ticker: dict}}."""
    campos = parse_proto_fields(payload)
    out: dict = {"event": {0: "NOT_SET", 1: "SNAPSHOT", 2: "UPDATE"}.get(_uno(campos, 1, 0), "?"),
                 "orders": [], "rules": {}}
    for _, entrada in campos.get(2, []):
        if not isinstance(entrada, bytes):
            continue
        grupo = _sub(parse_proto_fields(entrada), 2)
        cash = _sub(grupo, 2)
        if not cash:
            continue                                   # CFD: este sistema no los opera
        instr = _sub(cash, 1)
        ticker = _texto(_uno(instr, 2, b""))
        reglas = _sub(instr, 12)
        out["rules"][ticker] = {
            rol: {"delete": bool(_uno(_sub(reglas, n), 6, 0)),
                  "modify": bool(_uno(_sub(reglas, n), 7, 0))}
            for rol, n in (("limit", 5), ("stop", 6))
        }
        for _, oe in cash.get(2, []):
            if not isinstance(oe, bytes):
                continue
            o = _sub(parse_proto_fields(oe), 2)
            base, det = _sub(o, 2), _sub(o, 3)
            vol = _sub(base, 4)
            escala = _uno(vol, 2, 0) or 0
            volumen = (_uno(vol, 1, 0) or 0) / (10 ** escala) if vol else None
            precio = _texto(_uno(base, 6, b"")) or None
            out["orders"].append({
                "order_id": _uno(o, 4) or _signed64(_uno(o, 1)),
                "signed_order_id": _signed64(_uno(o, 1)),
                "instrument_id": _uno(grupo, 1) or _uno(instr, 1),
                "symbol": ticker,
                "side": ORDER_SIDE.get(_uno(base, 1, 0), "?"),
                "type": ORDER_TYPE.get(_uno(base, 2, 0), "?"),
                "name": ORDER_NAME.get(_uno(base, 3, 0), "?"),
                "volume": volumen,
                "amount": _uno(base, 5),
                "price": float(precio) if precio else None,
                "expiration": _uno(base, 7),
                "market_price": _double(_uno(base, 8)),
                "status": ORDER_STATUS.get(_uno(det, 7, 0), "?"),
                "create_time": _uno(det, 2),
                "origin": _uno(det, 3),
            })
    return out
