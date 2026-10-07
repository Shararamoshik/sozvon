"""Скачать страницы источников и сохранить чистый текст для дословных цитат."""
import html
import re
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
PAGES = HERE.parent / 'data' / 'pages'
PAGES.mkdir(parents=True, exist_ok=True)

HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
           'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8'}

JOBS = {
    'issek-ai-2025.md': 'https://issek.hse.ru/news/1083541394.html',
    'mymeet.md': 'https://mymeet.ai/ru/',
    'followup-main.md': 'https://follow-up.tech/',
    'followup-onprem.md': 'https://follow-up.tech/on-premise/',
    'itworld.md': 'https://www.it-world.ru/tech/fu9r1ysyy7wckcwo480wc48wccws44o.html',
    'rosneft-reestr.md': 'https://rosneft.ru/press/news/item/213929/',
    'tadviser-idp.md': 'https://www.tadviser.ru/index.php/%D0%A1%D1%82%D0%B0%D1%82%D1%8C%D1%8F:%D0%98%D0%BD%D1%82%D0%B5%D0%BB%D0%BB%D0%B5%D0%BA%D1%82%D1%83%D0%B0%D0%BB%D1%8C%D0%BD%D0%B0%D1%8F_%D0%BE%D0%B1%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D0%BA%D0%B0_%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%BE%D0%B2._%D0%9E%D0%B1%D0%B7%D0%BE%D1%80_TAdviser_2026',
}
PDF_JOBS = {
    'bft-2025.pdf': 'https://bft.ru/newspictures/21_%D0%BA%20%D0%BF.3.5_%D0%9F%D1%80%D0%B5%D0%B7%D0%B5%D0%BD%D1%82%D0%B0%D1%86%D0%B8%D1%8F_%D0%A0%D1%8B%D0%BD%D0%BE%D0%BA%20%D0%91%D0%BE%D0%BB%D1%8C%D1%88%D0%B8%D1%85%20%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D1%85%20%D0%B8%20%D0%98%D0%98%20%D0%B2%20%D0%A0%D0%BE%D1%81%D1%81%D0%B8%D0%B8%202025.pdf',
}


def clean_html(raw: str) -> str:
    raw = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', raw)
    raw = re.sub(r'(?is)<br\s*/?>', '\n', raw)
    raw = re.sub(r'(?is)</(p|div|h[1-6]|li|tr)>', '\n', raw)
    raw = re.sub(r'(?s)<[^>]+>', ' ', raw)
    text = html.unescape(raw)
    lines = [' '.join(line.split()) for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)


def main() -> int:
    ok = failed = 0
    for name, url in JOBS.items():
        try:
            response = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=40, trust_env=False)
            text = clean_html(response.text)
            (PAGES / name).write_text(text, encoding='utf-8')
            print(f'{name} {response.status_code} chars={len(text)}')
            ok += 1
        except Exception as exc:
            print(f'{name} FAIL {type(exc).__name__}')
            failed += 1
    for name, url in PDF_JOBS.items():
        try:
            response = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=90, trust_env=False)
            path = PAGES / name
            path.write_bytes(response.content)
            print(f'{name} {response.status_code} bytes={len(response.content)}')
            ok += 1
        except Exception as exc:
            print(f'{name} FAIL {type(exc).__name__}')
            failed += 1
    print(f'FETCH_DONE ok={ok} failed={failed}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
