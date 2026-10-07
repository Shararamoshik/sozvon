# research — исследование рынка «Созвона»

Правило: любое число — либо источник с дословной цитатой, либо явное допущение команды.
Число без пометки считается ошибкой и валит `check_research.py`.

## Что где лежит

- `MARKET.md` — досье: разделы 1:1 критериям анализа защиты; диаграммы и источники приложены.
- `charts/` — 7 диаграмм + `manifest.json` (к какому файлу относится, какие входы, sha256).
- `data/inputs.json` — единственный источник чисел (`label: source | assumption`);
  у источников — `url` и `quote_key`, у допущений — `note`.
- `data/ledger.json` — журнал источников с дословными цитатами (проект grounded-citations).
- `data/pages/` — сохранённые копии страниц источников (для проверки цитат).
- `data/blocked.md` — что не удалось проверить автоматически (реестр ПО, закупки, число организаций).
- `scripts/` — пайплайн:
  - `fetch_pages.py` — скачать и очистить страницы;
  - `make_charts.py` — построить диаграммы (и пересобрать manifest.json);
  - `check_research.py` — проверки (числа, диаграммы, досье, DOCX, встроенные картинки в колоде);
  - `build_market_docx.py` — собрать `Исследование_рынка_Созвон.docx` (MARKET.md + приложение).

## Порядок запуска

```bash
research/.venv/bin/python research/scripts/make_charts.py
research/.venv/bin/python research/scripts/check_research.py
research/.venv/bin/python research/scripts/build_market_docx.py
```

Ожидаемый успех: `CHECK_OK charts=7 sources=18 assumptions=6` и `DOCX_OK`.

## Связанное

- Колода отбора 30.10 (вне репозитория) правится скриптом `scripts/pitch/patch_deck_charts.py`:
  подменяет схему на слайде «Рынок» и добавляет слайды «Динамика рынка» и «Карта конкурентов».
- Если недоступные ресурсы станут доступны — обновить пометки: `blocked.md`, в `inputs.json`
  сменить `label` на `source`, добавить URL/цитату и пересобрать диаграммы и досье.
