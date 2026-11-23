"""Typed tool calls, clarification and report formatting for project models."""
import json
from math import isclose, isfinite
import re
from time import monotonic

from pydantic import ValidationError

from .llm import LLMError, complete_json
from .project_finance import (
    ASSUMPTIONS, BASELINE, DEMO, FIELDS, FORMULA, ModelInputs, ModelRequest, Scenario,
    amount, breakeven, minimum_advance, number, project,
)


SYSTEM = '''Ты бизнес-аналитик в режиме финансового моделирования. Извлеки параметры и верни один JSON.
Никогда не считай результаты, не подставляй суммы, ставки, валюту, периоды, понижения или повышения от себя.
Актуальные подтверждённые вводные и имена сценариев приходят в state. В base указывай только явно заданные новые значения.
Денежные суммы в единицах валюты: 1,5 млн ₽ -> 1500000 и currency RUB. Доли в процентах: 40% -> 40.
Оплата в тот же месяц -> payment_delay_months=0. Без инвестиций -> capex=0. Без амортизации -> depreciation_months=0.
Базовые поля: months, currency, opening_cash, capex, revenue_monthly, fixed_cost_monthly, variable_cost_pct,
payment_delay_months, depreciation_months, discount_annual_pct. Неизвестные поля опускай: сервер запросит их.
Формат: {"action":"base","base":{},"scenarios":[],"scenario_names":[],"reset":false}.
Выбор action:
- base: первая модель, её расчёт или явно запрошенное изменение базовых вводных.
- investment: NPV, IRR или окупаемость. Явную ставку передай в discount_annual_pct.
- scenario: один сценарий «что если». Изменения относятся к базовой модели.
- compare: сравнение базы и сценариев. Добавь новые scenarios и точные имена сохранённых в scenario_names.
- sensitivity: несколько независимых изменений, одно на каждый сценарий. Включи все заданные факторы.
- breakeven: операционная и денежная безубыточность при оплате в текущем месяце.
- advance: минимальная доля аванса. Передай ровно один ранее сохранённый сценарий в scenario_names
или новый в scenarios. Без ссылки и новых условий используй базовые условия.
- clarify: неоднозначная задача, не хватает параметров сценария либо нужны данные или возможности вне этой модели.
Для clarify укажи конкретный question, base может содержать только однозначные новые базовые вводные.
Сценарии имеют ключи name, revenue_change_pct, fixed_change_pct, variable_cost_pct, payment_delay_months, advance_pct.
Пропускай необязательные параметры сценария, если их не указали. Не копируй параметры базы в сценарий.
Рост и падение выручки и постоянных затрат укажи знаковыми процентами от базы. Доля переменных расходов и отсрочка — новые значения.
«стрессовый сценарий из запроса 5» и аналогичные ссылки означают уже сохранённый сценарий из state.
Если сохранён один неблагоприятный сценарий, используй его точное имя из state для ссылки «стрессовый сценарий из запроса».
После вопроса об отсутствующих параметрах используй pending для продолжения той же операции.
Сброс reset=true допустим, только если пользователь явно начал новый проект. Поддерживается помесячная модель
с постоянной выручкой, фиксированными расходами, линейной амортизацией и отсрочкой. Налоги, кредиты,
остаточная стоимость, вероятность, отраслевые данные и произвольные исходящие платежи по датам за пределами модели.
Если их требуют учесть, объясни ограничение через clarify и предложи доступный расчёт.'''

MONTHLY = {
    'month': 'Месяц', 'revenue': 'Выручка', 'receipts': 'Поступления', 'costs': 'Денежные расходы',
    'depreciation': 'Амортизация', 'profit': 'Операционная прибыль', 'flow': 'Чистый денежный поток', 'cash': 'Остаток денег',
}
SCENARIO = 'Сценарий'
METRICS = [
    SCENARIO, 'Операционная прибыль', 'Остаток в конце', 'Минимальный остаток', 'Месяц первого разрыва',
    'Нужно финансирование', 'Поступления после горизонта', 'NPV', 'IRR за месяц, %', 'IRR за год, %', 'Месяц окупаемости',
]


def interpret(question, history, state):
    if question.strip().casefold().rstrip('.!') == 'рассчитай учебный пример':
        return ModelRequest(action='base', base=ModelInputs.model_validate(DEMO), reset=True), 0
    if question.lstrip().startswith('{'):
        return ModelRequest.model_validate_json(question), 0
    text = question.casefold()
    pending_action = state.get('pending', {}).get('action')
    quoted_rate = re.fullmatch(r'\s*([+-]?\d+(?:[.,]\d+)?)\s*%\s*(?:годовых)?[.!]?\s*', text)
    if quoted_rate and pending_action in {'investment', 'sensitivity'}:
        discount = quoted_rate[1].replace(',', '.')
        return ModelRequest(action=pending_action, base=ModelInputs(discount_annual_pct=discount)), 0
    hint = requested_action(text)
    context = {'state': state, 'recent_requests': [item['content'] for item in history if item['role'] == 'user'][-3:],
               'request': question}
    if hint:
        context['requested_action'] = hint
    tokens = 0
    for attempt in range(2):
        data, usage = complete_json(SYSTEM, json.dumps(context, ensure_ascii=False))
        tokens += usage.get('total_tokens', 0)
        if 'discount_annual_pct' in data and isinstance(data.get('base', {}), dict):
            discount = data.pop('discount_annual_pct')
            if discount is not None:
                data.setdefault('base', {})['discount_annual_pct'] = discount
        if isinstance(data.get('scenarios'), dict):
            data['scenarios'] = [
                {'name': name, **values} for name, values in data['scenarios'].items() if isinstance(values, dict)
            ]
        try:
            request = ModelRequest.model_validate(data)
            break
        except ValidationError as exc:
            if attempt:
                raise
            context['validation_feedback'] = [
                {'field': '.'.join(map(str, error['loc'])), 'issue': error['msg']}
                for error in exc.errors(include_url=False, include_input=False)
            ]
    if hint and request.action != 'clarify':
        request.action = hint
    elif request.action == 'base' and (request.scenarios or request.scenario_names):
        request.action = 'scenario'
    if request.action == 'base' and pending_action in {'investment', 'sensitivity'} and request.base.discount_annual_pct is not None:
        request.action = pending_action
    return request, tokens


def requested_action(text):
    if ('аванс' in text and 'минимальн' in text
            or re.search(r'минимальн\w*\s+дол\w*.*(?:платить|оплачивать)', text)):
        return 'advance'
    if 'чувствительност' in text or 'по одному фактору' in text:
        return 'sensitivity'
    if 'безубыточност' in text or re.search(r'операционн\w+\s+прибыль\s+равна\s+нулю', text):
        return 'breakeven'
    if 'сравни' in text and 'сценар' in text:
        return 'compare'
    if re.search(r'\b(?:npv|irr)\b|чистая приведённая стоимость|внутренняя норма доходности', text):
        return 'investment'
    return None


def questions_for(base, action):
    required = set(FIELDS) - {'discount_annual_pct', 'depreciation_months'}
    if base.capex:
        required.add('depreciation_months')
    if action in {'investment', 'sensitivity'}:
        required.add('discount_annual_pct')
    return [label for key, label in FIELDS.items() if key in required and getattr(base, key) is None]


def result(title, columns, rows, message, base, chart=None, **extra):
    return {'status': 'ok', 'title': title, 'columns': columns, 'rows': rows, 'message': message,
            'unit': base.currency, 'period': f'Месяц 0–{base.months}', 'filters': 'Вводные проекта',
            'formula': FORMULA, 'assumptions': ASSUMPTIONS, 'chart': chart, **extra}


def metric_row(scenario, projection):
    values = (scenario.name, projection.profit, projection.rows[-1]['cash'], projection.minimum_cash,
              projection.first_gap, projection.funding, projection.receivables, projection.npv,
              projection.irr_monthly, projection.irr_annual, projection.payback)
    return dict(zip(METRICS, map(number, values)))


def monthly_result(title, base, scenarios, message=None):
    rows, metrics = [], []
    for scenario in scenarios:
        projection = project(base, scenario)
        metrics.append(metric_row(scenario, projection))
        rows.extend({SCENARIO: scenario.name, **{label: number(row[key]) for key, label in MONTHLY.items()}}
                    for row in projection.rows)
        if len(scenarios) == 1 and message is None:
            message = (f'{scenario.name}. Операционная прибыль за {base.months} мес. — '
                       f'{amount(projection.profit)} {base.currency}, конечный остаток — '
                       f"{amount(projection.rows[-1]['cash'])} {base.currency}. ")
            if projection.first_gap is None:
                message += 'На конец каждого месяца денег хватает.'
            else:
                message += (f'Первый разрыв — месяц {projection.first_gap}, потребуется '
                            f'{amount(projection.funding)} {base.currency} без резерва.')
            if projection.receivables:
                message += f' После горизонта ожидается ещё {amount(projection.receivables)} {base.currency}.'
            if projection.npv is not None:
                message += f' NPV при годовой ставке {number(base.discount_annual_pct):g}% — {amount(projection.npv)} {base.currency}.'
            elif projection.payback is not None:
                message += f' Простая окупаемость — к концу месяца {projection.payback}.'
            if projection.irr_monthly is None and base.capex:
                message += ' IRR для этих потоков не определена однозначно.'
    if message is None:
        worst = max(metrics, key=lambda row: row['Нужно финансирование'])
        message = (f'Сравнено сценариев: {len(scenarios)}. '
                   + (f"Максимальная потребность в финансировании — {amount_from_float(worst['Нужно финансирование'])} "
                      f"{base.currency} в сценарии «{worst[SCENARIO]}»." if worst['Нужно финансирование']
                      else 'В рассмотренных сценариях разрыва на конец месяца нет.'))
    chart = {'type': 'line', 'x': MONTHLY['month'], 'y': MONTHLY['cash'], 'series': SCENARIO}
    return result(title, [SCENARIO, *MONTHLY.values()], rows, message, base, chart,
                  metrics={'columns': METRICS, 'rows': metrics}, bi_monthly=True)


def amount_from_float(value):
    return f'{value:,.2f}'.replace(',', ' ').replace('.', ',')


def sensitivity(base, scenarios):
    fields = {
        'revenue_change_pct': (0, 'Изменение выручки, %'), 'fixed_change_pct': (0, 'Изменение постоянных расходов, %'),
        'variable_cost_pct': (base.variable_cost_pct, 'Доля переменных расходов, %'),
        'payment_delay_months': (base.payment_delay_months, 'Отсрочка, мес.'), 'advance_pct': (0, 'Доля аванса, %'),
    }
    baseline = project(base, BASELINE)
    columns = [SCENARIO, 'NPV', 'Изменение NPV', 'Минимальный остаток', 'Изменение минимального остатка']
    rows = []
    tested = set()
    for scenario in scenarios:
        for key, (default, title) in fields.items():
            value = getattr(scenario, key)
            if value in (None, default) or (key, value) in tested:
                continue
            tested.add((key, value))
            name = f'{title} · {number(value):g}'
            forecast = project(base, Scenario(name=name, **{key: value}))
            values = (name, forecast.npv, forecast.npv - baseline.npv, forecast.minimum_cash,
                      forecast.minimum_cash - baseline.minimum_cash)
            rows.append(dict(zip(columns, map(number, values))))
    if not rows:
        raise ValueError('Укажите факторы и величину каждого изменения относительно базы.')
    strongest_npv = max(rows, key=lambda row: abs(row['Изменение NPV']))[SCENARIO]
    strongest_cash = max(rows, key=lambda row: abs(row['Изменение минимального остатка']))[SCENARIO]
    return result('Чувствительность проекта', columns, rows,
                  f'Сильнее всего на NPV влияет «{strongest_npv}», на минимальный остаток — «{strongest_cash}».',
                  base, {'type': 'bar', 'x': SCENARIO, 'y': 'Изменение NPV'})


def calculate(request, base, scenarios):
    action = request.action
    if action == 'breakeven':
        operating, cash = breakeven(base)
        columns = ['Порог', 'Месячная выручка']
        rows = [dict(zip(columns, (label, number(value)))) for label, value in
                (('Операционный, с амортизацией', operating), ('Денежный, при оплате в тот же месяц', cash))]
        operating_label = f'{amount(operating)} {base.currency}' if operating is not None else 'недостижим'
        cash_label = f'{amount(cash)} {base.currency}' if cash is not None else 'недостижим'
        message = (f'Месячная выручка в базе — {amount(base.revenue_monthly)} {base.currency}. '
                   f'Операционный порог — {operating_label}, денежный — {cash_label}.')
        return result('Точка безубыточности', columns, rows, message, base,
                      {'type': 'bar', 'x': columns[0], 'y': columns[1]})
    if action == 'advance':
        if len(scenarios) > 1:
            raise ValueError('Укажите один сценарий для расчёта аванса.')
        scenario = (scenarios[0] if scenarios else BASELINE).model_copy(update={'advance_pct': BASELINE.advance_pct})
        required = minimum_advance(base, scenario)
        if required is None:
            return monthly_result('Оценка аванса', base, [scenario],
                                  'Даже 100% оплаты в месяц продажи не покроют разрыв на конец месяца.')
        with_advance = scenario.model_copy(update={'name': f'С авансом {number(required):g}%', 'advance_pct': required})
        if with_advance.name == scenario.name:
            with_advance = with_advance.model_copy(update={'name': 'Пересчёт с авансом'})
        percent = f'{number(required):g}'.replace('.', ',')
        return monthly_result('Аванс для покрытия разрыва', base, [scenario, with_advance],
                              f'Минимальный аванс с точностью до 0,01 п.п. — {percent}% каждой продажи.')
    if action in {'scenario', 'compare', 'sensitivity'} and not scenarios:
        raise ValueError('Укажите изменение для сценария или назовите ранее рассчитанный сценарий.')
    if action == 'sensitivity':
        return sensitivity(base, scenarios)
    if action == 'compare':
        return monthly_result('Сравнение сценариев', base, [BASELINE, *scenarios])
    if action == 'scenario':
        return monthly_result('Сценарий проекта' if len(scenarios) == 1 else 'Сценарии проекта', base, scenarios)
    if action == 'investment':
        return monthly_result('Оценка инвестиций', base, scenarios or [BASELINE])
    return monthly_result('Финансовая модель проекта', base, [BASELINE])


def run(question, history, state, step):
    tokens = 0
    try:
        request, tokens = interpret(question, history, state)
    except (ValidationError, LLMError) as exc:
        message = str(exc) if isinstance(exc, LLMError) else 'Уточните значения. Проверьте суммы, горизонты и проценты.'
        return {'status': 'clarify', 'message': message}, tokens
    if request.reset:
        state = {}
    existing_base = state.get('base', {})
    changes = request.base.model_dump(exclude_none=True)
    if request.action not in {'base', 'clarify'}:
        changes = {field: value for field, value in changes.items()
                   if field not in existing_base or field == 'discount_annual_pct' and request.action in {'investment', 'sensitivity'}}
    base = ModelInputs.model_validate({**existing_base, **changes})
    saved = dict(state.get('scenarios', {}))
    request = resolve_reference(question, request, saved, base)
    selected = {scenario.name: scenario for scenario in request.scenarios}
    pending = state.get('pending', {})
    if not selected and pending.get('action') == request.action:
        selected = {scenario.name: scenario for scenario in ModelRequest.model_validate(pending).scenarios}
    unknown = [name for name in request.scenario_names if name not in saved and name not in selected]
    for name in request.scenario_names:
        if name in saved and name not in selected:
            selected[name] = Scenario.model_validate(saved[name])
    step('Бизнес-аналитик', 'Вводные и сценарии', {
        'operation': request.action, 'base': base.model_dump(mode='json', exclude_none=True),
        'scenarios': [scenario.model_dump(mode='json', exclude_none=True) for scenario in selected.values()],
    })
    model_state = {'base': base.model_dump(mode='json', exclude_none=True), 'scenarios': saved}
    if request.action != 'clarify':
        model_state['pending'] = request.model_copy(update={'scenarios': list(selected.values())}).model_dump(mode='json')
    elif pending:
        model_state['pending'] = pending
    missing = questions_for(base, request.action)
    if request.action == 'clarify' or missing or unknown or BASELINE.name in selected:
        message = request.question if request.action == 'clarify' else ''
        if not message and missing:
            message = 'Для расчёта уточните: ' + ', '.join(missing) + '.'
        if not message and unknown:
            message = 'Уточните условия сценария: ' + ', '.join(unknown) + '.'
        return {'status': 'clarify', 'message': message or 'Укажите другое название сценария.', 'model_state': model_state}, tokens
    step('Инженер данных', 'Проверка вводных', 'Типы, диапазоны и обязательные значения проверены. Источник — вводные из диалога.')
    try:
        started = monotonic()
        payload = calculate(request, base, list(selected.values()))
    except ValueError as exc:
        return {'status': 'clarify', 'message': str(exc), 'model_state': model_state}, tokens
    step('Аналитик данных', 'Расчёт модели Python', {'formula': FORMULA, 'assumptions': ASSUMPTIONS,
         'elapsed_ms': round((monotonic() - started) * 1000, 1)})
    step('BI-разработчик', 'Таблица и график', {'rows': len(payload['rows']), 'chart': payload['chart']})
    if not valid_result(base, payload):
        step('Тестировщик', 'Контроль расчёта', 'Обнаружено несоответствие в таблице или остатках денег.')
        return {'status': 'error', 'message': 'Таблица модели не прошла проверку денежного потока.'}, tokens
    step('Тестировщик', 'Контроль расчёта', 'Проверена структура таблицы, конечность чисел и сверка остатков по месяцам.')
    if request.action in {'scenario', 'compare'}:
        saved.update({name: scenario.model_dump(mode='json', exclude_none=True) for name, scenario in selected.items()})
    model_state.pop('pending', None)
    return {**payload, 'model_state': model_state}, tokens


def resolve_reference(question, request, saved, base):
    text = question.casefold()
    if request.action not in {'compare', 'advance'} or not re.search(r'стрессов\w+\s+сценар', text):
        return request
    if not re.search(r'из\s+запрос|ранее|сохран[её]н|прошл', text):
        return request
    candidates = [name for name in saved if 'стресс' in name.casefold()]
    if not candidates:
        for name, values in saved.items():
            scenario = Scenario.model_validate(values)
            if (scenario.revenue_change_pct < 0 or scenario.fixed_change_pct > 0
                    or scenario.variable_cost_pct is not None and scenario.variable_cost_pct > (base.variable_cost_pct or 0)
                    or scenario.payment_delay_months is not None and scenario.payment_delay_months > (base.payment_delay_months or 0)):
                candidates.append(name)
    if len(candidates) != 1:
        return request.model_copy(update={'action': 'clarify', 'question': 'Какой сохранённый сценарий использовать? Укажите его название.'})
    name = candidates[0]
    if request.action == 'advance':
        return request.model_copy(update={'scenarios': [], 'scenario_names': [name]})
    new = [scenario for scenario in request.scenarios if 'стресс' not in scenario.name.casefold() and scenario.name != name]
    refs = [value for value in request.scenario_names if 'стресс' not in value.casefold() and value != name]
    return request.model_copy(update={'scenarios': new, 'scenario_names': [*refs, name]})


def valid_result(base, payload):
    columns, rows, chart = payload['columns'], payload['rows'], payload['chart']
    if chart and not {chart['x'], chart['y']}.issubset(columns):
        return False
    balances = {}
    for row in rows:
        if set(row) != set(columns) or not all(isfinite(value) for value in row.values() if isinstance(value, (int, float))):
            return False
        if payload.get('bi_monthly'):
            month = row[MONTHLY['month']]
            previous_month, balance = balances.get(row[SCENARIO], (-1, base.opening_cash))
            next_balance = row[MONTHLY['cash']]
            if month != previous_month + 1 or not isclose(float(balance) + row[MONTHLY['flow']], next_balance,
                                                         rel_tol=1e-15, abs_tol=0.016):
                return False
            balances[row[SCENARIO]] = (month, next_balance)
    return all(month == base.months for month, _ in balances.values())
