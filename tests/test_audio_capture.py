"""Capture test doubles are dormant until start(), just like native streams."""

import struct
import sys
import threading
from types import SimpleNamespace

import av
import pytest


class FakeStream:
    def __init__(self, callback, data):
        self.callback = callback
        self.data = data
        self.active = False
        self.closed = False
        self.starts = 0

    def push(self, data=None):
        if self.active:
            self.callback(self.data if data is None else data, None)

    def start(self):
        self.active = True
        self.starts += 1
        self.push()

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, source, rate, channels, amplitude):
        self.source = source
        self.rate = rate
        self.channels = channels
        self.data = struct.pack("<h", amplitude) * rate * channels
        self.streams = []
        self.checked = []
        self.closed = False

    def devices(self):
        return [
            {"id": f"{self.source}:0", "name": "not-default"},
            {"id": f"{self.source}:1", "name": "system-default"},
        ]

    def default_device(self):
        return f"{self.source}:1"

    def format(self, device):
        self.checked.append(device)
        return self.rate, self.channels

    def open(self, device, rate, channels, callback):
        assert (rate, channels) == (self.rate, self.channels)
        assert device == self.default_device()
        stream = FakeStream(callback, self.data)
        self.streams.append(stream)
        return stream

    def close(self):
        self.closed = True


def fake_backends():
    return {
        "mic": FakeBackend("mic", 8000, 1, 8192),
        "system": FakeBackend("system", 16000, 2, 4096),
    }


@pytest.fixture
def capture_clock(monkeypatch):
    from sozvon.audio import capture

    clock = [100.0]
    monkeypatch.setattr(capture, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def test_capture_explicitly_starts_and_finalizes_separate_formats(tmp_path, capture_clock):
    from sozvon.audio.capture import CaptureService

    backends = fake_backends()
    service = CaptureService({"output_dir": str(tmp_path)}, backends=backends)
    service.start()
    result = service.stop()
    assert result["duration_ms"] == 1000
    assert result["warnings"] == []
    assert [t["source"] for t in result["tracks"]] == ["mic", "system"]
    for track in result["tracks"]:
        backend = backends[track["source"]]
        assert backend.checked == [backend.default_device()]
        assert backend.closed
        assert backend.streams[0].starts == 1
        assert backend.streams[0].closed
        with av.open(track["path"]) as container:
            assert container.duration == 1_000_000
            stream = container.streams.audio[0]
            assert stream.codec_context.sample_rate == backend.rate
            assert stream.codec_context.channels == backend.channels
            frames = list(container.decode(stream))
            assert sum(frame.samples for frame in frames) == backend.rate
        assert track["duration_ms"] == 1000
        assert track["peak"] == pytest.approx(0.25 if track["source"] == "mic" else 0.125)
    assert service.stop() == result


def test_sequential_start_offsets_align_simultaneous_tone_in_real_wavs(tmp_path, monkeypatch):
    import numpy as np

    from sozvon.audio import capture

    clock = [100.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    fake = fake_backends()
    for source, backend in fake.items():
        offset = 0 if source == "mic" else 0.2
        time_axis = offset + np.arange(round((1 - offset) * backend.rate)) / backend.rate
        pcm = np.where(
            (time_axis >= 0.4) & (time_axis < 0.6),
            12000 * np.sin(2 * np.pi * 440 * time_axis),
            0,
        ).astype("<i2")
        backend.data = np.repeat(pcm, backend.channels).tobytes()
    original = fake["mic"].open

    def slow_first_start(*args):
        stream = original(*args)
        start = stream.start

        def start_then_return_late():
            start()  # microphone captures at common epoch
            clock[0] += 0.2  # second native start cannot run before this returns

        stream.start = start_then_return_late
        return stream

    fake["mic"].open = slow_first_start
    service = capture.CaptureService({"output_dir": str(tmp_path)}, backends=fake)
    service.start()
    result = service.stop()
    onsets = []
    for track in result["tracks"]:
        with av.open(track["path"]) as container:
            raw = np.concatenate([f.to_ndarray().ravel() for f in container.decode(audio=0)])
            mono = raw[:: track["channels"]]
            onsets.append(np.flatnonzero(np.abs(mono) > 100)[0] / track["sample_rate"])
            assert mono.size == track["sample_rate"]
            assert not mono[: round(track["sample_rate"] * 0.4)].any()
    assert onsets[0] == pytest.approx(onsets[1], abs=1 / 8000)
    assert [t["start_offset_ms"] for t in result["tracks"]] == [0, 200]
    assert result["duration_ms"] == 1000


def test_padding_alone_is_not_returned_as_confirmed_capture(tmp_path, monkeypatch, capture_clock):
    import wave

    from sozvon.audio.capture import CaptureService

    fake = fake_backends()
    service = CaptureService({"output_dir": str(tmp_path)}, backends=fake)
    # Advance clock between starts, after the common epoch has actually been set.
    original_mic = fake["mic"].open

    def first_stream(*args):
        stream = original_mic(*args)
        start = stream.start

        def delay():
            start()
            capture_clock[0] += 0.2

        stream.start = delay
        return stream

    fake["mic"].open = first_stream
    original_write = wave.Wave_write.writeframesraw

    def reject_nonzero_system(file, data):
        if file.getnchannels() == 2 and any(data):
            raise OSError("disk full after padding")
        return original_write(file, data)

    monkeypatch.setattr(wave.Wave_write, "writeframesraw", reject_nonzero_system)
    service.start()
    assert service.failed.wait(1)
    result = service.stop()
    assert [t["source"] for t in result["tracks"]] == ["mic"]
    assert result["warnings"]
    with av.open(result["tracks"][0]["path"]) as container:
        assert sum(f.samples for f in container.decode(audio=0)) == 8000


def test_fake_stream_really_is_dormant_before_start():
    calls = []
    stream = FakeStream(lambda data, status: calls.append(data), b"test")
    stream.push()
    assert calls == []
    stream.start()
    assert calls == [b"test"]
    stream.stop()
    stream.push()
    assert calls == [b"test"]


def test_device_probe_uses_system_defaults_not_first(monkeypatch):
    from sozvon.audio import backends
    from sozvon.audio.operations import run

    fake = fake_backends()
    monkeypatch.setattr(backends, "create_backends", lambda: (fake, []))
    result = run("devices", {}, threading.Event(), lambda _: None)
    assert result == {
        "available": True,
        "reason": "",
        "microphones": fake["mic"].devices(),
        "outputs": fake["system"].devices(),
        "default_input": "mic:1",
        "default_output": "system:1",
    }
    assert all(backend.closed for backend in fake.values())


def test_devices_cleanup_error_is_reported_without_losing_other_source(monkeypatch):
    from sozvon.audio import backends

    fake = fake_backends()

    def fail():
        raise RuntimeError("native cleanup failed")

    fake["mic"].close = fail
    monkeypatch.setattr(backends, "create_backends", lambda: (fake, []))
    result = backends.devices()
    assert result["outputs"]
    assert fake["system"].closed
    assert "закрыть" in result["reason"]


def test_sounddevice_adapter_checks_and_starts_real_style_stream(monkeypatch):
    from sozvon.audio.backends import SoundDeviceBackend

    calls = []

    def raw_stream(**kwargs):
        calls.append(kwargs)
        return FakeStream(lambda data, status: kwargs["callback"](data, 1, None, status), b"pcm")

    module = SimpleNamespace(
        query_devices=lambda *args, **kw: (
            {"name": "USB mic", "max_input_channels": 1, "default_samplerate": 44100}
            if args
            else [
                {"name": "ignored", "max_input_channels": 0},
                {"name": "USB mic", "max_input_channels": 1},
            ]
        ),
        default=SimpleNamespace(device=(1, 9)),
        check_input_settings=lambda **kw: calls.append(kw),
        RawInputStream=raw_stream,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    backend = SoundDeviceBackend()
    assert backend.default_device() == "mic:1"
    assert backend.devices() == [{"id": "mic:1", "name": "USB mic"}]
    assert backend.format("mic:1") == (44100, 1)
    assert calls[0] == {"device": 1, "samplerate": 44100, "channels": 1, "dtype": "int16"}
    received = []
    stream = backend.open("mic:1", 44100, 1, lambda data, status: received.append(data))
    assert not stream.active and not received
    stream.start()
    assert received == [b"pcm"]
    backend.close()


def test_wasapi_adapter_uses_patch_library_own_format_and_explicit_start(monkeypatch):
    from sozvon.audio.backends import WasapiBackend

    calls = []
    terminated = []
    info = {
        "index": 42,
        "name": "Headphones [Loopback]",
        "defaultSampleRate": 48000,
        "maxInputChannels": 2,
    }

    class PyAudio:
        def get_loopback_device_info_generator(self):
            return iter([info])

        def get_default_wasapi_loopback(self):
            return info

        def get_device_info_by_index(self, index):
            assert index == 42
            return info

        def is_format_supported(self, rate, **kwargs):
            calls.append((rate, kwargs))
            return True

        def open(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["start"] is False
            inner = FakeStream(
                lambda data, status: kwargs["stream_callback"](data, 1, None, 0), b"pcm"
            )
            return SimpleNamespace(
                start_stream=inner.start,
                stop_stream=inner.stop,
                close=inner.close,
                is_active=lambda: inner.active,
            )

        def terminate(self):
            terminated.append(True)

    monkeypatch.setitem(
        sys.modules, "pyaudiowpatch", SimpleNamespace(PyAudio=PyAudio, paInt16=8, paContinue=0)
    )
    backend = WasapiBackend()
    assert backend.default_device() == "loop:42"
    assert backend.devices() == [{"id": "loop:42", "name": info["name"]}]
    assert backend.format("loop:42") == (48000, 2)
    assert calls[0] == (48000, {"input_device": 42, "input_channels": 2, "input_format": 8})
    received = []
    stream = backend.open("loop:42", 48000, 2, lambda data, status: received.append(data))
    assert not received
    stream.start()
    assert received == [b"pcm"]
    stream.stop()
    stream.close()
    backend.close()
    assert terminated == [True]


@pytest.mark.parametrize("allow_partial", [False, True])
def test_missing_source_requires_explicit_partial_permission(tmp_path, allow_partial):
    from sozvon.audio.capture import CaptureService

    backend = fake_backends()["mic"]
    service = CaptureService(
        {"output_dir": str(tmp_path), "allow_partial": allow_partial}, backends={"mic": backend}
    )
    if allow_partial:
        service.start()
        result = service.stop()
        assert [t["source"] for t in result["tracks"]] == ["mic"]
        assert result["warnings"]
    else:
        with pytest.raises(RuntimeError, match="system|Систем|дорож"):
            service.start()
        assert not backend.streams
        assert not list(tmp_path.glob("*.wav"))
        assert backend.closed


def test_format_failure_preflight_does_not_start_either_stream(tmp_path):
    from sozvon.audio.capture import CaptureService

    backends = fake_backends()

    def fail(device):
        raise RuntimeError("unsupported")

    backends["system"].format = fail
    service = CaptureService({"output_dir": str(tmp_path)}, backends=backends)
    with pytest.raises(RuntimeError):
        service.start()
    assert not any(b.streams for b in backends.values())
    assert all(b.closed for b in backends.values())
    assert not list(tmp_path.glob("*.wav"))


def test_bounded_queue_overflow_stops_instead_of_silent_loss(tmp_path):
    from sozvon.audio.capture import CaptureService

    service = CaptureService({"output_dir": str(tmp_path)}, backends=fake_backends(), queue_size=1)
    callback = service._callback("mic")
    callback(b"\0\0", None)
    callback(b"\0\0", None)
    assert service.queue.qsize() == 1
    assert service.failed.is_set()
    assert any("очеред" in warning for warning in service.warnings)


def test_stop_on_stream_failure_preserves_already_written_audio(tmp_path, capture_clock):
    from sozvon.audio.capture import CaptureService

    backends = fake_backends()
    service = CaptureService({"output_dir": str(tmp_path)}, backends=backends)
    service.start()
    backends["mic"].streams[0].callback(b"", "input overflow")
    result = service.stop()
    assert result["warnings"]
    assert len(result["tracks"]) == 2
    assert result["duration_ms"] == 1000


def test_record_active_query_failure_returns_all_finalized_audio(
    tmp_path, monkeypatch, capture_clock
):
    from sozvon.audio import backends
    from sozvon.audio.operations import run

    fake = fake_backends()
    original = fake["system"].open

    class BrokenActiveQuery:
        def __init__(self, inner):
            self.start, self.stop, self.close = inner.start, inner.stop, inner.close

        @property
        def active(self):
            raise OSError("device disconnected during is_active")

    fake["system"].open = lambda *args: BrokenActiveQuery(original(*args))
    monkeypatch.setattr(backends, "create_backends", lambda: (fake, []))
    result = run("record", {"output_dir": str(tmp_path)}, threading.Event(), lambda _: None)
    assert {track["source"] for track in result["tracks"]} == {"mic", "system"}
    assert any("состояние" in warning and "сохранены" in warning for warning in result["warnings"])
    for track in result["tracks"]:
        with av.open(track["path"]) as container:
            frames = list(container.decode(audio=0))
            assert sum(frame.samples for frame in frames) == track["sample_rate"]
            assert any(frame.to_ndarray().any() for frame in frames)
    assert all(b.closed and b.streams[0].closed for b in fake.values())


def test_record_stop_event_saves_artifacts_and_emits_dict_levels(tmp_path, monkeypatch):
    from sozvon.audio import backends
    from sozvon.audio.operations import run

    fake = fake_backends()
    monkeypatch.setattr(backends, "create_backends", lambda: (fake, []))
    stop = threading.Event()
    events = []

    def emit(event):
        events.append(event)
        stop.set()

    result = run("record", {"output_dir": str(tmp_path), "max_seconds": 60}, stop, emit)
    assert len(result["tracks"]) == 2
    assert events
    assert all(isinstance(e, dict) for e in events)
    assert any("levels" in e and "duration_ms" in e for e in events)
    assert all(b.streams[0].closed for b in fake.values())


@pytest.mark.parametrize("seconds", [0, -1, 14401, float("nan"), True])
def test_record_duration_is_bounded_before_backend_load(tmp_path, seconds):
    from sozvon.audio.capture import CaptureService

    with pytest.raises(ValueError):
        CaptureService({"output_dir": str(tmp_path), "max_seconds": seconds}, backends={}).start()


def test_no_callback_data_is_not_a_successful_recording(tmp_path):
    from sozvon.audio.capture import CaptureService

    backends = fake_backends()
    for backend in backends.values():
        backend.data = b""
    service = CaptureService({"output_dir": str(tmp_path)}, backends=backends)
    service.start()
    with pytest.raises(RuntimeError, match="аудио|пуст"):
        service.stop()


@pytest.mark.parametrize("stage", ["open", "start"])
def test_start_failure_closes_all_resources_before_raising(tmp_path, stage):
    from sozvon.audio.capture import CaptureService

    fake = fake_backends()
    original = fake["system"].open

    def failing_open(*args):
        if stage == "open":
            raise OSError("hardware disconnected")
        stream = original(*args)

        def failing_start():
            raise OSError("hardware disconnected")

        stream.start = failing_start
        return stream

    fake["system"].open = failing_open
    service = CaptureService({"output_dir": str(tmp_path)}, backends=fake)
    with pytest.raises(RuntimeError, match="начать"):
        service.start()
    assert all(b.closed for b in fake.values())
    assert all(s.closed and not s.active for b in fake.values() for s in b.streams)
    assert service.writer is None or not service.writer.is_alive()


def test_record_limit_auto_stops_with_explanation(tmp_path, monkeypatch):
    from sozvon.audio import backends
    from sozvon.audio.operations import run

    fake = fake_backends()
    monkeypatch.setattr(backends, "create_backends", lambda: (fake, []))
    result = run(
        "record",
        {"output_dir": str(tmp_path), "max_seconds": 0.01},
        threading.Event(),
        lambda _: None,
    )
    assert len(result["tracks"]) == 2
    assert any("автоматически" in warning for warning in result["warnings"])


def test_writer_error_does_not_block_stop(tmp_path, monkeypatch):
    from sozvon.audio.capture import CaptureService

    service = CaptureService({"output_dir": str(tmp_path)}, backends=fake_backends())
    service.start()

    # Wait for known startup blocks, then simulate disk exhaustion on the next block.
    def failure(data):
        raise OSError("disk full")

    monkeypatch.setattr(service.tracks["mic"]["file"], "writeframesraw", failure)
    service.streams[0].push()
    assert service.failed.wait(1)
    try:
        result = service.stop()
    except RuntimeError as exc:
        assert "пуста" in str(exc)
    else:
        assert any("диск" in warning for warning in result["warnings"])
    assert not service.writer.is_alive()


def test_probe_missing_libraries_and_loader_errors_are_distinct(monkeypatch):
    from sozvon.audio import backends

    monkeypatch.setattr(backends.sys, "platform", "win32")

    def missing():
        raise ImportError("sounddevice")

    def loader():
        raise OSError("PortAudio")

    monkeypatch.setattr(backends, "SoundDeviceBackend", missing)
    monkeypatch.setattr(backends, "WasapiBackend", loader)
    result = backends.devices()
    assert result["available"] is False
    assert "модуль не установлен" in result["reason"]
    assert "PortAudio не загрузилась" in result["reason"]


@pytest.mark.parametrize("operation", ["devices", "record"])
def test_native_portaudio_exception_does_not_escape_safe_boundary(tmp_path, monkeypatch, operation):
    from sozvon.audio import backends
    from sozvon.audio.operations import run

    class NativePortAudioError(Exception):
        pass

    fake = fake_backends()

    def fail():
        raise NativePortAudioError("driver error")

    fake["mic"].devices = fail
    monkeypatch.setattr(backends, "create_backends", lambda: (fake, []))
    if operation == "devices":
        result = run(operation, {}, threading.Event(), lambda _: None)
        assert not result["available"]
        assert result["reason"]
    else:
        with pytest.raises(RuntimeError, match="дорож|запись"):
            run(operation, {"output_dir": str(tmp_path)}, threading.Event(), lambda _: None)
    assert all(b.closed for b in fake.values())


def test_snapshot_duration_and_levels_freeze_after_stop(tmp_path):
    import time

    from sozvon.audio.capture import CaptureService

    service = CaptureService({"output_dir": str(tmp_path)}, backends=fake_backends())
    service.start()
    result = service.stop()
    snapshot = service.snapshot()
    time.sleep(0.01)
    assert service.snapshot() == snapshot
    assert snapshot["duration_ms"] == result["duration_ms"]
