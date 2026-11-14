"""Read-only SQL tool. SQLite authorizer is the final enforcement boundary."""
import re
import sqlite3
import time

from .store import DATA_DB

FORBIDDEN = re.compile(r'(--|/\*|\*/|;|\b(attach|detach|pragma|insert|update|delete|drop|alter|create|replace|vacuum|reindex|analyze|load_extension)\b)', re.I)
ALLOWED_FUNCTIONS = {'sum', 'avg', 'min', 'max', 'count', 'round', 'coalesce', 'nullif', 'abs',
                     'date', 'strftime', 'substr', 'upper', 'lower', 'lag', 'lead', 'row_number',
                     'dense_rank', 'rank', 'total', 'ifnull', 'cast', 'printf'}


class SQLRejected(Exception):
    pass


def execute_readonly(sql, limit=500):
    sql = sql.strip()
    if sql.endswith(';'):
        sql = sql[:-1].strip()
    if not re.match(r'^(select|with)\b', sql, re.I) or FORBIDDEN.search(sql):
        raise SQLRejected('Разрешён только один запрос SELECT или WITH без изменений данных.')
    if len(sql) > 12000:
        raise SQLRejected('SQL превышает допустимую длину.')
    connection = sqlite3.connect(f'file:{DATA_DB}?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic() + 12

    def authorize(action, arg1, arg2, db, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 == 'positions' else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or arg1 or '').lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    connection.set_authorizer(authorize)
    connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    started = time.monotonic()
    try:
        cursor = connection.execute(sql)
        names = [x[0] for x in cursor.description or []]
        rows = [dict(row) for row in cursor.fetchmany(limit + 1)]
        if len(rows) > limit:
            raise SQLRejected(f'Результат больше {limit} строк. Уточните вопрос или добавьте группировку.')
        return {'columns': names, 'rows': rows, 'duration_ms': round((time.monotonic() - started) * 1000, 1)}
    except sqlite3.Error as exc:
        raise SQLRejected('SQL отклонён или не выполнен: ' + str(exc)) from exc
    finally:
        connection.close()

