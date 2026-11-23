import csv
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from psycopg import Error as PostgresError

from agent.agents import Orchestrator
from agent.llm import configuration
from agent.store import ROOT, dictionary, initialize, profile, state_connection
from agent.store import RUNTIME
from agent.sources import (SourceError, registered, inspect, tables, set_table,
                           add_upload, add_postgres, dashboard_query, filter_values, MAX_UPLOAD)
from agent.superset_client import guest_token, model_guest_token, publish_model_report

app = FastAPI(title='Т1 Дата-агент', version='0.1.0')


@app.on_event('startup')
def startup():
    initialize()


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ModeIn(BaseModel):
    mode: Literal['analytics', 'modeling']


class CardIn(BaseModel):
    description: str = Field(max_length=1000)
    unit: str = Field(max_length=200)
    formula: str = Field(max_length=1000)


class PostgresIn(BaseModel):
    name: str = Field(default='PostgreSQL', max_length=100)
    dsn: str = Field(min_length=15, max_length=2000)


class TableIn(BaseModel):
    table: str


class DashboardIn(BaseModel):
    source_id: str
    metric: str
    aggregation: str = 'sum'
    dimension: str | None = None
    filters: dict[str, str | int | float | None] = Field(default_factory=dict)
    date_field: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int = Field(default=30, ge=1, le=50)


class SavedDashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: DashboardIn
    chart_type: str = Field(default='bar', pattern='^(bar|line|donut)$')


@app.get('/api/superset')
def superset_status():
    config_file = RUNTIME / 'superset.json'
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    return {'url': 'http://127.0.0.1:8088', 'dashboard_id': config.get('dashboard_id'),
            'embedded_id': config.get('embedded_id'), 'configured': bool(config.get('embedded_id'))}


@app.get('/api/superset/guest-token')
def superset_guest_token():
    try:
        return {'token': guest_token()}
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(503, 'Не удалось получить токен дашборда Superset.') from exc


@app.get('/api/sources')
def sources():
    return registered()


@app.get('/api/sources/{source_id}')
def source_detail(source_id: str):
    try:
        return inspect(source_id)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put('/api/sources/{source_id}/table')
def choose_table(source_id: str, item: TableIn):
    try:
        return {k: v for k, v in set_table(source_id, item.table).items() if k != 'location'}
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/sources/upload')
async def upload_source(request: Request, filename: str, name: str = ''):
    if int(request.headers.get('content-length', '0') or 0) > MAX_UPLOAD:
        raise HTTPException(413, 'Файл превышает 30 МБ.')
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD:
            raise HTTPException(413, 'Файл превышает 30 МБ.')
        chunks.append(chunk)
    try:
        source = add_upload(name, filename, b''.join(chunks))
        return {k: v for k, v in source.items() if k != 'location'}
    except (SourceError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/sources/postgresql')
def connect_postgresql(item: PostgresIn):
    try:
        source = add_postgres(item.name, item.dsn)
        return {k: v for k, v in source.items() if k != 'location'}
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/dashboard/query')
def dashboard(item: DashboardIn):
    try:
        return dashboard_query(**item.model_dump())
    except (SourceError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/dashboard/values')
def dashboard_values(source_id: str, field: str):
    try:
        return filter_values(source_id, field)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/dashboards')
def list_dashboards():
    with state_connection() as con:
        return [dict(row) for row in con.execute('SELECT id,name,source_id,created_at FROM dashboards ORDER BY created_at DESC')]


@app.post('/api/dashboards')
def save_dashboard(item: SavedDashboardIn):
    dashboard(item.config)
    ident = str(uuid.uuid4())
    with state_connection() as con:
        con.execute('INSERT INTO dashboards VALUES(?,?,?,?,?)',
                    (ident, item.name, item.config.source_id, item.model_dump_json(), datetime.now().isoformat()))
    return {'id': ident, 'name': item.name}


@app.get('/api/dashboards/{ident}')
def saved_dashboard(ident: str):
    with state_connection() as con:
        row = con.execute('SELECT config FROM dashboards WHERE id=?', (ident,)).fetchone()
    if not row:
        raise HTTPException(404, 'Дашборд не найден.')
    return json.loads(row['config'])


@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/static/{name}')
def static(name: str):
    if name not in {'app.css', 'app.js', 'data.js', 'data.css'}:
        raise HTTPException(404)
    return FileResponse(ROOT / 'static' / name)


@app.get('/api/health')
def health():
    cfg = configuration()
    return {'status': 'ok', 'llm_available': cfg['available'], 'provider': cfg['provider'], 'model': cfg['model']}


@app.get('/api/source')
def source():
    return profile()


@app.get('/api/dictionary')
def get_dictionary():
    return dictionary()


@app.put('/api/dictionary/{field}')
def set_dictionary(field: str, card: CardIn):
    with state_connection() as con:
        row = con.execute('SELECT field FROM dictionary WHERE field=?', (field,)).fetchone()
        if not row:
            raise HTTPException(404, 'Unknown field')
        con.execute('UPDATE dictionary SET description=?,unit=?,formula=? WHERE field=?',
                    (card.description, card.unit, card.formula, field))
    return {'field': field, **card.model_dump()}


@app.get('/api/conversations')
def conversations():
    with state_connection() as con:
        return [dict(r) for r in con.execute('SELECT * FROM conversations ORDER BY created_at DESC')]


@app.post('/api/conversations')
def create_conversation(source_id: str = 'portfolio', mode: Literal['analytics', 'modeling'] = 'analytics'):
    if mode == 'analytics':
        try:
            inspect(source_id)
        except SourceError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        source_id = 'portfolio'
    ident = str(uuid.uuid4())
    now = datetime.now().isoformat()
    with state_connection() as con:
        con.execute('INSERT INTO conversations(id,title,created_at,source_id,mode) VALUES(?,?,?,?,?)',
                    (ident, 'Новый диалог', now, source_id, mode))
    return {'id': ident, 'title': 'Новый диалог', 'created_at': now, 'source_id': source_id, 'mode': mode}


@app.put('/api/conversations/{ident}/mode')
def change_mode(ident: str, item: ModeIn):
    with state_connection() as con:
        updated = con.execute('UPDATE conversations SET mode=? WHERE id=?', (item.mode, ident))
        if not updated.rowcount:
            raise HTTPException(404, 'Диалог не найден.')
    return {'mode': item.mode}


@app.delete('/api/conversations/{ident}')
def delete_conversation(ident: str):
    with state_connection() as con:
        conversation = con.execute('SELECT source_id FROM conversations WHERE id=?', (ident,)).fetchone()
        if not conversation:
            raise HTTPException(404, 'Conversation not found')
        con.execute('DELETE FROM events WHERE conversation_id=?', (ident,))
        con.execute('DELETE FROM messages WHERE conversation_id=?', (ident,))
        con.execute('DELETE FROM conversations WHERE id=?', (ident,))
    return {'deleted': True, 'id': ident, 'saved_reports_preserved': True}


@app.get('/api/conversations/{ident}/messages')
def messages(ident: str):
    with state_connection() as con:
        if not con.execute('SELECT 1 FROM conversations WHERE id=?', (ident,)).fetchone():
            raise HTTPException(404, 'Conversation not found')
        rows = con.execute('SELECT id,role,content,payload,created_at,mode FROM messages WHERE conversation_id=? ORDER BY id', (ident,))
        return [{**dict(r), 'payload': json.loads(r['payload']) if r['payload'] else None} for r in rows]


@app.post('/api/conversations/{ident}/messages')
def post_message(ident: str, item: MessageIn):
    now = datetime.now().isoformat()
    with state_connection() as con:
        conversation = con.execute('SELECT source_id,mode FROM conversations WHERE id=?', (ident,)).fetchone()
        if not conversation:
            raise HTTPException(404, 'Conversation not found')
        mode = conversation['mode']
        history = [dict(r) for r in con.execute('''SELECT role,content FROM messages
                    WHERE conversation_id=? AND mode=? ORDER BY id DESC LIMIT 8''', (ident, mode))][::-1]
        model_state = None
        if mode == 'modeling':
            previous = con.execute("""SELECT json_extract(payload,'$.model_state') FROM messages
                      WHERE conversation_id=? AND mode='modeling' AND role='assistant'
                      AND json_type(payload,'$.model_state')='object' ORDER BY id DESC LIMIT 1""", (ident,)).fetchone()
            if previous:
                model_state = json.loads(previous[0])
        is_first = con.execute('SELECT 1 FROM messages WHERE conversation_id=? LIMIT 1', (ident,)).fetchone() is None
        con.execute('INSERT INTO messages(conversation_id,role,content,created_at,mode) VALUES(?,?,?,?,?)',
                    (ident, 'user', item.content, now, mode))
        if is_first:
            con.execute('UPDATE conversations SET title=? WHERE id=?', (item.content[:56], ident))
    payload = Orchestrator().run(item.content, history, ident, conversation['source_id'],
                                 mode=mode, model_state=model_state)
    with state_connection() as con:
        con.execute('INSERT INTO messages(conversation_id,role,content,payload,created_at,mode) VALUES(?,?,?,?,?,?)',
                    (ident, 'assistant', payload['message'], json.dumps(payload, ensure_ascii=False, allow_nan=False),
                     datetime.now().isoformat(), mode))
    return payload


@app.get('/api/reports')
def reports():
    with state_connection() as con:
        return [dict(r) for r in con.execute('SELECT id,title,conversation_id,created_at FROM reports ORDER BY created_at DESC')]


@app.post('/api/reports/{conversation_id}/{message_id:int}')
def save_report(conversation_id: str, message_id: int):
    with state_connection() as con:
        row = con.execute('SELECT payload FROM messages WHERE id=? AND conversation_id=? AND role="assistant"',
                          (message_id, conversation_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Result not found')
        payload = json.loads(row['payload'])
        if payload['status'] != 'ok':
            raise HTTPException(400, 'Only completed reports can be saved')
        payload.pop('model_state', None)
        ident = str(uuid.uuid4())
        con.execute('INSERT INTO reports VALUES(?,?,?,?,?)', (ident, payload['title'], conversation_id,
                    json.dumps(payload, ensure_ascii=False), datetime.now().isoformat()))
    return {'id': ident, 'title': payload['title']}


@app.get('/api/reports/{ident}')
def report(ident: str):
    with state_connection() as con:
        row = con.execute('SELECT payload FROM reports WHERE id=?', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Report not found')
        return json.loads(row['payload'])


def _report(ident):
    with state_connection() as con:
        row = con.execute('SELECT payload FROM reports WHERE id=?', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Report not found')
        return json.loads(row['payload'])


def _monthly_model_report(ident):
    data = _report(ident)
    if data.get('mode') != 'modeling' or not data.get('bi_monthly'):
        raise HTTPException(400, 'Superset принимает помесячные отчёты моделирования.')
    return data


@app.post('/api/reports/{ident}/superset')
def report_superset_publish(ident: str):
    data = _monthly_model_report(ident)
    try:
        return publish_model_report(ident, data)
    except (OSError, KeyError, ValueError, PostgresError) as exc:
        raise HTTPException(503, 'Superset недоступен. Запустите Docker и настройте дашборд моделирования.') from exc


@app.get('/api/reports/{ident}/superset/guest-token')
def report_superset_token(ident: str):
    _monthly_model_report(ident)
    try:
        return {'token': model_guest_token(ident)}
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(503, 'Не удалось открыть дашборд моделирования в Superset.') from exc


@app.get('/api/reports/{ident}/export/{format}')
def export(ident: str, format: str):
    data = _report(ident)
    columns, rows = data['columns'], data['rows']
    if format == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(columns)
        writer.writerows([[row.get(c) for c in columns] for row in rows])
        return Response('\ufeff' + out.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="report.csv"'})
    if format == 'xlsx':
        wb = Workbook()

        def write_sheet(ws, headings, values):
            ws.append(headings)
            for row in values:
                ws.append([row.get(c) for c in headings])
            ws.freeze_panes = 'A2'
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(38, max(14, max(len(str(cell.value or '')) for cell in col[:50]) + 2))

        wb.active.title = 'Отчёт'
        write_sheet(wb.active, columns, rows)
        if measures := data.get('metrics'):
            summary = wb.create_sheet('Итоги сценариев', index=0)
            write_sheet(summary, measures['columns'], measures['rows'])
            wb.active = 0
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': 'attachment; filename="report.xlsx"'})
    if format == 'pdf':
        buf = io.BytesIO()
        font_path = Path('C:/Windows/Fonts/arial.ttf')
        font = 'Helvetica'
        if font_path.exists():
            pdfmetrics.registerFont(TTFont('ArialT1', str(font_path)))
            font = 'ArialT1'
        pdf = Canvas(buf, pagesize=landscape(A4))
        width, height = landscape(A4)
        def page_header():
            pdf.setFont(font, 15)
            pdf.drawString(38, height - 38, data['title'][:90])
            pdf.setFont(font, 9)
            pdf.drawString(38, height - 57, f"Период: {data.get('period','')}  |  Фильтры: {data.get('filters','')}  |  Единицы: {data.get('unit','')}"[:150])
        page_header()
        y = height - 88
        cell_width = (width - 76) / max(1, len(columns))
        pdf.setFont(font, 8)
        for i, col in enumerate(columns):
            pdf.drawString(38 + i * cell_width, y, str(col)[:25])
        y -= 16
        for row in rows:
            if y < 45:
                pdf.showPage()
                page_header()
                y = height - 88
            for i, col in enumerate(columns):
                pdf.drawString(38 + i * cell_width, y, str(row.get(col, ''))[:25])
            y -= 13
        chart = data.get('chart')
        if chart and rows:
            pdf.showPage()
            page_header()
        if chart and rows and chart.get('series'):
            _pdf_scenario_chart(pdf, font, width, chart, rows, data['unit'])
        elif chart and rows:
            values = [(str(r.get(chart['x'], '')), r.get(chart['y'])) for r in rows[:20]]
            values = [(label, value) for label, value in values if isinstance(value, (int, float))]
            if values:
                top = max(abs(v) for _, v in values) or 1
                bar_w = min(35, (width - 100) / len(values) - 4)
                for i, (label, value) in enumerate(values):
                    x = 50 + i * (bar_w + 7)
                    h = max(1, 300 * abs(value) / top)
                    pdf.setFillColor(colors.HexColor('#00A8E8'))
                    pdf.rect(x, 105, bar_w, h, fill=1, stroke=0)
                    pdf.setFillColor(colors.black)
                    pdf.saveState()
                    pdf.translate(x + 5, 95)
                    pdf.rotate(50)
                    pdf.setFont(font, 7)
                    pdf.drawString(0, 0, label[:18])
                    pdf.restoreState()
        pdf.save()
        return Response(buf.getvalue(), media_type='application/pdf',
                        headers={'Content-Disposition': 'attachment; filename="report.pdf"'})
    raise HTTPException(404, 'Unknown format')


def _pdf_scenario_chart(pdf, font, width, chart, rows, unit):
    series = {}
    for row in rows:
        series.setdefault(row[chart['series']], []).append((row[chart['x']], row[chart['y']]))
    values = [value for items in series.values() for _, value in items]
    low, high = min(0, *values), max(0, *values)
    months = max(1, *(month for items in series.values() for month, _ in items))
    left, right = 116, width - 38
    y = lambda value: 140 + 300 * (value - low) / (high - low or 1)
    x = lambda month: left + (right - left) * month / months
    pdf.setFont(font, 9)
    pdf.drawString(left, 465, f'Остаток денег, {unit}')
    for i in range(5):
        value = low + (high - low) * i / 4
        pdf.setStrokeColor(colors.HexColor('#dbe8ed'))
        pdf.line(left, y(value), right, y(value))
        pdf.drawRightString(left - 8, y(value) - 3, f'{value:,.0f}'.replace(',', ' '))
        if low == high:
            break
    pdf.setStrokeColor(colors.HexColor('#7e939e'))
    pdf.line(left, y(0), right, y(0))
    for month in range(0, months + 1, max(1, (months + 9) // 10)):
        pdf.drawCentredString(x(month), 123, str(month))
    pdf.drawCentredString((left + right) / 2, 104, 'Месяц')
    palette = ('#0089b8', '#a24172', '#237a61', '#7750a7', '#9b531d', '#3964a3', '#67544e', '#65731e', '#436268')
    for index, (name, points) in enumerate(series.items()):
        pdf.setStrokeColor(colors.HexColor(palette[index % len(palette)]))
        pdf.setLineWidth(2)
        path = pdf.beginPath()
        for point_index, (month, value) in enumerate(points):
            (path.moveTo if point_index == 0 else path.lineTo)(x(month), y(value))
        pdf.drawPath(path)
        label_x = left + (index % 3) * (right - left) / 3
        label_y = 83 - index // 3 * 15
        pdf.line(label_x, label_y + 3, label_x + 11, label_y + 3)
        pdf.drawString(label_x + 16, label_y, name[:35])
