"""Частичная правка текста существующих реплик."""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sozvon.core.limits import MAX_REVISION


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=50_000)
    speaker: str | None = Field(default=None, max_length=80)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value.encode("utf-8")
        if not value.strip() or any(ord(c) < 32 and c not in "\n\t\r" for c in value):
            raise ValueError("Нужен непустой текст без управляющих символов")
        return value

    @field_validator("speaker")
    @classmethod
    def clean_speaker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value.encode("utf-8")
        if any(ord(c) < 32 for c in value):
            raise ValueError("Недопустимая метка говорящего")
        return value.strip() or None


class TranscriptEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    base_revision: int = Field(ge=1, le=MAX_REVISION)
    edits: list[Edit] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({edit.id for edit in self.edits}) != len(self.edits):
            raise ValueError("Одна реплика указана несколько раз")
        return self
