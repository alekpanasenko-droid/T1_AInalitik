import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / '.runtime'
SOURCE = ROOT / 'data' / 'bank_portfolio.xlsx'
DATA_DB = RUNTIME / 'portfolio.sqlite3'
STATE_DB = RUNTIME / 'state.sqlite3'
FIELDS = ['as_of_date', 'snapshot_kind', 'side', 'product', 'currency', 'maturity_bucket', 'balance_ccy', 'fx_rub_per_unit', 'balance_rub', 'rate_annual']
DESCRIPTIONS = {
    'as_of_date': ('Дата среза', 'дата', ''),
    'snapshot_kind': ('Тип среза: MONTH_END или DAILY', 'текст', ''),
    'side': ('ASSET — активы, LIABILITY — пассивы', 'текст', ''),
    'product': ('Банковский продукт', 'текст', ''),
    'currency': ('Код валюты позиции', 'текст', ''),
    'maturity_bucket': ('Оставшийся срок до погашения', 'текст', ''),
    'balance_ccy': ('Остаток в исходной валюте', 'единиц валюты', 'SUM(balance_ccy) только в одной валюте'),
    'fx_rub_per_unit': ('Синтетический курс рубля за единицу валюты', '₽ / единицу валюты', ''),
    'balance_rub': ('Рублёвый эквивалент остатка', '₽', 'ROUND(balance_ccy * fx_rub_per_unit, 2); агрегат SUM(balance_rub)'),
    'rate_annual': ('Годовая ставка в долях единицы', 'доля', 'SUM(rate_annual * balance_rub) / SUM(balance_rub)'),
}


def state_connection():
    RUNTIME.mkdir(exist_ok=True)
    con = sqlite3.connect(STATE_DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def initialize():
    RUNTIME.mkdir(exist_ok=True)
    with state_connection() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id));
        CREATE TABLE IF NOT EXISTS dictionary(field TEXT PRIMARY KEY, description TEXT NOT NULL, unit TEXT NOT NULL, formula TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, title TEXT NOT NULL, conversation_id TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT,
            event TEXT NOT NULL, detail TEXT NOT NULL, duration_ms REAL, llm_tokens INTEGER, created_at TEXT NOT NULL);
        ''')
        for field, (description, unit, formula) in DESCRIPTIONS.items():
            con.execute('INSERT OR IGNORE INTO dictionary VALUES(?,?,?,?)', (field, description, unit, formula))
    if not DATA_DB.exists() or DATA_DB.stat().st_mtime < SOURCE.stat().st_mtime:
        import_source()


def import_source():
    if not SOURCE.exists():
        raise FileNotFoundError(f'Missing source: {SOURCE}')
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    sheet = workbook['positions']
    rows = sheet.values
    headers = next(rows)
    if list(headers) != FIELDS:
        raise ValueError('Unexpected XLSX schema')
    temp = DATA_DB.with_suffix('.tmp')
    if temp.exists():
        temp.unlink()
    con = sqlite3.connect(temp)
    try:
        con.execute('''CREATE TABLE positions (
            as_of_date TEXT NOT NULL, snapshot_kind TEXT NOT NULL, side TEXT NOT NULL,
            product TEXT NOT NULL, currency TEXT NOT NULL, maturity_bucket TEXT NOT NULL,
            balance_ccy REAL NOT NULL, fx_rub_per_unit REAL NOT NULL,
            balance_rub REAL NOT NULL, rate_annual REAL NOT NULL)''')
        for row in rows:
            if len(row) != len(FIELDS):
                raise ValueError('Invalid row width')
            values = list(row)
            values[0] = values[0].date().isoformat() if isinstance(values[0], datetime) else str(values[0])
            con.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?)', values)
        con.execute('CREATE INDEX positions_date_side ON positions(as_of_date, side, snapshot_kind)')
        con.commit()
    finally:
        con.close()
        workbook.close()
    os.replace(temp, DATA_DB)


def dictionary():
    with state_connection() as con:
        return [dict(r) for r in con.execute('SELECT * FROM dictionary ORDER BY field')]


def profile():
    con = sqlite3.connect(f'file:{DATA_DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        count = con.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
        columns = [dict(name=r[1], type=r[2]) for r in con.execute('PRAGMA table_info(positions)')]
        missing = {f: con.execute(f'SELECT COUNT(*) FROM positions WHERE "{f}" IS NULL OR CAST("{f}" AS TEXT) = ""').fetchone()[0] for f in FIELDS}
        duplicate_rows = con.execute('SELECT COUNT(*) - COUNT(DISTINCT as_of_date||"|"||side||"|"||product||"|"||currency||"|"||maturity_bucket) FROM positions').fetchone()[0]
        type_errors = con.execute('''SELECT COUNT(*) FROM positions WHERE
            date(as_of_date) IS NULL OR snapshot_kind NOT IN ('MONTH_END','DAILY')
            OR side NOT IN ('ASSET','LIABILITY') OR currency NOT IN ('RUB','USD','EUR')
            OR typeof(balance_ccy) NOT IN ('integer','real')
            OR typeof(fx_rub_per_unit) NOT IN ('integer','real')
            OR typeof(balance_rub) NOT IN ('integer','real')
            OR typeof(rate_annual) NOT IN ('integer','real')''').fetchone()[0]
        damaged = con.execute("SELECT COUNT(*) FROM positions WHERE product LIKE '%�%' OR maturity_bucket LIKE '%�%'").fetchone()[0]
        sample = [dict(r) for r in con.execute('SELECT * FROM positions LIMIT 5')]
        dates = con.execute('SELECT MIN(as_of_date), MAX(as_of_date), COUNT(DISTINCT as_of_date) FROM positions').fetchone()
        return dict(source='data/bank_portfolio.xlsx → .runtime/portfolio.sqlite3', table='positions', records=count,
                    columns=columns, missing=missing, duplicate_keys=duplicate_rows, type_errors=type_errors,
                    damaged_labels=damaged, date_range=[dates[0], dates[1]], distinct_dates=dates[2], sample=sample)
    finally:
        con.close()


def event(conversation_id, event_name, detail, duration_ms=None, llm_tokens=None):
    with state_connection() as con:
        con.execute('INSERT INTO events(conversation_id,event,detail,duration_ms,llm_tokens,created_at) VALUES(?,?,?,?,?,?)',
                    (conversation_id, event_name, json.dumps(detail, ensure_ascii=False), duration_ms, llm_tokens, datetime.now().isoformat()))
