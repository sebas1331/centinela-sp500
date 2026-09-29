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
