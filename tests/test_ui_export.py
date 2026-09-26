"""Export UI checks: transport is a synthetic contract, not a PDF renderer test."""
from urllib.parse import parse_qs, urlparse

import pytest
import test_ui_cloud_stt as fixture_ui
from test_ui_cloud_stt import open_meeting

browser = fixture_ui.browser
ui_server = fixture_ui.ui_server
feature_page = fixture_ui.feature_page


def test_export_pdf_download_and_error(feature_page):
    page, _ = feature_page
    detail = open_meeting(page)
    detail["reports"] = [{"id": "r1", "created_at": "2026-01-01", "stale": False,
                           "transcript_revision": 1, "document": {}}]
    with page.expect_response("**/api/meetings/synthetic"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    downloads, calls = [], []
    page.on("download", lambda download: downloads.append(download))
    fail = False

    def export(route):
        calls.append(parse_qs(urlparse(route.request.url).query))
        if fail:
            route.fulfill(status=409, json={"detail": "Отчёт устарел: создайте новую версию"})
        else:
            route.fulfill(body=b"%PDF-1.4 synthetic transport fixture", headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": 'attachment; filename="synthetic.pdf"'})
    page.route("**/export?*", export)
    page.locator("#export-button").click()
    page.locator("#export-format").select_option("pdf")
    page.locator("#export-content").select_option("report")
    page.locator("#export-include-transcript").check()
    page.locator("#export-include-quotes").uncheck()
    with page.expect_download() as event:
        page.locator("#download-export").click()
    assert event.value.suggested_filename == "synthetic.pdf"
    assert event.value.path().read_bytes().startswith(b"%PDF-")
    assert calls[-1] == {"format": ["pdf"], "content": ["report"], "include_transcript": ["1"],
                         "include_quotes": ["0"], "report_id": ["r1"]}
    fail = True
    page.locator("#download-export").click()
    page.locator("#export-error").wait_for(state="visible")
    assert "Отчёт устарел" in page.locator("#export-error").inner_text()
    assert len(downloads) == 1
    page.locator("#export-format").select_option("json")
    assert "вся история" in page.locator("#export-archive-note").inner_text().lower()
    assert page.locator("#export-include-quotes").is_disabled()
    page.keyboard.press("Escape")
    assert page.locator("#export-button").evaluate("e => e===document.activeElement")


@pytest.mark.parametrize("width,zoom", [(1440, 1), (1280, 1), (1024, 1), (640, 1), (390, 1), (1280, 2), (390, 2)])
def test_new_editors_dialogs_geometry(feature_page, width, zoom):
    page, _ = feature_page
    page.set_viewport_size({"width": width, "height": 900})
    page.evaluate("z => document.documentElement.style.zoom=String(z)", zoom)
    open_meeting(page)
    page.locator("#edit-transcript").click()
    page.locator("#export-button").click()
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('export-dialog').contains(document.activeElement)")
    page.keyboard.press("Escape")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>innerWidth+1 && e.getClientRects().length).map(e=>[e.id,e.className,Math.round(e.getBoundingClientRect().right)])")
    page.locator('[data-view="templates"]').click()
    page.locator("#new-template").click()
    page.locator("#template-preview-button").click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>innerWidth+1 && e.getClientRects().length).map(e=>[e.id,e.className,Math.round(e.getBoundingClientRect().right)])")
    page.keyboard.press("Escape")
