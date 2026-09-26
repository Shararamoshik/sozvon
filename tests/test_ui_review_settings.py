"""Браузерные review-регрессии: настоящий API, временные данные, тестовый keyring."""
import json

import pytest
import test_features_browser as fixture_ui

browser = fixture_ui.browser
real_page = fixture_ui.real_page
expect = pytest.importorskip("playwright.sync_api").expect


@pytest.fixture(autouse=True)
def isolated_vault(monkeypatch):
    """Ни одна браузерная проверка не читает и не меняет ключи пользователя."""
    values = {}
    monkeypatch.setattr("keyring.get_password", lambda service, account: values.get((service, account)))
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, account, key: values.update({(service, account): key}),
    )
    monkeypatch.setattr("keyring.delete_password", lambda service, account: values.pop((service, account)))
    return values


LLM_KEY = "synthetic-review-llm-not-a-real-key"
STT_KEY = "synthetic-review-stt-not-a-real-key"


def prepare_settings(page, service):
    service.settings.save({"llm": {"model": "review-existing-model", "api_key": LLM_KEY}})
    service.settings.save({"stt": {"engine": "cloud", "cloud": {
        "base_url": "https://review-stt.example/v1", "model": "review-existing-stt",
        "allow_remote": True, "api_key": STT_KEY,
    }}})
    page.reload()
    page.wait_for_function("() => !document.getElementById('settings-fields').disabled")
    page.locator('[data-view="settings"]').click()


def assert_no_secret_exposure(page, public, console):
    rendered = page.content() + page.locator("body").inner_text() + json.dumps(public) + str(console)
    for secret in (LLM_KEY, STT_KEY):
        assert secret not in rendered
    assert page.locator("#llm-key").input_value() == ""
    assert page.locator("#stt-cloud-key").input_value() == ""
    assert page.evaluate("() => Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0


@pytest.mark.parametrize("profile,checkbox", [("llm", "delete-key"), ("stt", "stt-cloud-delete-key")])
def test_pending_key_delete_survives_tab_switch(real_page, profile, checkbox):
    page, service, _ = real_page
    console = []
    page.on("console", lambda message: console.append(message.text))
    prepare_settings(page, service)
    original = service.settings.snapshot()
    page.locator(f'[data-settings-tab="{profile}"]').click()
    page.locator(f"#{checkbox}").check()
    page.locator('[data-settings-tab="recording"]').click()
    with page.expect_response(lambda response: response.url.endswith("/api/settings")
                              and response.request.method == "PUT") as pending:
        page.locator("#save-settings").click()
    response = pending.value
    payload = response.request.post_data_json
    selected = payload["llm"] if profile == "llm" else payload["stt"]["cloud"]
    assert selected.get("delete_key") is True
    assert response.status == 200
    expect(page.locator("#settings-status")).to_have_text("Настройки сохранены")
    public = page.request.get(page.url + "api/settings").json()
    assert public["llm"]["configured"] is (profile != "llm")
    assert public["stt"]["cloud"]["configured"] is (profile != "stt")
    assert service.settings.snapshot() == original
    assert not page.locator(f"#{checkbox}").is_checked()
    assert page.locator("#save-settings").is_disabled()
    assert_no_secret_exposure(page, public, console)


def test_multiple_pending_key_deletes_block_without_losing_marks(real_page):
    page, service, _ = real_page
    prepare_settings(page, service)
    page.locator("#delete-key").check()
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-cloud-delete-key").check()
    page.locator('[data-settings-tab="recording"]').click()
    writes = []
    page.on("request", lambda request: writes.append(request.post_data_json)
            if request.url.endswith("/api/settings") and request.method == "PUT" else None)
    page.locator("#save-settings").click()
    expect(page.locator("#settings-error")).to_be_visible()
    assert writes == [], "Несколько операций с ключами должны блокироваться до PUT"
    error = page.locator("#settings-error").inner_text()
    assert "LLM" in error and "STT" in error
    assert page.locator("#delete-key").is_checked()
    assert page.locator("#stt-cloud-delete-key").is_checked()
    assert not page.locator("#save-settings").is_disabled()
    assert service.settings.public()["llm"]["configured"]
    assert service.settings.public()["stt"]["cloud"]["configured"]

    # Снять одну отметку и повторить: в запросе остаётся ровно одна операция.
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-cloud-delete-key").uncheck()
    page.locator('[data-settings-tab="recording"]').click()
    page.locator("#save-settings").click()
    expect(page.locator("#settings-status")).to_have_text("Настройки сохранены")
    assert len(writes) == 1
    assert writes[0]["llm"]["delete_key"] is True
    assert "delete_key" not in writes[0]["stt"]["cloud"]
    assert not service.settings.public()["llm"]["configured"]
    assert service.settings.public()["stt"]["cloud"]["configured"]


@pytest.mark.parametrize("invalid_field,invalid_value", [
    ("stt-cloud-base", ""), ("stt-cloud-model", ""), ("stt-cloud-timeout", "0"),
])
def test_local_save_ignores_hidden_invalid_cloud_draft(real_page, invalid_field, invalid_value):
    page, service, _ = real_page
    prepare_settings(page, service)
    original_cloud = service.settings.public()["stt"]["cloud"]
    page.locator('[data-settings-tab="stt"]').click()
    page.locator(f"#{invalid_field}").fill(invalid_value)
    page.locator("#stt-engine").select_option("local")
    assert not page.locator("#stt-cloud-base").is_visible()
    assert page.locator("#stt-cloud-base").is_disabled()
    page.locator("#stt-path").fill("/synthetic/review-local-model")
    with page.expect_response(lambda response: response.url.endswith("/api/settings")
                              and response.request.method == "PUT") as pending:
        page.locator("#save-settings").click()
    response = pending.value
    assert response.status == 200, response.text()
    payload = response.request.post_data_json
    assert payload["stt"]["engine"] == "local"
    if "cloud" in payload["stt"]:
        assert all(original_cloud[key] == value for key, value in payload["stt"]["cloud"].items())
    expect(page.locator("#settings-status")).to_have_text("Настройки сохранены")
    public = page.request.get(page.url + "api/settings").json()
    assert public["stt"]["cloud"] == original_cloud
    assert public["stt"]["engine"] == "local"
    assert public["stt"]["model_path"] == "/synthetic/review-local-model"
    assert public["llm"]["model"] == "review-existing-model"
    page.reload()
    page.locator('[data-view="settings"]').click()
    page.locator('[data-settings-tab="stt"]').click()
    expect(page.locator("#stt-engine")).to_have_value("local")
    page.locator("#stt-engine").select_option("cloud")
    assert page.locator("#stt-cloud-base").input_value() == original_cloud["base_url"]
    assert page.locator("#stt-cloud-model").input_value() == original_cloud["model"]
    assert page.locator("#stt-cloud-timeout").input_value() == str(original_cloud["timeout_s"])


@pytest.mark.parametrize("profile,checkbox", [("llm", "delete-key"), ("stt", "stt-cloud-delete-key")])
def test_key_delete_is_not_confirmed_by_unchanged_readback(real_page, profile, checkbox):
    page, service, _ = real_page
    prepare_settings(page, service)
    page.locator(f'[data-settings-tab="{profile}"]').click()
    page.locator(f"#{checkbox}").check()
    page.locator('[data-settings-tab="recording"]').click()

    # Только PUT подтверждается без записи; GET остаётся настоящим и показывает прежний ключ.
    def no_write(route):
        if route.request.method == "PUT":
            route.fulfill(json={"saved": True})
        else:
            route.continue_()

    page.route("**/api/settings", no_write)
    page.locator("#save-settings").click()
    expect(page.locator("#settings-status")).to_contain_text("Сохранение не подтверждено")
    expect(page.locator("#settings-error")).to_be_visible()
    assert page.locator(f"#{checkbox}").is_checked()
    assert not page.locator("#save-settings").is_disabled()
    public = page.request.get(page.url + "api/settings").json()
    assert public["llm"]["configured"] and public["stt"]["cloud"]["configured"]
    assert_no_secret_exposure(page, public, [])


@pytest.mark.parametrize("profile", ["llm", "stt"])
@pytest.mark.parametrize("fail", [False, True])
def test_single_key_replacement_survives_tab_switch_and_redacts_errors(
    real_page, monkeypatch, profile, fail,
):
    page, service, _ = real_page
    prepare_settings(page, service)
    console = []
    page.on("console", lambda message: console.append(message.text))
    key = f"synthetic-review-{profile}-replacement-not-real"
    page.locator(f'[data-settings-tab="{profile}"]').click()
    page.locator("#llm-key" if profile == "llm" else "#stt-cloud-key").fill(key)
    page.locator('[data-settings-tab="recording"]').click()
    before = service.settings.snapshot()
    if fail:
        def unavailable(*args):
            raise RuntimeError(key)
        monkeypatch.setattr("keyring.set_password", unavailable)
    with page.expect_response(lambda response: response.url.endswith("/api/settings")
                              and response.request.method == "PUT") as pending:
        page.locator("#save-settings").click()
    response = pending.value
    payload = response.request.post_data_json
    profiles = [payload["llm"], payload["stt"].get("cloud", {})]
    assert sum("api_key" in item or "delete_key" in item for item in profiles) == 1
    assert profiles[0 if profile == "llm" else 1]["api_key"] == key
    if fail:
        assert response.status == 422
        expect(page.locator("#settings-status")).to_contain_text("Сохранение не подтверждено")
        expect(page.locator("#settings-error")).to_contain_text("хранилище ключей недоступно")
    else:
        assert response.status == 200
        expect(page.locator("#settings-status")).to_have_text("Настройки сохранены")
    saved_llm = service.settings.report_snapshot()[1]
    saved_stt = service.settings.stt_snapshot()[1]
    assert saved_llm == (key if profile == "llm" and not fail else LLM_KEY)
    assert saved_stt == (key if profile == "stt" and not fail else STT_KEY)
    public = page.request.get(page.url + "api/settings").json()
    assert service.settings.snapshot() == before
    assert_no_secret_exposure(page, public, console)
    assert key not in page.content() + str(console) + json.dumps(public) + response.text()


def test_local_save_keeps_pending_cloud_key_delete(real_page):
    page, service, _ = real_page
    prepare_settings(page, service)
    original_cloud = service.settings.snapshot()["stt"]["cloud"]
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-cloud-delete-key").check()
    page.locator("#stt-engine").select_option("local")
    page.locator('[data-settings-tab="recording"]').click()
    page.locator("#save-settings").click()
    expect(page.locator("#settings-status")).to_have_text("Настройки сохранены")
    public = page.request.get(page.url + "api/settings").json()
    assert public["stt"]["engine"] == "local"
    assert not public["stt"]["cloud"]["configured"]
    assert public["llm"]["configured"]
    assert service.settings.snapshot()["stt"]["cloud"] == original_cloud


def test_hidden_cloud_key_action_never_targets_a_different_saved_address(real_page):
    page, service, _ = real_page
    prepare_settings(page, service)
    original_cloud = service.settings.public()["stt"]["cloud"]
    page.locator('[data-settings-tab="stt"]').click()
    page.locator("#stt-cloud-base").fill("https://different-review-stt.example/v1")
    page.locator("#stt-cloud-delete-key").check()
    page.locator("#stt-engine").select_option("local")
    writes = []
    page.on("request", lambda request: writes.append(request.post_data_json)
            if request.url.endswith("/api/settings") and request.method == "PUT" else None)
    page.locator("#save-settings").click()
    expect(page.locator("#settings-error")).to_contain_text("STT")
    assert writes == []
    assert page.locator("#stt-cloud-delete-key").is_checked()
    assert service.settings.public()["stt"]["cloud"] == original_cloud
    expect(page.locator("#settings-status")).to_contain_text("Настройки не сохранены")


def open_export(page, service):
    mid = service.repo.create_text("Синтетическая review-проверка экспорта", "Проверка диалога.")
    page.locator("#refresh-library").click()
    page.locator(f'[data-meeting-id="{mid}"]').click()
    page.locator("#export-button").click()
    return mid


def test_export_network_failure_is_localized(real_page):
    page, service, _ = real_page
    mid = open_export(page, service)
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.route(f"**/api/meetings/{mid}/export?*", lambda route: route.abort("failed"))
    page.locator("#download-export").click()
    expect(page.locator("#export-error")).to_have_text(
        "Нет связи с приложением. Проверьте, что оно запущено, и повторите действие."
    )
    expect(page.locator("#export-status")).to_have_text("Файл не скачан")
    assert not downloads
    assert not page.locator("#download-export").is_disabled()


@pytest.mark.parametrize("failed", [False, True])
def test_export_reopen_clears_previous_status(real_page, failed):
    page, service, _ = real_page
    mid = open_export(page, service)
    if failed:
        page.route(f"**/api/meetings/{mid}/export?*", lambda route: route.abort("failed"))
        page.locator("#download-export").click()
        expect(page.locator("#export-status")).to_have_text("Файл не скачан")
    else:
        page.locator("#export-format").select_option("md")
        with page.expect_download() as pending:
            page.locator("#download-export").click()
        assert pending.value.failure() is None
        assert "Проверка диалога." in pending.value.path().read_text()
        expect(page.locator("#export-status")).to_have_text("Файл передан браузеру")
    page.locator("#close-export").click()
    page.locator("#export-button").click()
    expect(page.locator("#export-status")).to_have_text("")
    expect(page.locator("#export-error")).not_to_be_visible()
    expect(page.locator("#export-format")).to_have_value("docx")
