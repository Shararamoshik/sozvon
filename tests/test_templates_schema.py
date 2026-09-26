"""Контракт пользовательских шаблонов: только данные, не исполняемый код."""
import pytest


@pytest.mark.parametrize("change", ["duplicate", "name", "disabled", "too_many", "kind",
                                   "missing", "custom_key", "instructions", "title", "language"])
def test_template_rejects_invalid_structure(change):
    from sozvon.templates.builtin import builtin_spec
    from sozvon.templates.schema import TemplateSpec
    spec = builtin_spec("meeting").model_dump()
    if change == "duplicate":
        spec["sections"].append(spec["sections"][0].copy())
    elif change == "name":
        spec["name"] = "  "
    elif change == "disabled":
        for section in spec["sections"]:
            section["enabled"] = False
    elif change == "too_many":
        from uuid import uuid4
        spec["sections"] += [{"key": "custom_" + uuid4().hex, "title": "Ещё"} for _ in range(7)]
    elif change == "kind":
        spec["sections"][0]["kind"] = "tasks"
    elif change == "missing":
        spec["sections"].pop()
    elif change == "custom_key":
        spec["sections"].append({"key": "custom_bad", "title": "Ещё"})
    elif change == "instructions":
        spec["instructions"] = "x" * 4001
    elif change == "title":
        spec["sections"][0]["title"] = "  "
    elif change == "language":
        spec["language"] = "russian"
    with pytest.raises(ValueError):
        TemplateSpec.model_validate(spec)



def test_builtin_specs_are_valid_detached_structures():
    from sozvon.templates.builtin import builtin_spec
    from sozvon.templates.schema import CORE, TemplateSpec

    for key in ("meeting", "client", "technical"):
        spec = builtin_spec(key)
        assert isinstance(spec, TemplateSpec)
        assert [s.key for s in spec.sections] == list(CORE)
        assert spec.sections[3].kind == "tasks"
        spec.name = "Изменено"
        assert builtin_spec(key).name != spec.name


@pytest.mark.parametrize("field,value", [("name", "a\ud800"), ("instructions", "a\x00"), ("title", "a\x01")])
def test_template_text_is_safe_for_utf8_documents(field, value):
    from sozvon.templates.builtin import builtin_spec
    from sozvon.templates.schema import TemplateSpec
    spec = builtin_spec("meeting").model_dump()
    if field == "title":
        spec["sections"][0]["title"] = value
    else:
        spec[field] = value
    with pytest.raises(ValueError):
        TemplateSpec.model_validate(spec)
