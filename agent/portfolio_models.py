"""Read-only inputs and reproducible calculations for portfolio models."""
from calendar import monthrange
from datetime import date
from math import isclose, isfinite
import re
from statistics import fmean, linear_regression

from .sources import query_schema


MONTHS = (
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
)
GENITIVE_MONTHS = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
MONTH_END = "snapshot_kind='MONTH_END'"
FX_COLUMNS = ('Показатель', 'До, ₽', 'После, ₽', 'Изменение, ₽')
FORECAST_COLUMNS = ('Месяц', 'Тип', 'Сумма, ₽', 'Последнее значение, ₽', 'Линейный тренд, ₽')


def month_number(value):
    return value.year * 12 + value.month - 1


def month_end(index):
    year, month = divmod(index, 12)
    return date(year, month + 1, monthrange(year, month + 1)[1])


def request(question):
    """Recognize the explicit requests for monthly model comparison or FX revaluation."""
    prompt = question.lower()
    if (re.search(r'прогноз', prompt) and re.search(r'линейн\w*\s+тренд', prompt)
            and re.search(r'последн\w*\s+(?:известн\w*\s+)?значени', prompt)
            and re.search(r'сумм\w*\s+(?:актив|пассив)', prompt)):
        if ('актив' in prompt and 'пассив' in prompt
                or re.search(r'только\s+(?:рубл|валют|usd|eur|rub)|по\s+(?:продукт|валют|срок)', prompt)):
            return {'status': 'clarify', 'message': 'Эта модель сравнивает методы для общего месячного остатка по одной стороне. Укажите активы или пассивы по всем валютам.'}
        names = '|'.join(MONTHS)
        target = re.search(rf'\bна\s+({names})(?:\s+(?:и|по)\s+({names}))?\s+(20\d{{2}})\b', prompt)
        if not target:
            return {'status': 'clarify', 'message': 'На какие месяцы построить прогноз? Например, на ноябрь и декабрь 2026 года.'}
        first, second, year = target.groups()
        start = date(int(year), MONTHS.index(first) + 1, 1)
        end = date(int(year), MONTHS.index(second or first) + 1, 1)
        if not 0 <= month_number(end) - month_number(start) < 12:
            return {'status': 'clarify', 'message': 'Укажите месяцы прогноза в хронологическом порядке.'}
        side = 'LIABILITY' if re.search(r'сумм\w*\s+пассив', prompt) else 'ASSET'
        return {'status': 'ready', 'title': 'Прогноз суммы ' + ('активов' if side == 'ASSET' else 'пассивов'),
                'brief': question, 'unit': '₽', 'model': {'kind': 'forecast', 'side': side,
                    'start': start.isoformat(), 'end': end.isoformat()}}

    if (re.search(r'моделир|стресс[ -]?тест|смоделиру|переоцен', prompt)
            and 'курс' in prompt and 'актив' in prompt and 'пассив' in prompt
            and re.search(r'неизменн|фиксированн', prompt)):
        currencies = set(re.findall(r'\b(?:usd|eur)\b', prompt))
        change = re.findall(r'\b(рост\w*|увеличен\w*|повышен\w*|снижен\w*|падени\w*)\s+курс\w*\s+(?:usd|eur)\s+на\s+(\d+(?:[,.]\d+)?)\s*%', prompt)
        if len(currencies) != 1 or len(change) != 1 or prompt.count('%') != 1:
            return {'status': 'clarify', 'message': 'Укажите одну валюту (USD или EUR) и одно изменение курса, например рост на 10%.'}
        dates = re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', prompt)
        named_dates = re.findall(rf'\b(\d{{1,2}})\s+({"|".join(GENITIVE_MONTHS)})\s+(20\d{{2}})\b', prompt)
        try:
            if len(dates) + len(named_dates) != 1:
                raise ValueError
            if dates:
                snapshot_date = date.fromisoformat(dates[0])
            else:
                day, month, year = named_dates[0]
                snapshot_date = date(int(year), GENITIVE_MONTHS.index(month) + 1, int(day))
            if snapshot_date.day != monthrange(snapshot_date.year, snapshot_date.month)[1]:
                raise ValueError
        except ValueError:
            return {'status': 'clarify', 'message': 'Укажите одну дату месячного среза на конец месяца.'}
        direction, percentage = change[0]
        pct = float(percentage.replace(',', '.'))
        if direction.startswith(('снижен', 'падени')):
            pct = -pct
        if not isfinite(pct) or pct <= -100:
            return {'status': 'clarify', 'message': 'Укажите изменение, при котором новый курс остаётся положительным.'}
        currency = currencies.pop().upper()
        return {'status': 'ready', 'title': f'Валютный стресс-тест · {currency}',
                'brief': question, 'unit': '₽', 'model': {'kind': 'fx', 'currency': currency,
                    'date': snapshot_date.isoformat(), 'pct': pct}}
    return None


def proposal(model, source_id):
    schema = query_schema(source_id)
    required = {'as_of_date', 'snapshot_kind', 'side', 'balance_rub'}
    if model['kind'] == 'fx':
        required.update({'currency', 'balance_ccy', 'fx_rub_per_unit'})
    if schema['table'] != 'positions' or not required.issubset(c['name'] for c in schema['columns']):
        raise ValueError('Для этого расчёта выберите источник с таблицей positions и полями банковского портфеля.')
    if model['kind'] == 'forecast':
        return {
            'sql': f"""SELECT CAST(as_of_date AS TEXT) AS as_of_date, SUM(CAST(balance_rub AS DOUBLE PRECISION)) AS amount_rub
FROM positions WHERE {MONTH_END} AND side='{model['side']}' AND as_of_date<'{model['start']}'
GROUP BY positions.as_of_date ORDER BY positions.as_of_date""",
            'formula': 'Последнее значение и линейный тренд (МНК). MAE по одинаковым одношаговым прогнозам на прошлых месяцах.',
            'filters': f"snapshot_kind=MONTH_END, side={model['side']}; все валюты",
            'period': f"{model['start'][:7]} — {model['end'][:7]}",
        }
    currency = model['currency']
    return {
        'sql': f"""SELECT side, SUM(CAST(balance_rub AS DOUBLE PRECISION)) AS amount_rub,
SUM(CASE WHEN currency='{currency}' THEN CAST(balance_ccy AS DOUBLE PRECISION) ELSE 0 END) AS balance_ccy,
MIN(CASE WHEN currency='{currency}' THEN CAST(fx_rub_per_unit AS DOUBLE PRECISION) END) AS fx_min,
MAX(CASE WHEN currency='{currency}' THEN CAST(fx_rub_per_unit AS DOUBLE PRECISION) END) AS fx_max
FROM positions WHERE {MONTH_END} AND as_of_date='{model['date']}' AND side IN ('ASSET', 'LIABILITY')
GROUP BY side ORDER BY side""",
        'formula': 'Новый курс = исходный курс × (1 + шок / 100). Изменение в ₽ = остаток в валюте × (новый курс − исходный курс).',
        'period': model['date'],
        'filters': f"snapshot_kind=MONTH_END; все позиции; шок курса {currency} {model['pct']:+g}%",
    }


def rubles(amount):
    return f'{amount:,.2f}'.replace(',', ' ').replace('.', ',') + ' ₽'


def forecast(model, rows):
    dates = [date.fromisoformat(row['as_of_date']) for row in rows]
    indices = [month_number(day) for day in dates]
    start = month_number(date.fromisoformat(model['start']))
    stop = month_number(date.fromisoformat(model['end']))
    if len(rows) < 4:
        raise ValueError('Для сравнения методов нужны хотя бы четыре месячных среза до прогноза.')
    if indices != list(range(indices[0], start)) or any(day != month_end(i) for day, i in zip(dates, indices)):
        raise ValueError('Между срезами или перед началом прогноза есть пропуск. Нужен непрерывный ряд на конец каждого месяца.')
    values = [row['amount_rub'] for row in rows]
    if any(value is None or not isfinite(value) for value in values):
        raise ValueError('В месячном ряду есть отсутствующие или некорректные суммы.')
    naive_errors, trend_errors = [], []
    for i in range(3, len(values)):
        slope, intercept = linear_regression(range(i), values[:i])
        naive_errors.append(abs(values[i] - values[i - 1]))
        trend_errors.append(abs(values[i] - (intercept + slope * i)))
    naive_mae, trend_mae = fmean(naive_errors), fmean(trend_errors)
    use_trend = trend_mae < naive_mae
    slope, intercept = linear_regression(range(len(values)), values)
    table = [dict(zip(FORECAST_COLUMNS, (day.isoformat(), 'Факт', round(value, 2), None, None)))
             for day, value in zip(dates, values)]
    for index in range(start, stop + 1):
        trend = intercept + slope * (index - indices[0])
        selected = trend if use_trend else values[-1]
        table.append(dict(zip(FORECAST_COLUMNS, (month_end(index).isoformat(), 'Прогноз',
                                                 round(selected, 2), round(values[-1], 2), round(trend, 2)))))
    winner = 'линейный тренд' if use_trend else 'последнее значение'
    summary = (f'Для графика выбрано «{winner}». Средняя абсолютная ошибка за {len(naive_errors)} '
               f'проверочных месяцев: последнее значение — {rubles(naive_mae)}, линейный тренд — {rubles(trend_mae)}. '
               f'Прогноз до {month_end(stop):%d.%m.%Y} — {rubles(table[-1][FORECAST_COLUMNS[2]])}.')
    chart = {'type': 'line', 'x': FORECAST_COLUMNS[0], 'y': FORECAST_COLUMNS[2],
             'forecast_from': month_end(start).isoformat()}
    return {'columns': list(FORECAST_COLUMNS), 'rows': table}, chart, summary


def fx_stress(model, rows):
    if {row['side'] for row in rows} != {'ASSET', 'LIABILITY'}:
        raise ValueError('На указанную дату нет месячного среза с активами и пассивами.')
    rates = [row[key] for row in rows for key in ('fx_min', 'fx_max') if row[key] is not None]
    if not rates or not all(isfinite(value) and value > 0 and isclose(value, rates[0]) for value in rates):
        raise ValueError(f"Для {model['currency']} на эту дату нет единого положительного исходного курса.")
    rate = rates[0]
    new_rate = rate * (1 + model['pct'] / 100)
    amounts = {row['side']: (row['amount_rub'], row['balance_ccy'] * (new_rate - rate)) for row in rows}
    assets, liabilities = amounts['ASSET'], amounts['LIABILITY']
    gap = (assets[0] - liabilities[0], assets[1] - liabilities[1])
    table = [dict(zip(FX_COLUMNS, (label, round(before, 2), round(before + change, 2), round(change, 2))))
             for label, (before, change) in [('Активы', assets), ('Пассивы', liabilities), ('Активы − пассивы', gap)]]
    rate_before = f'{rate:.4f}'.rstrip('0').rstrip('.').replace('.', ',')
    rate_after = f'{new_rate:.4f}'.rstrip('0').rstrip('.').replace('.', ',')
    summary = (f"Курс {model['currency']} меняется с {rate_before} до {rate_after} ₽ за единицу при тех же валютных остатках. "
               f"Изменение активов: {rubles(assets[1])}, пассивов: {rubles(liabilities[1])}, "
               f"разницы между ними: {rubles(gap[1])}.")
    return {'columns': list(FX_COLUMNS), 'rows': table}, {'type': 'bar', 'x': FX_COLUMNS[0], 'y': FX_COLUMNS[3]}, summary


def calculate(model, rows):
    if model['kind'] == 'forecast':
        return forecast(model, rows)
    return fx_stress(model, rows)
