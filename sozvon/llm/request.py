"""Validate requests and reject oversized context without truncation."""

import json
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from .schema import SegmentId, StrictModel, Text

# UTF-8 bytes of the entire user context (title/template/language/segments JSON).
# This is a conservative prototype limit, not a provider-specific token estimate.
MAX_INPUT_BYTES = 64_000


class Segment(StrictModel):
    model_config = ConfigDict(extra="allow", strict=True, hide_input_in_errors=True)
    id: SegmentId
    text: Text


class Request(StrictModel):
    base_url: str
    protocol: Literal["openai", "anthropic"]
    model: Text
    api_key: str = Field(default="", max_length=8192, repr=False, exclude=True)
    timeout: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    segments: list[Segment] = Field(min_length=1)
    title: str = ""
    template: Literal["meeting", "client", "technical"] = "meeting"
    language: Text = "ru"

    @field_validator("api_key")
    @classmethod
    def header_safe_key(cls, value):
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("Некорректный формат API-ключа")
        return value

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [segment.id for segment in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError("Повторяющиеся id сегментов")
        return self


def prepare_request(payload: dict) -> tuple[Request, str]:
    try:
        request = Request.model_validate(payload)
        context = json.dumps(
            {
                "title": request.title,
                "template": request.template,
                "language": request.language,
                "segments": [segment.model_dump() for segment in request.segments],
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        size = len(context.encode("utf-8"))
    except (ValidationError, TypeError, ValueError, OverflowError):
        raise ValueError(
            "Некорректные параметры отчёта: проверьте модель, протокол, API-ключ, таймаут "
            "(0 < секунд ≤ 600) и непустые сегменты с уникальными id."
        ) from None
    if size > MAX_INPUT_BYTES:
        raise ValueError(
            f"Длинная расшифровка пока не поддерживается: лимит прототипа {MAX_INPUT_BYTES} "
            "байт UTF-8 для входного контекста. Разделите запись; запрос не отправлен, "
            "текст не обрезался."
        )
    return request, context
