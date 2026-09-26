import pytest

from sozvon.storage.repository import Repository
from sozvon.templates.builtin import builtin_spec


def test_create_survives_restart_as_independent_copy(tmp_path):
    from sozvon.storage.templates import create_template, get_template, list_templates
    repo = Repository(tmp_path)
    spec = builtin_spec("client")
    item = create_template(repo, spec)
    assert item["id"] not in {"meeting", "client", "technical"}
    assert not item["builtin"] and not item["archived"] and item["revision"] == 1
    assert item["spec"] == spec.model_dump()
    assert get_template(Repository(tmp_path), item["id"]) == item
    copy = create_template(repo, spec)
    assert copy["id"] != item["id"] and copy["spec"] == item["spec"]
    assert len(list_templates(repo)) == 5


def test_save_keeps_historical_spec_with_cas(tmp_path):
    from sozvon.core.errors import RevisionConflict
    from sozvon.storage.templates import create_template, get_template, save_template
    repo = Repository(tmp_path)
    item = create_template(repo, builtin_spec("meeting"))
    spec = builtin_spec("meeting")
    spec.name = "Мой"
    newer = save_template(repo, item["id"], 1, spec)
    assert newer["revision"] == 2 and newer["spec"]["name"] == "Мой"
    assert get_template(repo, item["id"], 1) == item
    with pytest.raises(RevisionConflict) as caught:
        save_template(repo, item["id"], 1, spec)
    assert caught.value.current_revision == 2
    assert save_template(repo, item["id"], 2, spec) == newer


def test_builtin_is_immutable(tmp_path):
    from sozvon.core.errors import ResourceConflict
    from sozvon.storage.templates import get_template, save_template
    repo = Repository(tmp_path)
    spec = builtin_spec("meeting")
    spec.name = "Изменено"
    with pytest.raises(ResourceConflict):
        save_template(repo, "meeting", 1, spec)
    assert get_template(repo, "meeting")["spec"] == builtin_spec("meeting").model_dump()


def test_archive_protects_default_builtins_and_revisions(tmp_path):
    from sozvon.core.errors import ResourceConflict, RevisionConflict
    from sozvon.storage.templates import (
        archive_template,
        create_template,
        get_template,
        list_templates,
        save_template,
    )
    repo = Repository(tmp_path)
    spec = builtin_spec("meeting")
    item = create_template(repo, spec)
    with pytest.raises(ResourceConflict):
        archive_template(repo, item["id"], 1, item["id"])
    with pytest.raises(ResourceConflict):
        archive_template(repo, "meeting", 1, item["id"])
    with pytest.raises(RevisionConflict):
        archive_template(repo, item["id"], 2, "meeting")
    archive_template(repo, item["id"], 1, "meeting")
    assert get_template(repo, item["id"])["archived"]
    assert all(t["id"] != item["id"] for t in list_templates(repo))
    with pytest.raises(ResourceConflict):
        save_template(repo, item["id"], 1, spec)
    assert get_template(repo, item["id"], 1)["spec"] == item["spec"]


def test_existing_custom_section_cannot_change_kind(tmp_path):
    from uuid import uuid4

    from sozvon.storage.templates import create_template, save_template
    from sozvon.templates.schema import TemplateSpec
    repo = Repository(tmp_path)
    raw = builtin_spec("meeting").model_dump()
    key = "custom_" + uuid4().hex
    raw["sections"].append({"key": key, "title": "Особое", "kind": "statements"})
    item = create_template(repo, TemplateSpec.model_validate(raw))
    assert item["spec"]["sections"][-1]["key"] == key
    raw["sections"][-1]["kind"] = "tasks"
    with pytest.raises(ValueError, match="Тип"):
        save_template(repo, item["id"], 1, TemplateSpec.model_validate(raw))
