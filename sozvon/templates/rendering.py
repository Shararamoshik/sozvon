"""Представление отчёта только по сохранённому снимку, без чтения БД шаблонов."""
from copy import deepcopy

from sozvon.templates.schema import CORE, LABELS


def sections_for_report(report) -> list[dict]:
    snapshot = report.get("meta", {}).get("template_snapshot")
    sections = snapshot["spec"]["sections"] if snapshot else [
        {"key": key, "title": LABELS[key], "kind": "tasks" if key == "tasks" else "statements",
         "enabled": True} for key in CORE
    ]
    document = report["document"]
    result = []
    for section in sections:
        if not section["enabled"]:
            continue
        key = section["key"]
        items = (document.get(key, []) if key in CORE else
                 document.get("custom_sections", {}).get(key, {}).get("items", []))
        result.append({"key": key, "title": section["title"], "kind": section["kind"],
                       "items": deepcopy(items)})
    return result
