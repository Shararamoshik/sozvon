"""The worker wire format is bounded JSON, never pickle or line-based text."""

import importlib
import importlib.util
import io
import struct

import pytest

from sozvon.runtime import framing


def wire(data):
    return struct.pack(">I", len(data)) + data


@pytest.mark.parametrize(
    "raw",
    [
        b"\x00",
        b"\x00\x00\x00",
        struct.pack(">I", 20) + b"{}",
        struct.pack(">I", 0),
        struct.pack(">I", 1024 * 1024 + 1),
        wire(b"not-json"),
        wire(b"\xff"),
        wire(b"[]"),
        wire(b"null"),
        wire(b'{"x":NaN}'),
        wire(b'{"x":1,"x":2}'),
        wire(b'{"x":1e9999}'),
        wire(b'{"x":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"),
    ],
    ids=[
        "short-header",
        "short-header-3",
        "short-body",
        "empty-frame",
        "oversize",
        "invalid-json",
        "invalid-utf8",
        "array",
        "null",
        "nan",
        "duplicate-key",
        "infinite-float",
        "deep-nesting",
    ],
)
def test_bad_frames_are_rejected_with_protocol_error(raw):
    assert hasattr(framing, "FrameError"), "bounded framing errors are not implemented"
    with pytest.raises(framing.FrameError):
        framing.read_frame(io.BytesIO(raw))


@pytest.mark.parametrize("message", [[1], {"x": float("nan")}, {"x": "x" * 1024 * 1024}])
def test_invalid_outgoing_frame_is_not_written(message):
    assert hasattr(framing, "FrameError"), "bounded framing errors are not implemented"
    stream = io.BytesIO()
    with pytest.raises(framing.FrameError):
        framing.write_frame(stream, message)
    assert stream.getvalue() == b""


class FragmentedStream(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 2))

    def write(self, data):
        return super().write(data[:2])


def test_partial_io_is_completed_and_maximum_frame_is_allowed():
    message = {"s": "x" * (1024 * 1024 - 8)}
    stream = FragmentedStream()
    framing.write_frame(stream, message)
    assert len(stream.getvalue()) == 4 + 1024 * 1024
    stream.seek(0)
    assert framing.read_frame(stream) == message


def test_outgoing_deep_json_is_rejected_before_writing():
    value = {}
    for _ in range(70):
        value = {"nested": value}
    stream = io.BytesIO()
    with pytest.raises(framing.FrameError):
        framing.write_frame(stream, value)
    assert stream.getvalue() == b""


def test_frame_round_trip_uses_four_byte_big_endian_utf8_json():
    assert importlib.util.find_spec("sozvon.runtime") is not None, "runtime is not implemented"
    framing = importlib.import_module("sozvon.runtime.framing")
    event = {"type": "progress", "stage": "Распознавание", "completed": 3}
    stream = io.BytesIO()
    framing.write_frame(stream, event)
    raw = stream.getvalue()
    assert struct.unpack(">I", raw[:4])[0] == len(raw[4:])
    assert "Распознавание".encode() in raw[4:]
    assert framing.read_frame(io.BytesIO(raw)) == event
    assert framing.read_frame(io.BytesIO()) is None
