"""Registered data sources and bounded, read-only dashboard queries."""
import csv
import io
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from .store import RUNTIME, state_connection

IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
MAX_UPLOAD = 30 * 1024 * 1024


class SourceError(ValueError):
    pass


def registered():
    with state_connection() as con:
        return [{k: row[k] for k in ('id', 'name', 'kind', 'table_name', 'created_at')}
                for row in con.execute('SELECT * FROM sources ORDER BY created_at, name')]


def get_source(source_id):
    with state_connection() as con:
        row = con.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
    if not row:
        raise SourceError('Источник данных не найден.')
    return dict(row)


def _connect(source):
    if source['kind'] == 'postgresql':
        try:
            import psycopg
        except ImportError as exc:
            raise SourceError('Для PostgreSQL установите зависимости из requirements.txt.') from exc
        try:
            con = psycopg.connect(source['location'], connect_timeout=5, options='-c default_transaction_read_only=on -c statement_timeout=12000 -c search_path=public')
            con.autocommit = True
            return con
        except Exception as exc:
            raise SourceError('Не удалось подключиться к PostgreSQL. Проверьте адрес, учётные данные и права доступа.') from exc
    path = Path(source['location']).resolve()
    if not path.is_file():
        raise SourceError('Файл базы данных не найден.')
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


@contextmanager
def managed(source):
    con = _connect(source)
    try:
        yield con
    finally:
        con.close()


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _tables(con, kind):
    if kind == 'postgresql':
        rows = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()
    else:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [row[0] for row in rows]


def tables(source_id):
    source = get_source(source_id)
    with managed(source) as con:
        return _tables(con, source['kind'])


def set_table(source_id, table):
    if table not in tables(source_id):
        raise SourceError('Таблица не найдена в выбранном источнике.')
    with state_connection() as con:
        con.execute('UPDATE sources SET table_name=? WHERE id=?', (table, source_id))
    return get_source(source_id)


def _columns(con, kind, table):
    if kind == 'postgresql':
        rows = con.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall()
        return [{'name': r[0], 'type': r[1]} for r in rows]
    return [{'name': r[1], 'type': r[2]} for r in con.execute('PRAGMA table_info(' + _quoted(table) + ')')]


def inspect(source_id):
    source = get_source(source_id)
    with managed(source) as con:
        available = _tables(con, source['kind'])
        table = source['table_name']
        if table not in available:
            raise SourceError('Сохранённая таблица отсутствует в источнике.')
        columns = _columns(con, source['kind'], table)
        if not columns:
            raise SourceError('В таблице нет полей.')
        count = con.execute('SELECT COUNT(*) FROM ' + _quoted(table)).fetchone()[0]
        sample = con.execute('SELECT * FROM ' + _quoted(table) + ' LIMIT 5').fetchall()
        sample = [dict(row) if isinstance(row, sqlite3.Row) else dict(zip([c['name'] for c in columns], row)) for row in sample]
        missing = {}
        for col in columns:
            ident = _quoted(col['name'])
            missing[col['name']] = con.execute('SELECT COUNT(*) FROM ' + _quoted(table) + ' WHERE ' + ident + ' IS NULL').fetchone()[0]
        duplicate_rows = None
        if source['kind'] == 'sqlite':
            group = ','.join(_quoted(c['name']) for c in columns)
            duplicate_rows = con.execute('SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) AS n FROM '
                                         + _quoted(table) + ' GROUP BY ' + group + ' HAVING COUNT(*)>1)').fetchone()[0]
    return {'id': source_id, 'name': source['name'], 'kind': source['kind'], 'table': table,
            'tables': available, 'records': count, 'columns': columns, 'missing': missing,
            'duplicate_rows': duplicate_rows, 'sample': sample}


def query_schema(source_id):
    source = get_source(source_id)
    with managed(source) as con:
        table = source['table_name']
        if table not in _tables(con, source['kind']):
            raise SourceError('Сохранённая таблица отсутствует в источнике.')
        return {'table': table, 'columns': _columns(con, source['kind'], table)}


def _register(name, kind, location, table):
    ident = str(uuid.uuid4())
    with state_connection() as con:
        con.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)',
                    (ident, name[:100], kind, str(location), table, datetime.now().isoformat()))
    return get_source(ident)


def add_postgres(name, dsn):
    if not dsn.startswith(('postgresql://', 'postgres://')):
        raise SourceError('Укажите URL PostgreSQL, начинающийся с postgresql://.')
    candidate = {'kind': 'postgresql', 'location': dsn}
    with managed(candidate) as con:
        found = _tables(con, 'postgresql')
    if not found:
        raise SourceError('В схеме public нет доступных таблиц.')
    return _register(name or 'PostgreSQL', 'postgresql', dsn, found[0])


def add_upload(name, filename, contents):
    if not contents or len(contents) > MAX_UPLOAD:
        raise SourceError('Размер файла должен быть от 1 байта до 30 МБ.')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.sqlite', '.sqlite3', '.db', '.csv', '.xlsx'}:
        raise SourceError('Поддерживаются SQLite (.db/.sqlite), CSV и XLSX.')
    folder = RUNTIME / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (str(uuid.uuid4()) + ('.sqlite3' if suffix in {'.csv', '.xlsx'} else suffix))
    if suffix in {'.db', '.sqlite', '.sqlite3'}:
        target.write_bytes(contents)
        kind = 'sqlite'
    else:
        if suffix == '.csv':
            try:
                text = contents.decode('utf-8-sig')
            except UnicodeDecodeError as exc:
                raise SourceError('CSV должен быть в кодировке UTF-8.') from exc
            reader = csv.reader(io.StringIO(text))
            headers = next(reader, [])
            rows = reader
        else:
            try:
                workbook = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
            except Exception as exc:
                raise SourceError('Не удалось прочитать XLSX-файл.') from exc
            rows = workbook.active.values
            headers = next(rows, [])
        headers = [str(h or '').strip() for h in headers]
        if not headers or any(not h for h in headers) or len(set(headers)) != len(headers):
            raise SourceError('Заголовки столбцов должны быть заполнены и уникальны.')
        if len(headers) > 100:
            raise SourceError('Слишком много столбцов: максимум 100.')
        con = sqlite3.connect(target)
        try:
            con.execute('CREATE TABLE data (' + ','.join(_quoted(h) + ' TEXT' for h in headers) + ')')
            placeholders = ','.join('?' for _ in headers)
            for index, row in enumerate(rows):
                if index >= 200000:
                    raise SourceError('Максимум 200 000 строк.')
                if len(row) != len(headers):
                    raise SourceError(f'Строка {index + 2} содержит неверное число полей.')
                con.execute('INSERT INTO data VALUES (' + placeholders + ')',
                            [None if v is None or v == '' else v.date().isoformat() if isinstance(v, datetime)
                             else v.isoformat() if isinstance(v, date) else str(v) for v in row])
            con.commit()
        except Exception:
            con.close()
            target.unlink(missing_ok=True)
            raise
        finally:
            if suffix == '.xlsx':
                workbook.close()
        con.close()
        kind = 'sqlite'
    try:
        with managed({'kind': kind, 'location': str(target)}) as con:
            found = _tables(con, kind)
        if not found:
            raise SourceError('В базе нет таблиц.')
        return _register(name or Path(filename).stem, kind, target, found[0])
    except sqlite3.DatabaseError as exc:
        target.unlink(missing_ok=True)
        raise SourceError('Файл SQLite повреждён или имеет неподдерживаемый формат.') from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise


def dashboard_query(source_id, metric, aggregation, dimension=None, filters=None,
                    date_field=None, date_from=None, date_to=None, limit=30):
    schema = query_schema(source_id)
    source = get_source(source_id)
    names = {c['name'] for c in schema['columns']}
    if metric != '*' and metric not in names:
        raise SourceError('Поле показателя не найдено.')
    if dimension and dimension not in names:
        raise SourceError('Поле разреза не найдено.')
    if aggregation not in {'count', 'sum', 'avg', 'min', 'max'}:
        raise SourceError('Неизвестная агрегация.')
    if aggregation != 'count' and metric == '*':
        raise SourceError('Выберите числовой показатель.')
    if aggregation != 'count':
        metric_type = next(c['type'].lower() for c in schema['columns'] if c['name'] == metric)
        if source_id != 'portfolio' and not any(word in metric_type for word in ('int', 'real', 'float', 'double', 'numeric', 'decimal', 'number')):
            if source['kind'] == 'postgresql':
                raise SourceError('Для этой операции выберите числовое поле.')
    filters = filters or {}
    if len(filters) > 8 or any(key not in names for key in filters):
        raise SourceError('Фильтр содержит неизвестное поле.')
    if (date_from or date_to) and (not date_field or date_field not in names):
        raise SourceError('Выберите поле даты для фильтра периода.')
    for value in (date_from, date_to):
        if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise SourceError('Дата должна иметь формат ГГГГ-ММ-ДД.')
    if date_from and date_to and date_from > date_to:
        raise SourceError('Начало периода позже конца.')
    marker = '%s' if source['kind'] == 'postgresql' else '?'
    where = []
    params = []
    for field, value in filters.items():
        if value is None or value == '':
            continue
        where.append(_quoted(field) + '=' + marker)
        params.append(value)
    if date_from:
        where.append(_quoted(date_field) + '>=' + marker)
        params.append(date_from)
    if date_to:
        where.append(_quoted(date_field) + '<=' + marker)
        params.append(date_to)
    target = '*' if metric == '*' else _quoted(metric)
    if source['kind'] == 'sqlite' and aggregation in {'sum', 'avg'}:
        target = 'CAST(' + target + ' AS REAL)'
    agg = 'COUNT(*)' if aggregation == 'count' and metric == '*' else aggregation.upper() + '(' + target + ')'
    select = (_quoted(dimension) + ' AS label, ' if dimension else '') + agg + ' AS value'
    sql = 'SELECT ' + select + ' FROM ' + _quoted(schema['table'])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    if dimension:
        sql += ' GROUP BY ' + _quoted(dimension) + ' ORDER BY value DESC LIMIT ' + str(min(max(int(limit), 1), 50))
    with managed(source) as con:
        if aggregation in {'sum', 'avg'} and source['kind'] == 'sqlite':
            check_sql = 'SELECT ' + _quoted(metric) + ' FROM ' + _quoted(schema['table'])
            for row in con.execute(check_sql):
                if row[0] is not None and str(row[0]).strip():
                    try:
                        float(row[0])
                    except (ValueError, TypeError) as exc:
                        raise SourceError('Поле показателя содержит текст; для суммы и среднего нужно числовое поле.') from exc
        rows = con.execute(sql, params).fetchall()
    values = [{'label': str(row[0]) if dimension else 'Всего', 'value': float(row[1] if dimension else row[0]) if (row[1] if dimension else row[0]) is not None else None}
              for row in rows]
    return {'source_id': source_id, 'table': schema['table'], 'metric': metric,
            'aggregation': aggregation, 'dimension': dimension, 'filters': filters,
            'date_field': date_field, 'date_from': date_from, 'date_to': date_to, 'rows': values}


def filter_values(source_id, field):
    schema = query_schema(source_id)
    if field not in {c['name'] for c in schema['columns']}:
        raise SourceError('Поле фильтра не найдено.')
    source = get_source(source_id)
    sql = 'SELECT ' + _quoted(field) + ', COUNT(*) AS n FROM ' + _quoted(schema['table'])
    sql += ' WHERE ' + _quoted(field) + ' IS NOT NULL GROUP BY ' + _quoted(field) + ' ORDER BY n DESC LIMIT 100'
    with managed(source) as con:
        return [str(row[0]) for row in con.execute(sql).fetchall()]
