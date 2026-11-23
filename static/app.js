const state={conversation:null,view:'chat',mode:'analytics',busy:false};
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>typeof v==='number'?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(v):String(v??'—');
async function api(path,options={}){const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const data=await r.json();if(!r.ok)throw Error(data.detail||'Ошибка сервера');return data}
function view(name, load=true){state.view=name;document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+name));document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===name));$('#view-label').textContent=({chat:state.mode==='modeling'?'Моделирование':'Диалог',source:'Источники данных',dashboard:'Дашборды',dictionary:'Карточка данных',reports:'Отчёты'})[name];$('#sidebar').classList.remove('open');if(name==='source')loadSource();if(name==='dashboard')loadDashboard();if(name==='dictionary')loadDictionary();if(name==='reports'&&load)loadReports()}
async function refreshHealth(){try{const x=await api('/health');$('#llm-status').textContent=x.llm_available?x.provider+' · готова':'LLM не настроена';$('.status-dot').classList.toggle('on',x.llm_available)}catch{$('#llm-status').textContent='Сервис недоступен'}}
async function refreshConversations(){const items=await api('/conversations');$('#conversations').innerHTML=items.map(c=>`<div class="conversation-row"><button class="conversation-item ${c.id===state.conversation?'selected':''}" data-id="${esc(c.id)}" title="${esc(c.title)}">${esc(c.title)}</button><button class="delete-chat" data-delete="${esc(c.id)}" aria-label="Удалить диалог: ${esc(c.title)}" title="Удалить диалог">×</button></div>`).join('');document.querySelectorAll('.conversation-item').forEach(b=>b.onclick=()=>openConversation(b.dataset.id));document.querySelectorAll('.delete-chat').forEach(b=>b.onclick=()=>deleteConversation(b.dataset.delete))}
async function deleteConversation(id){if(!confirm('Удалить диалог и все его сообщения? Сохранённые отчёты останутся.'))return;try{await api('/conversations/'+id,{method:'DELETE'});if(state.conversation===id){state.conversation=null;view('chat');emptyConversation()}await refreshConversations()}catch(e){alert(e.message)}}
function applyMode(mode) {
  state.mode = mode === 'modeling' ? 'modeling' : 'analytics';
  const active = state.mode === 'modeling';
  $('#modeling-mode').setAttribute('aria-pressed', String(active));
  $('#composer-source').hidden = active;
  $('#modeling-example').hidden = !active || Boolean($('#messages .message'));
  if (state.view === 'chat') $('#view-label').textContent = active ? 'Моделирование' : 'Диалог';
  $('#prompt').placeholder = active ? 'Опишите проект, бюджет или сценарий…' : 'Спросите о данных или задайте уточнение…';
  $('#composer-help').textContent = active ? 'Расскажите о выручке, расходах и сроках оплаты' : 'Расчёты выполняются SQL по выбранному источнику';
}

function emptyConversation() {
  $('#messages').innerHTML = state.mode === 'modeling' ? modelingWelcome : welcomeHTML;
  applyMode(state.mode);
}

async function toggleModeling() {
  if (state.busy) return;
  const mode = state.mode === 'modeling' ? 'analytics' : 'modeling';
  state.busy = true;
  $('#modeling-mode').disabled = true;
  try {
    if (state.conversation) {
      await api('/conversations/' + state.conversation + '/mode', {
        method: 'PUT', body: JSON.stringify({mode}),
      });
    }
    applyMode(mode);
    if (!$('#messages .message')) emptyConversation();
    $('#prompt').focus();
  } catch (error) {
    alert(error.message);
  } finally {
    state.busy = false;
    $('#modeling-mode').disabled = false;
  }
}

async function newConversation() {
  const params = new URLSearchParams({source_id: $('#chat-source').value || 'portfolio', mode: state.mode});
  const conversation = await api('/conversations?' + params, {method: 'POST'});
  state.conversation = conversation.id;
  view('chat');
  applyMode(conversation.mode);
  emptyConversation();
  refreshConversations();
  $('#prompt').focus();
}

const welcomeHTML = $('#messages').innerHTML;
const modelingWelcome = [
  '<div class="welcome"><div class="welcome-logo"><span>+</span>Т1</div>',
  '<div class="eyebrow">ФИНАНСОВАЯ МОДЕЛЬ</div><h1>Хватит ли денег проекту?</h1>',
  '<p>Укажите бюджет и условия работы проекта. Агент уточнит вводные,',
  ' рассчитает прибыль и покажет, когда может потребоваться финансирование.</p></div>',
].join('');

async function openConversation(id) {
  state.conversation = id;
  view('chat');
  const [conversations, items] = await Promise.all([api('/conversations'), api('/conversations/' + id + '/messages')]);
  if (state.conversation !== id) return;
  const selected = conversations.find(conversation => conversation.id === id);
  if (selected) {
    applyMode(selected.mode);
    await refreshSources();
    if (state.conversation !== id) return;
    $('#chat-source').value = selected.source_id || 'portfolio';
  }
  renderMessages(items);
  refreshConversations();
}

function resultTable(columns, rows) {
  const headers = columns.map(column => '<th scope="col">' + esc(column) + '</th>').join('');
  const body = rows.map(row => '<tr>' + columns.map(column => '<td>' + esc(fmt(row[column])) + '</td>').join('') + '</tr>').join('');
  return '<div class="table-scroll"><table><thead><tr>' + headers + '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

function renderPayload(p, messageId) {
  let html = '';
  if (p.status === 'ok') {
    const model = p.mode === 'modeling';
    const badge = model ? 'СЦЕНАРНЫЙ РАСЧЁТ' : 'SQL ПРОВЕРЕН';
    const meta = [p.period || 'Период по запросу', p.filters || 'Без дополнительных фильтров', p.unit || 'Единицы в таблице'];
    html += '<div class="answer-meta">' + meta.map(text => '<span>' + esc(text) + '</span>').join('') + '</div>';
    html += '<div class="result-card"><div class="result-card-head"><h3>' + esc(p.title) +
      '</h3><span class="mini-badge">' + badge + '</span></div>';
    if (p.chart) {
      const data = esc(JSON.stringify({chart: p.chart, rows: p.rows}));
      html += '<div class="chart-wrap" data-chart="' + data + '"></div>';
    }
    if (p.metrics) {
      html += '<h4 class="model-table-title">Итоги по сценариям</h4>' + resultTable(p.metrics.columns, p.metrics.rows);
      html += '<h4 class="model-table-title">Расчёт по месяцам</h4>';
    }
    html += resultTable(p.columns, p.rows) + '</div>';
    if (p.assumptions) html += '<p class="model-assumptions">' + esc(p.assumptions) + '</p>';
    if (messageId) html += '<div class="report-actions"><button data-save="' + esc(messageId) + '">＋ Сохранить отчёт</button></div>';
  } else {
    const status = p.status === 'clarify' ? 'Нужно уточнение' : p.status === 'refuse' ? 'Безопасный отказ' : 'Ошибка';
    html += '<div class="answer-meta"><span>' + status + '</span></div>';
  }
  const trace = esc(JSON.stringify({formula: p.formula, sql: p.sql, source: p.source, steps: p.trace}, null, 2));
  html += '<details class="trace"><summary>Трассировка · ' + esc(p.duration_ms) + ' мс · ' + esc(p.llm_tokens)
    + ' токенов</summary><pre>' + trace + '</pre></details>';
  setTimeout(drawCharts, 0);
  return html;
}

function drawScenarioChart(holder, {chart, rows}) {
  const entries = new Map();
  for (const row of rows) {
    if (!Number.isFinite(row[chart.x]) || !Number.isFinite(row[chart.y])) continue;
    const name = row[chart.series];
    if (!entries.has(name)) entries.set(name, []);
    entries.get(name).push([row[chart.x], row[chart.y]]);
  }
  if (!entries.size) return;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 760 250');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Остаток денег по месяцам для каждого сценария');
  const colors = ['#0089b8', '#a24172', '#237a61', '#7750a7', '#9b531d', '#3964a3', '#67544e', '#65731e', '#436268'];
  function line(tag, attributes, text) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    if (text !== undefined) node.textContent = text;
    svg.append(node);
    return node;
  }
  const points = [...entries.values()].flat();
  const monthMax = Math.max(1, ...points.map(([month]) => month));
  const values = points.map(([, value]) => value);
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const x = month => 100 + month * 630 / monthMax;
  const y = value => 205 - (value - min) * 176 / (max - min || 1);
  const label = {fill: '#627783', 'font-size': 12};
  for (let i = 0; i <= 4; i++) {
    const value = min + (max - min) * i / 4;
    line('line', {x1: 100, x2: 730, y1: y(value), y2: y(value), stroke: '#dfebef'});
    line('text', {x: 92, y: y(value) + 4, 'text-anchor': 'end', ...label}, fmt(value));
    if (max === min) break;
  }
  line('line', {x1: 100, x2: 730, y1: y(0), y2: y(0), stroke: '#7e939e', 'stroke-dasharray': '4 3'});
  const step = Math.max(1, Math.ceil(monthMax / 10));
  for (let month = 0; month <= monthMax; month += step) {
    line('text', {x: x(month), y: 225, 'text-anchor': 'middle', ...label}, month);
  }
  line('text', {x: 420, y: 247, 'text-anchor': 'middle', ...label}, 'Месяц');
  const legend = document.createElement('div');
  legend.className = 'model-chart-legend';
  [...entries].forEach(([name, series], index) => {
    const color = colors[index % colors.length];
    series.sort(([a], [b]) => a - b);
    line('polyline', {points: series.map(([month, val]) => x(month) + ',' + y(val)).join(' '),
      fill: 'none', stroke: color, 'stroke-width': 3, 'stroke-linejoin': 'round'});
    const label = document.createElement('span');
    const mark = document.createElement('i');
    mark.style.backgroundColor = color;
    label.append(mark, name);
    legend.append(label);
  });
  holder.append(svg, legend);
}
function renderMessages(items){$('#messages').innerHTML=items.length?'':(state.mode==='modeling'?modelingWelcome:welcomeHTML);for(const item of items){let node=document.createElement('div');node.className='message '+item.role;if(item.role==='user'){node.innerHTML=`<div class="user-bubble">${esc(item.content)}</div>`}else{node.innerHTML=`<div class="assistant-head"><span class="agent-avatar">✦</span> Т1 Дата-агент</div><div class="answer-message">${esc(item.content)}</div>${item.payload?renderPayload(item.payload,item.id):''}`}$('#messages').append(node)}$('#messages').scrollTop=$('#messages').scrollHeight;applyMode(state.mode);document.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>saveReport(b.dataset.save));document.querySelectorAll('[data-report-open]').forEach(b=>b.onclick=()=>openReport(b.dataset.reportOpen))}
function drawCharts() {
  document.querySelectorAll('.chart-wrap:not([data-drawn])').forEach(holder => {
    holder.dataset.drawn = '1';
    let payload;
    try { payload = JSON.parse(holder.dataset.chart); } catch { return; }
    const {chart, rows} = payload;
    if (chart.series) { drawScenarioChart(holder, payload); return; }
    const forecast = Boolean(chart.forecast_from);
    const points = (forecast ? rows : rows.slice(0, 18))
      .filter(row => row[chart.y] != null && Number.isFinite(Number(row[chart.y])));
    if (!points.length) return;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', forecast ? '0 0 760 270' : '0 0 760 250');
    function element(tag, attrs, label) {
      const item = document.createElementNS(svg.namespaceURI, tag);
      for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, value);
      if (label !== undefined) item.textContent = label;
      svg.append(item);
      return item;
    }

    const vals = points.map(row => Number(row[chart.y]));
    const min = forecast ? Math.min(...vals) : Math.min(0, ...vals);
    let max = forecast ? Math.max(...vals) : Math.max(0, ...vals);
    if (max === min) max = min + 1;
    const yFor = value => 205 - (value - min) / (max - min) * 185;
    const spacing = 665 / points.length;
    const zero = yFor(0);
    const axisFont = {'font-size': 10, fill: '#627783'};
    for (let i = 0; i < 4; i++) {
      const value = min + (max - min) * i / 3;
      const y = yFor(value);
      element('line', {x1: 80, y1: y, x2: 745, y2: y, stroke: '#edf2f4'});
      element('text', {x: 74, y: y + 4, 'text-anchor': 'end', ...axisFont}, fmt(value));
    }
    if (chart.type !== 'line') {
      element('line', {x1: 80, y1: zero, x2: 745, y2: zero, stroke: '#cbd9df'});
    }

    const locations = points.map((row, i) => ({
      x: 80 + (i + .5) * spacing,
      y: yFor(vals[i]),
      label: String(row[chart.x]),
      projected: forecast && String(row[chart.x]) >= chart.forecast_from,
    }));
    const actualColor = '#008bbc', forecastColor = '#31649a';
    if (chart.type === 'line') {
      const firstProjected = locations.findIndex(point => point.projected);
      function line(series, color, dash) {
        if (series.length < 2) return;
        const attrs = {points: series.map(({x, y}) => x + ',' + y).join(' '),
          fill: 'none', stroke: color, 'stroke-width': 3, 'stroke-linejoin': 'round'};
        if (dash) attrs['stroke-dasharray'] = dash;
        element('polyline', attrs);
      }
      line(firstProjected < 0 ? locations : locations.slice(0, firstProjected), actualColor);
      if (firstProjected >= 0) {
        line(locations.slice(Math.max(0, firstProjected - 1)), forecastColor, '7 5');
      }
    }

    const labelStep = Math.ceil(locations.length / 14);
    locations.forEach(({x, y, label, projected}, i) => {
      if (chart.type === 'line') {
        element('circle', {cx: x, cy: y, r: 4, fill: projected ? '#fff' : actualColor,
          stroke: projected ? forecastColor : actualColor, 'stroke-width': 2});
      } else {
        element('rect', {x: x - spacing * .31, y: Math.min(y, zero), width: spacing * .62,
          height: Math.max(1, Math.abs(zero - y)), rx: 3, fill: '#22b7ed'});
      }
      if (i % labelStep === 0 || i === locations.length - 1) {
        element('text', {x, y: 230, 'text-anchor': 'middle', ...axisFont},
          label.slice(0, forecast ? 7 : 18));
      }
    });

    if (forecast) {
      element('line', {x1: 272, y1: 252, x2: 300, y2: 252, stroke: actualColor, 'stroke-width': 3});
      element('text', {x: 308, y: 256, ...axisFont}, 'Факт');
      element('line', {x1: 368, y1: 252, x2: 396, y2: 252, stroke: forecastColor,
        'stroke-width': 3, 'stroke-dasharray': '7 5'});
      element('text', {x: 404, y: 256, ...axisFont}, 'Прогноз');
    }
    holder.append(svg);
  });
}
async function sendMessage(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  $('#send').disabled = true;
  $('#modeling-mode').disabled = true;
  $('#modeling-example').disabled = true;
  try {
    if (!state.conversation) await newConversation();
    const id = state.conversation;
    $('#prompt').value = '';
    $('#messages .welcome')?.remove();
    const loading = state.mode === 'modeling' ? 'Проверяем вводные и рассчитываем модель…' : 'Агенты обрабатывают запрос…';
    $('#messages').insertAdjacentHTML('beforeend', '<div class="message user"><div class="user-bubble">'
      + esc(text) + '</div></div><div class="loading" id="waiting">' + loading + '</div>');
    applyMode(state.mode);
    $('#messages').scrollTop = $('#messages').scrollHeight;
    await api('/conversations/' + id + '/messages', {method: 'POST', body: JSON.stringify({content: text})});
    if (state.conversation === id) await openConversation(id);
  } catch (error) {
    $('#waiting')?.remove();
    $('#messages').insertAdjacentHTML('beforeend', '<div class="message assistant error">' + esc(error.message) + '</div>');
  } finally {
    state.busy = false;
    $('#send').disabled = false;
    $('#modeling-mode').disabled = false;
    $('#modeling-example').disabled = false;
    $('#prompt').focus();
  }
}
async function loadDictionary(){const root=$('#dictionary-content');root.innerHTML='<div class="loading">Загружаем карточку…</div>';try{const cards=await api('/dictionary');root.innerHTML=`<div class="dictionary-grid">${cards.map(c=>`<div class="field-card" data-field="${esc(c.field)}"><h3>${esc(c.field)}</h3><label>ОПИСАНИЕ</label><input name="description" value="${esc(c.description)}"><label>ЕДИНИЦА</label><input name="unit" value="${esc(c.unit)}"><label>ФОРМУЛА / ПРАВИЛО</label><input name="formula" value="${esc(c.formula)}"><button>Сохранить изменения</button></div>`).join('')}</div>`;root.querySelectorAll('.field-card').forEach(card=>card.querySelector('button').onclick=async()=>{const body={};card.querySelectorAll('input').forEach(input=>body[input.name]=input.value);const btn=card.querySelector('button');btn.disabled=true;try{await api('/dictionary/'+card.dataset.field,{method:'PUT',body:JSON.stringify(body)});btn.textContent='Сохранено ✓'}catch(e){btn.textContent=e.message}finally{setTimeout(()=>{btn.disabled=false;btn.textContent='Сохранить изменения'},2000)}})}catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}}
async function saveReport(messageId){try{const r=await api('/reports/'+state.conversation+'/'+messageId,{method:'POST'});alert('Отчёт «'+r.title+'» сохранён. Выгрузка доступна в разделе «Отчёты».')}catch(e){alert(e.message)}}
async function loadReports(){const root=$('#reports-content');root.innerHTML='<div class="loading">Загружаем отчёты…</div>';try{const reports=await api('/reports');root.innerHTML=reports.length?`<div class="report-list">${reports.map(r=>`<div class="report-item"><h3>${esc(r.title)}</h3><p>${esc(new Date(r.created_at).toLocaleString('ru-RU'))}</p><div class="report-actions"><button data-report-open="${esc(r.id)}">Открыть</button><a href="/api/reports/${esc(r.id)}/export/csv">CSV</a><a href="/api/reports/${esc(r.id)}/export/xlsx">XLSX</a><a href="/api/reports/${esc(r.id)}/export/pdf">PDF</a></div></div>`).join('')}</div>`:'<div class="empty">Пока нет сохранённых отчётов. Задайте вопрос в диалоге и сохраните результат.</div>';root.querySelectorAll('[data-report-open]').forEach(b=>b.onclick=()=>openReport(b.dataset.reportOpen))}catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}}
async function mountModelingDashboard(id, button, mount) {
  button.disabled = true;
  mount.hidden = false;
  mount.textContent = 'Подготавливаем дашборд…';
  try {
    const info = await api('/reports/' + id + '/superset', {method: 'POST'});
    const {embedDashboard} = await import('https://esm.sh/@superset-ui/embedded-sdk@0.4.0');
    mount.replaceChildren();
    await embedDashboard({
      id: info.embedded_id, supersetDomain: info.url, mountPoint: mount,
      fetchGuestToken: async () => (await api('/reports/' + id + '/superset/guest-token')).token,
      dashboardUiConfig: {hideTitle: true, hideChartControls: true, filters: {visible: true}},
    });
    button.hidden = true;
  } catch (error) {
    mount.textContent = error.message;
    button.disabled = false;
  }
}

async function openReport(id) {
  try {
    const p = await api('/reports/' + id);
    view('reports', false);
    const path = '/api/reports/' + encodeURIComponent(id) + '/export/';
    const superset = p.bi_monthly
      ? '<button type="button" id="model-superset">Показать в Superset</button>' : '';
    $('#reports-content').innerHTML = '<button class="secondary-btn" id="back-reports">Все отчёты</button>'
      + '<div class="answer-message" style="margin-top:20px">' + esc(p.message) + '</div>'
      + renderPayload(p, null) + '<div class="report-actions">'
      + ['CSV', 'XLSX', 'PDF'].map(format => '<a href="' + esc(path + format.toLowerCase()) + '">Скачать '
        + format + '</a>').join('')
      + superset + '</div><div id="model-superset-mount" class="model-superset-mount" hidden></div>';
    $('#back-reports').onclick = loadReports;
    const button = $('#model-superset');
    if (button) button.onclick = () => mountModelingDashboard(id, button, $('#model-superset-mount'));
    drawCharts();
  } catch (error) {
    alert(error.message);
  }
}
document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>view(b.dataset.view));$('#new-chat').onclick=()=>{if(!state.busy)newConversation().catch(error=>alert(error.message))};
$('#modeling-mode').onclick=toggleModeling;
$('#modeling-example').onclick=()=>sendMessage('Рассчитай учебный пример');
$('#menu-toggle').onclick=()=>$('#sidebar').classList.toggle('open');$('#composer').onsubmit=e=>{e.preventDefault();sendMessage($('#prompt').value)};$('#prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#composer').requestSubmit()}};$('#messages').addEventListener('click',e=>{const button=e.target.closest('[data-prompt]');if(button&&$('#messages').contains(button))sendMessage(button.dataset.prompt)});
refreshHealth();refreshConversations().then(async()=>{const items=await api('/conversations');if(items.length)openConversation(items[0].id)}).catch(()=>{});
