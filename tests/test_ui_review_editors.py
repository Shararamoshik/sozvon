"""Chromium editor regressions against explicitly synthetic local API responses."""
from copy import deepcopy

import pytest
import test_ui_cloud_stt as fixture_ui
from playwright.sync_api import expect
from test_ui_cloud_stt import open_meeting
from test_ui_templates import template_routes

browser = fixture_ui.browser
ui_server = fixture_ui.ui_server
feature_page = fixture_ui.feature_page


def release_response(page, route, **response):
    """Release a held HTTP response and let its browser rendering finish."""
    with page.expect_event("requestfinished", predicate=lambda request: request.url == route.request.url):
        route.fulfill(**response)
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")


def test_pending_template_get_cannot_replace_new_draft(feature_page):
    page, state = feature_page
    template_routes(page, state)
    pending = []
    page.route("**/api/templates/meeting", lambda route: pending.append(route))
    page.locator('[data-view="templates"]').click()
    with page.expect_request("**/api/templates/meeting"):
        page.locator('[data-template-id="meeting"]').click()
    page.locator("#new-template").click()
    page.locator("#template-name").fill("Не терять новый черновик")
    page.locator("#template-name").evaluate("e => { window.draftField = e; }")
    release_response(page, pending[0], json=deepcopy(state["templates"][0]))
    expect(page.locator("#template-name")).to_have_value("Не терять новый черновик")
    expect(page.locator("#template-name")).to_be_enabled()
    assert page.locator("#template-name").evaluate("e => e === window.draftField")
    page.locator('[data-view="library"]').click()
    expect(page.locator("#leave-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#template-name")).to_have_value("Не терять новый черновик")


def test_reverting_to_original_during_save_keeps_navigation_guard(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    original = detail["segments"][0]["text"]
    page.locator("#edit-transcript").click()
    text = page.locator('[data-edit-text="s1"]')
    text.fill("Отправленная версия B")
    text.evaluate("e => { window.draftField = e; }")
    pending = []
    page.route("**/api/meetings/synthetic/transcript", lambda route: pending.append(route))
    with page.expect_request("**/api/meetings/synthetic/transcript"):
        page.locator("#save-transcript").click()
    text.fill(original)
    assert pending[0].request.post_data_json["edits"][0]["text"] == "Отправленная версия B"
    detail["segments"] = [{**detail["segments"][0], "id": "saved", "revision": 2,
                           "text": "Отправленная версия B"}]
    release_response(page, pending[0], json={"revision": 2, "segments": detail["segments"]})
    expect(page.locator("#transcript-conflict")).to_be_visible()
    expect(page.locator("#cancel-transcript")).to_be_enabled()
    expect(text).to_have_value(original)
    assert text.evaluate("e => e === window.draftField")
    page.locator('[data-view="library"]').click()
    expect(page.locator("#leave-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(text).to_have_value(original)
    page.locator("#transcript-open-current").click()
    expect(page.locator("#transcript-current-preview")).to_contain_text("Отправленная версия B")
    with page.expect_response("**/api/meetings/synthetic"):
        page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.locator("#report-button").click()
    expect(page.locator("#leave-dialog")).to_be_visible()
    page.locator("#stay-editing").click()
    expect(text).to_have_value(original)
    page.locator("#cancel-transcript").click()
    page.locator('[data-view="library"]').click()
    expect(page.locator("#leave-dialog")).not_to_be_visible()
    expect(page.locator("#library-view")).to_be_visible()


def test_template_save_unlocks_add_section_without_erasing_saved_status(feature_page):
    page, state = feature_page
    template_routes(page, state)
    page.locator('[data-view="templates"]').click()
    page.locator("#new-template").click()
    page.locator("#template-name").fill("Шаблон с продолжением")
    page.locator("#save-template").click()
    expect(page.locator("#template-status")).to_have_text("Сохранена версия 1")
    expect(page.locator("#save-template")).to_be_disabled()
    expect(page.locator("#template-add-section")).to_be_enabled()
    expect(page.locator("#template-status")).to_have_text("Сохранена версия 1")
    page.locator("#template-add-section").click()
    expect(page.locator('[data-section-key^="custom_"]')).to_have_count(1)
    expect(page.locator("#save-template")).to_be_enabled()
    page.locator("#save-template").click()
    expect(page.locator("#template-status")).to_have_text("Сохранена версия 2")
    expect(page.locator("#template-add-section")).to_be_enabled()
    assert len(state["templates"][-1]["spec"]["sections"]) == 7


def test_template_section_moved_to_boundary_keeps_keyboard_focus(feature_page):
    page, state = feature_page
    template_routes(page, state)
    page.locator('[data-view="templates"]').click()
    page.locator("#new-template").click()
    row = page.locator('[data-section-key="decisions"]')
    row.locator('[data-move="up"]').focus()
    page.keyboard.press("Enter")
    expect(page.locator("#template-sections > section").first).to_have_attribute("data-section-key", "decisions")
    expect(row.locator('[data-section-field="title"]')).to_be_focused()
    page.locator('[data-section-key="questions"] [data-move="down"]').focus()
    page.keyboard.press("Enter")
    expect(page.locator('[data-section-key="questions"] [data-section-field="title"]')).to_be_focused()


def test_removing_template_section_focuses_a_surviving_control(feature_page):
    page, state = feature_page
    template_routes(page, state)
    page.locator('[data-view="templates"]').click()
    page.locator("#new-template").click()
    page.locator("#template-add-section").click()
    row = page.locator('[data-section-key^="custom_"]')
    row.get_by_role("button", name="Удалить раздел").focus()
    page.keyboard.press("Enter")
    expect(row).to_have_count(0)
    expect(page.locator('[data-section-key="risks"] [data-section-field="title"]')).to_be_focused()


@pytest.mark.parametrize("close_method", ["button", "escape"])
def test_closed_history_version_cannot_replace_reopened_history(feature_page, close_method):
    page, _ = feature_page
    detail = open_meeting(page)
    page.route("**/transcript/revisions", lambda route: route.fulfill(json={"items": [
        {"revision": 1, "origin": "import", "created_at": "2026-01-01"}]}))
    pending = []
    page.route("**/transcript?revision=1", lambda route: pending.append(route))
    page.locator("#transcript-history").click()
    with page.expect_request("**/transcript?revision=1"):
        page.locator('[data-history-revision="1"]').click()
    if close_method == "escape":
        page.keyboard.press("Escape")
    else:
        page.locator("#close-history").click()
    expect(page.locator("#history-dialog")).not_to_be_visible()
    page.locator("#transcript-history").click()
    expect(page.locator("#history-label")).to_have_text("Выберите версию")
    release_response(page, pending[0], json={"revision": 1, "segments": detail["segments"]})
    expect(page.locator("#history-label")).to_have_text("Выберите версию")
    expect(page.locator("#history-content")).to_be_empty()
    expect(page.locator("#history-restore")).not_to_be_visible()
    assert page.locator("#history-dialog").evaluate("e => e.contains(document.activeElement)")


@pytest.mark.parametrize("stale_response", [
    {"json": {"items": [{"revision": 1, "origin": "import", "created_at": "2026-01-01"}]}},
    {"status": 500, "json": {"detail": "Устаревшая ошибка истории"}},
], ids=["list", "error"])
def test_closed_history_list_cannot_pollute_reopened_history(feature_page, stale_response):
    page, _ = feature_page
    open_meeting(page)
    pending = []
    page.route("**/transcript/revisions", lambda route: pending.append(route))
    with page.expect_request("**/transcript/revisions"):
        page.locator("#transcript-history").click()
    page.locator("#close-history").click()
    with page.expect_request("**/transcript/revisions"):
        page.locator("#transcript-history").click()
    release_response(page, pending[1], json={"items": [
        {"revision": 2, "origin": "manual", "created_at": "2026-01-02"}]})
    expect(page.locator('[data-history-revision="2"]')).to_be_visible()
    release_response(page, pending[0], **stale_response)
    expect(page.locator("#history-list button")).to_have_count(1)
    expect(page.locator('[data-history-revision="2"]')).to_be_visible()
    expect(page.locator("#history-error")).not_to_be_visible()


@pytest.mark.parametrize("release_while_hidden", [False, True])
def test_template_view_reentry_invalidates_pending_selection(feature_page, release_while_hidden):
    page, state = feature_page
    template_routes(page, state)
    pending = []
    page.route("**/api/templates/meeting", lambda route: pending.append(route))
    page.locator('[data-view="templates"]').click()
    with page.expect_request("**/api/templates/meeting"):
        page.locator('[data-template-id="meeting"]').click()
    page.locator('[data-view="library"]').click()
    if release_while_hidden:
        release_response(page, pending[0], json=deepcopy(state["templates"][0]))
    page.locator('[data-view="templates"]').click()
    if not release_while_hidden:
        release_response(page, pending[0], json=deepcopy(state["templates"][0]))
    expect(page.locator("#template-form")).not_to_be_visible()


def test_pending_template_selection_rechecks_dirty_fields(feature_page):
    page, state = feature_page
    template_routes(page, state)
    state["templates"].append({"id": "mine", "builtin": False, "archived": False,
                               "revision": 1, "spec": deepcopy(state["templates"][0]["spec"])})
    page.locator('[data-view="templates"]').click()
    page.locator('[data-template-id="mine"]').click()
    expect(page.locator("#template-name")).to_be_enabled()
    pending = []
    page.route("**/api/templates/meeting", lambda route: pending.append(route))
    with page.expect_request("**/api/templates/meeting"):
        page.locator('[data-template-id="meeting"]').click()
    page.locator("#template-name").fill("Правка во время загрузки")
    release_response(page, pending[0], json=deepcopy(state["templates"][0]))
    expect(page.locator("#template-name")).to_have_value("Правка во время загрузки")
    expect(page.locator("#template-name")).to_be_enabled()
    page.locator('[data-view="library"]').click()
    expect(page.locator("#leave-dialog")).to_be_visible()


def test_latest_template_selection_wins_out_of_order_responses(feature_page):
    page, state = feature_page
    template_routes(page, state)
    state["templates"].append({"id": "mine", "builtin": False, "archived": False,
                               "revision": 1, "spec": {**deepcopy(state["templates"][0]["spec"]),
                                                       "name": "Последний выбор"}})
    pending = []
    page.route("**/api/templates/meeting", lambda route: pending.append(route))
    page.locator('[data-view="templates"]').click()
    with page.expect_request("**/api/templates/meeting"):
        page.locator('[data-template-id="meeting"]').click()
    page.locator('[data-template-id="mine"]').click()
    expect(page.locator("#template-name")).to_have_value("Последний выбор")
    release_response(page, pending[0], json=deepcopy(state["templates"][0]))
    expect(page.locator("#template-name")).to_have_value("Последний выбор")
    expect(page.locator("#template-name")).to_be_enabled()
