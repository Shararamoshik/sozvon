"""Точечная интеграция диаграмм исследования в колоду отбора 30.10.

Колода не перегенерируется: правится на месте, ручные правки команды сохраняются.
Действия: бэкап → замена схемы на слайде «Рынок» → два новых слайда
(«Динамика рынка», «Карта конкурентов») → порядок слайдов → сохранение.
"""
import hashlib
import io
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

HERE = Path(__file__).resolve().parent
DECK = Path('/mnt/c/Users/Ivan/Documents/Новая папка/Презентация_для_отбора_30_10_Созвон.pptx')
CHARTS = HERE.parents[1] / 'research' / 'charts'
BACKUP_DIR = Path('/mnt/d/sozvon/artifacts/pitch-deck-backups')
OLD_DIAGRAM = HERE / 'diagram_tam_sam_som.png'
TITLE_BOX = (838200, 365125, 10515600, 1325563)
TITLE_FONT = 'FindSans Pro Regular'
BODY_FONT = 'Nimbus Sans Round Medium'
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ratio(path: Path) -> float:
    with Image.open(path) as image:
        return image.width / image.height


def extract_image(slide, name):
    shape = next(shape for shape in slide.shapes if shape.name == name)
    return shape.image.blob


def send_to_back(shape):
    element = shape._element
    tree = element.getparent()
    tree.remove(element)
    tree.insert(2, element)


def add_bg(slide, blob):
    picture = slide.shapes.add_picture(io.BytesIO(blob), 0, 0, Emu(12192000), Emu(6858000))
    send_to_back(picture)
    return picture


def set_title(slide, text):
    title = slide.shapes.title
    title.left, title.top, title.width, title.height = (Emu(v) for v in TITLE_BOX)
    frame = title.text_frame
    frame.text = text
    run = frame.paragraphs[0].runs[0]
    run.font.name = TITLE_FONT
    run.font.color.rgb = WHITE


def add_note(slide, x, y, w, h, lines, size=11):
    box = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
    frame = box.text_frame
    frame.word_wrap = True
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        run = paragraph.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.name = BODY_FONT
        run.font.color.rgb = WHITE
        paragraph.space_after = Pt(2)
    return box


def add_pic(slide, path, x, y, w):
    height = round(w / ratio(path))
    return slide.shapes.add_picture(str(path), Emu(x), Emu(y), Emu(w), Emu(height)), height


def main() -> int:
    prs = Presentation(str(DECK))
    if len(prs.slides) == 13:
        print('ALREADY PATCHED: слайдов 13, выходим')
        return 0
    if len(prs.slides) != 11:
        raise SystemExit(f'неожидаемое число слайдов: {len(prs.slides)}')

    # --- вычистить осиротевшие части удалённых служебных слайдов (иначе имена частей конфликтуют) ---
    keep = {sldid.get(qn('r:id')) for sldid in prs.slides._sldIdLst}
    for rId, rel in sorted(prs.part.rels.items()):
        if rel.reltype == RT.SLIDE and rId not in keep:
            print('DROPPED orphan rel', rId, '->', rel.target_part.partname)
            prs.part.drop_rel(rId)

    market_slide = prs.slides[4]
    old_diagram_hash = sha(OLD_DIAGRAM)
    new_chart = CHARTS / 'market_segmentation.png'
    target = next((shape for shape in market_slide.shapes if shape.name == 'Picture 10'), None)
    if target is None:
        raise SystemExit('на слайде «Рынок» нет фигуры Picture 10 — уже заменена?')
    if hashlib.sha256(target.image.blob).hexdigest() != old_diagram_hash:
        raise SystemExit('Picture 10 — не наша старая схема; аварийный останов')
    bg_blob = extract_image(market_slide, 'Рисунок 5')
    logo_blob = extract_image(market_slide, 'Рисунок 2')

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = BACKUP_DIR / f'Созвон_до-диаграмм_{stamp}.pptx'
    shutil.copy2(DECK, backup)
    print('BACKUP', backup)

    # --- слайд «Рынок»: новая схема сегментации вместо старой ---
    target._element.getparent().remove(target._element)
    pic_left, pic_top, pic_w = 7400000, 3400000, 4100000
    height = round(pic_w / ratio(new_chart))
    market_slide.shapes.add_picture(str(new_chart), Emu(pic_left), Emu(pic_top), Emu(pic_w), Emu(height))
    print('REPLACED diagram on «Рынок»')

    # --- новый слайд «Динамика рынка» (после «Рынка») ---
    slide_growth = prs.slides.add_slide(prs.slide_layouts[1])
    for placeholder in list(slide_growth.placeholders):
        if placeholder.placeholder_format.idx != 0:
            placeholder._element.getparent().remove(placeholder._element)
    add_bg(slide_growth, bg_blob)
    slide_growth.shapes.add_picture(io.BytesIO(logo_blob), Emu(10158984), Emu(646258),
                                    Emu(1438169), Emu(490285))
    set_title(slide_growth, 'Динамика рынка')
    left_x, width, gap, top_y = 838200, 5057800, 400000, 1950000
    growth = CHARTS / 'market_growth.png'
    adoption = CHARTS / 'adoption.png'
    _, growth_h = add_pic(slide_growth, growth, left_x, top_y, width)
    add_pic(slide_growth, adoption, left_x + width + gap, top_y, width)
    add_note(slide_growth, left_x, top_y + growth_h + 120000, width, 300000,
             ['Источник: БФТ/АБД, рынок больших данных и ИИ в России, 2024 (PDF)'], 10)
    add_note(slide_growth, left_x + width + gap, top_y + growth_h + 120000, width, 300000,
             ['Источник: ИСИЭЗ НИУ ВШЭ, 2025; доля среди организаций, применяющих ИИ'], 10)
    add_note(slide_growth, left_x, 5300000, 10515600, 1200000, [
        'Верхний контекст (не наш сегмент): рынок больших данных и ИИ России вырос с 30 до 433 млрд руб.; '
        'обработка текста и звука — одно из самых распространённых применений ИИ.',
        'Рынок протоколов совещаний консенсус-оценкой не измеряется: смотрите формулу и допущения на слайде «Рынок».',
        'Полное исследование рынка с источниками — research/MARKET.md в репозитории проекта.',
    ], 11)
    print('ADDED «Динамика рынка»')

    # --- новый слайд «Карта конкурентов» (после «Конкурентов») ---
    slide_map = prs.slides.add_slide(prs.slide_layouts[1])
    for placeholder in list(slide_map.placeholders):
        if placeholder.placeholder_format.idx != 0:
            placeholder._element.getparent().remove(placeholder._element)
    add_bg(slide_map, bg_blob)
    slide_map.shapes.add_picture(io.BytesIO(logo_blob), Emu(10158984), Emu(646258),
                                 Emu(1438169), Emu(490285))
    set_title(slide_map, 'Карта конкурентов')
    map_chart = CHARTS / 'competitors_map.png'
    map_w = 7600000
    _, map_h = add_pic(slide_map, map_chart, (12192000 - map_w) // 2, 1780000, map_w)
    add_note(slide_map, 838200, 1780000 + map_h + 60000, 10515600, 500000, [
        'Позиции — экспертная оценка команды по публичным материалам: mymeet.ai, follow-up.tech, it-world.ru.',
        'Таблица сравнения и источники — на слайде «Конкуренты» и в приложении «Источники».',
    ], 10)
    print('ADDED «Карта конкурентов»')

    # --- порядок: 1-5, Н1, 6-7, Н2, 8-11 ---
    xml_slides = prs.slides._sldIdLst
    elements = list(xml_slides)
    desired = [elements[i] for i in (0, 1, 2, 3, 4, 11, 5, 12, 6, 7, 8, 9, 10)]
    for element in elements:
        xml_slides.remove(element)
    for element in desired:
        xml_slides.append(element)

    prs.save(str(DECK))
    again = Presentation(str(DECK))
    print('SAVED slides', len(again.slides), '| порядок:', [
        shape.text_frame.text[:22] for slide in again.slides for shape in slide.shapes
        if shape.name.startswith('Заголовок')][:13])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
