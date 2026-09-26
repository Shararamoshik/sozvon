"""Parse provider replies without reflecting untrusted content in error messages."""

import json


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


def report_text(data: dict, protocol: str) -> str:
    try:
        if data.get("error"):
            raise ValueError
        if protocol == "openai":
            choice = data["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise RuntimeError(
                    "Модель не завершила отчёт или отклонила ответ. Повтора не было."
                )
            message = choice["message"]
            if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
                raise RuntimeError("Модель отказалась вернуть отчёт. Повтора запроса не было.")
            text = message["content"]
        else:
            if data.get("stop_reason") not in (None, "end_turn", "stop_sequence"):
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
    result = {
        key: value[key]
        for key in fields
        if key in value and type(value[key]) is int and value[key] >= 0
    }
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
