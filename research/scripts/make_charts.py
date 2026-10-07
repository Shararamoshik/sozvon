"""Диаграммы исследования рынка. Единственный источник чисел — data/inputs.json."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = json.loads((ROOT / 'data' / 'inputs.json').read_text(encoding='utf-8'))
OUT = ROOT / 'charts'
OUT.mkdir(parents=True, exist_ok=True)

NAVY, GREEN, MINT, SOFT, GREY, ORANGE = '#0F1B32', '#168F79', '#BFEDE0', '#F4F7FA', '#53657B', '#EBAF57'
WHITE = '#FFFFFF'
LIGHT = '#9FB4C6'
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'text.color': NAVY, 'axes.labelcolor': NAVY,
    'axes.edgecolor': GREY, 'xtick.color': GREY, 'ytick.color': GREY,
    'figure.facecolor': SOFT, 'axes.facecolor': SOFT, 'savefig.facecolor': SOFT,
})
MANIFEST = []
VALUES = DATA['values']


def value(key):
    return VALUES[key]['value']


def source_of(key):
    item = VALUES[key]
    if item['label'] == 'source':
        return item['url']
    return f"допущение: {item.get('note', '')}"


def save(fig, name, note, keys, assumptions):
    path = OUT / name
    fig.savefig(path, dpi=200, bbox_inches='tight')
    plt.close(fig)
    MANIFEST.append({'chart': name, 'note': note, 'keys': keys, 'assumptions': assumptions,
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'bytes': path.stat().st_size})
    print('SAVED', name, path.stat().st_size)


def footer(fig, text):
    fig.text(0.01, 0.005, text, fontsize=8, color=GREY)


def box(ax, x, y, w, h, text, color=WHITE, edge=None, fontsize=10, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.02,rounding_size=0.06',
                                linewidth=1.2, edgecolor=edge or GREY, facecolor=color))
    ax.text(x + w / 2, y + h / 2, text, ha='center', va='center', fontsize=fontsize,
            fontweight='bold' if bold else 'normal', wrap=True)


def arrow(ax, x1, y1, x2, y2, color=GREEN):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle='-|>', mutation_scale=16,
                                 linewidth=1.6, color=color, shrinkA=0, shrinkB=0))


def chart_segmentation():
    tam = value('org_count_industrial') / value('protocol_share') * 100
    sam = value('org_count_industrial')
    som = 3 * value('pilot_price') + 2 * value('license_price_year')
    som_text = f"{som / 1e6:.2f}".replace('.', ',')
    lic = f"{value('license_price_year'):,}".replace(',', ' ')
    pilot = f"{value('pilot_price'):,}".replace(',', ' ')
    tam_formula = f"TAM = {sam} / {value('protocol_share')}% = {tam:.0f} орг."
    sam_formula = f"SAM = {sam} × {lic} = {sam * value('license_price_year') / 1e6:.0f} млн ₽"
    som_formula1 = f"SOM = 3 × {pilot} + 2 × {lic}"
    som_formula2 = f"= {som:,} ₽".replace(',', ' ')
    fig, ax = plt.subplots(figsize=(7.6, 5.75))
    for radius, color in ((2.45, '#1E4D46'), (1.55, GREEN), (0.85, MINT)):
        ax.add_patch(Circle((2.6, 3.1), radius, facecolor=color, edgecolor='white', linewidth=1.8))
    ax.text(2.6, 4.98, f'TAM ≈ {tam:.0f} организаций', ha='center', color='white', fontsize=24, fontweight='bold')
    ax.text(2.6, 4.60, 'рынок целиком', ha='center', color=MINT, fontsize=13)
    ax.text(2.6, 1.72, f'SAM = {sam} организаций', ha='center', color=NAVY, fontsize=20, fontweight='bold')
    ax.text(2.6, 3.32, f'SOM = {som_text} млн ₽', ha='center', color=NAVY, fontsize=18, fontweight='bold')
    ax.text(2.6, 2.92, '(3 пилота + 2 лицензии)', ha='center', color=NAVY, fontsize=12)
    ax.text(5.55, 5.10, 'Формула расчёта', fontsize=20, fontweight='bold')
    ax.text(5.45, 4.45, tam_formula, fontsize=14)
    ax.text(5.45, 4.05, sam_formula, fontsize=14)
    ax.text(5.45, 3.65, som_formula1, fontsize=14)
    ax.text(5.45, 3.30, som_formula2, fontsize=14)
    ax.text(5.45, 2.65, '«≈» и правый столбец — допущения',
            fontsize=12, color=ORANGE, fontweight='bold')
    ax.text(5.45, 2.33, 'доля, цены — оценка команды', fontsize=12, color=GREY)
    ax.text(5.45, 2.01, 'Не подтверждены внешними данными.', fontsize=12, color=GREY)
    ax.set_xlim(0, 7.7)
    ax.set_ylim(0.5, 5.75)
    ax.axis('off')
    fig.subplots_adjust(left=0.02, right=0.60, top=0.97, bottom=0.03)
    footer(fig, 'Схема: расчёт по допущениям команды; источники — в досье')
    save(fig, 'market_segmentation.png',
         'Вложенные сегменты TAM/SAM/SOM с формулой и пометкой допущений',
         ['org_count_industrial', 'protocol_share', 'license_price_year', 'pilot_price'],
         ['число организаций SAM', 'доля с обязательным протоколом', 'цены лицензии и пилота'])


def chart_adoption():
    items = [('Компьютерное зрение', value('hse_share_vision'), LIGHT),
             ('Поддержка принятия решений', value('hse_share_decisions'), LIGHT),
             ('Обработка текста и звука', value('hse_share_speech_text'), GREEN),
             ('Повышение эффективности', value('hse_share_efficiency'), LIGHT)]
    fig, ax = plt.subplots(figsize=(9.2, 4.7))
    names = [item[0] for item in items][::-1]
    numbers = [item[1] for item in items][::-1]
    colors = [item[2] for item in items][::-1]
    bars = ax.barh(names, numbers, color=colors, height=0.55)
    for bar, number in zip(bars, numbers):
        ax.text(bar.get_width() + 1.6, bar.get_y() + bar.get_height() / 2, f'{number}%',
                va='center', fontsize=17, fontweight='bold')
    ax.set_xlim(0, 80)
    ax.set_xlabel('Доля среди организаций, УЖЕ применяющих ИИ (не среди всех компаний РФ), %',
                  fontsize=12, labelpad=14)
    ax.tick_params(labelsize=15)
    fig.subplots_adjust(bottom=0.26, top=0.94)
    ax.spines[['top', 'right']].set_visible(False)
    ax.annotate('наш сегмент: текст и звук', xy=(value('hse_share_speech_text') + 1.5, 1), xytext=(40, 2.35),
                arrowprops=dict(arrowstyle='->', color=ORANGE, lw=2), color=ORANGE,
                fontsize=14, fontweight='bold')
    footer(fig, 'Источник: ИСИЭЗ НИУ ВШЭ, 2025')
    save(fig, 'adoption.png', 'Почти треть пользователей ИИ уже обрабатывает текст и звук',
         ['hse_share_speech_text', 'hse_share_vision', 'hse_share_decisions', 'hse_share_efficiency'], [])


def chart_growth():
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    years = ['2019', '2024']
    totals = [value('market_bigdata_ai_2019'), value('market_bigdata_ai_2024')]
    bars = ax.bar(years, totals, color=[MINT, GREEN], width=0.55)
    for bar, total in zip(bars, totals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 12, f'{total} млрд руб.',
                ha='center', fontweight='bold', fontsize=36)
    ax.annotate(f"+{value('market_bigdata_ai_growth')}% год к году (2024)", xy=(1, totals[1] * 0.78),
                xytext=(0.16, 320), arrowprops=dict(arrowstyle='->', color=ORANGE, lw=2.6),
                color=ORANGE, fontsize=21, fontweight='bold')
    ax.set_ylim(0, 560)
    ax.set_ylabel('млрд руб.', fontsize=16)
    ax.set_title('Рынок больших данных и ИИ в России', fontsize=22, fontweight='bold', loc='left')
    ax.tick_params(labelsize=20)
    ax.spines[['top', 'right']].set_visible(False)
    fig.subplots_adjust(bottom=0.17)
    footer(fig, 'Источник: БФТ/АБД, рынок БД и ИИ, 2024')
    save(fig, 'market_growth.png', 'Рынок вырос с ~30 до 433 млрд руб. за пять лет; верхний контекст, не сегмент',
         ['market_bigdata_ai_2019', 'market_bigdata_ai_2024', 'market_bigdata_ai_growth'], [])


def chart_competitors():
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axvline(0.5, color=GREY, lw=0.8, alpha=0.45)
    ax.axhline(0.5, color=GREY, lw=0.8, alpha=0.45)
    offsets = {'Созвон': (8, 12), 'Таймлист': (-70, 7), 'FollowUP (on-prem)': (8, 6)}
    for item in DATA['competitors']:
        highlight = item['name'] == 'Созвон'
        ax.scatter(item['x'], item['y'], s=200 if highlight else 90,
                   color=ORANGE if highlight else GREEN, edgecolor='white',
                   linewidth=1.2, zorder=3)
        label = f"{item['name']} {item['ref']}".strip()
        ax.annotate(label, (item['x'], item['y']), xytext=offsets.get(item['name'], (7, 7)),
                    textcoords='offset points',
                    fontsize=10, fontweight='bold' if highlight else 'normal', color=NAVY)
        ax.text(item['x'], item['y'] - 0.075, item['kind'], fontsize=8, color=GREY, ha='center')
    ax.text(0.02, 1.005, 'Облако провайдера', fontsize=9, color=GREY, transform=ax.transAxes)
    ax.text(0.76, 1.005, 'Контур заказчика', fontsize=9, color=GREY, transform=ax.transAxes)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.08, 1.12)
    ax.set_xlabel('Автоматизация результата: только расшифровка → проверяемый протокол →', fontsize=10)
    ax.set_ylabel('Размещение: облако → контур заказчика →', fontsize=10)
    ax.text(0.0, -0.20, 'Позиции — экспертная оценка по публичным функциям продуктов, не измерение.\n'
                        'Ссылки на страницы конкурентов — в досье и приложении источников.',
            transform=ax.transAxes, fontsize=9, color=GREY)
    footer(fig, 'Источники: страницы продуктов; позиции — оценка команды')
    save(fig, 'competitors_map.png', 'Карта 2×2: конкуренты уже автоматизируют отчёты; on-premise у FollowUP есть',
         [], ['позиции конкурентов на осях — экспертная оценка'])


def chart_cost():
    hour = value('our_cloud_stt_25min') / 25 * 60
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6))
    left = axes[0]
    left.bar(['Локально', 'Через API'], [0, hour], color=[MINT, GREEN], width=0.45)
    left.set_title('Наши замеры: себестоимость 1 часа аудио', fontsize=11, fontweight='bold')
    left.set_ylabel('руб. за 60 минут', fontsize=10)
    left.set_ylim(0, 14)
    left.text(1, hour + 0.5, f'{hour:.1f} руб./ч'.replace('.', ','), ha='center', fontweight='bold')
    left.text(0, 0.5, '0 руб.\n(компьютер заказчика)', ha='center', fontsize=9)
    left.spines[['top', 'right']].set_visible(False)
    right = axes[1]
    right.bar(['FollowUP\n(от)', 'MyMeet\n(от, диапазон до 1100)'],
              [value('followup_price_month'), value('mymeet_price_month')],
              color=[LIGHT, LIGHT], width=0.45)
    right.set_title('Публичные тарифы конкурентов', fontsize=11, fontweight='bold')
    right.set_ylabel('руб. за сотрудника в месяц', fontsize=10)
    right.set_ylim(0, 1500)
    for index, number in enumerate([value('followup_price_month'), value('mymeet_price_month')]):
        right.text(index, number + 25, str(number), ha='center', fontweight='bold')
    right.text(0.5, 1230, 'On-premise FollowUP: до 3 млн руб. за проект —\nдругая единица, не пересчитываем на час',
               ha='center', fontsize=9, color=GREY)
    right.spines[['top', 'right']].set_visible(False)
    footer(fig, 'Наши замеры и публичные тарифы; единицы не смешиваются')
    save(fig, 'cost.png', 'Измеренная себестоимость часа против публичных тарифов; единицы разные и не смешиваются',
         ['our_cloud_stt_25min', 'followup_price_month', 'mymeet_price_month', 'followup_onprem_cost'], [])


def chart_data_flow():
    fig, ax = plt.subplots(figsize=(11.6, 4.4))
    labels = ['Аудио\nна компьютере', 'Распознавание\n(локально или API*)', 'Сегменты\nс таймкодами',
              'Отчёт\n(локально или API*)', 'Проверка цитат\n(локально)', 'DOCX / PDF']
    width, height, gap = 1.72, 1.0, 0.22
    for index, label in enumerate(labels):
        x = 0.25 + index * (width + gap)
        box(ax, x, 1.7, width, height, label, color=WHITE,
            edge=ORANGE if index in (1, 3) else GREY, fontsize=9)
        if index:
            arrow(ax, x - gap, 2.2, x, 2.2)
    ax.plot([3.02, 6.9], [1.35, 1.35], linestyle='--', color=ORANGE, linewidth=1.4)
    ax.text(4.95, 1.05, 'этапы со звёздочкой (*) могут передавать данные в сеть —\nтолько после отдельного разрешения пользователя',
            ha='center', fontsize=9, color=ORANGE)
    ax.text(0.25, 3.15, 'Путь обработки и границы данных', fontsize=13, fontweight='bold')
    ax.set_xlim(0, 12.6)
    ax.set_ylim(0.5, 3.6)
    ax.axis('off')
    footer(fig, 'Схема по документации проекта «Созвон»')
    save(fig, 'data_flow.png', 'Где данные остаются на компьютере, а где требуют разрешения на сеть',
         [], ['схема по документации проекта — описание, не измерение'])


def chart_pilot_plan():
    fig, ax = plt.subplots(figsize=(11.6, 5.0))
    box(ax, 0.3, 2.4, 2.0, 1.1, '10–20 разрешённых\nили обезличенных\nзаписей', edge=GREY, fontsize=10)
    box(ax, 3.0, 3.35, 2.6, 1.0, 'Ручной протокол\n(текущий процесс, база)', edge=GREY, fontsize=10)
    box(ax, 3.0, 1.55, 2.6, 1.0, 'Черновик «Созвона»\nна тех же файлах', edge=GREEN, fontsize=10, color=MINT)
    box(ax, 6.4, 2.4, 2.9, 1.1, 'Метрики:\nвремя · полнота поручений ·\nисправления · ошибки источников', edge=GREY, fontsize=9)
    box(ax, 10.0, 2.4, 1.9, 1.1, 'Решение:\nмасштабировать,\nдоработать, остановить', edge=ORANGE, fontsize=9)
    arrow(ax, 2.3, 2.95, 3.0, 3.85)
    arrow(ax, 2.3, 2.95, 3.0, 2.05)
    arrow(ax, 5.6, 3.85, 6.4, 3.05)
    arrow(ax, 5.6, 2.05, 6.4, 2.85)
    arrow(ax, 9.3, 2.95, 10.0, 2.95, color=ORANGE)
    ax.text(0.3, 4.35, 'План пилота: сравнение с ручным процессом на одинаковых записях', fontsize=13, fontweight='bold')
    ax.text(0.3, 1.05, 'Пилот ещё не проведён: это план проверки, а не полученный результат.', fontsize=9, color=ORANGE)
    ax.set_xlim(0, 12.2)
    ax.set_ylim(0.6, 4.8)
    ax.axis('off')
    footer(fig, 'Схема плана; метрики — после пилота')
    save(fig, 'pilot_plan.png', 'Как будет измеряться эффект: ручная база против черновика на одних и тех же записях',
         ['pilot_records'], ['состав метрик и порядок пилота — план команды'])


if __name__ == '__main__':
    chart_segmentation()
    chart_adoption()
    chart_growth()
    chart_competitors()
    chart_cost()
    chart_data_flow()
    chart_pilot_plan()
    (OUT / 'manifest.json').write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding='utf-8')
    print('CHARTS_OK', len(MANIFEST))
