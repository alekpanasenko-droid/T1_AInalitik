"""Copy the bundled SQLite dataset to the local PostgreSQL BI database."""
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent


def credentials():
    return dict(line.split('=', 1) for line in (ROOT / '.env.superset').read_text(encoding='utf-8').splitlines() if '=' in line)


def main():
    values = credentials()
    with psycopg.connect(host='127.0.0.1', port=5433, dbname='portfolio', user='portfolio_admin',
                         password=values['PORTFOLIO_DB_PASSWORD'], connect_timeout=10) as target:
        target.execute('''CREATE TABLE IF NOT EXISTS positions (
            as_of_date DATE NOT NULL, snapshot_kind TEXT NOT NULL, side TEXT NOT NULL,
            product TEXT NOT NULL, currency TEXT NOT NULL, maturity_bucket TEXT NOT NULL,
            balance_ccy DOUBLE PRECISION NOT NULL, fx_rub_per_unit DOUBLE PRECISION NOT NULL,
            balance_rub DOUBLE PRECISION NOT NULL, rate_annual DOUBLE PRECISION NOT NULL)''')
        target.execute('TRUNCATE positions')
        with sqlite3.connect(ROOT / '.runtime' / 'portfolio.sqlite3') as source:
            rows = source.execute('SELECT * FROM positions').fetchall()
        with target.cursor() as cursor:
            cursor.executemany('INSERT INTO positions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', rows)
        target.execute('CREATE INDEX IF NOT EXISTS positions_date_side ON positions(as_of_date, side, snapshot_kind)')
        target.execute('''DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='portfolio_reader') THEN
            CREATE ROLE portfolio_reader LOGIN;
          END IF;
        END $$''')
        target.execute(sql.SQL('ALTER ROLE portfolio_reader PASSWORD {}').format(
            sql.Literal(values['PORTFOLIO_READER_PASSWORD'])))
        target.execute('GRANT CONNECT ON DATABASE portfolio TO portfolio_reader')
        target.execute('GRANT USAGE ON SCHEMA public TO portfolio_reader')
        target.execute('GRANT SELECT ON positions TO portfolio_reader')
        print(f'Copied {len(rows)} portfolio rows to PostgreSQL')


if __name__ == '__main__':
    main()
