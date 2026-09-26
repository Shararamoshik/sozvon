"""Реальные настройки лимита/таймаута в настоящем приложении: сохранение, чтение назад, снимок экрана."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    folder = ROOT / 'artifacts' / ('llm-budget-ui-' + str(time.time_ns()))
    folder.mkdir(parents=True, exist_ok=False)
    data = folder / 'data'
    log = (folder / 'process.log').open('wb')
    process = subprocess.Popen([sys.executable, '-m', 'sozvon', '--no-browser', '--port', '0',
                                '--data-dir', str(data)],
                               stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    result: dict = {'ok': False}
    try:
        runtime = data / 'runtime.json'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not runtime.exists():
            if process.poll() is not None:
                raise AssertionError('Процесс завершился; см. process.log')
            time.sleep(0.05)
        url = json.loads(runtime.read_text(encoding='utf-8'))['url']
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(url)
            page.wait_for_function("() => !document.getElementById('settings-fields').disabled")
            page.locator('[data-view="settings"]').click()
            page.locator('[data-settings-tab="llm"]').click()
            limits = page.locator('#llm-max-output-tokens')
            timeout = page.locator('#llm-timeout')
            assert limits.input_value() == '16384', limits.input_value()
            assert timeout.input_value() == '300', timeout.input_value()
            hint = page.locator('#llm-budget-hint').inner_text().lower()
            assert all(word in hint for word in ('рассуждения', 'ответ', 'лимита', 'стоимость'))
            assert not page.locator('#save-settings').is_enabled()
            page.screenshot(path=str(folder / 'before-save.png'), full_page=True)
            limits.fill('24576')
            timeout.fill('450')
            assert page.locator('#save-settings').is_enabled()
            page.locator('#save-settings').click()
            page.wait_for_function(
                "() => document.getElementById('settings-status').textContent === 'Настройки сохранены'")
            page.reload()
            page.wait_for_function("() => !document.getElementById('settings-fields').disabled")
            page.locator('[data-view="settings"]').click()
            page.locator('[data-settings-tab="llm"]').click()
            limits = page.locator('#llm-max-output-tokens')
            timeout = page.locator('#llm-timeout')
            assert limits.input_value() == '24576', limits.input_value()
            assert timeout.input_value() == '450', timeout.input_value()
            page.screenshot(path=str(folder / 'saved.png'), full_page=True)
            browser.close()
        text = (data / 'config.toml').read_text(encoding='utf-8')
        assert 'max_output_tokens = 24576' in text and 'timeout_s = 450' in text, text
        result.update(ok=True, data_dir=str(data), config=str(data / 'config.toml'),
                      screenshots=[str(folder / 'before-save.png'), str(folder / 'saved.png')])
    finally:
        process.terminate()
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
        log.close()
    print('BUDGET_UI_OK ' + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
