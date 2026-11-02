const $=(selector,root=document)=>root.querySelector(selector);
const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
const siteMode=$('meta[name="postscope-mode"]')?.content==='static';
const webMode=siteMode||$('meta[name="postscope-mode"]')?.content==='browser';
let browserClient;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf=new Intl.NumberFormat('ko-KR');
const fmt=value=>value==null?'계산 불가':nf.format(Math.round(value*10)/10);
const names={dashboard:'대시보드',records:'게시 기록',comparison:'기간 비교',quality:'데이터 품질',reports:'내보내기',settings:'설정'};
const types={text:'텍스트',document:'문서',photo:'사진',video:'영상',audio:'오디오',other:'기타',unknown:'미확인'};
const state={workspace:null,workspaces:[],analysis:null,applied:null,page:'dashboard',heat:'count',heatTable:false,cursor:0,history:[],upload:null,connection:null,authStage:'code',chatOffset:0,loading:0,poller:null,workspaceRequest:0,analysisRequest:0,recordsRequest:0,previewRequest:0,applying:false,previewing:false,importBusy:false};

async function api(path,options={}){
  if(webMode){const url=new URL(siteMode?'./static-api.js':'./browser-api.js',import.meta.url);url.search=new URL(import.meta.url).search;browserClient??=import(url.href);return (await browserClient).request(path,options)}
  const headers={'X-PostScope-Token':$('meta[name="postscope-token"]').content,...options.headers};
  if(options.body&&!(options.body instanceof FormData)){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body)}
  const response=await fetch(path,{...options,headers,credentials:'same-origin'});
  if(!response.ok){let data;try{data=await response.json()}catch{data={detail:'요청을 처리하지 못했습니다. 서버 연결을 확인하세요.'}}throw new Error(typeof data.detail==='string'?data.detail:'입력값을 확인하세요.')}
  return response.json();
}
function toast(message,kind=''){
  const node=document.createElement('div');node.className='toast '+kind;node.textContent=message;
  $('#toast-region').append(node);setTimeout(()=>node.remove(),kind==='error'?6500:3500);
}
function feedback(message,dialog=$('dialog[open]'),error=true){
  const region=dialog?.querySelector('[data-dialog-feedback]');
  if(!region){toast(message,error?'error':'success');return}
  region.textContent=message;region.classList.toggle('error',error);region.hidden=false;
}
function clearFeedback(dialog){const region=dialog.querySelector('[data-dialog-feedback]');if(region){region.hidden=true;region.textContent=''}}
async function action(button,fn){
  if(button?.disabled)return;
  const html=button?.innerHTML,minHeight=button?.style.minHeight;
  if(button){button.style.minHeight=button.getBoundingClientRect().height+'px';button.disabled=true;button.setAttribute('aria-busy','true');button.innerHTML='<span class="spinner small-spinner"></span><span>처리 중</span>'}
  try{return await fn()}catch(error){feedback(error.message,button?.closest('dialog'))}
  finally{if(button){button.innerHTML=html;button.style.minHeight=minHeight;button.disabled=false;button.removeAttribute('aria-busy');if(button.id==='collect-start')invalidateSample()}}
}
function loading(value){state.loading+=value?1:-1;$('#loading-overlay').hidden=state.loading<=0}
function option(value,label){return `<option value="${esc(value)}">${esc(label)}</option>`}
function duration(seconds){if(seconds==null)return '계산 불가';if(seconds<60)return `${fmt(seconds)}<small>초</small>`;if(seconds<3600)return `${fmt(seconds/60)}<small>분</small>`;return `${fmt(seconds/3600)}<small>시간</small>`}
function localDate(value,timezone='Asia/Seoul'){return new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value))}
function timestamp(value,zone){return value?new Intl.DateTimeFormat('ko-KR',{timeZone:zone||'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'—'}
function readFilters(){const f=new FormData($('#filters'));return {workspace_id:state.workspace.id,chat_key:f.get('chat_key'),sender:f.get('sender'),start:f.get('start'),end:f.get('end'),timezone:f.get('timezone'),content:f.get('content'),unit:f.get('unit'),include_forwarded:$('#filters [name="include_forwarded"]').checked,session_minutes:Number(f.get('session_minutes'))}}
function dirty(){
  const current=state.workspace?readFilters():null;
  const changed=!!(state.applied&&current&&Object.keys(current).some(key=>current[key]!==state.applied[key]));
  $('#dirty-indicator').hidden=!changed;
  const button=$('#apply-filters');button.disabled=state.applying||!changed;
  button.textContent=state.applying?'적용 중…':'적용';
  $('#page-dashboard').setAttribute('aria-busy',String(state.applying));
  for(const button of $$('[data-period]')){
    const expected=periodRange(button.dataset.period);
    const selected=!!expected&&current?.start===expected.start&&current?.end===expected.end;
    button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));
  }
}
function fillSenders(){
  const chat=$('#chat-select').value,current=$('#sender-select').value;
  $('#sender-select').innerHTML=option('all','전체 작성자')+state.workspace.senders.filter(x=>x.chat_key===chat).map(x=>option(x.sender_key,x.label+(x.kind==='signature'?' · 개인 식별 아님':''))).join('');
  if($$('#sender-select option').some(x=>x.value===current))$('#sender-select').value=current;
  const data=state.workspace.chats.find(x=>x.chat_key===chat);
  $('#album-option').disabled=!data?.album_supported;
  $('#unit-select').title=data?.album_supported?'':'앨범 식별자가 없는 대화입니다.';
  if(!data?.album_supported)$('#unit-select').value='messages';
}
function periodRange(period){
  const chat=state.workspace?.chats.find(x=>x.chat_key===$('#chat-select').value);
  if(!chat||(!chat.first_message&&!chat.period_start)||(!chat.last_message&&!chat.period_end))return null;
  const zone=$('#timezone-select').value||state.workspace.timezone;
  const first=chat.period_start||localDate(chat.first_message,zone),end=chat.period_end||localDate(chat.last_message,zone);
  if(period==='history')return {start:first,end};
  const start=period==='all'?(chat.analysis_start||first):new Date(new Date(end+'T12:00:00Z').getTime()-(Number(period)-1)*86400000).toISOString().slice(0,10);
  return {start:start<first?first:start,end};
}
function setPeriod(period){const range=periodRange(period);if(range){$('#filter-start').value=range.start;$('#filter-end').value=range.end}dirty()}
function resetFilters(){
  const chat=$('#chat-select').value;$('#filters').reset();$('#chat-select').value=chat;
  $('#timezone-select').value=state.workspace.timezone;fillSenders();setPeriod('all');dirty();
}
async function loadWorkspaces(preferred){
  state.workspaces=await api('/api/workspaces');
  if(!state.workspaces.length){const demo=await api('/api/demo',{method:'POST'});state.workspaces=await api('/api/workspaces');preferred=demo.workspace_id}
  $('#workspace-select').innerHTML=state.workspaces.map(x=>option(x.id,x.title+(x.is_demo?' · 데모':''))).join('');
  const id=preferred||localStorage.getItem('postscope-workspace');
  await selectWorkspace(state.workspaces.some(x=>x.id===id)?id:state.workspaces[0].id);
}
async function selectWorkspace(id){
  const request=++state.workspaceRequest;++state.analysisRequest;++state.recordsRequest;
  state.applying=false;
  const workspace=await api('/api/workspaces/'+encodeURIComponent(id));
  if(request!==state.workspaceRequest)return;
  state.workspace=workspace;state.analysis=null;state.applied=null;state.cursor=0;state.history=[];
  localStorage.setItem('postscope-workspace',id);$('#workspace-select').value=id;
  $('#workspace-name').textContent=siteMode?(workspace.chats[0]?.title||workspace.title):workspace.title;$('#demo-banner').hidden=!workspace.is_demo;
  $('#chat-select').innerHTML=workspace.chats.map(x=>option(x.chat_key,x.title)).join('');
  $('.room-field').hidden=siteMode;
  $('[data-period="history"]').hidden=!workspace.chats.some(chat=>chat.analysis_start&&chat.analysis_start!==chat.period_start);
  $('#advanced-filters').open=false;
  $('#filters').reset();$('#timezone-select').value=workspace.timezone;fillSenders();
  for(const id of ['metric-total','metric-days','metric-interval','metric-peak'])$('#'+id).textContent='—';
  $('#dashboard-charts').hidden=true;$('#no-results').hidden=true;$('#days-footnote').hidden=true;$('#peak-footnote').textContent='';$('#analysis-range').textContent='';$('#snapshot-time').textContent='';$('#quality-brief').textContent='';$('#coverage-status').textContent='확인 중';$('#records-body').innerHTML='';
  navigate(state.page);
  if(workspace.chats.length){setPeriod(workspace.is_demo?'30':'all');if(workspace.is_demo)$('#filter-start').value='2026-10-01';await applyFilters()}
  else{$('#record-badge').textContent='0';$('#quality-details').innerHTML='';dirty()}
  if(request===state.workspaceRequest)renderQuality();
}
async function applyFilters(extra={},base=null){
  if(!state.workspace?.chats.length)return;
  const config={...(base||readFilters()),...extra},request=++state.analysisRequest;
  if(!config.start||!config.end){toast('시작일과 종료일을 입력하세요.','error');return}
  if(config.start>config.end){toast('시작일이 종료일보다 늦습니다.','error');return}
  state.applying=true;dirty();
  try{
    const result=await api('/api/analyses',{method:'POST',body:config});
    if(request!==state.analysisRequest||state.workspace.id!==config.workspace_id)return;
    state.analysis=result;state.applied=config;state.cursor=0;state.history=[];
    renderDashboard();renderQuality();renderComparison();renderReport();
    $('#header-export').disabled=false;
    if(state.page==='records')await loadRecords();
  }catch(error){if(request===state.analysisRequest)toast(error.message,'error')}
  finally{if(request===state.analysisRequest){state.applying=false;dirty()}}
}

const plotBase={paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'Nanum, sans-serif',size:11,color:'#718099'},margin:{t:10,r:12,b:30,l:38},xaxis:{showgrid:false,zeroline:false,tickfont:{size:10},tickcolor:'transparent',fixedrange:false},yaxis:{gridcolor:'#f0f3f8',zeroline:false,rangemode:'tozero',tickfont:{size:10}},showlegend:false,hovermode:'x unified',hoverlabel:{bgcolor:'white',bordercolor:'#e2e9f7',font:{family:'Nanum',size:10,color:'#516584'}},bargap:.55};
const plotOptions={responsive:true,displayModeBar:false,scrollZoom:false};
function plot(id,traces,layout={}){Plotly.react(id,traces,{...plotBase,...layout},plotOptions)}
function renderDashboard(){
  const d=state.analysis;if(!d)return;const m=d.metrics,unit=d.config.unit==='messages'?'건':'묶음';
  $('#metric-total').innerHTML=`${nf.format(m.total)}<small>${unit}</small>`;
  $('#metric-days').innerHTML=`${nf.format(m.active_days)}<small>일</small>`;
  $('#metric-interval').innerHTML=duration(m.median_interval);
  $('#metric-peak').innerHTML=m.peak_hours.length?`${m.peak_hours[0]}:00<small>${m.peak_hours.length>1?`외 ${m.peak_hours.length-1}개 동률`:'– '+((m.peak_hours[0]+1)%24)+':00'}</small>`:'계산 불가';
  $('#metric-peak').title=m.peak_hours.map(hour=>hour+'시').join(' · ');
  $('#peak-footnote').textContent=m.peak_hour_share==null?'비중 계산 불가':`전체 게시의 ${(m.peak_hour_share*100).toFixed(1)}% · ${d.config.timezone}`;
  $('#days-footnote').hidden=m.daily_average==null;$('#days-footnote').textContent=m.daily_average==null?'':`하루 평균 ${fmt(m.daily_average)}${unit}`;
  $('#record-badge').textContent=nf.format(m.total);
  $('#analysis-range').textContent=d.config.start.replaceAll('-','.')+' – '+d.config.end.replaceAll('-','.');
  $('#snapshot-time').textContent=(siteMode?'갱신 ':'')+timestamp(siteMode?state.workspace.created_at:d.created_at,d.config.timezone);$('#applied-context').textContent=`${d.config.timezone} · ${d.config.unit==='messages'?'메시지':'게시 묶음'}`;
  const range=periodRange('all'),summary=[];
  summary.push(d.config.start===range?.start&&d.config.end===range?.end?'전체 기간':`${d.config.start.replaceAll('-','.')} – ${d.config.end.replaceAll('-','.')}`);
  if(d.config.sender!=='all')summary.push(d.sender_label||'작성자 선택');
  if(d.config.content!=='all')summary.push(types[d.config.content]||(d.config.content==='pdf'?'PDF':d.config.content));
  if(d.config.unit!=='messages')summary.push('게시 묶음');
  if(!d.config.include_forwarded)summary.push('전달 제외');
  $('#filter-summary').textContent=summary.join(' · ');
  $('#coverage-status').textContent=d.quality.coverage;$('#coverage-dot').style.background=m.full_coverage?'#27b58a':'#cda25c';
  $('#coverage-status').style.color=m.full_coverage?'#5c967e':'#b49361';
  $('#quality-brief').textContent=`작성자 확인 ${d.quality.identified_ratio==null?'—':(d.quality.identified_ratio*100).toFixed(1)+'%'}${d.quality.quarantined?' · 제외 '+nf.format(d.quality.quarantined)+'건':''}${d.small_sample?' · 적은 표본':''}`;
  $('#session-count').textContent=nf.format(m.session_count)+'개';$('#session-size').textContent=fmt(m.session_median_size)+(m.session_median_size==null?'':'건');
  $('#daily-caption').hidden=siteMode||m.full_coverage;$('#daily-caption').textContent='범위 미확인 구간은 0건을 뜻하지 않습니다.';$('#no-results').hidden=m.total!==0;$('#dashboard-charts').hidden=m.total===0;
  $('#daily-chart').setAttribute('aria-description',m.full_coverage?'':'범위 미확인 구간은 0건을 뜻하지 않습니다.');
  $('#hours-caption').textContent=d.config.timezone;
  const daily=d.daily,tooltip=`%{x}<br>%{y} ${unit}<br>${d.config.timezone}<extra></extra>`;
  plot('daily-chart',[{x:daily.map(x=>x.date),y:daily.map(x=>x.status==='unknown'&&x.count===0?null:x.count),type:'scatter',mode:'lines',name:'관측 게시량',line:{color:'#5077ed',width:2.4,shape:'linear'},fill:'tozeroy',fillcolor:'rgba(75,113,230,.065)',hovertemplate:tooltip},{x:daily.map(x=>x.date),y:daily.map(x=>x.moving_average),type:'scatter',mode:'lines',name:'7일 이동평균',connectgaps:false,line:{color:'#6bbeb5',width:1.5,dash:'dot'},hovertemplate:`7일 평균 %{y:.1f} ${unit}<extra></extra>`}],{xaxis:{...plotBase.xaxis,type:'date',nticks:9,tickformat:siteMode?'%y.%m':'%m.%d'},yaxis:{...plotBase.yaxis,nticks:5}});
  renderWeekday();
  plot('hourly-chart',[{x:Array.from({length:24},(_,i)=>i),y:d.hours,type:'bar',marker:{color:d.hours.map((_,h)=>m.peak_hours.includes(h)?'#4168e9':'#b6c9f9'),cornerradius:3},hovertemplate:`%{x}시 · ${d.config.timezone}<br>%{y} ${unit}<extra></extra>`}],{xaxis:{...plotBase.xaxis,tickmode:'linear',dtick:3,ticksuffix:'시'},bargap:.35});
  plot('interval-chart',[{x:['1분 미만','1~5분','5~10분','10분~1시간','1~6시간','6시간 이상'],y:d.interval_histogram,type:'bar',marker:{color:'#9cb7f4',cornerradius:4},hovertemplate:'%{x}<br>%{y}개 관측 간격<extra></extra>'}],{bargap:.55});
  plot('types-chart',[{x:Object.keys(d.types).map(t=>types[t]||(t==='mixed'?'복합 유형':t)),y:Object.values(d.types),type:'bar',marker:{color:'#87a6f1',cornerradius:4},hovertemplate:'%{x}<br>%{y}건<extra></extra>'}]);
  plot('sessions-chart',[{x:['0초','5분 미만','5~10분','10~30분','30~60분','1시간 이상'],y:d.session_length_histogram||[0,0,0,0,0,0],type:'bar',marker:{color:'#a59bdc',cornerradius:4},hovertemplate:'%{x}<br>%{y}묶음<extra></extra>'}]);
  $('#sessions-caption').textContent=`인접 간격 ${d.config.session_minutes}분 이하`;
  renderHeat();
  setTimeout(resizeCharts,100);
}
function renderWeekday(){const d=state.analysis;if(!d)return;$('#weekday-mode option[value="average"]').disabled=!d.metrics.full_coverage;if(!d.metrics.full_coverage)$('#weekday-mode').value='count';const average=$('#weekday-mode').value==='average';plot('weekday-chart',[{x:['월','화','수','목','금','토','일'],y:average?d.weekday_average:d.weekday,type:'bar',marker:{color:d.weekday.map(v=>v===Math.max(...d.weekday)?'#5379ed':'#c2d2f8'),cornerradius:4},hovertemplate:`%{x}요일 · ${d.config.timezone}<br>%{y} ${average?'건/확인된 날짜':'건'}<extra></extra>`}],{margin:{t:15,r:5,b:28,l:30},bargap:.58,annotations:average&&!d.metrics.full_coverage?[{text:'조회 범위 미확인 · 평균 계산 제한',showarrow:false,xref:'paper',yref:'paper',x:.5,y:.5,font:{size:10,color:'#a2adbf'}}]:[]})}
function renderHeat(){
  const d=state.analysis;if(!d)return;
  if(state.heat==='rate'&&d.metrics.observed_hours===0)state.heat='count';
  const rate=state.heat==='rate',cells=rate?d.rates:d.heatmap;
  const maximum=Math.max(1,...cells.flat().filter(v=>v!=null));
  $$('[data-heat]').forEach(button=>{
    const selected=button.dataset.heat==='table'?state.heatTable:button.dataset.heat===state.heat;
    button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));
    if(button.dataset.heat==='rate'){button.disabled=d.metrics.observed_hours===0;button.title=button.disabled?'조회 범위가 확인되어야 계산할 수 있습니다.':''}
  });
  let html='<span></span>'+Array.from({length:24},(_,h)=>`<span class="heat-label">${h%3===0?String(h).padStart(2,'0'):''}</span>`).join('');
  for(let w=0;w<7;w++){
    html+=`<span class="heat-label">${['월','화','수','목','금','토','일'][w]}</span>`;
    for(let h=0;h<24;h++){
      const value=cells[w][h],known=d.exposure[w][h]>0,intensity=value==null?0:value/maximum;
      const color=`hsl(223 ${Math.round(60+intensity*16)}% ${Math.round(96-intensity*41)}%)`;
      const label=`${['월','화','수','목','금','토','일'][w]} ${h}–${h+1}시 · ${d.config.timezone} · ${value==null?'계산 불가':fmt(value)+(rate?'건/시간':'건')} · 관측 ${fmt(d.exposure[w][h])}시간${!known?' · 범위 미확인':''}`;
      html+=`<button type="button" class="heat-cell ${known?'':'unknown'}" style="background-color:${color}" data-label="${esc(label)}" aria-label="${esc(label)}" title="${esc(label)}"></button>`;
    }
  }
  $('#heatmap').innerHTML=html;$('#heat-scroll').hidden=state.heatTable;$('#heat-table').hidden=!state.heatTable;
  $('#heat-subtitle').hidden=siteMode||d.metrics.full_coverage;$('#heat-subtitle').textContent=rate?'확인된 관측 시간으로 계산합니다.':'사선: 조회 범위 미확인';
  $('#heatmap').setAttribute('aria-description',d.metrics.full_coverage?'':'사선: 조회 범위 미확인');
  if(state.heatTable)$('#heat-table').innerHTML='<table><caption>'+esc(rate?'시간당 게시율 (건/시간)':'관측 게시량 (건)')+'</caption><thead><tr><th>요일 / 시</th>'+Array.from({length:24},(_,h)=>`<th>${h}</th>`).join('')+'</tr></thead><tbody>'+cells.map((row,w)=>`<tr><th>${['월','화','수','목','금','토','일'][w]}</th>${row.map((value,h)=>`<td>${value==null?'—':fmt(value)}${!rate&&!d.exposure[w][h]?' *':''}</td>`).join('')}</tr>`).join('')+'</tbody></table>';
}
function resizeCharts(){$$('.plot').filter(x=>x.offsetParent&&x.data).forEach(x=>Plotly.Plots.resize(x))}
function setMenu(open){
  $('#sidebar').classList.toggle('open',open);$('#menu-backdrop').hidden=!open;
  $('#menu-toggle').setAttribute('aria-expanded',String(open));document.body.classList.toggle('menu-open',open);
}

function navigate(page){
  if(siteMode&&page==='settings')page='quality';
  state.page=names[page]?page:'dashboard';
  const hasChat=!!state.workspace?.chats.length;
  $$('.page-panel').forEach(panel=>panel.hidden=panel.id!=='page-'+state.page||(!hasChat&&['dashboard','records','comparison','reports'].includes(state.page)));
  $$('.nav-link').forEach(link=>{const active=link.dataset.page===state.page;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current')});
  $('#breadcrumb-page').textContent=names[state.page];$('#page-title').textContent=names[state.page];
  $('#filter-section').hidden=!hasChat||!['dashboard','records','comparison'].includes(state.page);
  $('#quality-strip').hidden=siteMode||!hasChat||state.page==='settings';
  $('#empty-state').hidden=hasChat||['settings','quality'].includes(state.page);
  $('#header-export').disabled=!state.analysis;
  setMenu(false);
  if(state.page==='records'&&state.analysis)loadRecords().catch(e=>toast(e.message,'error'));
  if(state.page==='quality')refreshQuality().catch(e=>toast(e.message,'error'));
  if(state.page==='settings')loadSettings().catch(e=>toast(e.message,'error'));
  if(state.page==='reports')renderReport();
  if(state.page==='comparison')renderComparison();
  if(state.page==='dashboard')setTimeout(resizeCharts,50);
}
async function loadRecords(){
  if(!state.analysis)return;
  const analysis=state.analysis,request=++state.recordsRequest,cursor=state.cursor;
  const data=await api(`/api/analyses/${analysis.id}/records?cursor=${cursor}&limit=50`);
  if(request!==state.recordsRequest||state.analysis?.id!==analysis.id)return;
  $('#records-body').innerHTML=data.items.length?data.items.map(row=>`<tr><td>${esc(row.message_id)}</td><td><span class="badge">${esc(row.sender_label)}</span>${row.sender_kind==='signature'?'<small> 개인 식별 아님</small>':''}</td><td>${esc(timestamp(row.sent_at_utc,analysis.config.timezone))}</td><td>${esc(types[row.content_kind]||row.content_kind)}</td><td>${row.members}건</td><td>${row.is_forwarded?'전달':'—'}</td></tr>`).join(''):'<tr><td colspan="6" class="table-empty">조건에 맞는 기록이 없습니다.</td></tr>';
  $('#records-total').textContent=nf.format(data.total)+'건';
  $('#records-position').textContent=`${data.total?cursor+1:0}–${Math.min(cursor+50,data.total)} / ${nf.format(data.total)}`;
  $('#records-prev').disabled=!state.history.length;$('#records-next').disabled=data.next_cursor==null;$('#records-next').dataset.cursor=data.next_cursor??'';
}
async function refreshQuality(){
  if(!state.workspace)return;
  const id=state.workspace.id,workspace=await api('/api/workspaces/'+state.workspace.id);
  if(state.workspace?.id===id){state.workspace=workspace;renderQuality()}
}
function renderQuality(){
  const data=state.analysis;
  if(data){
    const q=data.quality;
    $('#quality-badge').textContent=q.coverage;
    const pairs=[['저장된 메시지',fmt(q.total_stored)+'건'],['작성자 확인',q.identified_ratio==null?'—':(q.identified_ratio*100).toFixed(1)+'%'],['서비스 이벤트 제외',fmt(q.service_excluded)+'건'],['서명만 있는 비율',q.signature_ratio==null?'—':(q.signature_ratio*100).toFixed(1)+'%'],['중복 / 제외',fmt(q.duplicates)+' / '+fmt(q.quarantined)+'건'],['필드 충돌',fmt(q.conflicts)+'건'],['앨범 식별자',q.album_supported?'있음':'없음'],['불완전 앨범 제외',fmt(data.incomplete_albums)+'묶음'],['첫 기록',timestamp(q.first_message,data.config.timezone)],['마지막 기록',timestamp(q.last_message,data.config.timezone)],['마지막 갱신',timestamp(q.last_updated,data.config.timezone)],['확인된 조회 시간',fmt(data.metrics.observed_hours)+'시간']];
    $('#quality-details').innerHTML='<div class="detail-grid">'+pairs.map(([key,value])=>`<div class="detail-item"><span>${esc(key)}</span><b>${esc(value)}</b></div>`).join('')+'</div>';
  }else{$('#quality-details').innerHTML='<p class="muted">'+(state.workspace?.connection_required?'실제 Telegram 채널을 연결해야 수집이 시작됩니다.':'아직 가져온 기록이 없습니다.')+'</p>';$('#quality-badge').textContent=state.workspace?.connection_required?'연결 대기':'데이터 없음'}
  const statuses={queued:'대기',running:'수집 중',waiting:'재시도 대기',paused:'일시정지',completed:'완료',failed:'실패',cancelled:'취소'};
  $('#jobs-list').innerHTML=(state.workspace?.jobs||[]).map(job=>`<div class="job-item"><span class="badge">${esc(statuses[job.state]||job.state)}</span><div class="job-info">${job.kind==='json'?'JSON':'API'} · ${nf.format(job.processed)}건<small>${esc(job.error||'')}${job.next_retry_at?' · 재개 '+esc(timestamp(job.next_retry_at)):''}</small>${job.stats.same_file?'<small>이미 가져온 파일</small>':''}</div>${job.kind==='api'&&!['completed','cancelled','failed'].includes(job.state)?`<button class="button" data-job="${job.id}" data-action="${job.state==='paused'||!job.worker_active?'resume':'pause'}">${job.state==='paused'||!job.worker_active?'재개':'일시정지'}</button><button class="button" data-job="${job.id}" data-action="cancel">취소</button>`:''}</div>`).join('')||'<p class="muted small">작업 없음</p>';
  const rows=state.workspace?.quarantine||[];
  $('#quarantine-list').innerHTML=rows.length?'<table><thead><tr><th>행</th><th>제외 사유</th></tr></thead><tbody>'+rows.map(row=>`<tr><td>${row.row_number}</td><td>${esc(row.reason)}</td></tr>`).join('')+'</tbody></table>':'<p class="muted small">제외한 행 없음</p>';
}
function renderComparison(){
  const data=state.analysis;
  $('#comparison-current').textContent=data?`현재 ${data.config.start} – ${data.config.end}`:'';
  if(!data?.comparison){
    $('#comparison-result').innerHTML='<p class="muted">비교할 기준 기간을 선택하세요.</p>';
    if(data){const start=new Date(data.config.start+'T12:00:00Z'),end=new Date(data.config.end+'T12:00:00Z'),length=Math.round((end-start)/86400000)+1;$('#compare-end').value=new Date(start-86400000).toISOString().slice(0,10);$('#compare-start').value=new Date(start-length*86400000).toISOString().slice(0,10)}
    return;
  }
  const comparison=data.comparison;
  $('#comparison-result').innerHTML=`<div class="comparison-metrics"><div><span>기준 기간</span><strong>${nf.format(comparison.baseline.total)}</strong></div><div><span>현재 기간</span><strong>${nf.format(data.metrics.total)}</strong></div><div><span>차이</span><strong>${comparison.difference>0?'+':''}${nf.format(comparison.difference)}</strong></div></div><div class="comparison-notes"><p>증감률 ${comparison.percent==null?'계산 불가 (기준 0건)':comparison.percent.toFixed(1)+'%'}</p>${comparison.same_length?'':'<p>기간 길이가 다릅니다.</p>'}<p>${data.metrics.full_coverage&&comparison.baseline.full_coverage?`시간당 게시율 ${fmt(comparison.baseline.hourly_rate)} → ${fmt(data.metrics.hourly_rate)}건`:'범위 미확인 · 게시율 비교 불가'}</p>${data.small_sample?'<p>적은 표본</p>':''}</div>`;
}
function renderReport(){
  const data=state.analysis;
  $('#report-summary').innerHTML=data?`<b>${esc(data.chat_title)}</b><span>${nf.format(data.metrics.total)}${data.config.unit==='messages'?'건':'묶음'} · ${esc(data.sender_label||'전체 작성자')}</span><span>${esc(data.config.start)} – ${esc(data.config.end)} · ${esc(data.config.timezone)}</span><span class="small">${esc(data.quality.coverage)} · ${esc(timestamp(data.created_at,data.config.timezone))} 기준</span>`:'먼저 분석을 실행하세요.';
}
async function exportReport(format,button){
  if(!state.analysis)return;
  const analysisId=state.analysis.id;
  await action(button,async()=>{
    const data=await api('/api/reports',{method:'POST',body:{analysis_id:analysisId,format,include_ids:$('#include-ids').checked}});
    const link=document.createElement('a');link.href=data.download_url;link.download=data.filename;document.body.append(link);link.click();link.remove();toast('다운로드 시작','success');
  });
}
async function loadSettings(){
  const settings=await api('/api/settings');$('#retention-days').value=settings.retention_days;
  const connections=await api('/api/connections');
  const labels={connected:'연결됨',code:'코드 확인 중',password:'2단계 인증 중',expired:'만료됨'};
  $('#connection-status').innerHTML=connections.length?connections.map(connection=>`<div class="job-item"><span class="badge">${esc(labels[connection.state]||connection.state)}</span><button class="button" data-disconnect="${esc(connection.id)}">연결 해제</button></div>`).join(''):'<p class="muted small">연결된 계정 없음</p>';
}
async function createWorkspace(title){const data=await api('/api/workspaces',{method:'POST',body:{title,timezone:'Asia/Seoul'}});await loadWorkspaces(data.id);return data}
async function ensureImportWorkspace(){if(!state.workspace||state.workspace.is_demo)await createWorkspace('내 게시 기록');return state.workspace.id}
function syncImportButton(){
  const enabled=!!state.upload&&$('#accept-partial').checked&&!state.previewing&&!state.importBusy;
  $('#import-submit').disabled=!enabled;
}
async function openImport(tab='json'){
  clearFeedback($('#import-dialog'));$('#import-progress').hidden=true;
  $('#import-dialog').showModal();switchImportTab(tab);syncImportButton();
}
function switchImportTab(tab){
  $$('[data-import-tab]').forEach(button=>{const selected=button.dataset.importTab===tab;button.classList.toggle('selected',selected);button.setAttribute('aria-selected',String(selected))});
  $('#json-import-panel').hidden=tab!=='json';$('#api-import-panel').hidden=tab!=='api';
  clearFeedback($('#import-dialog'));
  if(webMode&&tab==='api'){
    $('#api-import-panel').innerHTML='<div class="notice">계정 연결·수집은 로컬 앱에서 사용할 수 있습니다.</div><a class="button primary full-width" href="https://github.com/hwanghj09/postscope/releases/latest" target="_blank" rel="noopener noreferrer">로컬 앱 다운로드 ↗</a>';
    return;
  }
  if(tab==='api'){
    const now=new Date();if(!$('#api-end').value)$('#api-end').value=localDate(now.toISOString());if(!$('#api-start').value)$('#api-start').value=localDate(new Date(now-7*86400000).toISOString());
  }
}
async function previewFile(file){
  if(!file)return;
  const request=++state.previewRequest;state.upload=null;state.previewing=true;$('#import-preview').hidden=true;syncImportButton();clearFeedback($('#import-dialog'));
  $('#import-progress').hidden=false;$('#import-progress').textContent='파일 확인 중…';
  try{
    if(!file.name.toLowerCase().endsWith('.json'))throw new Error('JSON 파일을 선택하세요.');
    if(file.size>100*1024*1024)throw new Error('100 MiB 이하의 JSON 파일을 선택하세요.');
    $('#import-progress').textContent='파일 확인 중…';
    const form=new FormData();form.append('file',file);
    const upload=await api('/api/imports/preview',{method:'POST',body:form});
    if(request!==state.previewRequest)return;
    state.upload=upload;$('#file-summary').textContent=`${upload.filename} · ${(upload.bytes/1024).toFixed(1)} KiB`;
    $('#import-chat').innerHTML=upload.schema.chats.map(chat=>option(chat.index,`${chat.name||'이름 없는 대화'} · ${nf.format(chat.count||0)}건`)).join('');
    $('#accept-partial').checked=false;$('#import-preview').hidden=false;
  }catch(error){if(request===state.previewRequest)feedback(error.message,$('#import-dialog'))}
  finally{if(request===state.previewRequest){state.previewing=false;$('#import-progress').hidden=true;syncImportButton()}}
}
async function pollJob(id){
  const job=await api('/api/jobs/'+id),stats=job.stats||{},workspaceId=job.workspace_id;
  const statuses={queued:'대기',running:'처리 중',waiting:'재시도 대기',paused:'일시정지',completed:'완료',failed:'실패',cancelled:'취소'};
  $('#import-progress').hidden=false;$('#import-progress').textContent=`${statuses[job.state]||job.state} · ${nf.format(job.processed)}건${job.next_retry_at?' · '+timestamp(job.next_retry_at)+' 재개':''}`;
  if(state.workspace?.id===workspaceId)await refreshQuality();
  if(['completed','failed','cancelled','paused'].includes(job.state)){
    clearInterval(state.poller);state.poller=null;
    if(job.state==='completed'){
      toast(stats.same_file?'이미 가져온 파일입니다.':`가져오기 완료 · ${nf.format(stats.inserted||0)}건 추가${stats.quarantined?' · '+nf.format(stats.quarantined)+'건 제외':''}`,'success');
      if(state.workspace?.id===workspaceId){await selectWorkspace(workspaceId);$('#import-dialog').close();location.hash=stats.quarantined?'quality':'dashboard';navigate(location.hash.slice(1))}
    }else if(job.state==='failed')feedback(job.error||'가져오기에 실패했습니다.',$('#import-dialog'));
  }
}
function startPolling(id){
  if(state.poller)clearInterval(state.poller);
  let busy=false;
  const poll=async()=>{if(busy)return;busy=true;try{await pollJob(id)}catch(error){clearInterval(state.poller);state.poller=null;toast(error.message,'error')}finally{busy=false}};
  state.poller=setInterval(poll,2000);poll();
}

$('#workspace-select').addEventListener('change',event=>selectWorkspace(event.target.value).catch(error=>toast(error.message,'error')));
$('#filters').addEventListener('submit',event=>{event.preventDefault();if(!state.applying)applyFilters()});
$('#filters').addEventListener('input',dirty);$('#filters').addEventListener('change',dirty);
$('#chat-select').addEventListener('change',()=>{fillSenders();setPeriod('all');dirty()});
$('#timezone-select').addEventListener('change',dirty);
$('#reset-filters').onclick=resetFilters;
$$('[data-period]').forEach(button=>button.onclick=()=>setPeriod(button.dataset.period));
$('#clear-empty-filters').onclick=()=>{resetFilters();applyFilters()};
$('#more-charts').addEventListener('toggle',()=>{if($('#more-charts').open)setTimeout(resizeCharts,0)});
$('#quality-link').onclick=()=>location.hash='quality';$('#header-export').onclick=()=>location.hash='reports';
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)||'dashboard'));
window.addEventListener('resize',resizeCharts);
$('#menu-toggle').onclick=()=>setMenu(!$('#sidebar').classList.contains('open'));
$('#menu-backdrop').onclick=()=>{setMenu(false);$('#menu-toggle').focus()};
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('#sidebar').classList.contains('open')){setMenu(false);$('#menu-toggle').focus()}});
$('#weekday-mode').onchange=renderWeekday;
$$('[data-heat]').forEach(button=>button.onclick=()=>{if(button.dataset.heat==='table')state.heatTable=!state.heatTable;else state.heat=button.dataset.heat;renderHeat()});
for(const type of ['focusin','mouseover'])$('#heatmap').addEventListener(type,event=>{if(event.target.dataset.label)$('#heat-value').textContent=event.target.dataset.label});
$('#heatmap').addEventListener('keydown',event=>{const cells=$$('.heat-cell'),index=cells.indexOf(document.activeElement),offset={ArrowLeft:-1,ArrowRight:1,ArrowUp:-24,ArrowDown:24}[event.key];if(offset!=null&&index>=0){event.preventDefault();cells[Math.max(0,Math.min(cells.length-1,index+offset))].focus()}});
$('#records-next').onclick=()=>{state.history.push(state.cursor);state.cursor=Number($('#records-next').dataset.cursor);loadRecords().catch(error=>toast(error.message,'error'))};
$('#records-prev').onclick=()=>{state.cursor=state.history.pop()||0;loadRecords().catch(error=>toast(error.message,'error'))};
$('#comparison-form').onsubmit=event=>{event.preventDefault();if(state.applied)applyFilters({compare_start:$('#compare-start').value,compare_end:$('#compare-end').value},state.applied)};
$('#compare-clear').onclick=()=>{if(state.applied){const {compare_start,compare_end,...config}=state.applied;applyFilters({},config)}};
$$('[data-export]').forEach(button=>button.onclick=()=>exportReport(button.dataset.export,button));
$('#new-workspace').onclick=()=>{clearFeedback($('#workspace-dialog'));$('#workspace-dialog').showModal()};
$('#start-own').onclick=()=>openImport();
$('#load-demo').onclick=()=>action($('#load-demo'),async()=>{const data=await api('/api/demo',{method:'POST'});await loadWorkspaces(data.workspace_id);location.hash='dashboard'});
$('#workspace-form').onsubmit=event=>{event.preventDefault();clearFeedback($('#workspace-dialog'));action($('#workspace-form button'),async()=>{await createWorkspace(new FormData(event.target).get('title'));$('#workspace-dialog').close();event.target.reset();location.hash='dashboard'})};
$$('.close-dialog').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$('#import-dialog').addEventListener('close',()=>{++state.previewRequest;state.previewing=false;syncImportButton()});
$$('[data-import]').forEach(button=>button.onclick=()=>openImport());
$$('[data-import-tab]').forEach(button=>button.onclick=()=>switchImportTab(button.dataset.importTab));
$('#json-file').onchange=event=>previewFile(event.target.files[0]);
for(const eventName of ['dragover','dragenter'])$('#dropzone').addEventListener(eventName,event=>{event.preventDefault();$('#dropzone').classList.add('dragover')});
$('#dropzone').addEventListener('dragleave',()=>$('#dropzone').classList.remove('dragover'));
$('#dropzone').addEventListener('drop',event=>{event.preventDefault();$('#dropzone').classList.remove('dragover');previewFile(event.dataTransfer.files[0])});
$('#confirm-range').onchange=event=>$('#import-range').hidden=!event.target.checked;
$('#accept-partial').onchange=syncImportButton;
$('#import-submit').onclick=async()=>{
  state.importBusy=true;
  await action($('#import-submit'),async()=>{
    if(!state.upload)throw new Error('JSON 파일을 선택하세요.');
    clearFeedback($('#import-dialog'));
    const confirmed=$('#confirm-range').checked;
    if(confirmed&&(!$('#import-start').value||!$('#import-end').value||!$('#source-timezone').value))throw new Error('원본 시간대와 확인한 기간을 입력하세요.');
    const selection={upload_id:state.upload.id,chat_index:Number($('#import-chat').value),source_timezone:$('#source-timezone').value||null,confirmed_start:confirmed?$('#import-start').value:null,confirmed_end:confirmed?$('#import-end').value:null,accept_partial:$('#accept-partial').checked};
    const workspaceId=await ensureImportWorkspace();
    const job=await api('/api/imports',{method:'POST',body:{...selection,workspace_id:workspaceId}});
    $('#import-preview').hidden=true;state.upload=null;startPolling(job.id);
  });
  state.importBusy=false;syncImportButton();
};
$('#login-form').onsubmit=event=>{
  event.preventDefault();clearFeedback($('#import-dialog'));
  action($('#login-form button'),async()=>{
    const form=new FormData(event.target);
    try{const connection=await api('/api/connections/login',{method:'POST',body:{api_id:Number(form.get('api_id')),api_hash:form.get('api_hash'),phone:form.get('phone')}});state.connection=connection.id;state.authStage='code';$('#login-form').hidden=true;$('#verify-form').hidden=false;$('#verify-label').innerHTML='로그인 코드<input name="credential" type="text" autocomplete="one-time-code" required>';$('#verify-form input').focus()}
    finally{event.target.reset()}
  });
};
$('#verify-form').onsubmit=event=>{
  event.preventDefault();clearFeedback($('#import-dialog'));
  action($('#verify-form button'),async()=>{
    const input=$('#verify-form input'),credential=input.value;input.value='';
    const data=await api('/api/connections/verify',{method:'POST',body:{connection_id:state.connection,[state.authStage==='password'?'password':'code']:credential}});
    if(data.state==='password'){state.authStage='password';$('#verify-label').innerHTML='2단계 인증 비밀번호<input name="credential" type="password" autocomplete="off" required>';$('#verify-form input').focus()}
    else{$('#verify-form').hidden=true;$('#api-collection').hidden=false;await fetchChats()}
  });
};
async function fetchChats(append=false){
  const connectionId=state.connection,data=await api(`/api/chats?connection_id=${encodeURIComponent(connectionId)}&offset=${append?state.chatOffset:0}`);
  if(state.connection!==connectionId)return;
  if(!append){$('#api-chat').innerHTML='';state.chatOffset=0}
  $('#api-chat').insertAdjacentHTML('beforeend',data.items.map(chat=>option(chat.chat_key,chat.title)).join(''));
  state.chatOffset+=data.items.length;$('#more-chats').hidden=!data.has_more;invalidateSample();
}
$('#fetch-chats').onclick=()=>action($('#fetch-chats'),()=>fetchChats());$('#more-chats').onclick=()=>action($('#more-chats'),()=>fetchChats(true));
function collectionConfig(){return {workspace_id:state.workspace.id,connection_id:state.connection,chat_key:$('#api-chat').value,start:$('#api-start').value,end:$('#api-end').value,timezone:$('#api-timezone').value}}
function invalidateSample(){$('#collect-start').disabled=true;$('#sample-result').hidden=true}
$('#sample-check').onclick=()=>action($('#sample-check'),async()=>{
  const config=collectionConfig(),sample=await api('/api/collections/sample',{method:'POST',body:config});
  if(JSON.stringify(config)!==JSON.stringify(collectionConfig()))return;
  $('#sample-result').hidden=false;$('#sample-result').textContent=`표본 ${sample.inspected}건 · 기간 내 ${sample.sample_count}건 · 작성자 확인 ${sample.identified}건`;
  $('#collect-start').disabled=false;
});
$('#api-collection').addEventListener('change',invalidateSample);
$('#collect-start').onclick=()=>action($('#collect-start'),async()=>{const config=collectionConfig();const workspace_id=await ensureImportWorkspace();const job=await api('/api/collections',{method:'POST',body:{...config,workspace_id}});$('#import-dialog').close();startPolling(job.id);location.hash='quality'});
$('#jobs-list').onclick=event=>{const button=event.target.closest('[data-job]');if(button)action(button,async()=>{await api(`/api/jobs/${button.dataset.job}/actions`,{method:'POST',body:{action:button.dataset.action,connection_id:state.connection}});await refreshQuality();if(button.dataset.action==='resume')startPolling(button.dataset.job)})};
$('#settings-form').onsubmit=event=>{event.preventDefault();action($('#settings-form button'),async()=>{await api('/api/settings',{method:'PUT',body:{retention_days:Number($('#retention-days').value)}});toast('저장됨','success')})};
$('#connect-settings').onclick=()=>openImport('api');
$('#connection-status').onclick=event=>{const button=event.target.closest('[data-disconnect]');if(button)action(button,async()=>{const result=await api('/api/connections/'+button.dataset.disconnect,{method:'DELETE'});if(state.connection===button.dataset.disconnect){state.connection=null;$('#login-form').hidden=false;$('#api-collection').hidden=true;$('#verify-form').hidden=true;invalidateSample()}await loadSettings();toast(result.remote_revoked?'연결 해제됨':'로컬 연결 해제 · 원격 해제 미확인','success')})};
$('#delete-workspace').onclick=()=>{$('#delete-confirm').value='';clearFeedback($('#delete-dialog'));$('#delete-dialog').showModal()};
$('#delete-form').onsubmit=event=>{event.preventDefault();action($('#delete-form button'),async()=>{await api('/api/workspaces/'+state.workspace.id+'?confirm='+encodeURIComponent($('#delete-confirm').value),{method:'DELETE'});$('#delete-dialog').close();state.analysis=null;localStorage.removeItem('postscope-workspace');await loadWorkspaces();toast('삭제됨','success')})};
if(siteMode){
  document.body.classList.add('static-site');
  $$('[data-import],.workspace-label,.workspace-picker,[data-page="settings"],#start-own').forEach(node=>node.hidden=true);
  $('#workspace-select').disabled=true;
  $('#header-export').innerHTML='<svg><use href="#i-download"/></svg>다운로드';
  $('#heat-value').textContent='';
  $('#applied-context').hidden=true;
  $('[data-page="quality"]').lastChild.textContent='수집 정보';
  $('[data-page="reports"]').lastChild.textContent='다운로드';
  names.quality='수집 정보';names.reports='다운로드';
  $$('.metric-label').forEach((node,index)=>node.firstChild.textContent=['게시물','게시일','게시 간격 중앙값','가장 활발한 시간'][index]);
  $('#demo-banner>span').innerHTML='<span class="demo-label">DEMO</span>합성 데모 데이터';
  $('#include-ids').closest('details').hidden=true;
  $('#load-demo').hidden=true;
  $('#empty-state h2').textContent='실제 데이터 연결 대기';
  $('#empty-state p').textContent='채널 연결 후 매일 수집한 게시 통계를 표시합니다.';
}
loading(true);loadWorkspaces().then(()=>navigate(location.hash.slice(1)||'dashboard')).catch(error=>toast(error.message,'error')).finally(()=>loading(false));
