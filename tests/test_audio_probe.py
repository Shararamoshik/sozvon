"""Audio probes use real PyAV decoding, never capture hardware."""

import math
import struct
import threading
import wave

import pytest


def make_wav(path, rate=8000, channels=2, seconds=1):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        frames = [
            int(16000 * math.sin(2 * math.pi * 440 * n / rate)) for n in range(rate * seconds)
        ]
        output.writeframes(b"".join(struct.pack("<h", frame) * channels for frame in frames))
    return path


def test_audio_info_decodes_stereo_duration_independently(tmp_path):
    from sozvon.audio.operations import run

    path = make_wav(tmp_path / "reference.wav")
    result = run("audio_info", {"path": str(path)}, threading.Event(), lambda event: None)
    assert result["duration_ms"] == 1000
    assert result["sample_rate"] == 8000
    assert result["channels"] == 2
    assert result["peak"] == pytest.approx(16000 / 32768, abs=0.001)
    assert result["rms"] == pytest.approx(16000 / 32768 / math.sqrt(2), abs=0.001)


@pytest.mark.parametrize("path", ["https://example.org/audio.wav", "relative.wav", ""])
def test_audio_info_requires_absolute_local_file(path, monkeypatch):
    import av

    from sozvon.audio.probe import audio_info

    def forbidden(*args, **kwargs):
        pytest.fail("invalid path reached decoder; network-capable IO forbidden in test")

    monkeypatch.setattr(av, "open", forbidden)
    with pytest.raises(ValueError, match="локальн"):
        audio_info(path)


def test_audio_info_reports_corrupt_file(tmp_path):
    from sozvon.audio.probe import audio_info

    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"not audio")
    with pytest.raises(ValueError, match="аудио"):
        audio_info(str(path))


def test_audio_info_honors_cancel(tmp_path):
    from sozvon.audio.probe import audio_info

    event = threading.Event()
    event.set()
    with pytest.raises(RuntimeError, match="отмен"):
        audio_info(str(make_wav(tmp_path / "reference.wav")), event)


def test_audio_info_rejects_excessive_decode_budget(tmp_path, monkeypatch):
    from sozvon.audio import probe

    monkeypatch.setattr(probe, "MAX_AUDIO_SECONDS", 0.5, raising=False)
    with pytest.raises(ValueError, match="предел"):
        probe.audio_info(str(make_wav(tmp_path / "reference.wav")))


def test_network_protocols_never_reach_decoder(tmp_path, monkeypatch):
    import av

    from sozvon.audio.probe import audio_info

    def forbidden(*args, **kwargs):
        pytest.fail("URL must be rejected before decoder opens it")

    monkeypatch.setattr(av, "open", forbidden)
    with pytest.raises(ValueError, match="локальн"):
        audio_info("https://example.org/audio.wav")


def test_local_decoder_disables_secondary_protocols(tmp_path, monkeypatch):
    import av

    from sozvon.audio.probe import audio_info

    original = av.open

    def guarded(file, *args, **kwargs):
        assert not isinstance(file, str)
        assert kwargs["options"] == {"protocol_whitelist": ""}
        return original(file, *args, **kwargs)

    monkeypatch.setattr(av, "open", guarded)
    assert audio_info(str(make_wav(tmp_path / "safe.wav")))["duration_ms"] == 1000
