import gzip
import json


FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
AUDIO_ONLY_RESPONSE = 0b1011
ERROR_INFORMATION = 0b1111

SERIALIZATION_RAW = 0b0000
SERIALIZATION_JSON = 0b0001
COMPRESSION_NONE = 0b0000
COMPRESSION_GZIP = 0b0001


def json_payload(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_packet(
    message_type, flags, serialization, compression, payload,
    event=None, sequence=None, session_id=None,
):
    optional = b""
    if sequence is not None:
        optional += sequence.to_bytes(4, "big", signed=True)
    if event is not None:
        optional += event.to_bytes(4, "big", signed=True)
    if session_id is not None:
        session_bytes = session_id.encode("utf-8")
        optional += len(session_bytes).to_bytes(4, "big") + session_bytes
    return (
        bytes([0x11, (message_type << 4) | flags, (serialization << 4) | compression, 0])
        + optional
        + len(payload).to_bytes(4, "big")
        + payload
    )


def make_event_packet(event, payload, session_id=None):
    return make_packet(
        FULL_CLIENT_REQUEST,
        0b0100,
        SERIALIZATION_JSON,
        COMPRESSION_NONE,
        json_payload(payload),
        event=event,
        session_id=session_id,
    )


def parse_packet(data):
    if len(data) < 8:
        raise ValueError("Volcengine packet is too short")
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = 4
    event = None
    sequence = None
    session_id = None
    error_code = None
    if message_type == ERROR_INFORMATION:
        if len(data) < 12:
            raise ValueError("Volcengine error packet is too short")
        error_code = int.from_bytes(data[offset:offset + 4], "big")
        offset += 4
    if flags & 0b0011:
        sequence = int.from_bytes(data[offset:offset + 4], "big", signed=True)
        offset += 4
    if flags & 0b0100:
        event = int.from_bytes(data[offset:offset + 4], "big", signed=True)
        offset += 4
        session_id_size = int.from_bytes(data[offset:offset + 4], "big")
        offset += 4
        session_id = data[offset:offset + session_id_size].decode("utf-8")
        offset += session_id_size
    payload_size = int.from_bytes(data[offset:offset + 4], "big")
    offset += 4
    payload = data[offset:offset + payload_size]
    if len(payload) != payload_size:
        raise ValueError("Volcengine packet payload is truncated")
    if compression == COMPRESSION_GZIP:
        payload = gzip.decompress(payload)
    if serialization == SERIALIZATION_JSON and payload:
        payload = json.loads(payload)
    return {
        "message_type": message_type,
        "flags": flags,
        "event": event,
        "sequence": sequence,
        "session_id": session_id,
        "error_code": error_code,
        "payload": payload,
    }
