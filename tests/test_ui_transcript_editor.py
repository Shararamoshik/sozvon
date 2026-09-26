"""Browser transcript editor checks with synthetic API fixtures."""
import test_ui_cloud_stt as fixture_ui
from test_ui_cloud_stt import open_meeting

browser = fixture_ui.browser
ui_server = fixture_ui.ui_server
feature_page = fixture_ui.feature_page


def test_edit_survives_poll_and_saves_new_revision(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    page.locator("#edit-transcript").click()
    text = page.locator('[data-edit-text="s1"]')
    text.fill("Срок не определён")
    page.locator('[data-edit-speaker="s1"]').fill("")
    text.focus()
    text.evaluate("e => {window.originalEditor=e; e.setSelectionRange(2,5)}")
    for _ in range(3):
        with page.expect_response("**/api/meetings/synthetic"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    assert text.input_value() == "Срок не определён"
    assert text.evaluate("e => e===window.originalEditor && e.selectionStart===2 && e.selectionEnd===5")
    writes = []

    def save(route):
        writes.append(route.request.post_data_json)
        detail["segments"] = [{**detail["segments"][0], "id": "s2", "revision": 2,
                               "text": "Срок не определён", "speaker": None}]
        route.fulfill(json={"revision": 2, "segments": detail["segments"]})
    page.route("**/api/meetings/synthetic/transcript", save)
    page.keyboard.press("Control+s")
    page.wait_for_function("() => document.getElementById('transcript-status').textContent.includes('Версия 2 сохранена')")
    assert writes == [{"base_revision": 1, "edits": [{"id": "s1", "text": "Срок не определён", "speaker": None}]}]
    assert "0:01" in page.locator("#transcript-content").inner_text()
    assert page.locator("#transcript-content input[type=number]").count() == 0


def test_conflict_keeps_draft(feature_page):
    page, _ = feature_page
    open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator('[data-edit-text="s1"]').fill("Мой черновик")
    page.route("**/transcript", lambda r: r.fulfill(status=409, json={
        "detail": "Конфликт версии", "current_revision": 2}))
    page.locator("#save-transcript").click()
    page.locator("#transcript-conflict").wait_for(state="visible")
    assert page.locator('[data-edit-text="s1"]').input_value() == "Мой черновик"
    assert page.get_by_role("button", name="Открыть новую версию", exact=True).is_visible()
    assert page.get_by_role("button", name="Скопировать мой черновик", exact=True).is_visible()
    assert page.get_by_role("button", name="Перезаписать", exact=True).count() == 0


def test_navigation_guard_also_protects_report_launch(feature_page):
    page, _ = feature_page
    open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator('[data-edit-text="s1"]').fill("Не потерять")
    page.locator('[data-view="library"]').click()
    assert page.locator("#leave-dialog").is_visible()
    page.keyboard.press("Escape")
    assert page.locator('[data-edit-text="s1"]').input_value() == "Не потерять"
    calls = []
    page.route("**/report", lambda r: (calls.append(r.request), r.fulfill(json={"job_id": "j"})))
    page.locator("#report-button").click()
    assert page.locator("#leave-dialog").is_visible()
    assert not calls
    page.locator("#stay-editing").click()
    assert page.locator('[data-edit-text="s1"]').input_value() == "Не потерять"


def test_restore_creates_new_version(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    page.route("**/transcript/revisions", lambda r: r.fulfill(json={"items": [
        {"revision": 1, "origin": "import", "created_at": "2026-01-01"}]}))
    page.route("**/transcript?revision=1", lambda r: r.fulfill(json={"revision": 1,
        "segments": detail["segments"]}))
    calls = []

    def restore(route):
        calls.append(route.request.post_data_json)
        detail["segments"] = [{**detail["segments"][0], "id": "restored", "revision": 2}]
        route.fulfill(json={"revision": 2, "segments": detail["segments"]})
    page.route("**/transcript/restore", restore)
    page.locator("#transcript-history").click()
    page.locator('[data-history-revision="1"]').click()
    page.wait_for_function("() => document.getElementById('history-label').textContent === 'Версия 1, только чтение'")
    assert "Версия 1, только чтение" in page.locator("#history-dialog").inner_text()
    assert page.locator("#history-content textarea").count() == 0
    page.locator("#restore-transcript").click()
    assert not calls
    page.locator("#restore-confirmed").check()
    page.locator("#restore-transcript").click()
    page.wait_for_function("() => !document.getElementById('history-dialog').open")
    assert calls == [{"base_revision": 1, "target_revision": 1}]
    assert "Версия 2 сохранена" in page.locator("#transcript-status").inner_text()


def test_find_replace_only_changes_text(feature_page):
    page, _ = feature_page
    open_meeting(page)
    page.locator("#edit-transcript").click()
    assert page.locator("#preview-replace").is_disabled()
    page.locator("#transcript-search").fill("пятницу")
    page.locator("#transcript-replacement").fill("субботу")
    page.locator("#preview-replace").click()
    assert "Срок в пятницу" in page.locator("#replace-preview").inner_text()
    assert "Срок в субботу" in page.locator("#replace-preview").inner_text()
    assert page.locator('[data-edit-text="s1"]').input_value() == "Срок в пятницу"
    page.locator("#apply-replace").click()
    assert page.locator('[data-edit-text="s1"]').input_value() == "Срок в субботу"
    assert page.locator('[data-edit-speaker="s1"]').input_value() == "Анна"
    assert "0:01" in page.locator("#transcript-content").inner_text()
    assert not page.locator("#save-transcript").is_disabled()
    page.locator('[data-edit-text="s1"]').focus()
    page.keyboard.press("Control+z")
    assert page.locator('[data-edit-text="s1"]').input_value() == "Срок в пятницу"


def test_old_evidence_opens_historical_revision(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    detail["reports"] = [{"id": "r", "created_at": "2026-01-01", "stale": True,
        "transcript_revision": 1, "document": {"summary": [{"text": "Прежний срок", "evidence": [
            {"segment_id": "old", "quote": "Прежняя цитата"}]}]}}]
    detail["segments"][0]["revision"] = 2
    page.route("**/transcript?revision=1", lambda r: r.fulfill(json={"revision": 1,
        "segments": [{"id": "old", "text": "Прежняя цитата", "ordinal": 0,
                      "start_ms": None, "end_ms": None, "speaker": None}]}))
    with page.expect_response("**/api/meetings/synthetic"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.locator('[data-detail-tab="report"]').click()
    page.locator(".evidence-toggle").click()
    page.locator(".evidence-jump").click()
    page.wait_for_function("() => document.getElementById('history-label').textContent === 'Версия 1, только чтение'")
    assert "Прежняя цитата" in page.locator("#history-content").inner_text()
    assert page.locator("#history-content .highlight").count() == 1
    assert "§ 1" in page.locator("#history-content").inner_text()
    assert page.locator("#history-content audio").count() == 0


def test_busy_meeting_cannot_save_transcript(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator('[data-edit-text="s1"]').fill("Не потерять во время задания")
    detail["job"] = {"id": "j", "meeting_id": "synthetic", "operation": "report", "status": "running"}
    with page.expect_response("**/api/meetings/synthetic"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("() => !document.getElementById('job-banner').hidden")
    assert page.locator("#save-transcript").is_disabled()
    assert "задания" in page.locator("#transcript-status").inner_text()
    assert page.locator('[data-edit-text="s1"]').input_value() == "Не потерять во время задания"


def test_navigation_waits_for_pending_save(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator('[data-edit-text="s1"]').fill("Сохранить прежде перехода")
    pending = []
    page.route("**/transcript", lambda route: pending.append(route))
    page.locator("#save-transcript").click()
    page.wait_for_timeout(50)
    assert len(pending) == 1
    page.locator('[data-view="library"]').click()
    assert not page.locator("#leave-dialog").is_visible()
    assert page.locator("#meeting-view").is_visible()
    detail["segments"] = [{**detail["segments"][0], "id": "s2", "revision": 2,
                           "text": "Сохранить прежде перехода"}]
    pending[0].fulfill(json={"revision": 2, "segments": detail["segments"]})
    page.wait_for_function("() => document.getElementById('transcript-status').textContent.includes('Версия 2 сохранена')")
    page.locator('[data-view="library"]').click()
    assert page.locator("#library-view").is_visible()


def test_search_updates_after_typing_and_context_reset(feature_page):
    page, _ = feature_page
    open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator("#transcript-search").fill("пятницу")
    assert "Совпадений: 1" in page.locator("#search-count").inner_text()
    page.locator('[data-edit-text="s1"]').fill("Больше совпадений нет")
    assert "Совпадений: 0" in page.locator("#search-count").inner_text()
    page.locator("#cancel-transcript").click()
    assert "несохранённые" not in page.locator("#transcript-status").inner_text()
    assert "Совпадений: 1" in page.locator("#search-count").inner_text()


def test_focused_textarea_is_not_covered_by_savebar(feature_page):
    page, _ = feature_page
    open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator('[data-edit-text="s1"]').focus()
    geometry = page.evaluate("""() => {
      const input = document.querySelector('[data-edit-text="s1"]').getBoundingClientRect();
      const bar = document.getElementById('transcript-savebar').getBoundingClientRect();
      return {bottom:input.bottom,barTop:bar.top};
    }""")
    assert geometry["bottom"] <= geometry["barTop"]


def test_typing_during_save_preserves_newer_draft_with_compare_actions(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator('[data-edit-text="s1"]').fill("Отправленная правка")
    pending = []
    page.route("**/transcript", lambda r: pending.append(r))
    page.locator("#save-transcript").click()
    page.locator('[data-edit-text="s1"]').fill("Более новая правка")
    detail["segments"] = [{**detail["segments"][0], "id": "saved", "revision": 2,
                           "text": "Отправленная правка"}]
    pending[0].fulfill(json={"revision": 2, "segments": detail["segments"]})
    page.locator("#transcript-conflict").wait_for(state="visible")
    assert page.locator('[data-edit-text="s1"]').input_value() == "Более новая правка"
    assert page.locator("#transcript-open-current").is_visible()


def test_real_backend_editor_and_template_readback(browser, tmp_path):
    """Real FastAPI + SQLite + browser; no model, recording, network or real keys."""
    import socket
    import threading
    import time

    import uvicorn

    from sozvon.web.server import create_app

    app = create_app(tmp_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(5000)
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        page.goto(url + "/#key=" + app.state.launch_key)
        page.wait_for_function("() => !document.getElementById('settings-fields').disabled")
        page.locator("#new-recording").click()
        page.locator("#new-title").fill("Синтетическая проверка UI и SQLite")
        page.locator("#new-text").fill("Срок в пятницу")
        page.locator('#new-text-form button[type="submit"]').click()
        page.locator("#edit-transcript").click()
        page.locator("#transcript-content textarea").fill("Срок не определён")
        page.locator("#save-transcript").click()
        page.wait_for_function("() => document.getElementById('transcript-status').textContent.includes('Версия 2 сохранена')")
        mid = app.state.service.repo.list()[0]["id"]
        detail = app.state.service.repo.detail(mid)
        assert detail["transcript_revision"] == 2
        assert detail["segments"][0]["text"] == "Срок не определён"
        page.locator("#transcript-history").click()
        page.locator('[data-history-revision="1"]').click()
        page.wait_for_function("() => document.getElementById('history-label').textContent.includes('Версия 1, только чтение')")
        assert "Срок в пятницу" in page.locator("#history-content").inner_text()
        page.locator("#restore-confirmed").check()
        page.locator("#restore-transcript").click()
        page.wait_for_function("() => !document.getElementById('history-dialog').open")
        assert app.state.service.repo.detail(mid)["transcript_revision"] == 3
        page.locator('[data-view="templates"]').click()
        page.locator("#new-template").click()
        page.locator("#template-name").fill("Реальная структура без модели")
        page.locator("#template-custom-kind").select_option("tasks")
        page.locator("#template-add-section").click()
        page.locator('[data-section-key^="custom_"] [data-section-field="title"]').fill("Дальнейшие задачи")
        page.locator("#save-template").click()
        page.wait_for_function("() => document.getElementById('template-status').textContent.includes('Сохранена версия 1')")
        page.locator('[data-view="settings"]').click()
        page.locator("#report-template option", has_text="Реальная структура без модели").wait_for(state="attached")
        value = page.locator("#report-template option", has_text="Реальная структура без модели").get_attribute("value")
        page.locator("#report-template").select_option(value)
        page.locator("#save-settings").click()
        page.wait_for_function("() => document.getElementById('settings-status').textContent === 'Настройки сохранены'")
        assert app.state.service.settings.public()["template"] == value
    finally:
        page.close()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()
