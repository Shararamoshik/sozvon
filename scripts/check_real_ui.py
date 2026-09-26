"""Проверка реального локального сервера через браузер, без модели и API."""
import json
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
import uvicorn

from sozvon.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / "artifacts/e2e"
output.mkdir(parents=True, exist_ok=True)
app = create_app(output / "data")
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8108, log_level="warning"))
thread = threading.Thread(target=server.run)
thread.start()
errors = []
try:
    import time
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    assert server.started
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width":1280,"height":800})
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(f"http://127.0.0.1:8108/#key={app.state.launch_key}")
        page.locator('#new-recording').wait_for(state='visible')
        page.locator('#new-recording').click()
        page.locator('#new-title').fill('Проверка сохранения')
        page.locator('#new-text').fill('Анна: я обновлю инструкцию, срок пока не обещаю.\nИлья: пятница пока только предложение.')
        page.get_by_role('button',name='Добавить текст',exact=True).click()
        page.locator('#meeting-title').filter(has_text='Проверка сохранения').wait_for()
        page.get_by_role('tab',name='Заметки',exact=True).click()
        page.locator('#meeting-notes').fill('Личная заметка не уходит в отчёт.')
        page.get_by_role('button',name='Сохранить заметки',exact=True).click()
        expect(page.locator("#notes-status")).to_contain_text("Сохран")
        page.reload()
        page.locator('#session-status').wait_for(state='hidden')
        page.locator('#meeting-list button').first.click()
        page.get_by_role('tab',name='Заметки',exact=True).click()
        assert page.locator('#meeting-notes').input_value() == 'Личная заметка не уходит в отчёт.'
        page.screenshot(path=str(output/'notes.png'),full_page=True)
        page.get_by_role('button',name='Настройки',exact=False).first.click()
        page.locator('#llm-model').fill('local-example')
        page.get_by_role('button',name='Сохранить настройки',exact=True).click()
        expect(page.locator("#settings-status")).to_contain_text("Сохран")
        for width in (1440,1280,1024,640,390):
            page.set_viewport_size({"width":width,"height":800})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output/f'settings-{width}.png'),full_page=True)
        assert not errors, errors
        browser.close()
    (output/'result.json').write_text(json.dumps({"ok":True,"console_errors":errors,"widths":[1440,1280,1024,640,390]},ensure_ascii=False,indent=2))
    print('REAL_BACKEND_E2E_OK')
finally:
    server.should_exit=True
    thread.join(15)
