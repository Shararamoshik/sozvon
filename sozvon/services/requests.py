"""Строгие тела команд; не допускают строковое согласие или дробную ревизию."""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sozvon.core.limits import MAX_REVISION


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class TranscribeInput(Command):
    base_revision: int | None = Field(default=None, ge=0, le=MAX_REVISION)
    replace_confirmed: bool = False
    cloud_confirmed_url: str | None = Field(default=None, min_length=1, max_length=2048)


class ReportInput(Command):
    template_id: str | None = Field(default=None, min_length=1, max_length=64)
    template_revision: int | None = Field(default=None, ge=1, le=MAX_REVISION)

    @model_validator(mode="after")
    def complete_choice(self):
        if (self.template_id is None) != (self.template_revision is None):
            raise ValueError("Укажите шаблон и его версию вместе")
        return self
