const state={conversation:null,view:'chat',busy:false};
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>typeof v==='number'?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(v):String(v??'—');
async function api(path,options={}){const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const data=await r.json();if(!r.ok)throw Error(data.detail||'Ошибка сервера');return data}
function view(name){state.view=name;document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+name));document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===name));$('#view-label').textContent=({chat:'Диалог',source:'Источники данных',dashboard:'Дашборды',dictionary:'Карточка данных',reports:'Отчёты'})[name];$('#sidebar').classList.remove('open');if(name==='source')loadSource();if(name==='dashboard')loadDashboard();if(name==='dictionary')loadDictionary();if(name==='reports')loadReports()}
async function refreshHealth(){try{const x=await api('/health');$('#llm-status').textContent=x.llm_available?x.provider+' · готова':'LLM не настроена';$('.status-dot').classList.toggle('on',x.llm_available)}catch{$('#llm-status').textContent='Сервис недоступен'}}
async function refreshConversations(){const items=await api('/conversations');$('#conversations').innerHTML=items.map(c=>`<div class="conversation-row"><button class="conversation-item ${c.id===state.conversation?'selected':''}" data-id="${esc(c.id)}" title="${esc(c.title)}">${esc(c.title)}</button><button class="delete-chat" data-delete="${esc(c.id)}" aria-label="Удалить диалог: ${esc(c.title)}" title="Удалить диалог">×</button></div>`).join('');document.querySelectorAll('.conversation-item').forEach(b=>b.onclick=()=>openConversation(b.dataset.id));document.querySelectorAll('.delete-chat').forEach(b=>b.onclick=()=>deleteConversation(b.dataset.delete))}
async function deleteConversation(id){if(!confirm('Удалить диалог и все его сообщения? Сохранённые отчёты останутся.'))return;try{await api('/conversations/'+id,{method:'DELETE'});if(state.conversation===id){state.conversation=null;view('chat');$('#messages').innerHTML=welcomeHTML}await refreshConversations()}catch(e){alert(e.message)}}
async function newConversation(){const sourceId=$('#chat-source').value||'portfolio';const x=await api('/conversations?source_id='+encodeURIComponent(sourceId),{method:'POST'});state.conversation=x.id;view('chat');$('#messages').innerHTML=welcomeHTML;refreshConversations();$('#prompt').focus()}
const welcomeHTML=$('#messages').innerHTML;
async function openConversation(id){state.conversation=id;view('chat');await refreshSources();const conversations=await api('/conversations');const selected=conversations.find(c=>c.id===id);if(selected)$('#chat-source').value=selected.source_id||'portfolio';const items=await api('/conversations/'+id+'/messages');renderMessages(items);refreshConversations()}
function renderMessages(items){$('#messages').innerHTML=items.length?'':welcomeHTML;for(const item of items){let node=document.createElement('div');node.className='message '+item.role;if(item.role==='user'){node.innerHTML=`<div class="user-bubble">${esc(item.content)}</div>`}else{node.innerHTML=`<div class="assistant-head"><span class="agent-avatar">✦</span> Т1 Дата-агент</div><div class="answer-message">${esc(item.content)}</div>${item.payload?renderPayload(item.payload,item.id):''}`}$('#messages').append(node)}$('#messages').scrollTop=$('#messages').scrollHeight;document.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>saveReport(b.dataset.save));document.querySelectorAll('[data-report-open]').forEach(b=>b.onclick=()=>openReport(b.dataset.reportOpen))}
function renderPayload(p,messageId){let html='';if(p.status==='ok'){html+=`<div class="answer-meta"><span>${esc(p.period||'Период по запросу')}</span><span>${esc(p.filters||'Без дополнительных фильтров')}</span><span>${esc(p.unit||'Единицы в таблице')}</span></div>`;html+=`<div class="result-card"><div class="result-card-head"><h3>${esc(p.title)}</h3><span class="mini-badge">SQL ПРОВЕРЕН</span></div>${p.chart?`<div class="chart-wrap" data-chart="${esc(JSON.stringify({chart:p.chart,rows:p.rows}))}"></div>`:''}<div class="table-scroll"><table><thead><tr>${p.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${p.rows.map(row=>`<tr>${p.columns.map(c=>`<td>${esc(fmt(row[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;if(messageId)html+=`<div class="report-actions"><button data-save="${messageId}">＋ Сохранить отчёт</button></div>`}else{html+=`<div class="answer-meta"><span>${p.status==='clarify'?'Нужно уточнение':p.status==='refuse'?'Безопасный отказ':'Ошибка'}</span></div>`}html+=`<details class="trace"><summary>Трассировка · ${esc(p.duration_ms)} мс · ${esc(p.llm_tokens)} токенов</summary><pre>${esc(JSON.stringify({formula:p.formula,sql:p.sql,source:p.source,steps:p.trace},null,2))}</pre></details>`;setTimeout(drawCharts,0);return html}
function drawCharts() {
  document.querySelectorAll('.chart-wrap:not([data-drawn])').forEach(holder => {
    holder.dataset.drawn = '1';
    let payload;
    try { payload = JSON.parse(holder.dataset.chart); } catch { return; }
    const {chart, rows} = payload;
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
async function sendMessage(text){if(state.busy||!text.trim())return;if(!state.conversation)await newConversation();state.busy=true;$('#send').disabled=true;$('#prompt').value='';$('#messages').innerHTML+=`<div class="message user"><div class="user-bubble">${esc(text)}</div></div><div class="loading" id="waiting">Шесть агентов обрабатывают запрос и проверяют расчёт…</div>`;$('#messages').scrollTop=$('#messages').scrollHeight;try{await api('/conversations/'+state.conversation+'/messages',{method:'POST',body:JSON.stringify({content:text})});await openConversation(state.conversation)}catch(e){$('#waiting')?.remove();$('#messages').innerHTML+=`<div class="message assistant error">${esc(e.message)}</div>`}finally{state.busy=false;$('#send').disabled=false;$('#prompt').focus()}}
async function loadDictionary(){const root=$('#dictionary-content');root.innerHTML='<div class="loading">Загружаем карточку…</div>';try{const cards=await api('/dictionary');root.innerHTML=`<div class="dictionary-grid">${cards.map(c=>`<div class="field-card" data-field="${esc(c.field)}"><h3>${esc(c.field)}</h3><label>ОПИСАНИЕ</label><input name="description" value="${esc(c.description)}"><label>ЕДИНИЦА</label><input name="unit" value="${esc(c.unit)}"><label>ФОРМУЛА / ПРАВИЛО</label><input name="formula" value="${esc(c.formula)}"><button>Сохранить изменения</button></div>`).join('')}</div>`;root.querySelectorAll('.field-card').forEach(card=>card.querySelector('button').onclick=async()=>{const body={};card.querySelectorAll('input').forEach(input=>body[input.name]=input.value);const btn=card.querySelector('button');btn.disabled=true;try{await api('/dictionary/'+card.dataset.field,{method:'PUT',body:JSON.stringify(body)});btn.textContent='Сохранено ✓'}catch(e){btn.textContent=e.message}finally{setTimeout(()=>{btn.disabled=false;btn.textContent='Сохранить изменения'},2000)}})}catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}}
async function saveReport(messageId){try{const r=await api('/reports/'+state.conversation+'/'+messageId,{method:'POST'});alert('Отчёт «'+r.title+'» сохранён. Выгрузка доступна в разделе «Отчёты».')}catch(e){alert(e.message)}}
async function loadReports(){const root=$('#reports-content');root.innerHTML='<div class="loading">Загружаем отчёты…</div>';try{const reports=await api('/reports');root.innerHTML=reports.length?`<div class="report-list">${reports.map(r=>`<div class="report-item"><h3>${esc(r.title)}</h3><p>${esc(new Date(r.created_at).toLocaleString('ru-RU'))}</p><div class="report-actions"><button data-report-open="${esc(r.id)}">Открыть</button><a href="/api/reports/${esc(r.id)}/export/csv">CSV</a><a href="/api/reports/${esc(r.id)}/export/xlsx">XLSX</a><a href="/api/reports/${esc(r.id)}/export/pdf">PDF</a></div></div>`).join('')}</div>`:'<div class="empty">Пока нет сохранённых отчётов. Задайте вопрос в диалоге и сохраните результат.</div>';root.querySelectorAll('[data-report-open]').forEach(b=>b.onclick=()=>openReport(b.dataset.reportOpen))}catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}}
async function openReport(id){try{const p=await api('/reports/'+id);view('reports');$('#reports-content').innerHTML=`<button class="secondary-btn" id="back-reports">← Все отчёты</button><div class="answer-message" style="margin-top:20px">${esc(p.message)}</div>${renderPayload(p,null)}<div class="report-actions"><a href="/api/reports/${id}/export/csv">Скачать CSV</a><a href="/api/reports/${id}/export/xlsx">Скачать XLSX</a><a href="/api/reports/${id}/export/pdf">Скачать PDF</a></div>`;$('#back-reports').onclick=loadReports;drawCharts()}catch(e){alert(e.message)}}
document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>view(b.dataset.view));$('#new-chat').onclick=newConversation;$('#menu-toggle').onclick=()=>$('#sidebar').classList.toggle('open');$('#composer').onsubmit=e=>{e.preventDefault();sendMessage($('#prompt').value)};$('#prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#composer').requestSubmit()}};$('#messages').addEventListener('click',e=>{const button=e.target.closest('[data-prompt]');if(button&&$('#messages').contains(button))sendMessage(button.dataset.prompt)});
refreshHealth();refreshConversations().then(async()=>{const items=await api('/conversations');if(items.length)openConversation(items[0].id)}).catch(()=>{});
