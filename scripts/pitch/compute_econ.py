"""Расчёт TAM-SAM-SOM и точки безубыточности из econ.json.

Каждый вход обязан иметь source (проверяемый источник) или assumption=true.
Результат печатается и сохраняется в econ_out.json, который читает fill_pitch.py.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / 'econ.json').read_text(encoding='utf-8'))


def value(key: str) -> float:
    item = DATA[key]
    if 'source' not in item and not item.get('assumption'):
        raise SystemExit(f'{key}: нужен source или assumption=true')
    return float(item['value'])


def rub(number: float) -> str:
    return f'{number:,.0f}'.replace(',', ' ') + ' ₽'


def million(number: float) -> str:
    millions = number / 1_000_000
    digits = 2 if millions < 10 else 1
    return f'{millions:.{digits}f}'.replace('.', ',') + ' млн ₽'


def main() -> int:
    organizations = value('organizations_sam')
    share = value('protocol_share')
    license_year = value('price_license_year_rub')
    pilot = value('price_pilot_rub')
    team = value('team_cost_year_rub')
    cloud_stt = value('cloud_stt_per_25min_rub')
    competitor = value('competitor_onpremise_rub')

    organizations_tam = organizations / share
    tam = organizations_tam * license_year
    sam = organizations * license_year
    som = 3 * pilot + 2 * license_year
    break_even = team / license_year

    result = {
        'tam_rub': tam, 'sam_rub': sam, 'som_rub': som,
        'tam_orgs': round(organizations_tam), 'sam_orgs': round(organizations),
        'license_year': rub(license_year), 'pilot': rub(pilot),
        'tam_million': million(tam), 'sam_million': million(sam), 'som_million': million(som),
        'break_even_clients': f'{break_even:.1f}'.replace('.', ','),
        'cloud_25min': f'{cloud_stt:.2f}'.replace('.', ',') + ' ₽',
        'competitor_onpremise': million(competitor),
    }
    (HERE / 'econ_out.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    print('TAM (весь сегмент, где протокол обязателен):', result['tam_million'],
          f"= {result['tam_orgs']} организаций x {result['license_year']}")
    print('SAM (промышленные холдинги с дочерними обществами):', result['sam_million'],
          f"= {result['sam_orgs']} организаций")
    print('SOM (первый год: 3 пилота + 2 лицензии):', result['som_million'])
    print('Цена лицензии на организацию в год:', result['license_year'])
    print('Себестоимость 25 минут облачного распознавания:', result['cloud_25min'])
    print('Точка безубыточности при текущей команде:', result['break_even_clients'], 'клиентов')
    print('Заявленная стоимость on-premise у конкурента:', result['competitor_onpremise'])
    print('saved', HERE / 'econ_out.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
