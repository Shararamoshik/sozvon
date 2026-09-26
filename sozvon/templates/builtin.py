"""Неизменяемые исходные спецификации встроенных шаблонов."""
from sozvon.templates.schema import CORE, LABELS, TemplateSpec

NAMES = {"meeting": "Рабочая встреча", "client": "Встреча с клиентом",
         "technical": "Техническое обсуждение"}
RULES = {"meeting": "Выдели решения, задачи и нерешённые вопросы.",
         "client": "Выдели запрос клиента, ограничения и договорённости.",
         "technical": "Выдели выбранные решения, альтернативы и технические риски."}


def builtin_spec(template_id: str) -> TemplateSpec:
    if template_id not in NAMES:
        raise KeyError(template_id)
    return TemplateSpec.model_validate({
        "name": NAMES[template_id], "instructions": RULES[template_id],
        "sections": [{"key": key, "title": LABELS[key],
                      "kind": "tasks" if key == "tasks" else "statements"} for key in CORE],
    })
