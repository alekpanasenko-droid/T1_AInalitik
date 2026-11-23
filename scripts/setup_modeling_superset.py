"""Provision the report dataset and embedded charts in the local BI stack."""
import json
from urllib.parse import quote

from scripts import setup_superset as bi

from agent.store import RUNTIME
from agent.superset_client import local_credentials, modeling_connection


def main():
    with modeling_connection() as connection:
        connection.execute('''CREATE TABLE IF NOT EXISTS modeling_results (
            report_id UUID NOT NULL, currency TEXT NOT NULL, scenario TEXT NOT NULL, month INTEGER NOT NULL,
            cash DOUBLE PRECISION NOT NULL, profit DOUBLE PRECISION NOT NULL, net_flow DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (report_id, scenario, month))''')
        connection.execute('GRANT SELECT ON modeling_results TO portfolio_reader')
    credentials = local_credentials()
    bi.token = bi.call('POST', '/api/v1/security/login', {
        'username': credentials['SUPERSET_ADMIN_USERNAME'],
        'password': credentials['SUPERSET_ADMIN_PASSWORD'], 'provider': 'db',
    })['access_token']
    bi.csrf = bi.call('GET', '/api/v1/security/csrf_token/')['result']
    password = quote(credentials['PORTFOLIO_READER_PASSWORD'], safe='')
    database = bi.find_or_create('/api/v1/database/', 'database_name', 'Т1 учебный портфель', {
        'database_name': 'Т1 учебный портфель',
        'sqlalchemy_uri': f'postgresql+psycopg2://portfolio_reader:{password}@portfolio-db:5432/portfolio',
        'expose_in_sqllab': True, 'allow_dml': False,
    })
    dataset = bi.find_or_create('/api/v1/dataset/', 'table_name', 'modeling_results', {
        'database': database, 'table_name': 'modeling_results', 'schema': 'public',
    })
    title = 'Т1 · Финансовое моделирование'
    dashboard = bi.find_or_create('/api/v1/dashboard/', 'dashboard_title', title, {
        'dashboard_title': title, 'published': True,
    })
    row = 'ROW-MODEL'
    charts = []
    for name, field in [('Остаток денег', 'cash'), ('Операционная прибыль', 'profit'), ('Денежный поток', 'net_flow')]:
        viz_type = 'echarts_timeseries_line'
        params = {
            'viz_type': viz_type, 'datasource': f'{dataset}__table', 'x_axis': 'month',
            'groupby': ['scenario'], 'time_range': 'No filter', 'row_limit': 1000,
            'x_axis_title': 'Месяц', 'legendType': 'scroll', 'show_legend': True,
            'metrics': [{'expressionType': 'SIMPLE', 'column': {'column_name': field},
                         'aggregate': 'SUM', 'label': name}],
        }
        definition = {'slice_name': f'Модель · {name}', 'viz_type': viz_type, 'datasource_id': dataset,
                      'datasource_type': 'table', 'params': json.dumps(params, ensure_ascii=False),
                      'dashboards': [dashboard]}
        chart = bi.find_or_create('/api/v1/chart/', 'slice_name', definition['slice_name'], definition)
        bi.call('PUT', f'/api/v1/chart/{chart}', definition)
        charts.append((f'CHART-MODEL-{field}', chart, definition['slice_name']))
    layout = {
        'DASHBOARD_VERSION_KEY': 'v2',
        'ROOT_ID': {'id': 'ROOT_ID', 'type': 'ROOT', 'children': ['GRID_ID']},
        'GRID_ID': {'id': 'GRID_ID', 'type': 'GRID', 'children': [row], 'parents': ['ROOT_ID']},
        row: {'id': row, 'type': 'ROW', 'children': [key for key, *_ in charts],
              'parents': ['ROOT_ID', 'GRID_ID'], 'meta': {'background': 'BACKGROUND_TRANSPARENT'}},
    }
    for key, chart, name in charts:
        layout[key] = {'id': key, 'type': 'CHART', 'children': [], 'parents': ['ROOT_ID', 'GRID_ID', row],
                       'meta': {'chartId': chart, 'width': 4, 'height': 40, 'sliceName': name}}
    filters = [{
        'id': 'NATIVE_FILTER-MODEL-SCENARIO', 'name': 'Сценарий', 'filterType': 'filter_select', 'type': 'NATIVE_FILTER',
        'targets': [{'datasetId': dataset, 'column': {'name': 'scenario'}}],
        'defaultDataMask': {'extraFormData': {}, 'filterState': {}, 'ownState': {}},
        'controlValues': {'enableEmptyFilter': False, 'multiSelect': True},
        'cascadeParentIds': [], 'scope': {'rootPath': ['ROOT_ID'], 'excluded': []},
    }]
    bi.call('PUT', f'/api/v1/dashboard/{dashboard}', {
        'dashboard_title': title, 'published': True, 'position_json': json.dumps(layout, ensure_ascii=False),
        'json_metadata': json.dumps({'native_filter_configuration': filters}, ensure_ascii=False),
    })
    embedded = bi.call('POST', f'/api/v1/dashboard/{dashboard}/embedded', {
        'allowed_domains': ['http://127.0.0.1:8000', 'http://127.0.0.1:8001', 'http://127.0.0.1:8765'],
    })['result']
    (RUNTIME / 'modeling_superset.json').write_text(json.dumps({
        'dashboard_id': dashboard, 'dataset_id': dataset, 'embedded_id': embedded['uuid'],
    }), encoding='utf-8')
    print(f'Modeling dashboard ready: {bi.BASE}/superset/dashboard/{dashboard}/')


if __name__ == '__main__':
    main()
