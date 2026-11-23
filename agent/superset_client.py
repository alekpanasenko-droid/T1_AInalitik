"""Issue short-lived local Superset guest tokens for the embedded dashboard."""
import http.cookiejar
import json
import urllib.request
from uuid import UUID

import psycopg

from .store import ROOT, RUNTIME

BASE = 'http://127.0.0.1:8088'


def local_credentials():
    return dict(line.split('=', 1) for line in (ROOT / '.env.superset').read_text(encoding='utf-8').splitlines()
                if '=' in line and not line.startswith('#'))


def guest_token(config=None, rls=()):
    config = config if config is not None else json.loads((RUNTIME / 'superset.json').read_text(encoding='utf-8'))
    values = local_credentials()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None, token=None, csrf=None):
        headers = {'Content-Type': 'application/json', 'Referer': BASE + '/'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if csrf:
            headers['X-CSRFToken'] = csrf
        request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers, method='POST' if body is not None else 'GET')
        with opener.open(request, timeout=12) as response:
            return json.load(response)

    access = call('/api/v1/security/login', {
        'username': values['SUPERSET_ADMIN_USERNAME'], 'password': values['SUPERSET_ADMIN_PASSWORD'],
        'provider': 'db', 'refresh': True,
    })['access_token']
    csrf = call('/api/v1/security/csrf_token/', token=access)['result']
    result = call('/api/v1/security/guest_token/', {
        'user': {'username': 't1-viewer', 'first_name': 'T1', 'last_name': 'Viewer'},
        'resources': [{'type': 'dashboard', 'id': config['embedded_id']}], 'rls': list(rls),
    }, token=access, csrf=csrf)
    return result['token']


def modeling_config():
    return json.loads((RUNTIME / 'modeling_superset.json').read_text(encoding='utf-8'))


def modeling_connection():
    return psycopg.connect(host='127.0.0.1', port=5433, dbname='portfolio', user='portfolio_admin',
                           password=local_credentials()['PORTFOLIO_DB_PASSWORD'], connect_timeout=5,
                           options='-c statement_timeout=5000')


def publish_model_report(report_id, payload):
    from .project_dialog import MONTHLY, SCENARIO

    config = modeling_config()
    report_id = UUID(report_id)
    rows = [(report_id, payload['unit'], row[SCENARIO], row[MONTHLY['month']], row[MONTHLY['cash']],
             row[MONTHLY['profit']], row[MONTHLY['flow']]) for row in payload['rows']]
    with modeling_connection() as connection, connection.cursor() as cursor:
        cursor.executemany('''INSERT INTO modeling_results
            (report_id,currency,scenario,month,cash,profit,net_flow) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (report_id,scenario,month) DO NOTHING''', rows)
    return {'url': BASE, 'embedded_id': config['embedded_id']}


def model_guest_token(report_id):
    config = modeling_config()
    rule = {'dataset': config['dataset_id'], 'clause': "report_id = '" + str(UUID(report_id)) + "'"}
    return guest_token(config, [rule])
