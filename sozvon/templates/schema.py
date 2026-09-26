"""Структура шаблона отчёта, не исполняемый шаблон HTML/Python."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CORE = ("summary", "decisions", "proposals", "tasks", "questions", "risks")
LABELS = dict(zip(CORE, ("Кратко", "Решения", "Предложения", "Задачи",
                        "Открытые вопросы", "Риски"), strict=True))


def _document_text(value: str) -> str:
    value.encode("utf-8")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError("Недопустимые управляющие символы")
    return value


class SectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    key: str = Field(pattern=r"^(summary|decisions|proposals|tasks|questions|risks|custom_[a-f0-9]{32})$")
    title: str = Field(min_length=1, max_length=80)
    kind: Literal["statements", "tasks"] = "statements"
    enabled: bool = True
    instructions: str = Field(default="", max_length=600)

    _safe_text = field_validator("title", "instructions")(_document_text)

    @model_validator(mode="after")
    def canonical_kind(self):
        if self.key in CORE and self.kind != ("tasks" if self.key == "tasks" else "statements"):
            raise ValueError("Тип системного раздела менять нельзя")
        if not self.title.strip():
            raise ValueError("Введите название раздела")
        return self


class TemplateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    language: str = Field(default="ru", pattern=r"^[a-z]{2,3}$")
    detail: Literal["brief", "normal", "detailed"] = "normal"
    instructions: str = Field(default="", max_length=4000)
    sections: list[SectionSpec] = Field(min_length=1, max_length=12)

    _safe_text = field_validator("name", "description", "instructions")(_document_text)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Введите название шаблона")
        return value

    @model_validator(mode="after")
    def structure(self):
        keys = [section.key for section in self.sections]
        if len(set(keys)) != len(keys):
            raise ValueError("Разделы не должны повторяться")
        if not set(CORE).issubset(keys):
            raise ValueError("Системные разделы можно выключить, но не удалить")
        if not any(section.enabled for section in self.sections):
            raise ValueError("Включите хотя бы один раздел")
        return self
