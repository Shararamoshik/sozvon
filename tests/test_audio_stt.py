"""STT tests inject the native model only; operation logic remains real."""

import sys
import threading
from types import SimpleNamespace

import pytest


def install_model(monkeypatch, factory):
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=factory))


def stt_payload(tmp_path, **kwargs):
    from test_audio_probe import make_wav

    model = tmp_path / "local-model"
    model.mkdir(exist_ok=True)
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    audio = make_wav(tmp_path / "input.wav")
    return {
        "path": str(audio),
        "model_path": str(model),
        "device": "cpu",
        "compute_type": "int8",
        "language": "auto",
        **kwargs,
    }


def test_local_transcribe_materializes_segments_without_download(tmp_path, monkeypatch):
    from sozvon.audio.operations import run

    calls = []

    class Model:
        def __init__(self, model_path, *, device, compute_type, local_files_only=False):
            calls.append((model_path, device, compute_type, local_files_only))

        def transcribe(self, path, *, language):
            assert language is None
            assert str(path).endswith(".wav")
            return iter([SimpleNamespace(start=0.1, end=0.8, text=" Привет! ")]), None

    install_model(monkeypatch, Model)
    events = []
    payload = stt_payload(tmp_path)
    result = run("transcribe", payload, threading.Event(), events.append)
    assert calls == [(payload["model_path"], "cpu", "int8", True)]
    assert result["device"] == "cpu"
    assert result["warnings"] == []
    segment = result["segments"][0]
    assert segment == {
        "id": segment["id"],
        "ordinal": 0,
        "start_ms": 100,
        "end_ms": 800,
        "speaker": None,
        "text": "Привет!",
    }
    assert isinstance(segment["id"], str) and segment["id"]
    assert all(isinstance(event, dict) and event["type"] == "progress" for event in events)


@pytest.mark.parametrize("model_path", ["tiny", "https://invalid.local/model", "missing-model"])
def test_transcribe_rejects_model_ids_before_import(tmp_path, monkeypatch, model_path):
    from sozvon.audio.stt import transcribe

    def forbidden(*args, **kwargs):
        pytest.fail("model construction must not happen for nonlocal models")

    install_model(monkeypatch, forbidden)
    with pytest.raises(ValueError, match="локальн"):
        transcribe(stt_payload(tmp_path, model_path=model_path), threading.Event(), lambda _: None)


def test_auto_retries_entire_lazy_iteration_on_cpu(tmp_path, monkeypatch):
    from sozvon.audio.stt import transcribe

    calls = []

    class Model:
        def __init__(self, path, *, device, compute_type, local_files_only=True):
            calls.append((device, compute_type))
            self.device = device

        def transcribe(self, path, *, language):
            def stream():
                yield SimpleNamespace(
                    start=0, end=0.4, text="CPU" if self.device == "cpu" else "GPU"
                )
                if self.device != "cpu":
                    raise RuntimeError("CUDA unavailable during iteration")

            return stream(), None

    install_model(monkeypatch, Model)
    result = transcribe(stt_payload(tmp_path, device="auto"), threading.Event(), lambda _: None)
    assert calls == [("auto", "int8"), ("cpu", "int8")]
    assert result["device"] == "cpu"
    assert [s["text"] for s in result["segments"]] == ["CPU"]
    assert result["warnings"]


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_explicit_device_never_falls_back(tmp_path, monkeypatch, device):
    from sozvon.audio.stt import transcribe

    calls = []

    class Model:
        def __init__(self, path, **kwargs):
            calls.append(kwargs["device"])
            raise RuntimeError("native backend failed")

    install_model(monkeypatch, Model)
    with pytest.raises(RuntimeError):
        transcribe(stt_payload(tmp_path, device=device), threading.Event(), lambda _: None)
    assert calls == [device]


def test_empty_speech_is_error_not_success(tmp_path, monkeypatch):
    from sozvon.audio.stt import transcribe

    class Model:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, path, *, language):
            return iter([SimpleNamespace(start=0, end=1, text="  ")]), None

    install_model(monkeypatch, Model)
    with pytest.raises(RuntimeError, match="Речь не обнаружена"):
        transcribe(stt_payload(tmp_path), threading.Event(), lambda _: None)


def test_cancel_after_lazy_iteration_must_not_be_success_or_retry(tmp_path, monkeypatch):
    from sozvon.audio.stt import transcribe

    stop = threading.Event()
    calls = []

    class Model:
        def __init__(self, path, **kwargs):
            calls.append(kwargs["device"])

        def transcribe(self, path, *, language):
            def stream():
                yield SimpleNamespace(start=0, end=1, text="not committed")
                stop.set()

            return stream(), None

    install_model(monkeypatch, Model)
    with pytest.raises(RuntimeError, match="отмен"):
        transcribe(stt_payload(tmp_path, device="auto"), stop, lambda _: None)
    assert calls == ["auto"]


def test_missing_local_tokenizer_is_rejected_before_native_download(tmp_path, monkeypatch):
    from pathlib import Path

    from sozvon.audio.stt import transcribe

    payload = stt_payload(tmp_path)
    (Path(payload["model_path"]) / "tokenizer.json").unlink()
    install_model(monkeypatch, lambda *args, **kwargs: pytest.fail("must stay offline"))
    with pytest.raises(ValueError, match="tokenizer.json"):
        transcribe(payload, threading.Event(), lambda _: None)


def test_two_tracks_are_mixed_to_16k_mono_without_concatenation(tmp_path, monkeypatch):
    import hashlib
    import wave
    from pathlib import Path

    import av
    import numpy as np

    from sozvon.audio.operations import run

    mic = tmp_path / "mic.wav"
    system = tmp_path / "system.wav"
    # Different rate, channel count AND sample format must be resampled independently.
    data = (12000 * np.sin(2 * np.pi * 440 * np.arange(8000) / 8000)).astype("<i2")
    with wave.open(str(mic), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(data.tobytes())
    stereo = np.zeros((2, 96000), dtype=np.float32)
    stereo[:, 48000:] = 0.3 * np.sin(2 * np.pi * 880 * np.arange(48000) / 48000)
    with av.open(str(system), "w") as output:
        stream = output.add_stream("pcm_f32le", rate=48000)
        stream.layout = "stereo"
        frame = av.AudioFrame.from_ndarray(stereo, format="fltp", layout="stereo")
        frame.sample_rate = 48000
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (mic, system)]
    seen = []

    class Model:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, path, *, language):
            seen.append(Path(path))
            assert Path(path) not in (mic, system)
            with av.open(path) as container:
                assert container.duration == 2_000_000
                stream = container.streams.audio[0]
                assert stream.codec_context.sample_rate == 16000
                assert stream.codec_context.channels == 1
                raw = np.concatenate([f.to_ndarray().ravel() for f in container.decode(stream)])
            assert np.max(np.abs(raw[:16000])) > 2000  # mic actually included
            assert np.max(np.abs(raw[16000:])) > 2000  # system actually included
            for part, frequency in ((raw[2000:14000], 440), (raw[18000:30000], 880)):
                frequencies = np.fft.rfftfreq(part.size, d=1 / 16000)
                dominant = frequencies[np.abs(np.fft.rfft(part)).argmax()]
                assert dominant == pytest.approx(frequency, abs=2)
            return iter([SimpleNamespace(start=0, end=2, text="both sources")]), None

    install_model(monkeypatch, Model)
    payload = stt_payload(tmp_path)
    payload.pop("path")
    payload.update(paths=[str(mic), str(system)], output_dir=str(tmp_path / "staging"))
    result = run("transcribe", payload, threading.Event(), lambda _: None)
    assert result["segments"][0]["text"] == "both sources"
    assert seen and not seen[0].exists()
    assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (mic, system)]


@pytest.mark.parametrize("device", ["cpu", "auto"])
@pytest.mark.parametrize("kind", ["large_text", "many_segments"])
def test_oversized_stt_segments_fail_helpfully_without_truncation_or_retry(
    tmp_path, monkeypatch, device, kind
):
    from sozvon.audio.stt import transcribe

    calls = []
    closed = []

    class Model:
        def __init__(self, path, **kwargs):
            calls.append(kwargs["device"])

        def transcribe(self, path, *, language):
            def segments():
                try:
                    for _ in range(10000 if kind == "many_segments" else 1):
                        yield SimpleNamespace(
                            start=0, end=1, text="речь" if kind == "many_segments" else "я" * 600000
                        )
                finally:
                    closed.append(True)

            return segments(), None

    install_model(monkeypatch, Model)
    with pytest.raises(ValueError, match="Расшифровка.*предел.*Разделите"):
        transcribe(stt_payload(tmp_path, device=device), threading.Event(), lambda _: None)
    assert calls == [device]
    assert closed == [True]


def test_full_stt_result_envelope_fits_real_framing_with_escaped_unicode(tmp_path, monkeypatch):
    import json

    from sozvon.audio.stt import transcribe
    from sozvon.runtime.framing import MAX_FRAME_SIZE, encode_frame

    text = 'я"\\\n' * 10000

    class Model:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, path, *, language):
            return iter([SimpleNamespace(start=0, end=1, text=text)]), None

    install_model(monkeypatch, Model)
    result = transcribe(stt_payload(tmp_path), threading.Event(), lambda _: None)
    message = {"type": "result", "result": result}
    frame = encode_frame(message)
    assert len(frame) - 4 < MAX_FRAME_SIZE
    assert json.loads(frame[4:]) == message
    assert result["segments"][0]["text"] == text.strip()


@pytest.mark.parametrize("budget", ["MAX_SEGMENTS_BYTES", "MAX_RESULT_BYTES"])
def test_stt_serialized_size_boundary_is_exact(tmp_path, monkeypatch, budget):
    import json

    from sozvon.audio import stt
    from sozvon.runtime.framing import encode_frame

    class Model:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, path, *, language):
            return iter([SimpleNamespace(start=0, end=1, text='речь\\n"\\\\')]), None

    install_model(monkeypatch, Model)
    payload = stt_payload(tmp_path)
    result = stt.transcribe(payload, threading.Event(), lambda _: None)
    envelope = {"type": "result", "result": result}
    size = (
        len(json.dumps(result["segments"], ensure_ascii=False, separators=(",", ":")).encode())
        if budget == "MAX_SEGMENTS_BYTES"
        else len(encode_frame(envelope)) - 4
    )
    monkeypatch.setattr(stt, budget, size)
    assert stt.transcribe(payload, threading.Event(), lambda _: None)["segments"]
    monkeypatch.setattr(stt, budget, size - 1)
    with pytest.raises(stt.TranscriptLimitError, match="Разделите"):
        stt.transcribe(payload, threading.Event(), lambda _: None)


def test_stt_budget_includes_result_envelope_and_metadata(tmp_path, monkeypatch):
    from sozvon.audio.stt import transcribe

    class Model:
        def __init__(self, path, **kwargs):
            self.model = SimpleNamespace(device="x" * (1024 * 1024))

        def transcribe(self, path, *, language):
            return iter([SimpleNamespace(start=0, end=1, text="small transcript")]), None

    install_model(monkeypatch, Model)
    with pytest.raises(ValueError, match="Расшифровка.*предел.*Разделите"):
        transcribe(stt_payload(tmp_path), threading.Event(), lambda _: None)


@pytest.mark.parametrize("second_source", [False, True])
def test_stt_rejects_over_30_minutes_before_model_or_inference(
    tmp_path, monkeypatch, second_source
):
    import wave

    from sozvon.audio.stt import transcribe

    payload = stt_payload(tmp_path, output_dir=str(tmp_path / "staging"))
    long_audio = tmp_path / "long.wav"
    # Real, small, low-rate WAV; metadata is sufficient to reject before expensive decode.
    with wave.open(str(long_audio), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        for _ in range(1801):
            output.writeframesraw(b"\0\0" * 8000)
    payload["paths"] = [payload["path"], str(long_audio)] if second_source else [str(long_audio)]
    install_model(monkeypatch, lambda *a, **kw: pytest.fail("duration reached WhisperModel"))
    with pytest.raises(ValueError, match="30 минут.*Разделите"):
        transcribe(payload, threading.Event(), lambda _: None)
    assert not list((tmp_path / "staging").rglob("*.f32"))
    assert not list((tmp_path / "staging").rglob("*.wav"))


def test_stt_decoded_duration_guard_without_trusted_container_duration(tmp_path, monkeypatch):
    import av

    from sozvon.audio import mix
    from sozvon.audio.stt import transcribe

    original = av.open

    class NoDuration:
        def __init__(self, container):
            self.container = container
            self.streams = container.streams
            self.duration = None

        def __enter__(self):
            self.container.__enter__()
            return self

        def __exit__(self, *args):
            return self.container.__exit__(*args)

        def decode(self, stream):
            return self.container.decode(stream)

    monkeypatch.setattr(av, "open", lambda *a, **kw: NoDuration(original(*a, **kw)))
    monkeypatch.setattr(mix, "MAX_STT_SECONDS", 0.5, raising=False)
    install_model(
        monkeypatch, lambda *a, **kw: pytest.fail("decoded duration reached WhisperModel")
    )
    with pytest.raises(ValueError, match="30 минут.*Разделите"):
        transcribe(stt_payload(tmp_path), threading.Event(), lambda _: None)


def test_stt_accepts_exact_duration_limit(tmp_path, monkeypatch):
    from sozvon.audio import mix
    from sozvon.audio.stt import transcribe

    monkeypatch.setattr(mix, "MAX_STT_SECONDS", 1, raising=False)

    class Model:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, path, *, language):
            return iter([SimpleNamespace(start=0, end=1, text="boundary")]), None

    install_model(monkeypatch, Model)
    assert transcribe(stt_payload(tmp_path), threading.Event(), lambda _: None)["segments"]


def test_stt_legacy_factory_without_local_files_only_stays_compatible(tmp_path, monkeypatch):
    from sozvon.audio.stt import transcribe

    class LegacyModel:
        def __init__(self, path, *, device, compute_type):
            assert device == "cpu" and compute_type == "int8"

        def transcribe(self, path, *, language):
            return iter([SimpleNamespace(start=0, end=1, text="legacy")]), None

    install_model(monkeypatch, LegacyModel)
    result = transcribe(stt_payload(tmp_path), threading.Event(), lambda _: None)
    assert result["segments"][0]["text"] == "legacy"


def test_auto_cancellation_during_iteration_cleans_staging_without_retry(tmp_path, monkeypatch):
    from pathlib import Path

    from sozvon.audio.stt import transcribe

    stop = threading.Event()
    seen = []

    class Model:
        def __init__(self, path, **kwargs):
            assert not seen, "must not retry after cancellation"

        def transcribe(self, path, *, language):
            seen.append(Path(path))

            def stream():
                stop.set()
                yield SimpleNamespace(start=0, end=1, text="cancelled")

            return stream(), None

    install_model(monkeypatch, Model)
    with pytest.raises(RuntimeError, match="отмен"):
        transcribe(stt_payload(tmp_path, device="auto"), stop, lambda _: None)
    assert seen and not seen[0].exists()


@pytest.mark.parametrize("paths", [[], "not-a-list", ["https://invalid.local/audio"]])
def test_stt_invalid_sources_do_not_construct_model(tmp_path, monkeypatch, paths):
    from sozvon.audio.stt import transcribe

    def forbidden(*args, **kwargs):
        pytest.fail("invalid paths must not construct model")

    install_model(monkeypatch, forbidden)
    with pytest.raises(ValueError):
        transcribe(stt_payload(tmp_path, paths=paths), threading.Event(), lambda _: None)
