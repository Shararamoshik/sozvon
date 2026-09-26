"""Неизменяемые исходные спецификации встроенных шаблонов."""
from sozvon.templates.schema import CORE, LABELS, TemplateSpec

NAMES = {"meeting": "Рабочая встреча", "client": "Встреча с клиентом",
         "technical": "Техническое обсуждение", "interview": "Собеседование"}
RULES = {"meeting": "Выдели решения, задачи и нерешённые вопросы.",
         "client": "Выдели запрос клиента, ограничения и договорённости.",
         "technical": "Выдели выбранные решения, альтернативы и технические риски.",
         "interview": "Отчёт по результатам собеседования строго по записи: кто кандидат, "
                      "что подтверждено его ответами, где пробелы, на чём договорились. "
                      "Держи 3–5 пунктов в разделе и пиши по одному предложению. "
                      "Не выдумывай баллы, оценки, опыт и сроки, которых нет в расшифровке; "
                      "неизвестные owner и due оставляй null."}
DESCRIPTIONS = {"interview": "Компактный результат собеседования по кандидату"}
DETAIL = {"interview": "brief"}
TITLES = {"interview": {"summary": "Итог по кандидату", "decisions": "Вердикт и договорённости",
                        "proposals": "Рекомендации", "tasks": "Следующие шаги",
                        "questions": "Не выяснено", "risks": "Пробелы и риски"}}
SECTION_RULES = {"interview": {
    "summary": "2–3 пункта: кто кандидат, уровень и общий вывод строго по ответам.",
    "decisions": "Только то, на чём действительно договорились: условия, формат, следующий этап.",
    "proposals": "Что предложила или могла бы предложить сторона: грейд, развитие, условия.",
    "tasks": "Следующие шаги с ответственным и сроком; если их не называли — null.",
    "questions": "Что осталось без ответа или требует проверки у кандидата.",
    "risks": "Пробелы в знаниях и сомнительные места ответов; без домыслов о личности."}}


def builtin_ids() -> tuple[str, ...]:
    """Единственный список встроенных шаблонов: порядок задаёт выдачу по умолчанию."""
    return tuple(NAMES)


def builtin_spec(template_id: str) -> TemplateSpec:
    if template_id not in NAMES:
        raise KeyError(template_id)
    titles = TITLES.get(template_id, {})
    rules = SECTION_RULES.get(template_id, {})
    return TemplateSpec.model_validate({
        "name": NAMES[template_id], "instructions": RULES[template_id],
        "description": DESCRIPTIONS.get(template_id, ""), "detail": DETAIL.get(template_id, "normal"),
        "sections": [{"key": key, "title": titles.get(key, LABELS[key]),
                      "kind": "tasks" if key == "tasks" else "statements",
                      "instructions": rules.get(key, "")} for key in CORE],
    })
