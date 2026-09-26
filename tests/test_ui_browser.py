"""Browser UI checks against explicit in-test API fixtures, not backend E2E."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_ui_contract import WEB, render_page

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def ui_server():
    settings = {
        "llm": {"base_url": "http://127.0.0.1:1234/v1", "protocol": "openai", "model": "",
                "configured": False, "allow_remote": False},
        "stt": {"model_path": "", "device": "cpu", "language": "auto"},
        "recording": {"input_device": None, "output_device": None},
        "template": "meeting", "data_dir": "/test-only/data",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                data, mime = render_page().encode(), "text/html; charset=utf-8"
            elif self.path.startswith("/static/"):
                target = WEB / self.path.lstrip("/")
                if not target.is_file():
                    self.send_error(404)
                    return
                data = target.read_bytes()
                mime = {".css": "text/css", ".js": "text/javascript", ".ttf": "font/ttf"}.get(
                    target.suffix, "application/octet-stream"
                )
            elif self.path == "/api/session":
                data, mime = json.dumps({"csrf": "ui-test-only"}).encode(), "application/json"
            elif self.path == "/api/meetings":
                data, mime = b'{"items":[],"job":null}', "application/json"
            elif self.path == "/api/templates":
                from sozvon.templates.builtin import builtin_spec
                items = [{"id": key, "builtin": True, "archived": False, "revision": 1,
                          "spec": builtin_spec(key).model_dump()} for key in ("meeting", "client", "technical")]
                data, mime = json.dumps({"items": items}).encode(), "application/json"
            elif self.path == "/api/settings":
                data, mime = json.dumps(settings).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    worker.join()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except playwright.Error:
            candidates = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
            if not candidates:
                pytest.skip("Install Playwright Chromium to run UI browser checks")
            browser = p.chromium.launch(headless=True, executable_path=str(candidates[-1]))
        yield browser
        browser.close()


@pytest.fixture
def page(browser, ui_server):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(ui_server)
    page.locator("#new-recording").wait_for(state="visible")
    page.wait_for_function("!document.getElementById('new-recording').disabled")
    page.wait_for_function("!document.getElementById('settings-fields').disabled")
    yield page
    page.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (1280, 900), (1024, 768), (640, 450), (390, 844)])
def test_layout_and_native_modal_focus_do_not_overflow(page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.locator("#new-recording").click()
    assert page.locator("#new-dialog").evaluate("element => element.open")
    assert page.locator("#new-title").evaluate("element => document.activeElement === element")
    for _ in range(16):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('new-dialog').contains(document.activeElement)")
    geometry = page.evaluate("({viewport: innerWidth, width: document.documentElement.scrollWidth})")
    assert geometry["viewport"] == width
    assert geometry["width"] <= width
    page.keyboard.press("Escape")
    assert not page.locator("#new-dialog").evaluate("element => element.open")
    assert page.locator("#new-recording").evaluate("element => document.activeElement === element")


def test_search_icon_is_in_same_row_and_saved_key_status_is_honest(page):
    row = page.locator(".search-field").evaluate(
        "e => ({direction:getComputedStyle(e).flexDirection, h:e.getBoundingClientRect().height})"
    )
    assert row["direction"] == "row"
    assert row["h"] < 50
    page.locator('[data-view="settings"]').click()
    assert "не сохранён" in page.locator("#key-state").inner_text()


def test_settings_switch_and_poll_preserve_unsaved_text(page):
    page.locator('[data-view="settings"]').click()
    page.locator("#llm-model").fill("unsaved-test-model")
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-path").fill("/unsaved/local/model")
    page.wait_for_timeout(3500)
    assert page.locator("#llm-model").input_value() == "unsaved-test-model"
    assert page.locator("#stt-path").input_value() == "/unsaved/local/model"
    page.locator('[data-view="library"]').click()
    assert page.locator("#leave-dialog").evaluate("element => element.open")
    page.keyboard.press("Escape")
    assert page.locator("#settings-view").is_visible()
    assert page.locator("#stt-path").input_value() == "/unsaved/local/model"


def test_unauthorized_bootstrap_has_no_login_or_fake_success(browser, ui_server):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/session", lambda route: route.fulfill(status=401, json={"detail": "No session"}))
    page.goto(ui_server)
    page.wait_for_function("document.getElementById('session-status').textContent.includes('ярлык')")
    assert page.locator("#new-recording").is_disabled()
    assert not errors
    page.close()


def test_hidden_invalid_settings_input_reveals_correct_tab_on_submit(page):
    page.locator('[data-view="settings"]').click()
    page.locator("#llm-base").fill("not a valid url")
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#save-settings").click()
    assert page.locator("#settings-llm").is_visible()
    assert page.locator("#llm-base").evaluate("element => document.activeElement === element")


def test_secret_bootstrap_is_removed_before_request_and_not_persisted(browser, ui_server):
    page = browser.new_page()
    requests = []

    def session(route):
        requests.append({
            "method": route.request.method,
            "body": route.request.post_data_json,
        })
        route.fulfill(json={"csrf": "bootstrap-test-only"})

    page.route("**/api/session", session)
    page.goto(ui_server + "/#key=not-a-real-launch-key")
    page.wait_for_function("!document.getElementById('new-recording').disabled")
    assert page.url == ui_server + "/"
    assert requests == [{"method": "POST", "body": {"key": "not-a-real-launch-key"}}]
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    page.close()


def test_failed_import_keeps_text_and_shows_backend_detail(page):
    calls = []

    def fail(route):
        calls.append(route.request)
        route.fulfill(status=409, json={"detail": "Проверяемая причина отказа"})

    page.route("**/api/import/text", fail)
    page.locator("#new-recording").click()
    page.locator("#new-title").fill("Название <img src=x>")
    page.locator("#new-text").fill("Текст не должен пропасть после ошибки.")
    page.locator('#new-text-form button[type="submit"]').click()
    page.locator("#new-error").wait_for(state="visible")
    assert page.locator("#new-error").inner_text() == "Проверяемая причина отказа"
    assert page.locator("#new-text").input_value() == "Текст не должен пропасть после ошибки."
    assert page.locator("#new-dialog").evaluate("element => element.open")
    assert len(calls) == 1
    assert calls[0].headers["x-csrf-token"] == "ui-test-only"


def test_notes_poll_save_and_evidence_render_safely(page):
    detail = {
        "id": "fixture-meeting", "title": "<img src=x onerror=alert(1)>", "kind": "text",
        "status": "ready", "error": None, "notes": "Исходная заметка", "sources": [],
        "job": None, "segments": [{"id": "s-1", "ordinal": 0, "text": "<script>bad()</script>",
                                  "start_ms": None, "end_ms": None, "speaker": None}],
        "reports": [{"id": "r-1", "model": "test-fixture", "created_at": "2026-01-01T00:00:00Z",
                     "document": {"summary": [{"text": "Тестовый пункт", "evidence": [
                         {"segment_id": "s-1", "quote": "<script>bad()</script>"}]}]}}],
    }
    item = {key: detail[key] for key in ("id", "title", "kind", "status", "error")}
    item.update(created_at="2026-01-01T00:00:00Z", segment_count=1, report_count=1)
    saved = []
    page.route("**/api/meetings", lambda route: route.fulfill(json={"items": [item], "job": None}))
    page.route("**/api/meetings/fixture-meeting", lambda route: route.fulfill(json=detail))

    def notes(route):
        text = route.request.post_data_json["text"]
        saved.append(text)
        detail["notes"] = text
        route.fulfill(json={"saved": True})

    page.route("**/api/meetings/fixture-meeting/notes", notes)
    page.locator("#refresh-library").click()
    page.locator('[data-meeting-id="fixture-meeting"]').click()
    page.locator("#panel-report").wait_for(state="visible")
    assert page.locator("#panel-report").is_visible()
    assert page.locator("#meeting-title").inner_text() == detail["title"]
    assert page.locator("#meeting-title img").count() == 0
    page.locator(".evidence-toggle").click()
    page.locator(".evidence-jump").click()
    assert page.locator("#panel-transcript").is_visible()
    assert page.locator(".segment.highlight").count() == 1
    assert page.locator("#transcript-content script").count() == 0
    page.locator('[data-detail-tab="notes"]').click()
    page.locator("#meeting-notes").fill("Черновик, который нельзя стереть")
    page.wait_for_timeout(3500)
    assert page.locator("#meeting-notes").input_value() == "Черновик, который нельзя стереть"
    page.locator("#save-notes").click()
    page.wait_for_function("document.getElementById('notes-status').textContent === 'Заметки сохранены'")
    assert saved == ["Черновик, который нельзя стереть"]
    assert page.locator("#save-notes").is_disabled()


def test_settings_save_reads_back_and_omits_readonly_fields(page):
    writes = []
    current = {}

    def settings(route):
        if route.request.method == "PUT":
            payload = route.request.post_data_json
            writes.append(payload)
            current.update(payload)
            current["llm"]["configured"] = True
            current["data_dir"] = "/test-only/data"
            route.fulfill(json={"saved": True})
        else:
            route.fulfill(json=current)

    page.route("**/api/settings", settings)
    page.locator('[data-view="settings"]').click()
    page.locator("#llm-model").fill("test-model")
    page.locator("#save-settings").click()
    page.wait_for_function("document.getElementById('settings-status').textContent === 'Настройки сохранены'")
    assert len(writes) == 1
    assert "data_dir" not in writes[0]
    assert "api_key" not in writes[0]["llm"]
    assert "delete_key" not in writes[0]["llm"]
    assert page.locator("#llm-model").input_value() == "test-model"
    assert page.locator("#save-settings").is_disabled()


def test_assets_console_and_network_are_local(browser, ui_server):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    urls, errors, bad_responses = [], [], []
    page.on("request", lambda request: urls.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("response", lambda response: bad_responses.append(response.url) if response.status >= 400 else None)
    page.goto(ui_server)
    page.wait_for_function("!document.getElementById('settings-fields').disabled")
    page.evaluate("document.fonts.ready")
    page.locator('[data-view="settings"]').click()
    page.locator('[data-settings-tab="stt"]').click()
    page.locator('[data-view="about"]').click()
    assert not errors
    assert not bad_responses
    assert all(url.startswith(ui_server + "/") for url in urls)
    page.close()


def test_library_status_tracks_job_only_changes(page):
    item = {"id": "fixture", "title": "Тест обновления статуса", "created_at": "2026-01-01",
            "kind": "audio", "status": "processing", "error": None, "segment_count": 0,
            "report_count": 0, "duration_ms": 1000}
    job = {"id": "job-fixture", "meeting_id": "fixture", "operation": "transcribe",
           "status": "running", "stage": "Чтение файла", "progress": None, "error": None}
    page.route("**/api/meetings", lambda route: route.fulfill(json={"items": [item], "job": job}))
    page.locator("#refresh-library").click()
    page.wait_for_function("document.querySelector('.status-badge')?.textContent === 'Чтение файла'")
    job["stage"] = "Распознавание"
    page.locator("#refresh-library").click()
    page.wait_for_function("document.querySelector('.status-badge')?.textContent === 'Распознавание'")


def test_job_error_stays_visible_and_cancelling_cannot_be_stopped_twice(page):
    job = {"id": "job-fixture", "meeting_id": "fixture", "operation": "record",
           "status": "cancelling", "stage": "Сохраняем запись", "progress": None, "error": None}
    page.route("**/api/meetings", lambda route: route.fulfill(json={"items": [], "job": job}))
    page.locator("#refresh-library").click()
    page.locator("#job-banner").wait_for(state="visible")
    assert page.locator("#stop-job").is_disabled()
    job.update(status="failed", error="Не удалось сохранить дорожку")
    page.locator("#refresh-library").click()
    page.locator("#global-error").wait_for(state="visible")
    assert "Не удалось сохранить дорожку" in page.locator("#global-error").inner_text()


def test_200_percent_zoom_keeps_settings_save_reachable(page):
    page.locator('[data-view="settings"]').click()
    page.evaluate("document.documentElement.style.zoom = '2'")
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-path").fill("/zoom-test/model")
    button = page.locator("#save-settings")
    button.scroll_into_view_if_needed()
    assert button.is_visible()
    geometry = page.evaluate("({viewport:innerWidth,width:document.documentElement.scrollWidth})")
    assert geometry["width"] <= geometry["viewport"]
    rect = button.bounding_box()
    assert rect and 0 <= rect["x"] < 1280 and rect["x"] + rect["width"] <= 1280


def test_stale_report_is_marked_and_cannot_jump_to_new_transcript(page):
    detail = {"id":"stale", "title":"Устаревший отчёт", "kind":"text", "status":"ready",
              "error":None, "notes":"", "sources":[], "job":None,
              "segments":[{"id":"new", "ordinal":0, "text":"Срок 2 мая"}],
              "reports":[{"id":"r", "model":"test", "created_at":"2026-01-01",
                          "stale":True, "document":{"summary":[{"text":"Срок 1 мая",
                          "evidence":[{"segment_id":"old","quote":"Срок 1 мая"}]}]}}]}
    item = {"id":"stale", "title":"Устаревший отчёт", "kind":"text", "status":"ready",
            "created_at":"2026-01-01", "error":None, "segment_count":1, "report_count":1}
    page.route("**/api/meetings",lambda route:route.fulfill(json={"items":[item],"job":None}))
    page.route("**/api/meetings/stale",lambda route:route.fulfill(json=detail))
    page.locator("#refresh-library").click()
    page.locator('[data-meeting-id="stale"]').click()
    page.locator("#panel-report").wait_for(state="visible")
    assert "устарел" in page.locator("#report-content").inner_text().lower()
    page.locator(".evidence-toggle").click()
    assert page.locator(".evidence-jump").is_disabled()
    page.route("**/api/meetings/stale/export?**", lambda route: route.fulfill(
        status=409, json={"detail": "Отчёт устарел: создайте новую версию"}))
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.locator("#export-button").click()
    page.locator("#export-format").select_option("md")
    page.locator("#download-export").click()
    page.locator("#export-error").wait_for(state="visible")
    assert "устарел" in page.locator("#export-error").inner_text()
    assert not downloads
    page.locator("#export-content").select_option("transcript")
    assert not page.locator("#download-export").is_disabled()
