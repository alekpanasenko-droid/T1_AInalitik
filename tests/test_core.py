import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from app import create_conversation, delete_conversation, messages

from agent.store import initialize, profile, state_connection
from agent.sql_tool import execute_readonly, SQLRejected
from agent.agents import DataAnalyst, Orchestrator


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize()

    def test_source_profile(self):
        data = profile()
        self.assertEqual(data['records'], 756)
        self.assertEqual(len(data['columns']), 10)
        self.assertEqual(data['duplicate_keys'], 0)
        self.assertEqual(data['type_errors'], 0)

    def test_reference_total(self):
        result = execute_readonly("SELECT ROUND(SUM(balance_rub), 2) AS total FROM positions WHERE as_of_date='2026-10-31' AND side='ASSET' AND snapshot_kind='MONTH_END'")
        self.assertAlmostEqual(result['rows'][0]['total'], 1022303980.96, places=2)

    def test_sql_readonly(self):
        for sql in ['DELETE FROM positions', 'SELECT * FROM sqlite_master',
                    'WITH x AS (SELECT * FROM sqlite_master) SELECT * FROM x',
                    'SELECT load_extension("x")', 'SELECT * FROM positions; DROP TABLE positions']:
            with self.subTest(sql=sql), self.assertRaises(SQLRejected):
                execute_readonly(sql)

    def test_fx_effect_reference(self):
        question = 'Как изменился курс USD с 12 на 13 октября 2026 года и как это повлияло на пассивы при фиксированном остатке?'
        plan, _ = DataAnalyst().run(question, question, [])
        result = execute_readonly(plan['sql'])
        self.assertAlmostEqual(result['rows'][0]['fx_effect_rub'], 589329.15, places=2)
        self.assertAlmostEqual(result['rows'][0]['volume_effect_rub'], 43996.98, places=2)

    @patch('agent.agents.event')
    def test_forecast_from_monthly_snapshots(self, _event):
        question = ('Возьми все месячные срезы MONTH_END из positions. Спрогнозируй общую сумму активов '
                    'в рублёвом эквиваленте SUM(balance_rub) на ноябрь и декабрь 2026 года. '
                    'Сравни прогноз по последнему известному значению с простым линейным трендом.')
        result = Orchestrator().run(question, [], 'test')
        self.assertEqual(result['status'], 'ok', result['message'])
        self.assertEqual(len(result['rows']), 14)
        self.assertNotIn('2026-10-13', [row['Месяц'] for row in result['rows']])
        self.assertEqual(result['chart']['forecast_from'], '2026-11-30')
        self.assertEqual(result['rows'][-1]['Тип'], 'Прогноз')
        self.assertAlmostEqual(result['rows'][-1]['Сумма, ₽'], 1022303980.96, places=2)
        self.assertAlmostEqual(result['rows'][-1]['Линейный тренд, ₽'], 1047330433.49, places=2)
        self.assertIn('8 790 502,91 ₽', result['message'])

    @patch('agent.agents.event')
    def test_fx_stress_revalues_both_sides(self, _event):
        question = ('Возьми месячный срез на 31 октября 2026 года. Смоделируй рост курса USD на 10% '
                    'при неизменных остатках balance_ccy и неизменных курсах других валют. Покажи, '
                    'как изменятся рублёвые суммы активов и пассивов и разница между ними.')
        result = Orchestrator().run(question, [], 'test')
        self.assertEqual(result['status'], 'ok', result['message'])
        for row, expected in zip(result['rows'], (7550962.73, 7710541.75, -159579.02), strict=True):
            self.assertAlmostEqual(row['Изменение, ₽'], expected, places=2)
            self.assertAlmostEqual(row['После, ₽'] - row['До, ₽'], expected, places=2)
        self.assertIn('97,05 до 106,755', result['message'])

    def test_delete_conversation_keeps_saved_report(self):
        ident = create_conversation()['id']
        try:
            report_id = str(uuid.uuid4())
            with state_connection() as con:
                con.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',
                            (ident, 'user', 'disposable test', '2026-01-01'))
                con.execute('INSERT INTO events(conversation_id,event,detail,created_at) VALUES(?,?,?,?)',
                            (ident, 'test', '{}', '2026-01-01'))
                con.execute('INSERT INTO reports VALUES(?,?,?,?,?)',
                            (report_id, 'Saved test report', ident, '{}', '2026-01-01'))
            response = delete_conversation(ident)
            self.assertTrue(response['saved_reports_preserved'])
            with self.assertRaises(HTTPException):
                messages(ident)
            with state_connection() as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM events WHERE conversation_id=?', (ident,)).fetchone()[0], 0)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM reports WHERE id=?', (report_id,)).fetchone()[0], 1)
        finally:
            with state_connection() as con:
                con.execute('DELETE FROM reports WHERE conversation_id=?', (ident,))


if __name__ == '__main__':
    unittest.main()
