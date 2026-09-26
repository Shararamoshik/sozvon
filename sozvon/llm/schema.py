"""Strict report schema and literal citation checks (not semantic entailment)."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError


def normalize_whitespace(value: str) -> str:
    """Collapse Unicode whitespace only; do not fuzzy-match or rewrite words."""
    return " ".join(value.split())


def _nonblank(value: str) -> str:
    value.encode("utf-8")  # Reject unpaired JSON surrogates before IPC serialization.
    if not normalize_whitespace(value):
        raise ValueError("Пустое значение")
    return value


Text = Annotated[str, AfterValidator(_nonblank)]
SegmentId = Text | int


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Evidence(StrictModel):
    segment_id: SegmentId
    quote: Text


class Statement(StrictModel):
    text: Text
    evidence: list[Evidence] = Field(min_length=1)


class Task(Statement):
    # Required keys: an unknown owner/deadline must be explicit JSON null.
    owner: Text | None
    due: Text | None


class Document(StrictModel):
    summary: list[Statement]
    decisions: list[Statement]
    proposals: list[Statement]
    tasks: list[Task]
    questions: list[Statement]
    risks: list[Statement]


def validate_document(data: object, segments: list[dict]) -> Document:
    """Reject the entire report if any source is missing or any quote is invented."""
    try:
        document = Document.model_validate(data)
    except ValidationError:
        raise ValueError("Отчёт не соответствует обязательной структуре с цитатами.") from None
    statements = [item for name in Document.model_fields for item in getattr(document, name)]
    if not statements:
        raise ValueError("Модель вернула пустой отчёт. Подтверждённых пунктов нет.")
    sources = {segment["id"]: normalize_whitespace(segment["text"]) for segment in segments}
    for statement in statements:
        for evidence in statement.evidence:
            source = sources.get(evidence.segment_id)
            if source is None:
                raise ValueError("Отчёт содержит ссылку на несуществующий источник.")
            if normalize_whitespace(evidence.quote) not in source:
                raise ValueError("Цитата в отчёте не найдена в указанном сегменте расшифровки.")
    return document
