"""Generate local Superset/PostgreSQL credentials without printing secrets."""
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
target = ROOT / '.env.superset'
if not target.exists():
    target.write_text('\n'.join([
        'SUPERSET_SECRET_KEY=' + secrets.token_urlsafe(48),
        'SUPERSET_ADMIN_USERNAME=admin',
        'SUPERSET_ADMIN_PASSWORD=' + secrets.token_urlsafe(20),
        'PORTFOLIO_DB_PASSWORD=' + secrets.token_urlsafe(24),
        'PORTFOLIO_READER_PASSWORD=' + secrets.token_urlsafe(24),
        '',
    ]), encoding='utf-8')
    print('Local Superset credentials created in .env.superset')
else:
    print('Existing .env.superset preserved')
