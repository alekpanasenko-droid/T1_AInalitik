"""Expose the PostgreSQL BI copy as a selectable source in the T1 chat."""
from pathlib import Path
from urllib.parse import quote

from agent.sources import add_postgres, registered
from agent.store import initialize

ROOT = Path(__file__).resolve().parent.parent


def main():
    initialize()
    name = 'Портфель · PostgreSQL / Superset'
    if any(source['name'] == name for source in registered()):
        print('PostgreSQL source already registered')
        return
    values = dict(line.split('=', 1) for line in (ROOT / '.env.superset').read_text(encoding='utf-8').splitlines() if '=' in line)
    password = quote(values['PORTFOLIO_READER_PASSWORD'], safe='')
    source = add_postgres(name, f'postgresql://portfolio_reader:{password}@127.0.0.1:5433/portfolio')
    print('Registered source:', source['name'])


if __name__ == '__main__':
    main()
