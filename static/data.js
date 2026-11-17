const dataState={sources:[],sourceId:localStorage.getItem('t1_source')||'portfolio',schema:null,chartType:'bar'};
const option=(value,label)=>`<option value="${esc(value)}">${esc(label)}</option>`;
const sourceOptions=()=>dataState.sources.map(s=>option(s.id,s.name+' · '+s.kind)).join('');

async function refreshSources(){
  dataState.sources=await api('/sources');
  if(!dataState.sources.some(s=>s.id===dataState.sourceId))dataState.sourceId=dataState.sources[0]?.id||null;
  localStorage.setItem('t1_source',dataState.sourceId||'');
  const chat=$('#chat-source');if(chat){const prior=chat.value;chat.innerHTML=sourceOptions();chat.value=dataState.sources.some(s=>s.id===prior)?prior:dataState.sourceId}
}

async function loadSource(){
  const root=$('#source-content');root.innerHTML='<div class="loading">Загружаем подключения…</div>';
  try{
    await refreshSources();
    root.innerHTML=`<div class="source-layout"><div class="section-card"><div class="section-title">Подключённые источники</div><div class="section-body"><div class="source-list">${dataState.sources.map(s=>`<button class="source-choice ${s.id===dataState.sourceId?'chosen':''}" data-source="${esc(s.id)}"><b>${esc(s.name)}</b><span>${esc(s.kind.toUpperCase())} · ${esc(s.table_name)}</span></button>`).join('')}</div></div></div><div class="section-card"><div class="section-title">Добавить источник</div><div class="section-body"><label class="form-label">Имя подключения</label><input class="control" id="new-source-name" placeholder="Например, Продажи 2026"><label class="form-label">Файл SQLite, CSV или XLSX</label><input class="control" type="file" id="source-file" accept=".db,.sqlite,.sqlite3,.csv,.xlsx"><button class="primary-btn" id="upload-source">Загрузить файл</button><div class="form-divider">или PostgreSQL</div><label class="form-label">URL подключения</label><input class="control" id="postgres-dsn" type="password" autocomplete="off" placeholder="postgresql://user:password@host:5432/database"><button class="secondary-btn" id="connect-postgres">Подключить PostgreSQL</button><p class="form-help">Адрес хранится локально в папке .runtime и не попадает в Git. Для PostgreSQL используйте пользователя с правом чтения.</p><div id="source-feedback"></div></div></div></div><div id="source-detail"></div>`;
    root.querySelectorAll('[data-source]').forEach(b=>b.onclick=async()=>{dataState.sourceId=b.dataset.source;localStorage.setItem('t1_source',dataState.sourceId);$('#chat-source').value=dataState.sourceId;state.conversation=null;$('#messages').innerHTML=welcomeHTML;await loadSource()});
    $('#upload-source').onclick=uploadDataSource;
    $('#connect-postgres').onclick=connectPostgresSource;
    await renderSourceDetail();
  }catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}
}

async function uploadDataSource(){
  const file=$('#source-file').files[0];if(!file){$('#source-feedback').textContent='Выберите файл.';return}
  const btn=$('#upload-source');btn.disabled=true;$('#source-feedback').textContent='Загрузка и проверка…';
  try{
    const url='/api/sources/upload?filename='+encodeURIComponent(file.name)+'&name='+encodeURIComponent($('#new-source-name').value);
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
    const result=await response.json();if(!response.ok)throw Error(result.detail||'Ошибка загрузки');
    dataState.sourceId=result.id;localStorage.setItem('t1_source',result.id);$('#chat-source').value=result.id;state.conversation=null;await loadSource();
  }catch(e){$('#source-feedback').textContent=e.message}finally{btn.disabled=false}
}

async function connectPostgresSource(){
  const btn=$('#connect-postgres');btn.disabled=true;$('#source-feedback').textContent='Проверяем соединение…';
  try{const result=await api('/sources/postgresql',{method:'POST',body:JSON.stringify({name:$('#new-source-name').value||'PostgreSQL',dsn:$('#postgres-dsn').value})});dataState.sourceId=result.id;localStorage.setItem('t1_source',result.id);$('#chat-source').value=result.id;state.conversation=null;await loadSource()}
  catch(e){$('#source-feedback').textContent=e.message}finally{btn.disabled=false}
}

async function renderSourceDetail(){
  const root=$('#source-detail');if(!dataState.sourceId)return;
  try{
    const d=await api('/sources/'+dataState.sourceId);dataState.schema=d;
    const missing=Object.values(d.missing).reduce((a,b)=>a+b,0);
    root.innerHTML=`<div class="section-card"><div class="section-title">${esc(d.name)} <span class="mini-badge">${esc(d.kind.toUpperCase())}</span></div><div class="section-body"><div class="source-toolbar"><label class="form-label">Таблица <select class="control" id="source-table">${d.tables.map(t=>option(t,t)).join('')}</select></label><button class="secondary-btn" id="open-source-dashboard">Построить дашборд →</button></div><div class="stat-grid source-stats"><div class="stat"><b>${fmt(d.records)}</b><span>СТРОК</span></div><div class="stat"><b>${d.columns.length}</b><span>ПОЛЕЙ</span></div><div class="stat"><b>${fmt(missing)}</b><span>ПУСТЫХ ЗНАЧЕНИЙ</span></div></div><div class="table-scroll"><table class="data-table"><thead><tr><th>Поле</th><th>Тип</th><th>Пропуски</th></tr></thead><tbody>${d.columns.map(c=>`<tr><td>${esc(c.name)}</td><td>${esc(c.type)}</td><td>${fmt(d.missing[c.name])}</td></tr>`).join('')}</tbody></table></div></div></div><div class="section-card"><div class="section-title">Пример записей</div><div class="table-scroll"><table class="data-table"><thead><tr>${d.columns.map(c=>`<th>${esc(c.name)}</th>`).join('')}</tr></thead><tbody>${d.sample.map(row=>`<tr>${d.columns.map(c=>`<td>${esc(fmt(row[c.name]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
    root.querySelector('.source-stats').insertAdjacentHTML('beforeend',`<div class="stat"><b>${d.duplicate_rows===null?'—':fmt(d.duplicate_rows)}</b><span>${d.duplicate_rows===null?'ПОВТОРЫ НЕ ПРОВЕРЕНЫ':'ПОВТОРНЫХ СТРОК'}</span></div>`);
    $('#source-table').value=d.table;
    $('#source-table').onchange=async e=>{try{await api('/sources/'+dataState.sourceId+'/table',{method:'PUT',body:JSON.stringify({table:e.target.value})});await renderSourceDetail()}catch(err){alert(err.message)}};
    $('#open-source-dashboard').onclick=()=>view('dashboard');
  }catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}
}

async function loadDashboard(){
  const root=$('#dashboard-content');root.innerHTML='<div class="loading">Готовим конструктор…</div>';
  try{
    await refreshSources();
    root.innerHTML=`<div class="section-card"><div class="section-title">Настройки анализа</div><div class="section-body"><div class="dashboard-controls"><label>ИСТОЧНИК<select class="control" id="dash-source">${sourceOptions()}</select></label><label>ПОКАЗАТЕЛЬ<select class="control" id="dash-metric"></select></label><label>ОПЕРАЦИЯ<select class="control" id="dash-aggregation">${option('sum','Сумма')}${option('avg','Среднее')}${option('count','Количество')}${option('min','Минимум')}${option('max','Максимум')}</select></label><label>РАЗРЕЗ<select class="control" id="dash-dimension"></select></label><label>ГРАФИК<select class="control" id="dash-chart">${option('bar','Столбцы')}${option('line','Линия')}${option('donut','Кольцо')}</select></label></div><div class="filter-head"><b>Фильтры</b><button class="secondary-btn" id="add-filter">＋ Добавить фильтр</button></div><div id="dash-filters"></div><div class="dashboard-actions"><button class="primary-btn" id="run-dashboard">Построить дашборд</button><button class="secondary-btn" id="save-dashboard">Сохранить</button></div><div id="dash-feedback"></div></div></div><div id="dashboard-result"></div><div class="section-card"><div class="section-title">Сохранённые дашборды</div><div class="section-body" id="saved-dashboards"></div></div>`;
    root.querySelector('.filter-head').insertAdjacentHTML('beforebegin','<div class="period-controls"><label>ПОЛЕ ДАТЫ<select class="control" id="dash-date-field"></select></label><label>С ДАТЫ<input class="control" type="date" id="dash-date-from"></label><label>ПО ДАТУ<input class="control" type="date" id="dash-date-to"></label></div>');
    $('#dash-source').value=dataState.sourceId;
    $('#dash-source').onchange=async e=>{dataState.sourceId=e.target.value;localStorage.setItem('t1_source',e.target.value);$('#dash-filters').innerHTML='';await fillDashboardFields()};
    $('#add-filter').onclick=addDashboardFilter;
    $('#run-dashboard').onclick=runDashboard;
    $('#save-dashboard').onclick=saveCurrentDashboard;
    await fillDashboardFields();await refreshSavedDashboards();
  }catch(e){root.innerHTML=`<div class="error">${esc(e.message)}</div>`}
}

async function fillDashboardFields(){
  const d=await api('/sources/'+dataState.sourceId);dataState.schema=d;
  const fields=d.columns.map(c=>option(c.name,c.name+' · '+c.type)).join('');
  $('#dash-metric').innerHTML=option('*','Все записи')+fields;
  $('#dash-dimension').innerHTML=option('','Без разреза')+fields;
  $('#dash-date-field').innerHTML=option('','Без фильтра периода')+fields;
  const preferred=d.columns.find(c=>/balance_rub|amount|revenue|sales|сумм|выручк/i.test(c.name));
  if(preferred)$('#dash-metric').value=preferred.name;else $('#dash-aggregation').value='count';
  const dim=d.columns.find(c=>/as_of_date|date|period|month|дата/i.test(c.name));
  if(dim)$('#dash-dimension').value=dim.name;
  if(dim)$('#dash-date-field').value=dim.name;
  $('#dashboard-result').innerHTML='';
}

function addDashboardFilter(){
  const root=$('#dash-filters');if(root.children.length>=6)return;
  const row=document.createElement('div');row.className='filter-row';
  row.innerHTML=`<select class="control filter-field">${dataState.schema.columns.map(c=>option(c.name,c.name)).join('')}</select><select class="control filter-value"><option value="">Все значения</option></select><button class="secondary-btn" aria-label="Удалить фильтр">×</button>`;
  root.append(row);row.querySelector('button').onclick=()=>row.remove();
  row.querySelector('.filter-field').onchange=()=>loadFilterValues(row);
  loadFilterValues(row);
}

async function loadFilterValues(row){
  try{const field=row.querySelector('.filter-field').value;const values=await api('/dashboard/values?source_id='+encodeURIComponent(dataState.sourceId)+'&field='+encodeURIComponent(field));row.querySelector('.filter-value').innerHTML=option('','Все значения')+values.map(v=>option(v,v)).join('')}
  catch(e){row.querySelector('.filter-value').innerHTML=option('',e.message)}
}

function dashboardConfig(){
  const filters={};document.querySelectorAll('.filter-row').forEach(row=>{const value=row.querySelector('.filter-value').value;if(value)filters[row.querySelector('.filter-field').value]=value});
  return {source_id:dataState.sourceId,metric:$('#dash-metric').value,aggregation:$('#dash-aggregation').value,dimension:$('#dash-dimension').value||null,filters,date_field:$('#dash-date-field').value||null,date_from:$('#dash-date-from').value||null,date_to:$('#dash-date-to').value||null,limit:30};
}

async function runDashboard(){
  const root=$('#dashboard-result');root.innerHTML='<div class="loading">Считаем показатели…</div>';$('#dash-feedback').textContent='';
  try{
    const config=dashboardConfig();
    if(config.metric==='*'&&config.aggregation!=='count')throw Error('Для всех записей выберите операцию «Количество».');
    const [detail,total]=await Promise.all([api('/dashboard/query',{method:'POST',body:JSON.stringify(config)}),api('/dashboard/query',{method:'POST',body:JSON.stringify({...config,dimension:null})})]);
    const secondaryField=dataState.schema.columns.find(c=>c.name!==config.dimension&&c.name!==config.metric&&/product|category|currency|side|region|тип|категор|продукт|регион/i.test(c.name))?.name;
    const secondary=secondaryField?await api('/dashboard/query',{method:'POST',body:JSON.stringify({...config,dimension:secondaryField,limit:10})}):null;
    const rows=detail.rows;const totalValue=total.rows[0]?.value;
    root.innerHTML=`<div class="dashboard-summary"><div class="hero-kpi"><span>ОСНОВНОЙ ПОКАЗАТЕЛЬ</span><strong>${fmt(totalValue)}</strong><small>${esc(config.aggregation.toUpperCase())} · ${esc(config.metric)}</small></div><div class="hero-kpi accent"><span>ИСТОЧНИК</span><strong>${fmt(dataState.schema.records)}</strong><small>строк · ${esc(dataState.schema.name)}</small></div><div class="hero-kpi"><span>ФИЛЬТРЫ</span><strong>${Object.keys(config.filters).length}</strong><small>${esc(Object.entries(config.filters).map(([k,v])=>k+': '+v).join(', ')||'Все данные')}</small></div></div><div class="dashboard-grid"><div class="section-card chart-panel"><div class="section-title">${esc(config.metric)} ${config.dimension?'по '+esc(config.dimension):''}</div><div class="section-body" id="dashboard-chart"></div></div><div class="section-card"><div class="section-title">Данные графика</div><div class="table-scroll"><table class="data-table"><thead><tr><th>${esc(config.dimension||'Показатель')}</th><th>Значение</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.label)}</td><td>${fmt(r.value)}</td></tr>`).join('')}</tbody></table></div></div></div>`;
    renderDashboardChart($('#dashboard-chart'),rows,$('#dash-chart').value);
    if(secondary){root.querySelector('.dashboard-grid').insertAdjacentHTML('beforeend',`<div class="section-card chart-panel"><div class="section-title">Структура по ${esc(secondaryField)}</div><div class="section-body" id="dashboard-secondary"></div></div>`);renderDashboardChart($('#dashboard-secondary'),secondary.rows,'donut')}
  }catch(e){root.innerHTML=`<div class="error section-card section-body">${esc(e.message)}</div>`}
}

function renderDashboardChart(root,rows,type){
  const points=rows.filter(r=>Number.isFinite(r.value));
  if(!points.length){root.innerHTML='<div class="empty">Нет данных для выбранных условий.</div>';return}
  if(type==='donut'){
    const positive=points.filter(r=>r.value>0),sum=positive.reduce((a,b)=>a+b.value,0);
    if(!sum){root.innerHTML='<div class="empty">Кольцевая диаграмма требует положительных значений.</div>';return}
    let cursor=0;const palette=['#00a9e8','#16539a','#69d848','#75c8ee','#9b9fea','#ffb454','#bde6f7'];
    const stops=positive.map((r,i)=>{const start=cursor;cursor+=r.value/sum*100;return `${palette[i%palette.length]} ${start}% ${cursor}%`}).join(',');
    root.innerHTML=`<div class="donut-layout"><div class="donut" style="background:conic-gradient(${stops})"><span>${fmt(sum)}</span></div><div class="chart-legend">${positive.map((r,i)=>`<div><i style="background:${palette[i%palette.length]}"></i><span>${esc(r.label)}</span><b>${fmt(r.value)}</b></div>`).join('')}</div></div>`;return;
  }
  if(type==='line'){
    const ordered=[...points].sort((a,b)=>a.label.localeCompare(b.label));
    const values=ordered.map(r=>r.value),low=Math.min(...values,0),high=Math.max(...values,1),span=high-low||1;
    const coords=ordered.map((r,i)=>({x:50+(ordered.length===1?325:i*650/(ordered.length-1)),y:205-(r.value-low)/span*170,row:r}));
    const line=coords.map(p=>`${p.x},${p.y}`).join(' ');
    root.innerHTML=`<svg class="dash-line-chart" viewBox="0 0 750 245" role="img" aria-label="График динамики"><line x1="45" y1="205" x2="710" y2="205" stroke="#dceaf0"/><line x1="45" y1="35" x2="710" y2="35" stroke="#edf4f7"/><polyline points="${line}" fill="none" stroke="#00a9e8" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>${coords.map(p=>`<circle cx="${p.x}" cy="${p.y}" r="5" fill="#00a9e8"><title>${esc(p.row.label)}: ${esc(fmt(p.row.value))}</title></circle>`).join('')}<text x="45" y="229" fill="#79909d" font-size="11">${esc(ordered[0].label)}</text><text x="710" y="229" fill="#79909d" font-size="11" text-anchor="end">${esc(ordered.at(-1).label)}</text></svg>`;
    return;
  }
  const max=Math.max(...points.map(r=>Math.abs(r.value)),1);
  root.innerHTML=`<div class="dashboard-bars ${type==='line'?'line-bars':''}">${points.map(r=>`<div class="dashboard-bar-row" title="${esc(r.label)}: ${esc(fmt(r.value))}"><span>${esc(r.label)}</span><div class="bar-track"><div style="width:${Math.max(1,Math.abs(r.value)/max*100)}%"></div></div><b>${fmt(r.value)}</b></div>`).join('')}</div>`;
}

async function saveCurrentDashboard(){
  const name=prompt('Название дашборда',dataState.schema.name+' · '+$('#dash-metric').value);if(!name)return;
  try{await api('/dashboards',{method:'POST',body:JSON.stringify({name,config:dashboardConfig(),chart_type:$('#dash-chart').value})});$('#dash-feedback').textContent='Дашборд сохранён.';await refreshSavedDashboards()}
  catch(e){$('#dash-feedback').textContent=e.message}
}

async function refreshSavedDashboards(){
  const items=await api('/dashboards');const root=$('#saved-dashboards');
  root.innerHTML=items.length?`<div class="saved-dashboard-list">${items.map(x=>`<button class="source-choice" data-dashboard="${esc(x.id)}"><b>${esc(x.name)}</b><span>${esc(new Date(x.created_at).toLocaleString('ru-RU'))}</span></button>`).join('')}</div>`:'<div class="empty">Сохранённых дашбордов пока нет.</div>';
  root.querySelectorAll('[data-dashboard]').forEach(b=>b.onclick=async()=>{
    try{const saved=await api('/dashboards/'+b.dataset.dashboard);dataState.sourceId=saved.config.source_id;$('#dash-source').value=dataState.sourceId;await fillDashboardFields();$('#dash-metric').value=saved.config.metric;$('#dash-aggregation').value=saved.config.aggregation;$('#dash-dimension').value=saved.config.dimension||'';$('#dash-date-field').value=saved.config.date_field||'';$('#dash-date-from').value=saved.config.date_from||'';$('#dash-date-to').value=saved.config.date_to||'';$('#dash-chart').value=saved.chart_type;$('#dash-filters').innerHTML='';for(const [field,value] of Object.entries(saved.config.filters)){addDashboardFilter();const row=$('#dash-filters').lastElementChild;row.querySelector('.filter-field').value=field;await loadFilterValues(row);row.querySelector('.filter-value').value=String(value)}await runDashboard();$('#dashboard-result').scrollIntoView({behavior:'smooth'})}
    catch(e){alert(e.message)}
  });
}

$('#chat-source').onchange=()=>{dataState.sourceId=$('#chat-source').value;localStorage.setItem('t1_source',dataState.sourceId);state.conversation=null;$('#messages').innerHTML=welcomeHTML;refreshConversations()};
refreshSources().catch(()=>{});
