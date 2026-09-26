"""Normalize untrusted STT JSON without inventing speaker labels or timestamps."""

from uuid import uuid4

from .result_limits import MAX_RESULT_BYTES, MAX_SEGMENTS_BYTES, json_size


def validate_result_size(result: dict) -> None:
    if json_size({"type": "result", "result": result}) > MAX_RESULT_BYTES:
        raise ValueError("Результат распознавания не помещается в безопасный канал")


def _check_segments(result):
    if json_size(result) > MAX_SEGMENTS_BYTES:
        raise ValueError("Результат распознавания слишком большой; текст не обрезан")
    return result


def _text(value):
    if not isinstance(value, str):
        raise ValueError("Сервис вернул неверный текст")  # noqa: TRY004 - public validation error
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Сервис вернул недопустимые символы") from None
    if any((ord(c) < 32 and c not in "\n\r\t") or 127 <= ord(c) <= 159 for c in value):
        raise ValueError("Сервис вернул недопустимые управляющие символы")
    return value.strip()


def normalize_response(data: dict, duration_ms: int) -> tuple[list[dict], list[str]]:
    if not isinstance(data, dict):
        raise ValueError("Сервис вернул неверный объект ответа")  # noqa: TRY004
    rows = data.get("segments")
    if rows is not None and not isinstance(rows, list):
        raise ValueError("Сервис вернул неверный список сегментов")
    if rows:
        result = []
        previous = -1
        total_bytes = 2
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Сервис вернул неверную реплику")  # noqa: TRY004
            text = _text(row.get("text"))
            start, end = row.get("start"), row.get("end")
            # Bounded comparisons reject NaN/inf/huge integers before multiplication.
            if (
                type(start) not in (float, int)
                or type(end) not in (float, int)
                or not 0 <= start <= end <= (duration_ms + 1000) / 1000
                or start < previous
            ):
                raise ValueError("Сервис вернул некорректные временные границы")
            previous = start
            if not text:
                continue
            item = {
                "id": uuid4().hex,
                "ordinal": len(result),
                "start_ms": round(start * 1000),
                "end_ms": round(end * 1000),
                "speaker": None,
                "text": text,
            }
            total_bytes += json_size(item) + bool(result)
            if total_bytes > MAX_SEGMENTS_BYTES:
                raise ValueError("Результат распознавания слишком большой; текст не обрезан")
            result.append(item)
        if result:
            return _check_segments(result), []
    text = _text(data.get("text"))
    if not text:
        raise ValueError("Сервис не вернул распознанную речь")
    return _check_segments(
        [
            {
                "id": uuid4().hex,
                "ordinal": 0,
                "start_ms": None,
                "end_ms": None,
                "speaker": None,
                "text": text,
            }
        ]
    ), ["Сервис не вернул таймкоды; ссылки будут вести на абзацы"]
