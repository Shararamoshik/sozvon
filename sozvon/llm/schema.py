"""Strict report schema and literal citation checks (not semantic entailment)."""

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_serializer,
    model_validator,
)

from sozvon.templates.schema import CORE


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


class CustomSection(StrictModel):
    kind: Literal["statements", "tasks"]
    items: list[Statement | Task]

    @model_validator(mode="after")
    def item_types(self):
        if self.kind == "tasks" and any(not isinstance(item, Task) for item in self.items):
            raise ValueError("В разделе задач обязательны owner и due")
        if self.kind == "statements" and any(isinstance(item, Task) for item in self.items):
            raise ValueError("В текстовом разделе ожидаются текстовые пункты")
        return self


class Document(StrictModel):
    summary: list[Statement]
    decisions: list[Statement]
    proposals: list[Statement]
    tasks: list[Task]
    questions: list[Statement]
    risks: list[Statement]
    custom_sections: dict[str, CustomSection] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def legacy_compatible(self, handler):
        data = handler(self)
        if not self.custom_sections:
            data.pop("custom_sections", None)
        return data


def validate_document(data: object, segments: list[dict], template_spec=None) -> Document:
    """Reject the entire report if any source is missing or any quote is invented."""
    try:
        document = Document.model_validate(data)
    except ValidationError:
        raise ValueError("Отчёт не соответствует обязательной структуре с цитатами.") from None
    statements = [item for name in CORE for item in getattr(document, name)]
    statements += [item for section in document.custom_sections.values() for item in section.items]
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
    if template_spec is not None:
        if any(s.key in CORE and not s.enabled and getattr(document, s.key)
               for s in template_spec.sections):
            raise ValueError("Отчёт содержит пункты отключённого раздела.")
        expected = {s.key for s in template_spec.sections if s.enabled and s.key not in CORE}
        if set(document.custom_sections) != expected:
            raise ValueError("Отчёт содержит неверный набор пользовательских разделов.")
        if any(document.custom_sections[s.key].kind != s.kind
               for s in template_spec.sections if s.key in expected):
            raise ValueError("Отчёт содержит неверный тип пользовательского раздела.")
    return document
