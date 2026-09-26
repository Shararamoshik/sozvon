from uuid import uuid4

import pytest

from sozvon.llm.schema import validate_document
from sozvon.templates.builtin import builtin_spec
from sozvon.templates.schema import CORE, TemplateSpec

SEGMENTS = [{"id": "s1", "text": "Анна подготовит план."}]


def custom(kind="tasks"):
    key = "custom_" + uuid4().hex
    raw = builtin_spec("meeting").model_dump()
    raw["sections"].append({"key": key, "title": "Особое", "kind": kind})
    document = {name: [] for name in CORE}
    item = {"text": "Подготовить план", "evidence": [{"segment_id": "s1", "quote": "подготовит план"}]}
    if kind == "tasks":
        item.update(owner=None, due=None)
    document["custom_sections"] = {key: {"kind": kind, "items": [item]}}
    return key, TemplateSpec.model_validate(raw), document


def test_custom_tasks_require_owner_and_due():
    key, _, doc = custom()
    assert validate_document(doc, SEGMENTS).custom_sections[key].items[0].owner is None
    del doc["custom_sections"][key]["items"][0]["owner"]
    with pytest.raises(ValueError):
        validate_document(doc, SEGMENTS)


def test_template_rejects_unrequested_custom_key():
    _key, spec, doc = custom()
    validate_document(doc, SEGMENTS, template_spec=spec)
    spec.sections.pop()
    with pytest.raises(ValueError, match="раздел"):
        validate_document(doc, SEGMENTS, template_spec=spec)


def test_disabled_core_section_must_be_empty():
    _key, spec, doc = custom()
    spec.sections[0].enabled = False
    doc["summary"] = [{"text": "План", "evidence": [{"segment_id": "s1", "quote": "план"}]}]
    with pytest.raises(ValueError, match="отключён"):
        validate_document(doc, SEGMENTS, template_spec=spec)


def test_custom_kind_must_match_template():
    key, spec, doc = custom()
    doc["custom_sections"][key]["kind"] = "statements"
    del doc["custom_sections"][key]["items"][0]["owner"]
    del doc["custom_sections"][key]["items"][0]["due"]
    with pytest.raises(ValueError, match="тип"):
        validate_document(doc, SEGMENTS, template_spec=spec)


@pytest.mark.parametrize("change", ["foreign", "invented", "empty_evidence", "missing_due", "wrong_kind", "missing_key", "empty_report"])
def test_custom_section_cannot_bypass_evidence_or_structure(change):
    key, spec, doc = custom()
    item = doc["custom_sections"][key]["items"][0]
    if change == "foreign":
        item["evidence"][0]["segment_id"] = "not-real"
    elif change == "invented":
        item["evidence"][0]["quote"] = "несуществующая цитата"
    elif change == "empty_evidence":
        item["evidence"] = []
    elif change == "missing_due":
        del item["due"]
    elif change == "wrong_kind":
        doc["custom_sections"][key]["kind"] = "statements"
    elif change == "missing_key":
        doc["summary"] = [{"text": "План", "evidence": [{"segment_id": "s1", "quote": "план"}]}]
        doc["custom_sections"] = {}
    elif change == "empty_report":
        doc["custom_sections"][key]["items"] = []
    with pytest.raises(ValueError):
        validate_document(doc, SEGMENTS, template_spec=spec)
