import csv
import io
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from agent.agents import Orchestrator
from agent.llm import configuration
from agent.store import ROOT, dictionary, initialize, profile, state_connection
from agent.store import RUNTIME
from agent.sources import (SourceError, registered, inspect, tables, set_table,
                           add_upload, add_postgres, dashboard_query, filter_values, MAX_UPLOAD)
from agent.superset_client import guest_token

app = FastAPI(title='Т1 Дата-агент', version='0.1.0')


@app.on_event('startup')
def startup():
    initialize()


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


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
def create_conversation(source_id: str = 'portfolio'):
    try:
        inspect(source_id)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    ident = str(uuid.uuid4())
    now = datetime.now().isoformat()
    with state_connection() as con:
        con.execute('INSERT INTO conversations(id,title,created_at,source_id) VALUES(?,?,?,?)', (ident, 'Новый диалог', now, source_id))
    return {'id': ident, 'title': 'Новый диалог', 'created_at': now, 'source_id': source_id}


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
        rows = con.execute('SELECT id,role,content,payload,created_at FROM messages WHERE conversation_id=? ORDER BY id', (ident,))
        return [{**dict(r), 'payload': json.loads(r['payload']) if r['payload'] else None} for r in rows]


@app.post('/api/conversations/{ident}/messages')
def post_message(ident: str, item: MessageIn):
    now = datetime.now().isoformat()
    with state_connection() as con:
        conversation = con.execute('SELECT source_id FROM conversations WHERE id=?', (ident,)).fetchone()
        if not conversation:
            raise HTTPException(404, 'Conversation not found')
        history = [dict(r) for r in con.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 8', (ident,))][::-1]
        con.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',
                    (ident, 'user', item.content, now))
        if not history:
            con.execute('UPDATE conversations SET title=? WHERE id=?', (item.content[:56], ident))
    payload = Orchestrator().run(item.content, history, ident, conversation['source_id'])
    with state_connection() as con:
        con.execute('INSERT INTO messages(conversation_id,role,content,payload,created_at) VALUES(?,?,?,?,?)',
                    (ident, 'assistant', payload['message'], json.dumps(payload, ensure_ascii=False), datetime.now().isoformat()))
    return payload


@app.get('/api/reports')
def reports():
    with state_connection() as con:
        return [dict(r) for r in con.execute('SELECT id,title,conversation_id,created_at FROM reports ORDER BY created_at DESC')]


@app.post('/api/reports/{conversation_id}/{message_id}')
def save_report(conversation_id: str, message_id: int):
    with state_connection() as con:
        row = con.execute('SELECT payload FROM messages WHERE id=? AND conversation_id=? AND role="assistant"',
                          (message_id, conversation_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Result not found')
        payload = json.loads(row['payload'])
        if payload['status'] != 'ok':
            raise HTTPException(400, 'Only completed reports can be saved')
        ident = str(uuid.uuid4())
        con.execute('INSERT INTO reports VALUES(?,?,?,?,?)', (ident, payload['title'], conversation_id,
                    row['payload'], datetime.now().isoformat()))
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
        ws = wb.active
        ws.title = 'Отчёт'
        ws.append(columns)
        for row in rows:
            ws.append([row.get(c) for c in columns])
        ws.freeze_panes = 'A2'
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(38, max(14, max(len(str(cell.value or '')) for cell in list(col)[:50]) + 2))
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
