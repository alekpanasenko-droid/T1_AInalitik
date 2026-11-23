"""Six role-specific agents coordinated by a bounded, inspectable harness."""
import json
from math import isfinite
import re
import time

from . import portfolio_models
from .llm import complete_json, LLMError
from .sql_tool import execute_readonly, SQLRejected
from .store import dictionary, event, profile
from .sources import inspect

SCHEMA = '''Table positions: as_of_date TEXT YYYY-MM-DD, snapshot_kind TEXT (MONTH_END/DAILY),
side TEXT (ASSET/LIABILITY), product TEXT, currency TEXT (RUB/USD/EUR),
maturity_bucket TEXT, balance_ccy REAL, fx_rub_per_unit REAL, balance_rub REAL, rate_annual REAL.
For monthly trends use snapshot_kind='MONTH_END'. Never add balance_ccy across currencies.
Use SUM(balance_rub) for ruble totals. Russian «в рублях» means RUB EQUIVALENT across all currencies.
Add currency='RUB' only for «рублёвые позиции», «только RUB» or an explicit currency filter.
Dates available 2025-11-30 through 2026-10-31,
plus 2026-10-12 and 2026-10-13 DAILY. Individual positions are not bank-wide financial statements.
Product and maturity labels are in Russian; use exact values from data when filtering.'''
ROLES = ['Оркестратор', 'Бизнес-аналитик', 'Инженер данных', 'Аналитик данных', 'BI-разработчик', 'Тестировщик']


def source_schema(source_id):
    if source_id == 'portfolio':
        return SCHEMA
    data = inspect(source_id)
    return ('Источник: ' + data['name'] + '. Таблица ' + data['table'] + '. Поля и типы: '
            + ', '.join(c['name'] + ' ' + c['type'] for c in data['columns'])
            + '. Пример строк: ' + json.dumps(data['sample'][:3], ensure_ascii=False, default=str)
            + '. Используй только эту таблицу. Не предполагай, что это банковские данные.')


class BusinessAnalyst:
    name = 'Бизнес-аналитик'

    def run(self, question, history, source_id='portfolio'):
        if re.search(r'\b(удали|удалить|измени|изменить|перезапиши|обойди|powershell|drop|delete|update)\b', question, re.I):
            return {'status': 'refuse', 'message': 'Изменять исходные данные или обходить разрешённый SQL-инструмент нельзя. Могу выполнить запрос только на чтение.'}, 0
        model_request = portfolio_models.request(question)
        if model_request:
            return model_request, 0
        if (source_id == 'portfolio' and re.search(r'курс', question, re.I) and re.search(r'фиксированн', question, re.I)
                and re.search(r'\b(USD|EUR)\b', question, re.I) and len(re.findall(r'\b\d{1,2}\b', question)) >= 2
                and re.search(r'актив|пассив', question, re.I)):
            return {'status': 'ready', 'brief': question, 'title': 'Курсовой эффект при фиксированном остатке', 'unit': '₽'}, 0
        system = '''Ты бизнес-аналитик учебного банковского дата-агента. Определи, достаточно ли данных для расчёта.
Если неясны метрика, период, валюта/единица, разрез или критерий (например «текущий баланс», «нормальная ситуация», «проблемные», «средняя сумма»), верни status="clarify" и один конкретный вопрос в message. Не делай расчёт.
Если просят ROI/доходность банка, которой нет в схеме, status="refuse" с объяснением.
Если просят считать дневной срез полноценным месячным, status="refuse".
«Общая сумма активов/пассивов в рублях» — рублёвый эквивалент ВСЕХ валют через balance_rub.
Только слова «рублёвые позиции», «только RUB» означают фильтр currency='RUB'.
Если пользователь указал сравниваемые даты, валюту, сторону баланса и сценарий «при фиксированном остатке»,
данных достаточно для расчёта курсового эффекта: остаток в валюте на первой дате × изменение курса.
Иначе status="ready", brief — точная формулировка метрики, дат, фильтров и группировки, title — краткое название, unit — единица. Ответ JSON.'''
        if source_id != 'portfolio':
            system = '''Ты бизнес-аналитик дата-агента. По схеме источника определи метрику, период, фильтры и разрез. Если они неоднозначны, верни JSON {"status":"clarify","message":"один конкретный вопрос"}. Если данных для метрики нет, верни status="refuse" и объяснение. Иначе верни {"status":"ready","brief":"точная постановка","title":"краткое название","unit":"единица, если известна"}. Не выдумывай поля и числа.'''
        context = '\n'.join(f'{m["role"]}: {m["content"]}' for m in history[-8:])
        result, usage = complete_json(system + '\n' + source_schema(source_id), f'История:\n{context}\nТекущий вопрос: {question}')
        status = result.get('status')
        if status not in {'ready', 'clarify', 'refuse'}:
            raise LLMError('Бизнес-аналитик вернул неизвестный статус.')
        if status == 'ready' and not result.get('brief'):
            raise LLMError('Бизнес-аналитик не указал постановку расчёта.')
        return result, usage.get('total_tokens', 0)


class DataEngineer:
    name = 'Инженер данных'

    def run(self, source_id='portfolio'):
        if source_id != 'portfolio':
            data = inspect(source_id)
            issues = ['Есть пропуски'] if any(data['missing'].values()) else []
            return {'source': data['name'] + ' / ' + data['table'], 'records': data['records'], 'issues': issues}
        data = profile()
        issues = []
        if data['records'] == 0:
            issues.append('Источник пуст')
        if data['duplicate_keys']:
            issues.append(f"Повторных ключей: {data['duplicate_keys']}")
        if data['type_errors']:
            issues.append(f"Ошибок типов и кодов: {data['type_errors']}")
        if any(data['missing'].values()):
            issues.append('Есть пропуски')
        if data['damaged_labels']:
            issues.append('В названиях продуктов и сроков есть повреждённые символы')
        return {'source': data['source'], 'records': data['records'], 'issues': issues}


class DataAnalyst:
    name = 'Аналитик данных'

    def run(self, question, brief, history, feedback='', source_id='portfolio'):
        if source_id == 'portfolio' and re.search(r'курс', question, re.I) and re.search(r'фиксированн', question, re.I):
            month_names = {'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'мая': 5, 'май': 5,
                           'июн': 6, 'июл': 7, 'август': 8, 'сентябр': 9, 'октябр': 10,
                           'ноябр': 11, 'декабр': 12}
            month = next((number for stem, number in month_names.items() if stem in question.lower()), None)
            year_match = re.search(r'\b20\d{2}\b', question)
            days = re.findall(r'\b\d{1,2}\b', question)
            currency_match = re.search(r'\b(USD|EUR)\b', question, re.I)
            if month and year_match and len(days) >= 2 and currency_match:
                first, second = (f'{year_match.group()}-{month:02d}-{int(day):02d}' for day in days[:2])
                side = 'LIABILITY' if re.search(r'пассив', question, re.I) else 'ASSET'
                currency = currency_match.group().upper()
                sql = f'''WITH daily AS (
                  SELECT as_of_date, SUM(balance_ccy) AS balance_ccy,
                         MAX(fx_rub_per_unit) AS fx, SUM(balance_rub) AS amount_rub
                  FROM positions WHERE snapshot_kind='DAILY' AND side='{side}'
                    AND currency='{currency}' AND as_of_date IN ('{first}','{second}')
                  GROUP BY as_of_date)
                SELECT a.as_of_date AS date_before, b.as_of_date AS date_after,
                       a.fx AS fx_before, b.fx AS fx_after,
                       a.balance_ccy AS fixed_balance_ccy,
                       ROUND(a.balance_ccy*(b.fx-a.fx),2) AS fx_effect_rub,
                       ROUND((b.balance_ccy-a.balance_ccy)*b.fx,2) AS volume_effect_rub,
                       ROUND(b.amount_rub-a.amount_rub,2) AS actual_change_rub
                FROM daily a JOIN daily b ON a.as_of_date='{first}' AND b.as_of_date='{second}' '''
                return {'sql': sql, 'formula': 'B₁ × (FX₂ − FX₁); изменение объёма = (B₂ − B₁) × FX₂',
                        'filters': f"snapshot_kind=DAILY, side={side}, currency={currency}",
                        'period': f'{first} — {second}', 'unit': '₽'}, 0
        card = dictionary()
        system = '''Ты аналитик данных. Сгенерируй один read-only SQLite SELECT (разрешены WITH CTE) по таблице positions. Ответ JSON: {"sql":"...","formula":"...","filters":"...","period":"...","unit":"..."}.
Все числа вычисляются SQL, не тобой. Для месячных рядов исключай DAILY. Не используй sqlite_master, PRAGMA, подзапросы к другим таблицам, модификации, несколько инструкций. Результат — агрегированная таблица, не более 500 строк. Если в вопросе нужен курс, учитывай side/currency/date и не превращай изменение курса в доказанную причину без сценарного расчёта. Если запрос неразрешим, верни {"error":"причина"}.'''
        system += '''\nДля долей используй: WITH agg AS (SELECT product, SUM(balance_rub) AS amount_rub FROM positions WHERE ... GROUP BY product) SELECT product, amount_rub, ROUND(100.0*amount_rub/SUM(amount_rub) OVER (),2) AS share_pct FROM agg. Сохраняй фильтр side и дату в CTE. Не добавляй ставку, если её не просили.
Курсовой эффект при фиксированном остатке: SUM(balance_ccy) на первой дате × (курс второй даты − курс первой даты), в рублях. Обе даты должны иметь одинаковые фильтры side и currency.'''
        if source_id != 'portfolio':
            system = '''Ты аналитик данных. Верни JSON {"sql":"один SELECT или WITH","formula":"формула","filters":"фильтры","period":"период","unit":"единица"}. Используй только таблицу и поля указанного источника. SQL только для чтения, максимум 500 строк. Для CSV/XLSX числовые столбцы имеют тип TEXT: в SQLite применяй CAST(поле AS REAL) для SUM/AVG. Для PostgreSQL применяй его синтаксис. Все числа вычисляются запросом. Если задача неразрешима, верни {"error":"причина"}.'''
        result, usage = complete_json(system + '\n' + source_schema(source_id) + ('\nКарточка: ' + json.dumps(card, ensure_ascii=False) if source_id == 'portfolio' else ''),
                                      'Исходный запрос: ' + question + '\nПостановка: ' + brief + '\nПредыдущие реплики: ' + json.dumps(history[-4:], ensure_ascii=False)
                                      + ('\nИсправь предыдущую ошибку: ' + feedback if feedback else ''))
        if result.get('error'):
            raise SQLRejected(str(result['error']))
        if not result.get('sql'):
            raise LLMError('Аналитик данных не вернул SQL.')
        return result, usage.get('total_tokens', 0)


class BIDeveloper:
    name = 'BI-разработчик'

    def run(self, title, unit, result):
        cols, rows = result['columns'], result['rows']
        numeric = [c for c in cols if any(isinstance(r.get(c), (int, float)) for r in rows)]
        dimensions = [c for c in cols if c not in numeric]
        for preferred in ('as_of_date', 'product', 'currency', 'maturity_bucket'):
            if preferred in dimensions:
                dimensions.remove(preferred)
                dimensions.insert(0, preferred)
                break
        chart = None
        if rows and numeric and dimensions:
            x = dimensions[0]
            kind = 'line' if re.search(r'date|month|period|дата|месяц', x, re.I) else 'bar'
            chart = {'type': kind, 'x': x, 'y': numeric[0], 'title': title, 'unit': unit}
        return chart


class Tester:
    name = 'Тестировщик'

    def run(self, question, brief, proposal, result, chart, source_id='portfolio', model=None):
        if not result['columns']:
            return {'passed': False, 'message': 'Пустая структура результата'}, 0
        if not result['rows']:
            return {'passed': False, 'message': 'Нет строк для заданных условий. Уточните период или фильтры.'}, 0
        if chart and (chart['x'] not in result['columns'] or chart['y'] not in result['columns']):
            return {'passed': False, 'message': 'Поля графика не совпадают с результатом'}, 0
        if model:
            numeric = [row[chart['y']] for row in result['rows']]
            if not all(isfinite(value) for value in numeric):
                return {'passed': False, 'message': 'Числовой ряд модели содержит некорректные значения.'}, 0
            return {'passed': True, 'message': 'Срезы, числовой ряд модели и поля графика проверены.'}, 0
        if source_id == 'portfolio' and re.search(r'пассив', question, re.I) and not re.search(r'side\s*=\s*[\'\"]LIABILITY[\'\"]', proposal['sql'], re.I):
            return {'passed': False, 'message': 'Для пассивов отсутствует фильтр side=LIABILITY.'}, 0
        if source_id == 'portfolio' and re.search(r'актив', question, re.I) and not re.search(r'side\s*=\s*[\'\"]ASSET[\'\"]', proposal['sql'], re.I):
            return {'passed': False, 'message': 'Для активов отсутствует фильтр side=ASSET.'}, 0
        if re.search(r'дол[яюи]|share|percent|процент', question, re.I) and not any(
                re.search(r'share|percent|pct|дол[яюи]|процент', c, re.I) for c in result['columns']):
            return {'passed': False, 'message': 'Запрошена доля, но в таблице нет доли по категориям.'}, 0
        if (source_id == 'portfolio' and re.search(r'(общ[а-я]*\s+сумм[а-я]*|динамик[а-я]*)\s+(актив[а-я]*|пассив[а-я]*)\s+в\s+рублях', question, re.I)
                and not re.search(r'только\s+(рубл|RUB)|рубл[а-я]*\s+позици', question, re.I)
                and re.search(r'currency\s*=\s*[\'\"]RUB[\'\"]', proposal['sql'], re.I)):
            return {'passed': False, 'message': 'SQL исключил валютные позиции, хотя запрошен общий рублёвый эквивалент.'}, 0
        system = '''Ты тестировщик SQL-расчёта. Независимо сравни постановку задачи с SQL и его фильтрами,
периодом, агрегацией и группировкой. Особое правило: «в рублях» означает рублёвый эквивалент ВСЕХ валют,
а не фильтр currency='RUB', если нет слов «только рублёвые позиции». Для месячной динамики нужен MONTH_END.
Не придумывай чисел. Ответ JSON: {"passed": true/false, "message": "краткое объяснение"}.'''
        if source_id != 'portfolio':
            system = '''Ты тестировщик аналитического SQL. Сравни вопрос пользователя с SQL, фильтрами, периодом и группировкой. Используй только поля из схемы. Ответ JSON: {"passed":true/false,"message":"краткое объяснение"}. Не выдумывай чисел.'''
        verdict, usage = complete_json(system + '\n' + source_schema(source_id),
                                       'Исходный запрос: ' + question + '\nПостановка: ' + brief + '\nSQL: ' + proposal['sql'] +
                                       '\nКолонки результата: ' + ', '.join(result['columns']))
        passed = verdict.get('passed') is True
        return {'passed': passed, 'message': verdict.get('message', 'SQL не прошёл контроль.')}, usage.get('total_tokens', 0)


class Orchestrator:
    name = 'Оркестратор'
    max_steps = 12

    def run(self, question, history, conversation_id, source_id='portfolio'):
        started = time.monotonic()
        trace = []
        tokens = 0

        def step(agent, action, detail):
            if len(trace) >= self.max_steps:
                raise RuntimeError('Превышено число шагов агента.')
            trace.append({'agent': agent, 'action': action, 'detail': detail})

        try:
            step(self.name, 'План', 'Уточнить задачу → проверить источник → получить данные → рассчитать → визуализировать → проверить')
            brief, n = BusinessAnalyst().run(question, history, source_id)
            tokens += n
            step(BusinessAnalyst.name, 'Уточнение требований', brief)
            if brief['status'] != 'ready':
                return self._finish(brief['status'], brief.get('message', 'Уточните запрос.'), trace, started, conversation_id, tokens)
            source = DataEngineer().run(source_id)
            step(DataEngineer.name, 'Проверка источника', source)
            if source['records'] == 0 or any('Ошибок типов' in x for x in source['issues']):
                return self._finish('error', 'Источник пуст или содержит критичные ошибки типов. Проверьте профиль данных.', trace, started, conversation_id, tokens)
            feedback = ''
            model = brief.get('model')
            model_summary = None
            for attempt in range(2):
                if model:
                    proposal = portfolio_models.proposal(model, source_id)
                else:
                    proposal, n = DataAnalyst().run(question, brief['brief'], history, feedback, source_id)
                    tokens += n
                step(DataAnalyst.name, 'План расчёта', proposal)
                try:
                    result = execute_readonly(proposal['sql'], source_id=source_id)
                except SQLRejected as exc:
                    feedback = str(exc)
                    step(DataAnalyst.name, 'Ошибка инструмента', feedback)
                    if attempt == 0 and not model:
                        continue
                    raise
                step(DataAnalyst.name, 'SQL-инструмент', {'rows': len(result['rows']), 'duration_ms': result['duration_ms']})
                if model:
                    model_started = time.monotonic()
                    result, chart, model_summary = portfolio_models.calculate(model, result['rows'])
                    step(DataAnalyst.name, 'Расчёт модели Python', {
                        'summary': model_summary, 'duration_ms': round((time.monotonic() - model_started) * 1000, 1)})
                else:
                    chart = BIDeveloper().run(brief.get('title', 'Результат анализа'), brief.get('unit', proposal.get('unit', '')), result)
                step(BIDeveloper.name, 'Визуализация', chart or 'Таблица без графика')
                check, n = Tester().run(question, brief['brief'], proposal, result, chart, source_id, model)
                tokens += n
                step(Tester.name, 'Контроль результата', check)
                if check['passed'] or model:
                    break
                feedback = check['message']
            if not check['passed']:
                return self._finish('error', 'Не удалось проверить расчёт: ' + check['message'], trace, started, conversation_id, tokens)
            summary = model_summary or f"Получено {len(result['rows'])} строк по запросу «{brief.get('title', 'анализ данных')}». Значения рассчитаны SQL по учебному источнику."
            if not model and len(result['rows']) == 1:
                numeric = [(key, value) for key, value in result['rows'][0].items() if isinstance(value, (int, float))]
                if numeric:
                    summary += ' ' + '; '.join(f'{key}: {value:,.2f}'.replace(',', ' ') for key, value in numeric[:5]) + '.'
            payload = self._finish('ok', summary, trace, started, conversation_id, tokens)
            payload.update(title=brief.get('title', 'Результат анализа'), source_id=source_id, unit=brief.get('unit') or proposal.get('unit', ''),
                           period=proposal.get('period', ''), filters=proposal.get('filters', ''), formula=proposal.get('formula', ''),
                           sql=proposal['sql'], columns=result['columns'], rows=result['rows'], chart=chart, source=source)
            return payload
        except (LLMError, SQLRejected, RuntimeError, ValueError) as exc:
            step('Оркестратор', 'Остановка', str(exc)) if len(trace) < self.max_steps else None
            return self._finish('error', str(exc), trace, started, conversation_id, tokens)

    @staticmethod
    def _finish(status, message, trace, started, conversation_id, tokens):
        elapsed = round((time.monotonic() - started) * 1000, 1)
        payload = {'status': status, 'message': message, 'trace': trace, 'duration_ms': elapsed, 'llm_tokens': tokens}
        event(conversation_id, 'agent_response', {'status': status, 'trace': trace}, elapsed, tokens)
        return payload
