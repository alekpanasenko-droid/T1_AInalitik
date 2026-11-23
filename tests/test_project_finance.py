import json
from io import BytesIO
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import load_workbook

from agent.project_finance import BASELINE, DEMO, ModelInputs, Scenario, breakeven, minimum_advance, project
from agent.store import initialize
from app import MessageIn, ModeIn, change_mode, create_conversation, export, messages, post_message, report, save_report


class FinanceTests(unittest.TestCase):
    def test_base_and_delayed_collections(self):
        base = ModelInputs.model_validate(DEMO)
        projection = project(base, BASELINE)
        self.assertEqual(projection.profit, 2100000)
        self.assertEqual(projection.rows[-1]['cash'], 4100000)
        self.assertAlmostEqual(float(projection.npv), 295024.04, places=2)
        self.assertAlmostEqual(float(projection.irr_annual), 41.30, places=2)
        self.assertEqual(projection.payback, 10)

        scenario = Scenario(name='Стрессовый', revenue_change_pct=-20, fixed_change_pct=10, payment_delay_months=1)
        stress = project(base, scenario)
        self.assertEqual(stress.first_gap, 1)
        self.assertEqual(stress.funding, 640000)
        self.assertEqual(stress.profit, -780000)
        self.assertEqual(stress.rows[-1]['cash'], 20000)
        self.assertEqual(stress.receivables, 1200000)
        advance = minimum_advance(base, scenario)
        self.assertEqual(str(advance), '53.34')
        self.assertEqual(project(base, scenario.model_copy(update={'advance_pct': advance})).funding, 0)
        self.assertEqual(breakeven(base)[1], 1000000)

    def test_conversation_mode_keeps_inputs_and_saves_selected_model(self):
        with TemporaryDirectory() as temp, patch('agent.store.STATE_DB', Path(temp) / 'state.sqlite3'):
            initialize()
            ident = create_conversation()['id']
            change_mode(ident, ModeIn(mode='modeling'))

            def send(**data):
                return post_message(ident, MessageIn(content=json.dumps(data)))

            initial = {key: value for key, value in DEMO.items() if key != 'discount_annual_pct'}
            incomplete = send(action='base', base={'months': 12, 'currency': 'RUB'})
            self.assertEqual(incomplete['status'], 'clarify')
            ready = send(action='base', base=initial)
            self.assertEqual(ready['status'], 'ok')
            change_mode(ident, ModeIn(mode='analytics'))
            change_mode(ident, ModeIn(mode='modeling'))
            investment = send(action='investment')
            self.assertEqual(investment['status'], 'clarify')
            self.assertIn('ставка дисконтирования', investment['message'])
            updated = send(action='investment', base={'discount_annual_pct': 18})
            self.assertEqual(updated['metrics']['rows'][0]['NPV'], 295024.04)
            stress = send(action='scenario', scenarios=[{'name': 'Стрессовый', 'revenue_change_pct': -20,
                           'fixed_change_pct': 10, 'payment_delay_months': 1, 'advance_pct': None}])
            self.assertEqual(stress['metrics']['rows'][0]['Нужно финансирование'], 640000)
            compared = send(action='compare', scenario_names=['Стрессовый'],
                            scenarios=[{'name': 'Оптимистичный', 'revenue_change_pct': 15}])
            self.assertEqual(len(compared['rows']), 39)
            self.assertEqual(compared['chart']['series'], 'Сценарий')
            report_id = save_report(ident, messages(ident)[-1]['id'])['id']
            saved = report(report_id)
            self.assertTrue(saved['bi_monthly'])
            self.assertNotIn('model_state', saved)
            with BytesIO(export(report_id, 'xlsx').body) as data:
                workbook = load_workbook(data, read_only=True)
                self.assertEqual(workbook.sheetnames, ['Итоги сценариев', 'Отчёт'])
                workbook.close()
            self.assertTrue(export(report_id, 'pdf').body.startswith(b'%PDF'))
            advance = send(action='advance', scenario_names=['Стрессовый'])
            self.assertEqual(advance['status'], 'ok')
            self.assertIn('53,34%', advance['message'])
            self.assertEqual(advance['metrics']['rows'][1]['Нужно финансирование'], 0)


if __name__ == '__main__':
    unittest.main()
