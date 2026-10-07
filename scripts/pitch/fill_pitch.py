"""Заполнение шаблона отбора 30.10 (кейс «Созвон»). Оригинал шаблона не изменяется.

Порядок: python compute_econ.py → python fill_pitch.py → python check_pitch.py
"""
import json
import shutil
from pathlib import Path

from pptx import Presentation
from pptx.util import Pt

HERE = Path(__file__).resolve().parent
SOURCE_DIR = Path('/mnt/c/Users/Ivan/Documents/Новая папка')
TARGET = SOURCE_DIR / 'Презентация_для_отбора_30_10_Созвон.pptx'
ECON = json.loads((HERE / 'econ_out.json').read_text(encoding='utf-8'))


def set_lines(shape, lines, size=None):
    """Заменить текст фигуры.

    '!' в начале строки — жирный подзаголовок, '• ' — маркер (собственный маркер
    шаблона при этом не дублируется: строка начинается без символа '•'),
    строка с отступом в три пробела дописывается в предыдущий абзац.
    """
    frame = shape.text_frame
    frame.clear()
    paragraphs = []
    for raw in lines:
        continuation = raw.startswith('   ')
        text = raw.strip()
        bold = text.startswith('!')
        if bold:
            text = text[1:]
        if text.startswith('• '):
            text = text[2:]
        if continuation and paragraphs:
            run = paragraphs[-1].add_run()
            run.text = ' ' + text
            if size:
                run.font.size = Pt(size)
            continue
        paragraph = frame.paragraphs[0] if not paragraphs else frame.add_paragraph()
        run = paragraph.add_run()
        run.text = text
        run.font.bold = bold
        if size:
            run.font.size = Pt(size)
        paragraph.space_after = Pt(4)
        paragraphs.append(paragraph)
    return shape


def clear(shape):
    shape.text_frame.clear()


def find(slide, name=None, contains=None, occurrence=0):
    hits = [shape for shape in slide.shapes if shape.has_text_frame
            and ((name and shape.name == name) or
                 (contains and contains.lower() in shape.text_frame.text.lower()))]
    if not hits:
        raise LookupError(f'не найдена фигура name={name} contains={contains}')
    return hits[occurrence]


def main() -> int:
    if TARGET.exists():
        raise SystemExit(f'{TARGET} уже существует — не перезаписываю')
    template = next(SOURCE_DIR.glob('*отбора*.pptx'))
    shutil.copy2(template, TARGET)
    prs = Presentation(str(TARGET))
    slides = list(prs.slides)

    # --- слайд 1: титул ---
    set_lines(find(slides[0], name='Заголовок 1'),
              ['Созвон — протокол совещания по аудиозаписи'])
    set_lines(find(slides[0], name='Подзаголовок 2'), ['Команда СИИ'])
    set_lines(find(slides[0], name='TextBox 5'), ['Красноярск, 2026'])
    set_lines(find(slides[0], name='TextBox 3'),
              ['Акселерационная программа «Пятый элемент» · отбор 30.10'], 12)

    # --- слайд 2: проблема и решение ---
    set_lines(find(slides[1], name='Объект 2'), [
        '!Проблема и решение',
        '• Ручная расшифровка и оформление протокола занимают часы после каждого совещания',
        '   → решения и поручения фиксируются с задержкой, часть теряется при передаче.',
        '• Техническая речь, термины и перебивания затрудняют распознавание',
        '   → ошибки в именах, сроках и формулировках попадают в документ.',
        '• Проверить, кто и что сказал, можно только вручную — по памяти или переслушивая запись.',
        'Наша гипотеза: если каждый пункт протокола привязан к дословной цитате из записи,',
        'черновик быстрее проходит проверку и меньше поручений теряется.',
    ], 15)

    # --- слайд 3: продукт ---
    set_lines(find(slides[2], name='Объект 2'), [
        '!ФОРМУЛА ПРОДУКТА',
        '• Мы делаем продукт «Созвон», помогающий секретарям и руководителям рабочих групп',
        '   промышленных компаний решать проблему ручной подготовки протоколов совещаний',
        '   при помощи технологии распознавания речи и языковой модели, которая привязывает',
        '   каждый пункт отчёта к дословной цитате из конкретного фрагмента записи.',
        '• Работает на компьютере пользователя: аудио не уходит в чужое облако,',
        '   внешний сервис подключается только по явному разрешению.',
        '• Отчёт собирается по шаблону компании: разделы, порядок, подробность и язык',
        '   задаёт шаблон, а не разработчик.',
    ], 15)
    clear(find(slides[2], name='TextBox 5'))

    # --- слайд 4: целевая аудитория ---
    set_lines(find(slides[3], name='Объект 2'), [
        '!Секретарь технического совещания, 35–50 лет',
        '• 3–5 совещаний в неделю по 40–60 минут, протокол обязателен по регламенту',
        '• Оформляет документ после работы; хочет меньше ручной работы, а не «ещё один отчёт».',
        '!Руководитель рабочей группы, 40–55 лет',
        '• Отвечает за исполнение поручений; не может сверить каждую формулировку по памяти',
        '• Нужен документ, которому доверяет и который можно защитить перед руководством.',
        '!Координатор проекта и подрядчик',
        '• Межорганизационные совещания: нужен единый формат договорённостей на выходе.',
    ], 13)

    # --- слайд 5: рынок ---
    set_lines(find(slides[4], name='Объект 2'), [
        '!Границы: подготовка проверяемых протоколов рабочих совещаний в РФ, 2026 год',
        'Не весь рынок ИИ и не вся транскрибация.',
        f"TAM — организации РФ, где протокол обязателен: {ECON['tam_million']} в год",
        f"     ({ECON['tam_orgs']} организаций × {ECON['license_year']} за лицензию)",
        f"SAM — промышленные холдинги с дочерними обществами: {ECON['sam_million']} в год",
        f"     ({ECON['sam_orgs']} организаций)",
        f"SOM — первый год: 3 пилота + 2 лицензии: {ECON['som_million']}",
        '!Как считали',
        '• Число организаций × цена лицензии на организацию в год.',
        '• Все входные числа и пометки «источник / допущение» — в приложении «Источники».',
        f"• Ориентир сверху: у конкурента локальное размещение заявлено до "
        f"{ECON['competitor_onpremise']} и сроком 4–5 месяцев.",
        'Числа рынка — оценка команды на проверяемых допущениях, а не данные исследования.',
    ], 13)

    # --- слайд 5: схема TAM-SAM-SOM с нашими числами вместо картинки шаблона ---
    diagram = HERE / 'diagram_tam_sam_som.png'
    if not diagram.is_file():
        raise SystemExit('нет diagram_tam_sam_som.png — сначала выполните make_diagram.py')
    for shape in list(slides[4].shapes):
        if shape.name == 'Рисунок 4':
            box = (shape.left, shape.top, shape.width, shape.height)
            shape._element.getparent().remove(shape._element)
            slides[4].shapes.add_picture(str(diagram), box[0], box[1], box[2], box[3])

    # --- слайд 6: конкуренты (таблица) ---
    table = [shape.table for shape in slides[5].shapes
             if getattr(shape, 'has_table', False) and shape.has_table][0]
    rows = [
        ['Что сравниваем', 'LiveDigital', 'MyMeet', 'tl;dv', 'Созвон'],
        ['Где работает', 'облако в РФ или сервер заказчика', 'облако: бот или загрузка файла',
         'облако: бот в Zoom/Meet/Teams', 'локально на компьютере пользователя'],
        ['Что делает с записью', 'транскрибация, резюме, ключевые цитаты, вопросы к записи',
         'транскрипт со спикерами, отчёт, задачи с дедлайнами',
         'транскрипт со спикерами и таймкодами, клипы, вопросы',
         'расшифровка и отчёт по шаблону, где у каждого пункта цитата'],
        ['Проверка вывода', 'машинная проверка цитаты не заявлена', 'не заявлена', 'не заявлена',
         'цитата проверяется в сегменте, иначе отчёт не сохраняется'],
        ['Вход', 'их платформа видеосвязи', 'звонок или файл', 'звонок',
         'готовый аудиофайл или запись ПК, бот не нужен'],
    ]
    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            cell = table.cell(row_index, column_index)
            cell.text = value
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(11)
                    run.font.bold = row_index == 0 or column_index == 4
    set_lines(find(slides[5], contains='Не делайте слишком большую'), [
        'Источники: livedigital.space, mymeet.ai, tldv.io, IT-World, ИСИЭЗ НИУ ВШЭ (проверено 07.10.2026).',
        'Сравнение по публичным материалам: подписки конкурентов мы не покупали.',
    ], 11)
    set_lines(find(slides[5], contains='Сравнительная таблица'), [
        '!Почему выберут нас: запись уже есть, протокол нужно проверить построчно,',
        'а данные нельзя отправлять в чужое облако.',
    ], 14)

    # --- слайд 7: бизнес-модель ---
    set_lines(find(slides[6], name='Объект 2'), [
        '!Способы монетизации',
        f"• Лицензия на организацию в год — {ECON['license_year']}",
        f"• Пилот на 10–20 записях — {ECON['pilot']}; далее переход на лицензию",
        '• Поддержка и адаптация шаблонов протокола под регламент заказчика',
        '!Себестоимость',
        '• Локальное распознавание — 0 ₽ за час аудио: считает компьютер заказчика',
        f"• Облачный режим — по факту: замер {ECON['cloud_25min']} за 25 минут аудио",
        f"Точка безубыточности при текущей команде — {ECON['break_even_clients']} лицензий в год.",
        'Все цены — гипотезы команды, требуют проверки на интервью с заказчиком.',
    ], 14)
    clear(find(slides[6], name='TextBox 4'))

    # --- слайд 8: текущее состояние ---
    set_lines(find(slides[7], name='Объект 2'), [
        '!Что уже работает: прототип 0.2.2, локальное приложение',
        '• Импорт аудио и текста, запись микрофона и системного звука',
        '• Распознавание: локальная модель или OpenAI-совместимый API по разрешению',
        '• Редактор расшифровки с версиями, поиском и заменой; свои шаблоны отчёта',
        '• Отчёт с дословными цитатами; экспорт DOCX, PDF, Markdown, JSON',
        '!Чем подтверждено',
        '• 945 тестов пройдено в Linux, 848 — в Windows; сквозной сценарий в собранном приложении',
        '• Отчёт по реальной записи 25 минут: 24 пункта, каждый с цитатой',
        '• Репозиторий и технический отчёт: github.com/Shararamoshik/sozvon,',
        '   docs/TECHNICAL-REPORT.md',
        '!Чего пока нет: разделение говорящих, словарь терминов, роли и аудит, интеграции с СЭД.',
    ], 13)

    # --- слайд 9: дорожная карта ---
    set_lines(find(slides[8], name='Объект 2'), [
        '!до 12.2026 — проверка применимости',
        '• Интервью с секретарём и руководителем рабочей группы: формат протокола, критерии качества',
        '• Пилот на 10–20 разрешённых или обезличенных записях одного типа встреч',
        '• Метрики: время проверки, полнота поручений, число исправлений',
        '!Q1 2027 — качество распознавания',
        '• Разделение говорящих, словарь отраслевых терминов, оценка на технической речи',
        '!Q2 2027 и далее — корпоративный контур',
        '• Локальные модели, роли, аудит, интеграция с СЭД и системой поручений',
        '• Проверка закупочного маршрута и требований информационной безопасности',
    ], 14)

    # --- убрать подписи-заглушки под местами для иллюстраций (слайды 2, 3, 4, 7) ---
    for position in (1, 2, 3, 6):
        clear(find(slides[position], name='TextBox 10'))

    # --- служебный слайд 12 становится «Как это работает» ---
    module_slide = slides[11]
    set_lines(find(module_slide, name='Заголовок 1'), ['Как это работает'])
    steps = [
        'Загрузите запись или вставьте текст — модель ещё не вызывается',
        'Запустите распознавание и поправьте расшифровку: каждая правка сохраняется как версия',
        'Соберите отчёт по шаблону и выгрузите документ: каждый пункт ссылается на цитату',
    ]
    placeholders = sorted([shape for shape in module_slide.shapes
                           if shape.has_text_frame and shape.name == 'Объект 2'],
                          key=lambda shape: shape.left)
    for shape, text in zip(placeholders, steps, strict=True):
        set_lines(shape, [text], 14)

    # --- убрать служебные слайды: 11 (палитра), 13, 14, 15 ---
    xml_slides = prs.slides._sldIdLst
    for position in (15, 14, 13, 11):
        xml_slides.remove(list(xml_slides)[position - 1])

    prs.save(str(TARGET))
    again = Presentation(str(TARGET))
    leftovers = [shape.text_frame.text.strip() for slide in again.slides for shape in slide.shapes
                 if shape.has_text_frame and 'Наборный текст' in shape.text_frame.text]
    print('SAVED', TARGET, 'slides', len(again.slides), 'leftovers', len(leftovers))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
