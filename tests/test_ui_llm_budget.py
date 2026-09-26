"""LLM budget UI regressions; synthetic APIs and isolated temporary data only."""
import pytest
import test_ui_cloud_stt as fixture_ui

browser = fixture_ui.browser
ui_server = fixture_ui.ui_server
feature_page = fixture_ui.feature_page
expect = pytest.importorskip("playwright.sync_api").expect


def open_settings(page):
    page.wait_for_function("() => !document.getElementById('settings-fields').disabled")
    page.locator('[data-view="settings"]').click()
    page.locator('[data-settings-tab="llm"]').click()


def test_legacy_settings_show_budget_defaults_and_explain_cost(feature_page):
    page, state = feature_page
    assert "max_output_tokens" not in state["settings"]["llm"]
    assert "timeout_s" not in state["settings"]["llm"]
    open_settings(page)
    expect(page.locator("#llm-max-output-tokens")).to_have_value("16384")
    expect(page.locator("#llm-timeout")).to_have_value("300")
    expect(page.get_by_label("Лимит вывода, токенов", exact=True)).to_have_attribute(
        "id", "llm-max-output-tokens"
    )
    expect(page.get_by_label("Таймаут запроса, с", exact=True)).to_have_attribute(
        "id", "llm-timeout"
    )
    hint = page.locator("#llm-budget-hint").inner_text().lower()
    assert "рассуждения" in hint and "ответ" in hint
    assert "лимита" in hint and "стоимость" in hint
    assert "один запрос" in hint and "без автоматических повторов" in hint
    expect(page.locator("#save-settings")).to_be_disabled()
    assert not state["writes"]
