"""Настоящий сервер и браузер без подмен API, данные только в tmp_path."""
import socket
import threading
import time

import pytest
import uvicorn
from test_ui_browser import browser as browser  # noqa: PLC0414 - общая fixture

from sozvon.web.server import create_app


@pytest.fixture
def real_page(tmp_path, browser):
    app = create_app(tmp_path / 'data')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        page.goto(f'http://127.0.0.1:{port}/#key={app.state.launch_key}')
        page.wait_for_function("() => !document.getElementById('new-recording').disabled")
        yield page, app.state.service, errors
        assert errors == []
    finally:
        page.close()
        server.should_exit = True
        thread.join(10)
        sock.close()
        app.state.service.close()


def test_real_template_create_and_transcript_edit_survive_reload(real_page):
    page, service, _ = real_page
    page.locator('[data-view="templates"]').click()
    page.locator('#new-template').click()
    page.locator('#template-name').fill('Настоящее сохранение')
    page.locator('#template-custom-kind').select_option('tasks')
    page.locator('#template-add-section').click()
    page.locator('[data-section-key^="custom_"] [data-section-field="title"]').fill('Наши действия')
    page.locator('#save-template').click()
    page.wait_for_function("() => document.getElementById('template-status').textContent.includes('Сохранена версия 1')")
    page.reload()
    page.locator('[data-view="templates"]').click()
    page.get_by_role('button', name='Настоящее сохранение', exact=False).click()
    page.locator('#template-form').wait_for(state='visible')
    assert page.locator('#template-name').input_value() == 'Настоящее сохранение'
    assert page.locator('[data-section-key^="custom_"] [data-section-field="title"]').input_value() == 'Наши действия'
    mid = service.repo.create_text('Синтетическая проверка редактора', 'Изначальная фраза')
    page.locator('[data-view="library"]').click()
    page.get_by_role('button', name='Синтетическая проверка редактора', exact=False).click()
    page.locator('[data-detail-tab="transcript"]').click()
    page.locator('#edit-transcript').click()
    page.locator('[data-edit-text]').fill('Проверенная правка')
    page.locator('#save-transcript').click()
    page.wait_for_function("() => document.getElementById('transcript-status').textContent.includes('сохранена')")
    assert service.repo.detail(mid)['transcript_revision'] == 2
    assert service.repo.detail(mid)['segments'][0]['text'] == 'Проверенная правка'


@pytest.mark.parametrize('width,height,zoom', [(1440, 900, 1), (1024, 768, 1), (390, 844, 1), (1280, 900, 2)])
def test_real_feature_screens_have_no_overflow(real_page, width, height, zoom):
    from pathlib import Path
    page, service, _ = real_page
    page.set_viewport_size({'width': width, 'height': height})
    if zoom != 1:
        page.evaluate('(zoom) => document.documentElement.style.zoom = String(zoom)', zoom)
    folder = Path(__file__).resolve().parents[1] / 'artifacts' / 'features-ui'
    folder.mkdir(parents=True, exist_ok=True)
    page.locator('[data-view="templates"]').click()
    page.locator('#new-template').click()
    page.locator('#template-name').fill('Синтетический пример шаблона')
    page.locator('#template-custom-kind').select_option('tasks')
    page.locator('#template-add-section').click()
    geometry = page.evaluate('() => ({width:innerWidth,scroll:document.documentElement.scrollWidth})')
    assert geometry['scroll'] <= geometry['width']
    page.screenshot(path=str(folder / f'templates-{width}-{zoom}.png'), full_page=True)
    page.locator('[data-view="library"]').click()
    page.locator('#discard-editing').click()
    mid = service.repo.create_text('Синтетическая геометрия', 'Первая реплика для проверки.\nВторая реплика.')
    page.locator('#refresh-library').click()
    page.locator(f'[data-meeting-id="{mid}"]').click()
    page.locator('[data-detail-tab="transcript"]').click()
    page.locator('#edit-transcript').click()
    geometry = page.evaluate('() => ({width:innerWidth,scroll:document.documentElement.scrollWidth})')
    assert geometry['scroll'] <= geometry['width']
    page.screenshot(path=str(folder / f'transcript-{width}-{zoom}.png'), full_page=True)


def test_real_browser_downloads_pdf_not_error_json(real_page):
    page, service, _ = real_page
    mid = service.repo.create_text('Синтетический экспорт', 'Проверка браузерной загрузки PDF.')
    page.locator('#refresh-library').click()
    page.locator(f'[data-meeting-id="{mid}"]').click()
    page.locator('#export-button').click()
    page.locator('#export-format').select_option('pdf')
    with page.expect_download() as pending:
        page.locator('#download-export').click()
    download = pending.value
    assert download.failure() is None
    from pathlib import Path
    data = Path(download.path()).read_bytes()
    assert data.startswith(b'%PDF-')
    assert download.suggested_filename.endswith('-transcript.pdf')
    page.locator('#close-export').click()
    assert page.locator('#export-button').evaluate('e => e === document.activeElement')
