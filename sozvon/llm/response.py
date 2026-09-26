"""Parse provider replies without reflecting untrusted content in error messages."""

import json

MAX_COUNTER = 2**63 - 1
MAX_MESSAGE = 500


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError


def parse_json(text: str | bytes | bytearray) -> dict:
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (ValueError, TypeError, RecursionError):
        raise RuntimeError("API вернул некорректный JSON; отчёт не сохранён.") from None


def _counter(value):
    if type(value) is int and 0 <= value <= MAX_COUNTER:
        return value
    return None


def _budget(max_output_tokens) -> str:
    """Только собственный бюджет запроса; это не ответ провайдера."""
    if type(max_output_tokens) is not int or not 0 < max_output_tokens <= 65536:
        return ""
    return f" Запрошенный лимит: {max_output_tokens} токенов."


def _spent(usage) -> str:
    """Числовые счётчики из allowlist: ни текста, ни неизвестных полей."""
    if not isinstance(usage, dict):
        return ""
    total = usage.get("completion_tokens", usage.get("output_tokens"))
    reasoning = usage.get("reasoning_tokens")
    if total is None:
        return ""
    text = f" Израсходовано {total} токенов"
    if reasoning is not None:
        text += f", из них на рассуждения {reasoning}"
    return text + "."


def _limit_error(label: str, data: dict, max_output_tokens) -> RuntimeError:
    message = f"Модель исчерпала лимит ответа ({label})." + _budget(max_output_tokens)
    message += _spent(safe_usage(data.get("usage")))
    message += " Отчёт не сохранён. Автоматического повтора не было."
    return RuntimeError(message[:MAX_MESSAGE])


def report_text(data: dict, protocol: str, *, max_output_tokens: int | None = None) -> str:
    try:
        if data.get("error"):
            raise ValueError
        if protocol == "openai":
            choice = data["choices"][0]
            reason = choice.get("finish_reason")
            if reason == "length":
                raise _limit_error("finish_reason=length", data, max_output_tokens)
            if reason == "content_filter":
                raise RuntimeError(
                    "Провайдер отфильтровал ответ по своим правилам "
                    "(finish_reason=content_filter); отчёт не сохранён. Повтора не было."
                )
            if reason not in (None, "stop"):
                raise RuntimeError(
                    "Модель не завершила отчёт или отклонила ответ. Повтора не было."
                )
            message = choice["message"]
            if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
                raise RuntimeError("Модель отказалась вернуть отчёт. Повтора запроса не было.")
            text = message["content"]
        else:
            reason = data.get("stop_reason")
            if reason == "max_tokens":
                raise _limit_error("stop_reason=max_tokens", data, max_output_tokens)
            if reason == "refusal":
                raise RuntimeError("Модель отказалась вернуть отчёт. Повтора запроса не было.")
            if reason not in (None, "end_turn", "stop_sequence"):
                raise RuntimeError(
                    "Модель не завершила отчёт или отклонила ответ. Повтора не было."
                )
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise ValueError
            text = "".join(block["text"] for block in blocks if block["type"] == "text")
        if not isinstance(text, str):
            raise TypeError
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise RuntimeError("API вернул ответ без корректного текстового отчёта.") from None
    if not text.strip():
        raise RuntimeError("API вернул пустой ответ; отчёт не создан.")
    return text


def safe_usage(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    fields = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    result = {}
    for key in fields:
        number = _counter(value.get(key))
        if number is not None:
            result[key] = number
    reasoning = _counter(value.get("reasoning_tokens"))
    for key in ("completion_tokens_details", "output_tokens_details"):
        details = value.get(key)
        if reasoning is None and isinstance(details, dict):
            reasoning = _counter(details.get("reasoning_tokens"))
    if reasoning is not None:
        result["reasoning_tokens"] = reasoning
    return result or None


def check_status(status: int):
    if 200 <= status < 300:
        return
    if 300 <= status < 400:
        raise RuntimeError("API вернул перенаправление. Проверьте адрес; переход запрещён.")
    hints = {
        400: "Проверьте модель и совместимость формата JSON с API.",
        401: "Проверьте API-ключ.",
        403: "У API-ключа нет доступа к этой модели.",
        404: "Проверьте базовый адрес API и имя модели.",
        413: "Провайдер не принимает такой размер контекста.",
        429: "Исчерпана квота или превышена частота запросов.",
    }
    hint = hints.get(status, "Провайдер недоступен или отклонил запрос.")
    raise RuntimeError(f"Ошибка API (HTTP {status}). {hint} Автоматического повтора не было.")
