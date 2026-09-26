"""Template management UI against synthetic contract, no model calls."""
from copy import deepcopy

import test_ui_cloud_stt as fixture_ui
from test_ui_cloud_stt import SPEC, open_meeting

browser = fixture_ui.browser
ui_server = fixture_ui.ui_server
feature_page = fixture_ui.feature_page


def template_routes(page, state):
    writes = []

    def templates(route):
        method = route.request.method
        path = route.request.url.split("/api/templates", 1)[1]
        payload = route.request.post_data_json if method != "GET" else None
        if method != "GET":
            writes.append((method, path, payload))
        items = state["templates"]
        target = next((item for item in items if path.split("/")[1:2] == [item["id"]]), None)
        if method == "GET":
            route.fulfill(json=target if path else {"items": [i for i in items if not i["archived"]]})
        elif path.endswith("/archive"):
            target["archived"] = True
            route.fulfill(json={"archived": True})
        elif path.endswith("/copy") or method == "POST":
            spec = deepcopy(target["spec"] if path else payload["spec"])
            if path:
                spec["name"] = payload.get("name", spec["name"] + " — копия")
            item = {"id": f"synthetic-{len(items)}", "builtin": False,
                    "archived": False, "revision": 1, "spec": spec}
            items.append(item)
            route.fulfill(status=201, json=item)
        else:
            assert payload["base_revision"] == target["revision"]
            target.update(spec=payload["spec"], revision=target["revision"] + 1)
            route.fulfill(json=target)
    page.route("**/api/templates**", templates)
    return writes


def test_create_copy_edit_archive_template(feature_page):
    page, state = feature_page
    writes = template_routes(page, state)
    page.locator('[data-view="templates"]').click()
    page.locator('[data-template-id="meeting"]').click()
    page.locator("#template-form").wait_for(state="visible")
    assert page.locator("#template-name").is_disabled()
    page.locator("#template-copy").click()
    page.wait_for_function("!document.getElementById('template-fields').disabled")
    page.locator("#template-name").fill("Мой шаблон")
    page.locator("#template-custom-kind").select_option("tasks")
    page.locator("#template-add-section").click()
    custom = page.locator('[data-section-key^="custom_"]')
    key = custom.get_attribute("data-section-key")
    assert len(key) == len("custom_") + 32
    custom.locator('[data-section-field="title"]').fill("Мои задачи <img>")
    custom.locator('[data-move="up"]').click()
    page.locator("#save-template").click()
    page.wait_for_function("document.getElementById('template-status').textContent.includes('Сохранена версия 2')")
    saved = state["templates"][-1]
    assert saved["spec"]["name"] == "Мой шаблон"
    assert saved["spec"]["sections"][-2]["key"] == key
    assert saved["spec"]["sections"][-2]["kind"] == "tasks"
    page.reload()
    page.locator('[data-view="templates"]').click()
    page.locator('[data-template-id="synthetic-1"]').click()
    page.locator("#template-form").wait_for(state="visible")
    assert page.locator("#template-name").input_value() == "Мой шаблон"
    page.locator("#archive-template").click()
    page.locator("#confirm-template-archive").click()
    page.wait_for_function("!document.querySelector('[data-template-id=\"synthetic-1\"]')")
    assert state["templates"][-1]["archived"]
    page.locator("#new-template").click()
    page.locator("#template-name").fill("С нуля")
    page.locator("#save-template").click()
    page.wait_for_function("document.getElementById('template-status').textContent.includes('Сохранена версия 1')")
    assert state["templates"][-1]["spec"]["name"] == "С нуля"
    assert writes[0][1] == "/meeting/copy"


def test_template_conflict_and_dirty_guard_keep_fields(feature_page):
    page, state = feature_page
    template_routes(page, state)
    state["templates"].append({"id": "mine", "builtin": False, "archived": False,
                               "revision": 1, "spec": deepcopy(SPEC)})
    page.locator('[data-view="templates"]').click()
    page.locator('[data-template-id="mine"]').click()
    page.locator("#template-name").fill("Черновик шаблона")
    page.route("**/api/templates/mine", lambda r: r.fulfill(status=409, json={
        "detail": "Шаблон изменён в другой вкладке", "current_revision": 2}))
    page.locator("#save-template").click()
    page.locator("#templates-error").wait_for(state="visible")
    assert page.locator("#template-name").input_value() == "Черновик шаблона"
    page.locator('[data-view="library"]').click()
    assert page.locator("#leave-dialog").is_visible()
    page.keyboard.press("Escape")
    assert page.locator("#template-name").input_value() == "Черновик шаблона"
    page.locator('[data-view="library"]').click()
    page.locator("#discard-editing").click()
    assert page.locator("#library-view").is_visible()


def test_template_preview_makes_no_model_request(feature_page):
    page, state = feature_page
    template_routes(page, state)
    requests = []
    page.on("request", lambda r: requests.append(r.url) if r.method != "GET" else None)
    page.locator('[data-view="templates"]').click()
    page.locator("#new-template").click()
    page.locator("#template-name").fill("Пример")
    page.locator('[data-section-key="summary"] [data-section-field="title"]').fill("<script>Структура</script>")
    page.locator("#template-preview-button").click()
    assert "Пример структуры" in page.locator("#template-preview-dialog").inner_text()
    assert "<script>Структура</script>" in page.locator("#template-preview-dialog").inner_text()
    assert not requests
    assert page.locator("#template-preview-dialog script").count() == 0
    page.keyboard.press("Escape")
    assert page.locator("#template-preview-button").evaluate("e => e===document.activeElement")


def test_report_uses_snapshot_section_names(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    detail["reports"] = [{"id": "r", "created_at": "2026-01-01", "document": {},
        "sections": [{"key": "custom_" + "a" * 32, "title": "Снимок <img>", "kind": "tasks",
                      "items": [{"text": "Проверить", "owner": None, "due": None, "evidence": []}]}]}]
    with page.expect_response("**/api/meetings/synthetic"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.locator('[data-detail-tab="report"]').click()
    page.get_by_role("heading", name="Снимок <img>").wait_for(state="visible")
    assert "Кто: не указан" in page.locator("#report-content").inner_text()
    assert page.locator("#report-content img").count() == 0


def test_default_template_is_dynamic_and_saved(feature_page):
    page, state = feature_page
    state["templates"].append({"id": "personal", "builtin": False, "archived": False,
                               "revision": 4, "spec": {**deepcopy(SPEC), "name": "Личный"}})
    page.locator('[data-view="settings"]').click()
    page.locator("#report-template").select_option("personal")
    page.locator("#save-settings").click()
    page.wait_for_function("document.getElementById('settings-status').textContent === 'Настройки сохранены'")
    assert state["writes"][-1]["template"] == "personal"
    assert page.locator("#open-templates").is_visible()


def test_template_ctrl_s_saves_only_active_editor(feature_page):
    page, state = feature_page
    template_routes(page, state)
    page.locator('[data-view="templates"]').click()
    page.locator("#new-template").click()
    page.locator("#template-name").fill("Клавиатура")
    page.keyboard.press("Control+s")
    page.wait_for_function("document.getElementById('template-status').textContent.includes('Сохранена версия 1')")
    assert state["templates"][-1]["spec"]["name"] == "Клавиатура"
