"""Схема TAM-SAM-SOM для слайда «Рынок»: рисуем свою, числа берём из econ_out.json.

Заменяет картинку шаблона, в которой были чужие значения (1 млрд / 300 млн / 100 млн руб).
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / 'diagram_tam_sam_som.png'
ECON = json.loads((HERE / 'econ_out.json').read_text(encoding='utf-8'))

WIDTH, HEIGHT = 787, 461
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
GREEN_DARK = (17, 167, 139, 235)
GREEN_MID = (34, 168, 137, 190)
GREEN_LIGHT = (127, 227, 196, 170)
INK = (14, 31, 61, 255)
GREY = (107, 120, 146, 255)
ARROW = (110, 124, 148, 255)


def font(path: str, size: int):
    return ImageFont.truetype(path, size)


def main() -> int:
    image = Image.new('RGBA', (WIDTH, HEIGHT), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    center = (215, 250)
    circles = ((200, GREEN_DARK), (140, GREEN_MID), (85, GREEN_LIGHT))
    for radius, color in circles:
        draw.ellipse([center[0] - radius, center[1] - radius,
                      center[0] + radius, center[1] + radius],
                     fill=color, outline=(255, 255, 255, 120), width=2)

    number_font = font(FONT_BOLD, 30)
    small_number_font = font(FONT_BOLD, 24)
    label_font = font(FONT_BOLD, 26)
    note_font = font(FONT, 14)

    numbers = ((f"{ECON['tam_million']}", center[0], 100, number_font),
               (f"{ECON['sam_million']}", center[0], 172, number_font),
               (f"{ECON['som_million']}", center[0], 250, small_number_font))
    for text, x, y, used_font in numbers:
        box = draw.textbbox((0, 0), text, font=used_font)
        draw.text((x - (box[2] - box[0]) / 2, y - (box[3] - box[1]) / 2), text,
                  font=used_font, fill=(255, 255, 255, 255))

    labels = (
        ('TAM', f"{ECON['tam_orgs']} организаций РФ, где протокол обязателен",
         f"{ECON['license_year']} за лицензию в год", 78, (340, 95), (452, 95)),
        ('SAM', f"{ECON['sam_orgs']} промышленных холдингов с дочерними обществами",
         'сегмент первого пилота', 215, (305, 215), (452, 215)),
        ('SOM', 'первый год: 3 пилота + 2 лицензии',
         'достижимый объём по плану', 352, (280, 352), (452, 352)),
    )
    for title, note, extra, y, start, end in labels:
        draw.line([start, end], fill=ARROW, width=2)
        draw.polygon([(end[0], end[1]), (end[0] - 9, end[1] - 5), (end[0] - 9, end[1] + 5)],
                     fill=ARROW)
        draw.text((end[0] + 12, y - 22), title, font=label_font, fill=INK)
        draw.text((end[0] + 12, y + 8), note, font=note_font, fill=GREY)
        draw.text((end[0] + 12, y + 26), extra, font=note_font, fill=GREY)

    note = 'Оценка команды по формуле «число организаций × цена лицензии»'
    box = draw.textbbox((0, 0), note, font=note_font)
    draw.text(((WIDTH - (box[2] - box[0])) / 2, HEIGHT - 26), note, font=note_font, fill=GREY)

    image.save(OUT)
    print('SAVED', OUT, image.size)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
