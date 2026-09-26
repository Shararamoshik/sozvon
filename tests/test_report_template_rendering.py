from sozvon.templates.builtin import builtin_spec
from sozvon.templates.schema import CORE, LABELS


def test_report_sections_follow_snapshot_not_current_template(tmp_path):
    from uuid import uuid4

    from sozvon.templates.rendering import sections_for_report
    key = "custom_" + uuid4().hex
    spec = builtin_spec("meeting").model_dump()
    spec["sections"][0]["title"] = "Сохранённое название"
    spec["sections"][1]["enabled"] = False
    spec["sections"].insert(0, {"key": key, "title": "Мой раздел", "enabled": True, "kind": "tasks"})
    report = {"meta": {"template_snapshot": {"id": "snapshot", "revision": 1, "spec": spec}},
              "document": {"summary": [{"text": "Итог"}], "custom_sections": {
                  key: {"kind": "tasks", "items": [{"text": "Задача"}]}}}}
    sections = sections_for_report(report)
    assert sections[0] == {"key": key, "title": "Мой раздел", "kind": "tasks", "items": [{"text": "Задача"}]}
    assert sections[1]["title"] == "Сохранённое название"
    assert "decisions" not in [s["key"] for s in sections]


def test_legacy_report_has_fixed_six_sections():
    from sozvon.templates.rendering import sections_for_report
    report = {"meta": {}, "document": {"summary": [{"text": "Итог"}]}}
    sections = sections_for_report(report)
    assert [s["key"] for s in sections] == list(CORE)
    assert [s["title"] for s in sections] == [LABELS[key] for key in CORE]
    assert sections[3]["kind"] == "tasks" and sections[3]["items"] == []
    assert "template_snapshot" not in report["meta"]


def test_repository_report_sections_remain_frozen_after_template_edit(tmp_path):
    from sozvon.storage.repository import Repository
    from sozvon.storage.templates import create_template, save_template
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Есть план")
    spec = builtin_spec("meeting")
    template = create_template(repo, spec)
    snapshot = {key: template[key] for key in ("id", "revision", "spec")}
    repo.save_report(mid, {"model": "test", "document": {"summary": []}, "template_snapshot": snapshot})
    original = repo.detail(mid)["reports"][0]
    assert original["sections"][0]["title"] == "Кратко"
    spec.sections[0].title = "Теперь иначе"
    save_template(repo, template["id"], 1, spec)
    assert repo.detail(mid)["reports"][0] == original
