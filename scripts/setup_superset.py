"""Create the T1 sample database, charts and dashboard in local Superset."""
import json
import urllib.request
from urllib.parse import quote
import http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8088'


def local_credentials():
    values = {}
    for line in (ROOT / '.env.superset').read_text(encoding='utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    return values


cookies = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
token = ''
csrf = ''


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json', 'Referer': BASE + '/'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if csrf:
        headers['X-CSRFToken'] = csrf
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{method} {path}: HTTP {exc.code}: {exc.read().decode("utf-8")[:500]}') from exc


def find_or_create(endpoint, key, value, body):
    for item in call('GET', endpoint + '?page_size=100').get('result', []):
        if item.get(key) == value:
            return item['id']
    response = call('POST', endpoint, body)
    return response['id']


def main():
    global token, csrf
    credentials = local_credentials()
    token = call('POST', '/api/v1/security/login', {
        'username': credentials['SUPERSET_ADMIN_USERNAME'],
        'password': credentials['SUPERSET_ADMIN_PASSWORD'], 'provider': 'db', 'refresh': True,
    })['access_token']
    csrf = call('GET', '/api/v1/security/csrf_token/')['result']
    reader_password = quote(credentials['PORTFOLIO_READER_PASSWORD'], safe='')
    database_id = find_or_create('/api/v1/database/', 'database_name', 'Т1 учебный портфель', {
        'database_name': 'Т1 учебный портфель',
        'sqlalchemy_uri': f'postgresql+psycopg2://portfolio_reader:{reader_password}@portfolio-db:5432/portfolio',
        'expose_in_sqllab': True, 'allow_dml': False,
    })
    dataset_id = find_or_create('/api/v1/dataset/', 'table_name', 'positions', {
        'database': database_id, 'table_name': 'positions', 'schema': None,
    })
    dashboard_id = find_or_create('/api/v1/dashboard/', 'dashboard_title', 'Т1 · Банковский портфель', {
        'dashboard_title': 'Т1 · Банковский портфель', 'published': True,
    })
    charts = []
    balance_metric = {'expressionType': 'SIMPLE', 'column': {'column_name': 'balance_rub'},
                      'aggregate': 'SUM', 'label': 'Остаток, ₽'}
    def field_filter(field, value):
        return {'expressionType': 'SIMPLE', 'subject': field, 'operator': '==',
                'comparator': value, 'clause': 'WHERE'}

    definitions = [
        ('Динамика активов и пассивов', 'echarts_timeseries_line', {
            'viz_type': 'echarts_timeseries_line', 'datasource': f'{dataset_id}__table',
            'x_axis': 'as_of_date', 'metrics': [balance_metric],
            'groupby': ['side'], 'time_range': 'No filter', 'granularity_sqla': 'as_of_date',
            'adhoc_filters': [field_filter('snapshot_kind', 'MONTH_END')],
            'row_limit': 100, 'show_legend': True,
        }),
        ('Структура портфеля по продуктам', 'echarts_timeseries_bar', {
            'viz_type': 'echarts_timeseries_bar', 'datasource': f'{dataset_id}__table',
            'x_axis': 'product', 'metrics': [balance_metric],
            'adhoc_filters': [field_filter('as_of_date', '2026-10-31')],
            'row_limit': 30, 'show_legend': False,
        }),
        ('Активы по валютам · 31.10.2026', 'pie', {
            'viz_type': 'pie', 'datasource': f'{dataset_id}__table',
            'groupby': ['currency'], 'metric': balance_metric,
            'adhoc_filters': [field_filter('as_of_date', '2026-10-31'), field_filter('side', 'ASSET')],
            'row_limit': 10, 'show_legend': True, 'show_labels': True,
        }),
        ('Пассивы по продуктам · 31.10.2026', 'echarts_timeseries_bar', {
            'viz_type': 'echarts_timeseries_bar', 'datasource': f'{dataset_id}__table',
            'x_axis': 'product', 'metrics': [balance_metric],
            'adhoc_filters': [field_filter('as_of_date', '2026-10-31'), field_filter('side', 'LIABILITY')],
            'row_limit': 20, 'show_legend': False,
        }),
        ('Активы по срокам · 31.10.2026', 'echarts_timeseries_bar', {
            'viz_type': 'echarts_timeseries_bar', 'datasource': f'{dataset_id}__table',
            'x_axis': 'maturity_bucket', 'metrics': [balance_metric],
            'adhoc_filters': [field_filter('as_of_date', '2026-10-31'), field_filter('side', 'ASSET')],
            'row_limit': 20, 'show_legend': False,
        }),
        ('Динамика активов по валютам', 'echarts_timeseries_line', {
            'viz_type': 'echarts_timeseries_line', 'datasource': f'{dataset_id}__table',
            'x_axis': 'as_of_date', 'metrics': [balance_metric], 'groupby': ['currency'],
            'time_range': 'No filter', 'granularity_sqla': 'as_of_date',
            'adhoc_filters': [field_filter('snapshot_kind', 'MONTH_END'), field_filter('side', 'ASSET')],
            'row_limit': 100, 'show_legend': True,
        }),
    ]
    for title, viz_type, params in definitions:
        chart_body = {
            'slice_name': title, 'viz_type': viz_type, 'datasource_id': dataset_id,
            'datasource_type': 'table', 'params': json.dumps(params, ensure_ascii=False),
            'dashboards': [dashboard_id],
        }
        chart_id = find_or_create('/api/v1/chart/', 'slice_name', title, chart_body)
        call('PUT', f'/api/v1/chart/{chart_id}', chart_body)
        charts.append((chart_id, title))
    chart_keys = [f'CHART-T1-{i}' for i in range(len(charts))]
    row_ids = [f'ROW-T1-{i}' for i in range((len(charts) + 1) // 2)]
    layout = {
        'DASHBOARD_VERSION_KEY': 'v2',
        'ROOT_ID': {'id': 'ROOT_ID', 'type': 'ROOT', 'children': ['GRID_ID']},
        'GRID_ID': {'id': 'GRID_ID', 'type': 'GRID', 'children': row_ids, 'parents': ['ROOT_ID']},
    }
    for row_index, row_id in enumerate(row_ids):
        layout[row_id] = {'id': row_id, 'type': 'ROW', 'children': chart_keys[row_index * 2:row_index * 2 + 2],
                          'parents': ['ROOT_ID', 'GRID_ID'], 'meta': {'background': 'BACKGROUND_TRANSPARENT'}}
    for i, (chart_id, title) in enumerate(charts):
        row_id = row_ids[i // 2]
        layout[chart_keys[i]] = {'id': chart_keys[i], 'type': 'CHART', 'children': [],
                                 'parents': ['ROOT_ID', 'GRID_ID', row_id],
                                 'meta': {'chartId': chart_id, 'width': 6, 'height': 35, 'sliceName': title}}
    native_filters = []
    for field, label in [('side', 'Активы / пассивы'), ('currency', 'Валюта')]:
        native_filters.append({
            'id': 'NATIVE_FILTER-T1-' + field.upper(), 'name': label,
            'filterType': 'filter_select', 'type': 'NATIVE_FILTER',
            'targets': [{'datasetId': dataset_id, 'column': {'name': field}}],
            'defaultDataMask': {'extraFormData': {}, 'filterState': {}, 'ownState': {}},
            'controlValues': {'enableEmptyFilter': False, 'multiSelect': True},
            'cascadeParentIds': [], 'scope': {'rootPath': ['ROOT_ID'], 'excluded': []},
        })
    call('PUT', f'/api/v1/dashboard/{dashboard_id}', {
        'dashboard_title': 'Т1 · Банковский портфель', 'published': True,
        'position_json': json.dumps(layout, ensure_ascii=False),
        'json_metadata': json.dumps({'native_filter_configuration': native_filters}, ensure_ascii=False),
    })
    try:
        embedded = call('GET', f'/api/v1/dashboard/{dashboard_id}/embedded').get('result') or {}
    except RuntimeError:
        embedded = {}
    if not embedded.get('uuid'):
        embedded = call('POST', f'/api/v1/dashboard/{dashboard_id}/embedded', {
            'allowed_domains': ['http://127.0.0.1:8765', 'http://127.0.0.1:8000']
        }).get('result') or {}
    embedded_id = embedded.get('uuid')
    if not embedded_id:
        raise RuntimeError('Superset did not return embedded dashboard UUID')
    config = ROOT / '.runtime' / 'superset.json'
    config.write_text(json.dumps({'dashboard_id': dashboard_id, 'embedded_id': embedded_id}, ensure_ascii=False), encoding='utf-8')
    print(f'Superset dashboard ready: {BASE}/superset/dashboard/{dashboard_id}/')


if __name__ == '__main__':
    main()
