import sqlite3
import io
import unittest
from pathlib import Path

from agent.sources import add_upload, dashboard_query, get_source, inspect, set_table, SourceError
from agent.sql_tool import execute_readonly, SQLRejected
from agent.store import initialize, state_connection
from openpyxl import Workbook


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize()

    def setUp(self):
        self.ids = []

    def tearDown(self):
        for ident in self.ids:
            source = get_source(ident)
            with state_connection() as con:
                con.execute('DELETE FROM sources WHERE id=?', (ident,))
            Path(source['location']).unlink(missing_ok=True)

    def test_two_independent_sources_and_filters(self):
        first = add_upload('Первый', 'first.csv', b'category,amount\nA,10\nB,20\nA,5\n')
        second = add_upload('Второй', 'second.csv', b'category,amount\nA,100\n')
        self.ids.extend([first['id'], second['id']])
        self.assertEqual(inspect(first['id'])['records'], 3)
        self.assertEqual(inspect(second['id'])['records'], 1)
        result = dashboard_query(first['id'], 'amount', 'sum', 'category', {'category': 'A'})
        self.assertEqual(result['rows'], [{'label': 'A', 'value': 15.0}])
        self.assertEqual(dashboard_query(second['id'], 'amount', 'sum')['rows'][0]['value'], 100.0)
        self.assertEqual(execute_readonly('SELECT SUM(CAST(amount AS REAL)) AS total FROM data', source_id=first['id'])['rows'][0]['total'], 35)

    def test_sqlite_tables_and_readonly_boundary(self):
        source = add_upload('SQLite', 'other.db', self._sqlite_file())
        self.ids.append(source['id'])
        self.assertEqual(inspect(source['id'])['records'], 2)
        set_table(source['id'], 'secondary')
        self.assertEqual(inspect(source['id'])['records'], 1)
        with self.assertRaises(SQLRejected):
            execute_readonly('SELECT * FROM primary_data', source_id=source['id'])
        with self.assertRaises(SQLRejected):
            execute_readonly('DELETE FROM secondary', source_id=source['id'])

    def test_reject_text_metric(self):
        source = add_upload('Текст', 'text.csv', b'category,amount\nA,unknown\n')
        self.ids.append(source['id'])
        with self.assertRaises(SourceError):
            dashboard_query(source['id'], 'amount', 'sum')

    def test_xlsx_import_and_date_filter(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['day', 'amount'])
        sheet.append(['2026-01-01', 12])
        sheet.append(['2026-02-01', 24])
        stream = io.BytesIO()
        workbook.save(stream)
        source = add_upload('XLSX', 'data.xlsx', stream.getvalue())
        self.ids.append(source['id'])
        result = dashboard_query(source['id'], 'amount', 'sum', date_field='day',
                                 date_from='2026-02-01', date_to='2026-02-28')
        self.assertEqual(result['rows'][0]['value'], 24)

    def _sqlite_file(self):
        from agent.store import RUNTIME
        path = RUNTIME / 'source-fixture.db'
        con = sqlite3.connect(path)
        try:
            con.execute('CREATE TABLE IF NOT EXISTS primary_data(value INTEGER)')
            con.execute('CREATE TABLE IF NOT EXISTS secondary(value INTEGER)')
            con.execute('DELETE FROM primary_data')
            con.execute('DELETE FROM secondary')
            con.executemany('INSERT INTO primary_data VALUES(?)', [(1,), (2,)])
            con.execute('INSERT INTO secondary VALUES(3)')
            con.commit()
        finally:
            con.close()
        contents = path.read_bytes()
        path.unlink()
        return contents


if __name__ == '__main__':
    unittest.main()
