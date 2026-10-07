"""Проверка презентации для отбора 30.10: заглушки, обязательные факты, структура.

Слайд контактов (номер 10) команда заполняет сама, поэтому его заглушки не считаются ошибкой.
"""
import sys
from pathlib import Path

from pptx import Presentation

CONTACTS_SLIDE = 12
FORBIDDEN = (
    'Проблема 1', 'Проблема 2', 'Параметр', 'Конкурент/', 'Имя Автора',
    'Наборный текст', 'Фото или иллюстрация', 'Фото участника команды',
    'Место для заголовка', 'Элементы которые вы можете использовать',
    'можно добавить', 'покажите', 'опишите', 'укажите', 'подсказка',
)
REQUIRED = (
    'Созвон', 'протокол', 'цитат', 'LiveDigital', 'MyMeet', 'tl;dv', 'TAM', 'SAM', 'SOM',
    'livedigital.space', 'mymeet.ai', 'tldv.io', 'github.com/Shararamoshik/sozvon',
    'Как это работает', 'Динамика рынка', 'Карта конкурентов', 'исследование',
)


def slide_texts(prs):
    out = []
    for index, slide in enumerate(prs.slides, 1):
        chunks = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                chunks.append(shape.text_frame.text)
            if getattr(shape, 'has_table', False) and shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        chunks.append(cell.text)
        out.append((index, '\n'.join(chunks)))
    return out


def main(path: Path) -> int:
    prs = Presentation(str(path))
    texts = slide_texts(prs)
    whole = '\n'.join(text for _, text in texts).lower()
    problems = []
    if len(prs.slides) != 13:
        problems.append(f'ожидалось 13 слайдов, получено {len(prs.slides)}')
    for number, text in texts:
        if number == CONTACTS_SLIDE:
            continue
        for token in FORBIDDEN:
            if token.lower() in text.lower():
                problems.append(f'слайд {number}: осталась заглушка «{token}»')
    for token in REQUIRED:
        if token.lower() not in whole:
            problems.append(f'нет обязательного содержания: «{token}»')
    tables = [shape.table for slide in prs.slides for shape in slide.shapes
              if getattr(shape, 'has_table', False) and shape.has_table]
    if not tables:
        problems.append('не найдена таблица конкурентов')
    elif tables[0]._tbl is not None:
        table = tables[0]
        filled = sum(1 for row in list(table.rows)[1:] for cell in list(row.cells)[1:]
                     if cell.text.strip())
        if filled < 16:
            problems.append(f'таблица конкурентов заполнена не полностью: {filled} из 16')
    if problems:
        print('CHECK_FAILED')
        for item in problems[:25]:
            print(' -', item)
        return 1
    print(f'CHECK_OK слайдов={len(prs.slides)}')
    return 0


if __name__ == '__main__':
    default = Path('/mnt/c/Users/Ivan/Documents/Новая папка/Презентация_для_отбора_30_10_Созвон.pptx')
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    raise SystemExit(main(target))
