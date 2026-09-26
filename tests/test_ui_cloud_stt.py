"""Rendered UI against explicitly synthetic v0.2 API contracts (not backend E2E)."""
from copy import deepcopy

import pytest
import test_ui_browser as base_ui

browser = base_ui.browser
ui_server = base_ui.ui_server

CORE = [("summary", "Кратко"), ("decisions", "Решения"), ("proposals", "Предложения"),
        ("tasks", "Задачи"), ("questions", "Вопросы"), ("risks", "Риски")]
SPEC = {"name": "Рабочая встреча", "description": "", "language": "ru", "detail": "normal",
        "instructions": "", "sections": [{"key": key, "title": title,
        "kind": "tasks" if key == "tasks" else "statements", "enabled": True,
        "instructions": ""} for key, title in CORE]}
SETTINGS = {"llm": {"base_url": "http://127.0.0.1:1234/v1", "model": "synthetic",
                   "protocol": "openai", "allow_remote": False, "configured": False},
            "stt": {"engine": "local", "model_path": "", "device": "cpu", "language": "auto",
                    "cloud": {"base_url": "https://stt.example/v1", "model": "synthetic-stt",
                    "response_format": "verbose_json", "timeout_s": 120,
                    "max_upload_bytes": 24000000, "allow_remote": False, "configured": False,
                    "endpoint": "https://stt.example/v1/audio/transcriptions"}},
            "recording": {"input_device": None, "output_device": None},
            "template": "meeting", "data_dir": "/synthetic/data"}


@pytest.fixture
def feature_page(browser, ui_server):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(3500)
    state = {"settings": deepcopy(SETTINGS), "writes": [], "errors": [],
             "templates": [{"id": "meeting", "revision": 1, "builtin": True,
                            "archived": False, "spec": deepcopy(SPEC)}]}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))

    def merge_settings(current, patch):
        # SettingsStore applies nested patches; omitted cloud fields stay saved.
        # Public readback exposes configured, never the submitted secret itself.
        for key, value in patch.items():
            if key in {"api_key", "delete_key"}:
                continue
            if isinstance(value, dict):
                merge_settings(current.setdefault(key, {}), value)
            else:
                current[key] = value
        if patch.get("delete_key"):
            current["configured"] = False
        elif patch.get("api_key"):
            current["configured"] = True

    def settings(route):
        if route.request.method == "PUT":
            payload = route.request.post_data_json
            state["writes"].append(payload)
            merge_settings(state["settings"], payload)
            route.fulfill(json={"saved": True})
        else:
            route.fulfill(json=state["settings"])
    page.route("**/api/settings", settings)
    page.route("**/api/templates", lambda r: r.fulfill(json={"items": state["templates"]}))
    page.goto(ui_server)
    page.wait_for_function("!document.getElementById('settings-fields').disabled")
    yield page, state
    assert not state["errors"]
    page.close()


def open_meeting(page, detail=None):
    detail = detail or {"id": "synthetic", "title": "Синтетическая встреча", "kind": "audio",
        "status": "ready", "error": None, "notes": "", "sources": [], "job": None,
        "segments": [{"id": "s1", "ordinal": 0, "revision": 1, "text": "Срок в пятницу",
                      "speaker": "Анна", "start_ms": 1000, "end_ms": 2500}], "reports": []}
    item = {**detail, "created_at": "2026-01-01", "segment_count": len(detail["segments"]),
            "report_count": len(detail["reports"])}
    page.route("**/api/meetings", lambda r: r.fulfill(json={"items": [item], "job": detail["job"]}))
    page.route("**/api/meetings/" + detail["id"], lambda r: r.fulfill(json=detail))
    page.locator('[data-view="library"]').click()
    page.locator("#refresh-library").click()
    page.locator('[data-meeting-id="' + detail["id"] + '"]').click()
    return detail


def test_switch_cloud_hides_local_model_requirement(feature_page):
    page, state = feature_page
    page.locator('[data-view="settings"]').click()
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-engine").select_option("cloud")
    assert not page.locator("#stt-path").is_visible()
    assert not page.locator("#stt-path").evaluate("e => e.required")
    assert page.locator("#stt-cloud-base").is_visible()
    page.locator("#stt-cloud-base").fill("https://synthetic.example/v1")
    page.locator("#stt-cloud-allow-remote").check()
    page.locator("#stt-cloud-base").fill("https://different.example/v1")
    assert not page.locator("#stt-cloud-allow-remote").is_checked()
    page.locator("#save-settings").click()
    page.wait_for_function("document.getElementById('settings-status').textContent === 'Настройки сохранены'")
    payload = state["writes"][-1]
    assert payload["stt"]["engine"] == "cloud"
    assert payload["stt"]["cloud"]["base_url"] == "https://different.example/v1"
    assert "endpoint" not in payload["stt"]["cloud"]
    assert "configured" not in payload["stt"]["cloud"]
    assert "api_key" not in payload["llm"]


def test_cloud_submit_requires_endpoint_confirmation(feature_page):
    page, state = feature_page
    state["settings"]["stt"]["engine"] = "cloud"
    state["settings"]["stt"]["cloud"].update(configured=True, allow_remote=True)
    page.reload()
    page.wait_for_function("!document.getElementById('settings-fields').disabled")
    open_meeting(page)
    calls = []

    def reject(route):
        calls.append(route.request.post_data_json)
        route.fulfill(status=409, json={"detail": "Адрес STT изменился: подтвердите новый"})
    page.route("**/transcribe", reject)
    page.locator("#transcribe-button").click()
    page.locator("#stt-confirm-dialog").wait_for(state="visible")
    assert page.locator("#stt-confirm-dialog").is_visible()
    assert "https://stt.example/v1/audio/transcriptions" in page.locator("#stt-confirm-dialog").inner_text()
    assert not calls
    page.locator("#stt-replace-confirmed").check()
    page.locator("#confirm-stt").click()
    page.locator("#stt-confirm-error").wait_for(state="visible")
    assert "Адрес STT изменился" in page.locator("#stt-confirm-error").inner_text()
    assert calls == [{"base_revision": 1, "replace_confirmed": True,
                      "cloud_confirmed_url": "https://stt.example/v1/audio/transcriptions"}]
    page.keyboard.press("Escape")
    assert page.locator("#transcribe-button").evaluate("e => e === document.activeElement")


def test_interrupted_job_is_terminal_for_record_dialog(feature_page):
    page, _ = feature_page
    page.route("**/api/meetings", lambda r: r.fulfill(json={"items": [], "job": {
        "id": "j", "meeting_id": "m", "operation": "record", "status": "interrupted"}}))
    page.locator("#refresh-library").click()
    with page.expect_response("**/api/meetings"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.locator("#new-recording").click()
    page.locator('[data-new-tab="record"]').click()
    assert not page.locator('#record-form button[type="submit"]').is_disabled()


def test_cloud_secret_is_separate_cleared_on_error_and_readback_checked(feature_page):
    page, state = feature_page
    page.locator('[data-view="settings"]').click()
    page.locator("#llm-key").fill("synthetic-not-a-real-llm-key")
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-engine").select_option("cloud")
    page.locator("#stt-cloud-key").fill("synthetic-not-a-real-stt-key")
    calls = []

    def reject(route):
        calls.append(route.request.post_data_json)
        route.fulfill(status=422, json={"detail": "Тестовый отказ"})
    page.route("**/api/settings", reject)
    page.locator("#save-settings").click()
    page.locator("#settings-error").wait_for(state="visible")
    assert calls == []
    assert "LLM" in page.locator("#settings-error").inner_text()
    assert "STT" in page.locator("#settings-error").inner_text()
    assert page.locator("#llm-key").input_value() == "synthetic-not-a-real-llm-key"
    assert page.locator("#stt-cloud-key").input_value() == "synthetic-not-a-real-stt-key"
    # Resolve the explicit conflict, then exercise the actual failed PUT.
    page.locator('[data-settings-tab="llm"]').click()
    page.locator("#llm-key").fill("")
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#save-settings").click()
    page.wait_for_function("document.getElementById('settings-error').textContent === 'Тестовый отказ'")
    assert calls[0]["stt"]["cloud"]["api_key"] == "synthetic-not-a-real-stt-key"
    assert "api_key" not in calls[0]["llm"]
    assert page.locator("#stt-cloud-key").input_value() == ""
    assert page.locator("#llm-key").input_value() == ""
    assert page.locator("#stt-cloud-model").input_value() == "synthetic-stt"

    def wrong_readback(route):
        if route.request.method == "PUT":
            route.fulfill(json={"saved": True})
        else:
            route.fulfill(json=state["settings"])
    page.route("**/api/settings", wrong_readback)
    page.locator("#save-settings").click()
    page.wait_for_function("document.getElementById('settings-status').textContent.includes('Сохранение не подтверждено')")
    assert page.locator("#stt-engine").input_value() == "cloud"


def test_about_does_not_claim_cloud_or_docx_are_unavailable(feature_page):
    page, _ = feature_page
    page.locator('[data-view="about"]').click()
    text = page.locator("#about-view").inner_text()
    assert "готовые экспорты DOCX" not in text
    assert "Распознавание — локально." not in text
    assert "SRT" in text


def test_api_errors_expose_status_and_payload_including_network(feature_page):
    page, _ = feature_page
    page.route("**/api/synthetic-error", lambda r: r.fulfill(status=409, json={
        "detail": "Конфликт", "current_revision": 7}))
    result = page.evaluate("""async () => {
      const {request} = await import('/static/js/api.js');
      try { await request('/api/synthetic-error'); }
      catch (e) {return {status:e.status,payload:e.payload,message:e.message};}
    }""")
    assert result == {"status": 409, "payload": {"detail": "Конфликт", "current_revision": 7},
                      "message": "Конфликт"}
    page.route("**/api/synthetic-error", lambda r: r.abort())
    result = page.evaluate("""async () => {
      const {request} = await import('/static/js/api.js');
      try { await request('/api/synthetic-error'); }
      catch (e) {return {status:e.status,payload:e.payload};}
    }""")
    assert result == {"status": 0, "payload": None}


def test_cloud_preflight_locks_context_until_confirmation(feature_page):
    page, state = feature_page
    state["settings"]["stt"]["engine"] = "cloud"
    page.reload()
    page.wait_for_function("!document.getElementById('settings-fields').disabled")
    open_meeting(page)
    pending = []
    page.route("**/api/settings", lambda r: pending.append(r))
    page.locator("#transcribe-button").click()
    page.wait_for_timeout(50)
    assert len(pending) == 1
    assert page.locator("#transcribe-button").is_disabled()
    page.locator('[data-view="library"]').click()
    assert page.locator("#meeting-view").is_visible()
    pending[0].fulfill(json=state["settings"])
    page.locator("#stt-confirm-dialog").wait_for(state="visible")
    page.keyboard.press("Escape")


def test_saving_recording_settings_sends_only_pending_secret(feature_page):
    page, state = feature_page
    page.locator('[data-view="settings"]').click()
    page.locator("#llm-key").fill("synthetic-not-real")
    page.locator('[data-settings-tab="recording"]').click()
    page.locator("#save-settings").click()
    page.wait_for_function("document.getElementById('settings-status').textContent === 'Настройки сохранены'")
    assert state["writes"][-1]["llm"]["api_key"] == "synthetic-not-real"
    assert "api_key" not in state["writes"][-1]["stt"].get("cloud", {})
    assert "api_key" not in state["settings"]["llm"]
    assert state["settings"]["llm"]["configured"]
    assert page.locator("#llm-key").input_value() == ""
