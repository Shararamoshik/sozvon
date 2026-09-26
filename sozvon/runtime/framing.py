"""Bounded JSON objects over four-byte big-endian length-prefixed streams.

An empty stream is clean EOF; EOF inside a header/body is a protocol error.
No resynchronisation is attempted after a malformed frame.
"""

import json
import math
import struct
from typing import BinaryIO

MAX_FRAME_SIZE = 1024 * 1024


class FrameError(ValueError):
    """Malformed, truncated or oversized protocol frame."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FrameError("Повторяющееся поле JSON")
        result[key] = value
    return result


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise FrameError("Недопустимое число JSON")
    return number


def _constant(value):
    raise FrameError("Недопустимое число JSON")


def _check_depth(data: bytes) -> None:
    depth = 0
    quoted = escaped = False
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 64:
                raise FrameError("Слишком большая вложенность JSON")
        elif byte in (93, 125):
            depth -= 1


def encode_frame(message: dict) -> bytes:
    if not isinstance(message, dict):
        raise FrameError("Кадр должен быть объектом JSON")
    try:
        data = json.dumps(
            message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise FrameError("Невозможно сериализовать кадр JSON") from exc
    if not 0 < len(data) <= MAX_FRAME_SIZE:
        raise FrameError("Кадр превышает предел 1 MiB")
    _check_depth(data)
    return struct.pack(">I", len(data)) + data


def write_bytes(stream: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if written is None or written <= 0:
            raise OSError("Канал не принимает данные")
        view = view[written:]
    stream.flush()


def write_frame(stream: BinaryIO, message: dict) -> None:
    write_bytes(stream, encode_frame(message))


def _read_exact(stream: BinaryIO, size: int, *, clean_eof: bool = False) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            if not data and clean_eof:
                return None
            raise FrameError("Незавершённый кадр")
        data.extend(chunk)
    return bytes(data)


def read_frame(stream: BinaryIO) -> dict | None:
    header = _read_exact(stream, 4, clean_eof=True)
    if header is None:
        return None
    size = struct.unpack(">I", header)[0]
    if not 0 < size <= MAX_FRAME_SIZE:
        raise FrameError("Недопустимый размер кадра; предел 1 MiB")
    data = _read_exact(stream, size)
    assert data is not None
    _check_depth(data)
    try:
        message = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object,
            parse_float=_number,
            parse_constant=_constant,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FrameError("Некорректный JSON в кадре") from exc
    if not isinstance(message, dict):
        raise FrameError("Кадр должен быть объектом JSON")
    return message
