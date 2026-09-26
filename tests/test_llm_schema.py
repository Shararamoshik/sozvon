import copy

import pytest
from pydantic import BaseModel

SEGMENTS = [{"id": "s1", "text": "Анна\u00a0подготовит\nплан\u2003к пятнице."}]


def report():
    evidence = [{"segment_id": "s1", "quote": "Анна подготовит план"}]
    return {
        "summary": [{"text": "Подготовка плана.", "evidence": evidence}],
        "decisions": [],
        "proposals": [],
        "tasks": [{"text": "Подготовить план", "owner": None, "due": None, "evidence": evidence}],
        "questions": [],
        "risks": [],
    }


def test_pydantic_schema_accepts_verifiable_unicode_whitespace_citations():
    from sozvon.llm.schema import Document, validate_document

    assert issubclass(Document, BaseModel)
    validated = validate_document(report(), SEGMENTS)
    assert isinstance(validated, Document)
    assert validated.model_dump() == report()


@pytest.mark.parametrize(
    "change",
    [
        "missing_section",
        "empty_report",
        "unknown_segment",
        "invented_quote",
        "empty_evidence",
        "empty_quote",
        "blank_text",
        "extra_sources",
        "extra_item_sources",
        "owner_wrong_type",
        "due_wrong_type",
        "string_section",
        "boolean_segment_id",
        "null_evidence",
        "missing_owner",
    ],
)
def test_invalid_documents_fail_safely(change):
    from sozvon.llm.schema import validate_document

    doc = copy.deepcopy(report())
    if change == "missing_section":
        del doc["risks"]
    elif change == "empty_report":
        doc = {key: [] for key in doc}
    elif change == "unknown_segment":
        doc["summary"][0]["evidence"][0]["segment_id"] = "invented-secret-source"
    elif change == "invented_quote":
        doc["summary"][0]["evidence"][0]["quote"] = "unverified-secret-quote"
    elif change == "empty_evidence":
        doc["summary"][0]["evidence"] = []
    elif change == "empty_quote":
        doc["summary"][0]["evidence"][0]["quote"] = "\u00a0\t\n"
    elif change == "blank_text":
        doc["summary"][0]["text"] = "\u2003"
    elif change == "extra_sources":
        doc["sources"] = ["https://invented.test/secret"]
    elif change == "extra_item_sources":
        doc["summary"][0]["sources"] = ["secret-source"]
    elif change == "owner_wrong_type":
        doc["tasks"][0]["owner"] = 123
    elif change == "due_wrong_type":
        doc["tasks"][0]["due"] = []
    elif change == "string_section":
        doc["decisions"] = "secret-decision"
    elif change == "boolean_segment_id":
        doc["summary"][0]["evidence"][0]["segment_id"] = True
    elif change == "null_evidence":
        doc["summary"][0]["evidence"] = None
    elif change == "missing_owner":
        del doc["tasks"][0]["owner"]

    with pytest.raises(ValueError) as caught:
        validate_document(doc, SEGMENTS)
    assert "secret" not in str(caught.value)
    assert any(word in str(caught.value).lower() for word in ("отчёт", "цитат", "источник"))


def test_every_section_requires_real_evidence():
    from sozvon.llm.schema import validate_document

    for section in ("decisions", "proposals", "questions", "risks"):
        doc = report()
        doc[section] = [{"text": "Неподтверждённое утверждение", "evidence": []}]
        with pytest.raises(ValueError):
            validate_document(doc, SEGMENTS)
