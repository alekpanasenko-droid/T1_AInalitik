"""Read-only SQL tool. SQLite authorizer is the final enforcement boundary."""
import re
import sqlite3
import time

from .sources import get_source, _connect, query_schema

FORBIDDEN = re.compile(r'(--|/\*|\*/|;|\b(attach|detach|pragma|insert|update|delete|drop|alter|create|replace|vacuum|reindex|analyze|load_extension)\b)', re.I)
ALLOWED_FUNCTIONS = {'sum', 'avg', 'min', 'max', 'count', 'round', 'coalesce', 'nullif', 'abs',
                     'date', 'strftime', 'substr', 'upper', 'lower', 'lag', 'lead', 'row_number',
                     'dense_rank', 'rank', 'total', 'ifnull', 'cast', 'printf'}


class SQLRejected(Exception):
    pass


def execute_readonly(sql, limit=500, source_id='portfolio'):
    sql = sql.strip()
    if sql.endswith(';'):
        sql = sql[:-1].strip()
    if not re.match(r'^(select|with)\b', sql, re.I) or FORBIDDEN.search(sql):
        raise SQLRejected('Разрешён только один запрос SELECT или WITH без изменений данных.')
    if len(sql) > 12000:
        raise SQLRejected('SQL превышает допустимую длину.')
    source = get_source(source_id)
    schema = query_schema(source_id)
    connection = _connect(source)
    deadline = time.monotonic() + 12

    def authorize(action, arg1, arg2, db, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 == schema['table'] else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or arg1 or '').lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    if source['kind'] == 'sqlite':
        connection.set_authorizer(authorize)
        connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    started = time.monotonic()
    try:
        cursor = connection.execute(sql)
        names = [x[0] for x in cursor.description or []]
        raw = cursor.fetchmany(limit + 1)
        rows = [dict(row) if isinstance(row, sqlite3.Row) else dict(zip(names, row)) for row in raw]
        if len(rows) > limit:
            raise SQLRejected(f'Результат больше {limit} строк. Уточните вопрос или добавьте группировку.')
        return {'columns': names, 'rows': rows, 'duration_ms': round((time.monotonic() - started) * 1000, 1)}
    except Exception as exc:
        if isinstance(exc, SQLRejected):
            raise
        raise SQLRejected('SQL отклонён или не выполнен: ' + str(exc)) from exc
    finally:
        connection.close()

