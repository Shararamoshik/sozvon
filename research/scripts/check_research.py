"""Проверка исследования: происхождение чисел, диаграммы, досье, встроенность в колоду."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / 'data' / 'inputs.json').read_text(encoding='utf-8'))
CHART_NAMES = ['market_segmentation.png', 'adoption.png', 'market_growth.png',
               'competitors_map.png', 'cost.png', 'data_flow.png', 'pilot_plan.png']
DECK = Path('/mnt/c/Users/Ivan/Documents/Новая папка/Презентация_для_отбора_30_10_Созвон.pptx')
DECK_CHARTS = ['market_segmentation.png', 'market_growth.png', 'adoption.png', 'competitors_map.png']
SECTIONS = ['Определение рынка', 'Ёмкость и динамика', 'Целевая аудитория и проблема',
            'Конкурентная среда', 'Рыночные факторы', 'Качество исследования', 'Обоснованность идеи']

problems = []


def check(condition, message):
    if not condition:
        problems.append(message)


values = DATA['values']
for key, item in values.items():
    if item['label'] == 'source':
        check(str(item.get('url', '')).startswith('http'), f'{key}: нет url')
        check(bool(item.get('quote_key')), f'{key}: нет quote_key')
    else:
        check(bool(item.get('note')), f'{key}: допущение без пояснения')

manifest_path = ROOT / 'charts' / 'manifest.json'
check(manifest_path.is_file(), 'manifest.json отсутствует')
manifest = {}
if manifest_path.is_file():
    manifest = {item['chart']: item for item in json.loads(manifest_path.read_text(encoding='utf-8'))}
for name in CHART_NAMES:
    path = ROOT / 'charts' / name
    check(path.is_file() and path.stat().st_size > 20_000, f'charts: {name} отсутствует или меньше 20 КБ')
    check(name in manifest, f'manifest: нет записи для {name}')
    if name in manifest and path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        check(digest == manifest[name]['sha256'], f'manifest: хеш {name} не совпадает')

dossier = ROOT / 'MARKET.md'
check(dossier.is_file(), 'MARKET.md отсутствует')
if dossier.is_file():
    text = dossier.read_text(encoding='utf-8')
    for section in SECTIONS:
        check(section in text, f'MARKET.md: нет раздела «{section}»')
    for key, item in values.items():
        if item['label'] == 'source':
            check(item['url'] in text, f'MARKET.md: нет ссылки из {key}')
    check('допущен' in text.lower(), 'MARKET.md: не помечены допущения')

docx = ROOT / 'Исследование_рынка_Созвон.docx'
check(docx.is_file() and docx.stat().st_size > 20_000, 'DOCX отсутствует или меньше 20 КБ')

if DECK.is_file():
    with zipfile.ZipFile(DECK) as deck:
        embedded = {hashlib.sha256(deck.read(name)).hexdigest()
                    for name in deck.namelist() if name.startswith('ppt/media/')}
    for name in DECK_CHARTS:
        if name in manifest:
            check(manifest[name]['sha256'] in embedded, f'колода: нет картинки {name}')
else:
    print('WARN: колода не найдена — проверка встраивания пропущена')

if problems:
    print(f'CHECK_FAILED problems={len(problems)}')
    for item in problems:
        print('  -', item)
    sys.exit(1)
print(f'CHECK_OK charts={len(CHART_NAMES)} sources='
      f"{sum(1 for v in values.values() if v['label'] == 'source')} "
      f"assumptions={sum(1 for v in values.values() if v['label'] == 'assumption')}")
