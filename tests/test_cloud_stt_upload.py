"""All provider responses here are synthetic; transport never reaches paid services."""

import hashlib
import io
import threading
import wave
from email import policy
from email.parser import BytesParser

import av
import httpx
import numpy as np
import pytest

from sozvon.audio.mix import prepared_audio


def write_wave(path, signal, rate=16000):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(np.rint(signal).astype("<i2").tobytes())
    return str(path)


@pytest.fixture
def audio_payload(tmp_path):
    t = np.arange(16000) / 16000
    path = write_wave(tmp_path / "original.wav", 8000 * np.sin(2 * np.pi * 440 * t))
    return {
        "paths": [path],
        "output_dir": str(tmp_path / "staging"),
        "base_url": "http://127.0.0.1:9999/custom/v1",
        "model": "exact-model-ID",
        "response_format": "verbose_json",
        "timeout_s": 120,
        "max_upload_bytes": 24_000_000,
        "api_key": "synthetic-stt-key",
        "language": "ru",
        "allow_remote": False,
    }


def install_transport(monkeypatch, handler):
    from sozvon.audio import cloud_stt

    real_client = httpx.Client
    clients = []

    def factory(**options):
        assert options["trust_env"] is False
        assert options["follow_redirects"] is False
        client = real_client(transport=httpx.MockTransport(handler), **options)
        clients.append(client)
        return client

    monkeypatch.setattr(cloud_stt.httpx, "Client", factory)
    return clients


def multipart(content_type, content):
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + content
    )
    return {
        part.get_param("name", header="content-disposition"): part for part in message.iter_parts()
    }


@pytest.mark.parametrize("response_format", ["json", "verbose_json"])
def test_actual_multipart_upload(monkeypatch, audio_payload, response_format):
    from sozvon.audio.cloud_stt import transcribe_cloud

    audio_payload["response_format"] = response_format
    calls = []

    def handler(request):
        body = request.read()
        calls.append(request)
        assert str(request.url) == audio_payload["base_url"] + "/audio/transcriptions"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-stt-key"
        assert int(request.headers["content-length"]) == len(body)
        parts = multipart(request.headers["content-type"], body)
        assert parts["model"].get_payload(decode=True) == b"exact-model-ID"
        assert parts["language"].get_payload(decode=True) == b"ru"
        assert parts["response_format"].get_payload(decode=True).decode() == response_format
        assert ("timestamp_granularities[]" in parts) == (response_format == "verbose_json")
        assert parts["file"].get_filename() == "audio.flac"
        assert parts["file"].get_content_type() == "audio/flac"
        with av.open(io.BytesIO(parts["file"].get_payload(decode=True))) as src:
            assert sum(f.samples for f in src.decode(audio=0)) == 16000
        return httpx.Response(200, json={"text": "Синтетический ответ."})

    clients = install_transport(monkeypatch, handler)
    result = transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert result["segments"][0]["text"] == "Синтетический ответ."
    assert result["device"] == "cloud" and result["timed"] is False
    assert result["provider"] == "openai-compatible"
    assert len(calls) == 1
    assert clients[0].is_closed


def test_oversize_is_refused_before_send(monkeypatch, audio_payload):
    from sozvon.audio import cloud_stt

    observed_size = []
    real_flac = cloud_stt.make_flac

    def encode(*args):
        duration = real_flac(*args)
        size = args[1].stat().st_size
        observed_size.append(size)
        audio_payload["max_upload_bytes"] = size + 10  # FLAC fits, multipart does not.
        return duration

    monkeypatch.setattr(cloud_stt, "make_flac", encode)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "synthetic"})

    clients = install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="лимит") as error:
        cloud_stt.transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert "не отправлено" in str(error.value)
    assert str(audio_payload["max_upload_bytes"]) in str(error.value)
    assert observed_size and not calls
    assert clients[0].is_closed


@pytest.mark.parametrize(
    "status,match",
    [
        (302, "перенаправление"),
        (401, "ключ"),
        (403, "ключ"),
        (402, "баланс"),
        (413, "Уменьшите"),
        (429, "лимит"),
        (500, "HTTP 500"),
    ],
)
def test_no_redirect_retry_or_secret_in_error(monkeypatch, audio_payload, status, match):
    from sozvon.audio.cloud_stt import transcribe_cloud

    calls, responses = [], []

    def handler(request):
        calls.append(request)
        response = httpx.Response(
            status,
            content=b"synthetic-stt-key private-provider-body",
            headers={"Location": "https://elsewhere.invalid/secret"},
        )
        responses.append(response)
        return response

    clients = install_transport(monkeypatch, handler)
    with pytest.raises((ValueError, RuntimeError), match=match) as error:
        transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert "synthetic-stt-key" not in str(error.value)
    assert "private-provider-body" not in str(error.value)
    if status == 413:
        assert str(audio_payload["max_upload_bytes"]) in str(error.value)
        assert calls[0].headers["content-length"] in str(error.value)
    assert len(calls) == 1
    assert responses[0].is_closed and clients[0].is_closed


@pytest.mark.parametrize(
    "kind,match",
    [
        (httpx.ReadTimeout, "списать"),
        (httpx.ConnectError, "Связь"),
        (httpx.RemoteProtocolError, "Связь"),
    ],
)
def test_transport_error_has_no_retry_or_private_details(monkeypatch, audio_payload, kind, match):
    from sozvon.audio.cloud_stt import transcribe_cloud

    calls = []

    def handler(request):
        calls.append(request)
        raise kind("synthetic-stt-key private-exception-body", request=request)

    clients = install_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError, match=match) as error:
        transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert "synthetic-stt-key" not in str(error.value)
    assert "private-exception-body" not in str(error.value)
    assert len(calls) == 1 and clients[0].is_closed


def test_response_is_bounded_before_json_parsing(monkeypatch, audio_payload):
    from sozvon.audio import cloud_stt

    closed = []

    class Oversize(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                yield b"x" * 65536

        def close(self):
            closed.append(True)

    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=Oversize()))
    with pytest.raises(ValueError, match="безопасный размер"):
        cloud_stt.transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert closed == [True]


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "https://remote.example/v1", "allow_remote": False},
        {"base_url": "https://remote.example/v1", "allow_remote": True, "api_key": None},
        {"base_url": "http://remote.example/v1"},
        {"response_format": "bad"},
        {"model": ""},
        {"model": None},
        {"model": "bad\x00"},
        {"language": "not-a-language"},
        {"timeout_s": True},
        {"max_upload_bytes": -1},
        {"api_key": "bad\r\nkey"},
        {"api_key": "badюkey"},
        {"allow_remote": "true"},
    ],
)
def test_invalid_profile_or_missing_permission_fails_before_client(
    monkeypatch, audio_payload, change
):
    from sozvon.audio import cloud_stt

    audio_payload.update(change)

    def forbid(**kwargs):
        pytest.fail("Client created for invalid payload")

    monkeypatch.setattr(cloud_stt.httpx, "Client", forbid)
    with pytest.raises(ValueError):
        cloud_stt.transcribe_cloud(audio_payload, threading.Event(), lambda e: None)


@pytest.mark.parametrize(
    "body",
    [
        b"not json private-value",
        b"[]",
        b'{"text": NaN}',
        b'{"text":"first","text":"second"}',
        b'{"text":null}',
        b'{"text":"\\u0000"}',
    ],
)
def test_invalid_json_has_safe_stt_error(monkeypatch, audio_payload, body):
    from sozvon.audio.cloud_stt import transcribe_cloud

    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises((ValueError, RuntimeError)) as error:
        transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert "отчёт" not in str(error.value)
    assert "private-value" not in str(error.value)


def test_unsolicited_compression_is_rejected_without_decompression(monkeypatch, audio_payload):
    from sozvon.audio.cloud_stt import transcribe_cloud

    closed = []

    class MustNotRead(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("unsolicited compressed content must not be expanded")
            yield b""

        def close(self):
            closed.append(True)

    def handler(request):
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=MustNotRead())

    install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="сжат"):
        transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert closed == [True]


def test_external_profile_uses_only_explicit_model_and_key(monkeypatch, audio_payload):
    from sozvon.audio.cloud_stt import transcribe_cloud

    audio_payload.update(
        base_url="https://synthetic.example/custom/v1/",
        allow_remote=True,
        model="provider-specific-exact-ID",
        language="auto",
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://synthetic.example/custom/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer synthetic-stt-key"
        assert request.headers["accept-encoding"] == "identity"
        parts = multipart(request.headers["content-type"], request.read())
        assert parts["model"].get_payload(decode=True) == b"provider-specific-exact-ID"
        assert "language" not in parts
        return httpx.Response(200, json={"segments": [{"start": 0, "end": 1, "text": "synthetic"}]})

    install_transport(monkeypatch, handler)
    result = transcribe_cloud(audio_payload, threading.Event(), lambda e: None)
    assert result["model"] == "provider-specific-exact-ID" and result["timed"] is True
    assert len(calls) == 1


def test_both_tracks_are_encoded_to_flac(tmp_path):
    from sozvon.audio.cloud_stt import make_flac

    rate = 48000
    t = np.arange(rate) / rate
    mic = np.concatenate([8000 * np.sin(2 * np.pi * 440 * t), np.zeros(rate)])
    remote = np.concatenate(
        [np.zeros(rate), 6000 * np.sin(2 * np.pi * 880 * t), np.zeros(rate // 2)]
    )
    sources = [tmp_path / "mic.wav", tmp_path / "system.wav"]
    payload = {
        "paths": [write_wave(sources[0], mic, rate), write_wave(sources[1], remote, rate)],
        "output_dir": str(tmp_path / "staging"),
    }
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    with prepared_audio(payload, threading.Event()) as wav:
        target = wav.parent / "upload.flac"
        assert make_flac(wav, target, threading.Event()) == 2500
        assert target.read_bytes().startswith(b"fLaC")
        with av.open(str(target)) as decoded:
            assert decoded.streams.audio[0].sample_rate == 16000
            assert len(decoded.streams.audio[0].layout.channels) == 1
            assert decoded.duration / av.time_base == pytest.approx(2.5)
            signal = np.concatenate(
                [frame.to_ndarray().ravel() for frame in decoded.decode(audio=0)]
            )
        for start, frequency in [(0, 440), (16000, 880)]:
            spectrum = abs(np.fft.rfft(signal[start : start + 16000]))
            assert np.argmax(spectrum) == frequency
            assert np.max(abs(signal[start : start + 16000])) > 1000
    assert hashes == [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    assert list((tmp_path / "staging").iterdir()) == []
