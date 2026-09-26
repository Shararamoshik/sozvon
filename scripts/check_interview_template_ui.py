"""Настоящее приложение: шаблон собеседования виден в UI и сам восстанавливается в прежней базе.

Одноразовый ключ запуска тратится ровно один раз: на первом запуске его использует httpx,
на втором — сам браузер, а данные читаются через fetch прямо на странице.
"""
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]


def boot(data: Path, log) -> tuple[subprocess.Popen, str]:
    process = subprocess.Popen([sys.executable, '-m', 'sozvon', '--no-browser', '--port', '0',
                                '--data-dir', str(data)],
                               stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    runtime = data / 'runtime.json'
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('Процесс завершился; см. process.log')
        if runtime.exists():
            value = json.loads(runtime.read_text(encoding='utf-8'))
            if value['pid'] == process.pid:
                return process, value['url']
        time.sleep(0.05)
    raise AssertionError('Процесс не запустил HTTP')


def stop(process: subprocess.Popen):
    process.terminate()
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)


def main() -> int:
    folder = ROOT / 'artifacts' / ('interview-template-ui-' + str(time.time_ns()))
    folder.mkdir(parents=True, exist_ok=False)
    data = folder / 'data'
    log = (folder / 'process.log').open('wb')
    result: dict = {'ok': False}
    process = None
    try:
        process, url = boot(data, log)
        parts = urlsplit(url)
        base = f'{parts.scheme}://{parts.netloc}'
        with httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            response = client.post('/api/session', headers={'Origin': base},
                                   json={'key': parse_qs(parts.fragment)['key'][0]})
            response.raise_for_status()
            first = client.get('/api/templates').json()['items']
        assert 'interview' in {item['id'] for item in first}, first
        interview = next(item for item in first if item['id'] == 'interview')
        assert interview['spec']['detail'] == 'brief', interview['spec']['detail']
        # Имитируем прежнюю базу: встроенного шаблона собеседования в ней ещё не было.
        with sqlite3.connect(data / 'sozvon.db') as conn:
            conn.execute("DELETE FROM template_revisions WHERE template_id='interview'")
            conn.execute("DELETE FROM templates WHERE id='interview'")
        stop(process)
        process = None

        from playwright.sync_api import sync_playwright

        process, url = boot(data, log)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("() => !document.getElementById('settings-fields').disabled")
            items = page.evaluate("async () => (await (await fetch('/api/templates')).json()).items")
            assert 'interview' in {item['id'] for item in items}, items
            assert len(items) == 4, [item['id'] for item in items]
            page.locator('[data-view="settings"]').click()
            page.locator('[data-settings-tab="llm"]').click()
            page.locator('#report-template').select_option('interview')
            assert page.locator('#save-settings').is_enabled()
            page.locator('#save-settings').click()
            page.wait_for_function(
                "() => document.getElementById('settings-status').textContent === 'Настройки сохранены'")
            assert page.locator('#report-template').input_value() == 'interview'
            assert not errors, errors
            page.screenshot(path=str(folder / 'settings-interview.png'), full_page=True)
            browser.close()

        with sqlite3.connect(data / 'sozvon.db') as conn:
            assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM template_revisions WHERE template_id='interview'").fetchone()[0] == 1
        text = (data / 'config.toml').read_text(encoding='utf-8')
        assert 'template = "interview"' in text, text
        result.update(ok=True, data_dir=str(data),
                      templates=[item['id'] for item in items],
                      screenshot=str(folder / 'settings-interview.png'))
    finally:
        if process is not None:
            stop(process)
        log.close()
    print('INTERVIEW_TEMPLATE_OK ' + json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
