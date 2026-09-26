"""One-shot OpenAI-compatible STT; originals stay local and untouched."""

import json
import re
from contextlib import contextmanager
from pathlib import Path

import httpx

from sozvon.config import CloudStt
from sozvon.llm.response import parse_json
from sozvon.shared.network import is_loopback_url, validated_base_url

from .cloud_response import normalize_response, validate_result_size
from .mix import prepared_audio
from .probe import check_cancelled

MAX_RESPONSE_BYTES = 921_600


class _CancellableUpload(httpx.SyncByteStream):
    def __init__(self, source, stop_event):
        self.source = source
        self.stop_event = stop_event

    def __iter__(self):
        iterator = iter(self.source)
        while True:
            check_cancelled(self.stop_event)
            try:
                chunk = next(iterator)
            except StopIteration:
                return
            check_cancelled(self.stop_event)
            yield chunk

    def close(self):
        self.source.close()


@contextmanager
def _network_errors():
    try:
        yield
    except httpx.TimeoutException:
        raise RuntimeError(
            "Ответ STT не получен. Сервис мог обработать запрос; повтор может списать средства снова. "
            "Автоматического повтора не было"
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            "Связь с сервисом STT прервана; автоматического повтора не было"
        ) from None


def _check_status(status, size, limit):
    if 200 <= status < 300:
        return
    if 300 <= status < 400:
        raise ValueError("Сервис вернул перенаправление; переход с ключом запрещён")
    hints = {
        401: "Сервис отклонил ключ или доступ к модели. Проверьте настройки STT",
        403: "Сервис отклонил ключ или доступ к модели. Проверьте настройки STT",
        402: "Сервис не принял запрос: проверьте баланс",
        429: "Достигнут лимит сервиса. Автоматического повтора не было",
        413: (
            f"Запрос размером {size} байт отклонён сервисом (413); "
            f"настроенный лимит {limit} байт. Уменьшите файл или используйте локальное распознавание"
        ),
    }
    raise ValueError(
        hints.get(status, f"Сервис распознавания вернул HTTP {status}; повтора не было")
    )


def transcribe_cloud(payload, stop_event, emit):
    check_cancelled(stop_event)
    if not isinstance(payload, dict) or not {
        "base_url",
        "model",
        "response_format",
        "timeout_s",
        "max_upload_bytes",
    }.issubset(payload):
        raise ValueError("Не задан полный профиль облачного STT")
    try:
        CloudStt.model_validate(
            {
                name: payload[name]
                for name in ("base_url", "model", "response_format", "timeout_s", "allow_remote")
                if name in payload
            }
        )
    except ValueError:
        raise ValueError("Проверьте профиль облачного STT") from None
    base = validated_base_url(payload["base_url"])
    endpoint = base + "/audio/transcriptions"
    language = payload.get("language", "auto")
    if not isinstance(language, str) or not re.fullmatch(r"auto|[a-z]{2,3}", language):
        raise ValueError("Неверный код языка распознавания")
    limit = payload["max_upload_bytes"]
    if type(limit) is not int or not 0 < limit <= 100_000_000:
        raise ValueError("Недопустимый лимит запроса STT")
    key = payload.get("api_key")
    if key is not None and (not isinstance(key, str) or any(not 33 <= ord(c) <= 126 for c in key)):
        raise ValueError("Недопустимый ключ STT")
    if not is_loopback_url(base):
        if payload.get("allow_remote") is not True:
            raise ValueError("Разрешите отправку аудио во внешний сервис STT")
        if not key:
            raise ValueError("Для внешнего сервиса требуется отдельный ключ STT")
    response_format = payload["response_format"]
    emit({"type": "progress", "stage": "Подготовка аудио для сервиса"})
    with prepared_audio(payload, stop_event) as wav:
        flac = wav.parent / "upload.flac"
        duration_ms = make_flac(wav, flac, stop_event)
        fields = {"model": payload["model"], "response_format": response_format}
        if payload.get("language", "auto") != "auto":
            fields["language"] = payload["language"]
        if response_format == "verbose_json":
            fields["timestamp_granularities[]"] = "segment"
        timeout = httpx.Timeout(float(payload["timeout_s"]), connect=10.0)
        with (
            _network_errors(),
            flac.open("rb") as file,
            httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout) as client,
        ):
            request = client.build_request(
                "POST",
                endpoint,
                headers={
                    "Accept-Encoding": "identity",
                    **({"Authorization": "Bearer " + key} if key else {}),
                },
                data=fields,
                files={"file": ("audio.flac", file, "audio/flac")},
            )
            length = request.headers.get("Content-Length")
            limit = payload["max_upload_bytes"]
            if length is None or int(length) > limit:
                size = length if length is not None else "неизвестен"
                raise ValueError(
                    f"Размер запроса {size} байт превышает лимит {limit} байт; аудио не отправлено. "
                    "Уменьшите файл или используйте локальное распознавание"
                )
            emit({"type": "progress", "stage": "Отправка аудио в сервис"})
            check_cancelled(stop_event)
            request.stream = _CancellableUpload(request.stream, stop_event)
            response = client.send(request, stream=True)
            try:
                _check_status(response.status_code, int(length), limit)
                # httpx decoders can expand a compressed chunk before yielding the bounded slices.
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("Сервис STT вернул сжатый ответ; безопасное чтение запрещено")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    check_cancelled(stop_event)
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError("Ответ STT превышает безопасный размер")
                    body.extend(chunk)
            finally:
                response.close()
        check_cancelled(stop_event)
        try:
            data = parse_json(body)
        except RuntimeError:
            raise ValueError(
                "Сервис STT вернул некорректный JSON; расшифровка не сохранена"
            ) from None
        segments, warnings = normalize_response(data, duration_ms)
        result = {
            "segments": segments,
            "model": payload["model"],
            "device": "cloud",
            "warnings": warnings,
            "provider": "openai-compatible",
            "timed": all(row["start_ms"] is not None for row in segments),
        }
        validate_result_size(result)
        # Reject reflected credentials before IPC; redaction would silently alter speech.
        if key and json.dumps(key, ensure_ascii=False)[1:-1] in json.dumps(
            result, ensure_ascii=False
        ):
            raise ValueError(
                "Ответ STT содержит конфиденциальные данные; расшифровка не сохранена"
            )
        check_cancelled(stop_event)
        return result


def make_flac(wav: Path, destination: Path, stop_event) -> int:
    import av

    with (
        wav.open("rb") as source,
        av.open(source, mode="r", options={"protocol_whitelist": ""}) as src,
        av.open(str(destination), "w", format="flac") as dst,
    ):
        stream = dst.add_stream("flac", rate=16000)
        stream.layout = "mono"
        frames = 0
        for frame in src.decode(audio=0):
            check_cancelled(stop_event)
            frames += frame.samples
            for packet in stream.encode(frame):
                dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)
    return round(frames * 1000 / 16000)
