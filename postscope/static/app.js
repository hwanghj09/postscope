const $=(selector,root=document)=>root.querySelector(selector);
const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf=new Intl.NumberFormat('ko-KR');
const fmt=value=>value==null?'계산 불가':nf.format(Math.round(value*10)/10);
const names={dashboard:'분석 대시보드',records:'게시 기록',comparison:'기간 비교',quality:'데이터 품질',reports:'보고서 내보내기',settings:'설정'};
const titles={dashboard:['게시 기록을 한눈에.','흩어진 기록 속에서 날짜와 시간의 패턴을 발견하세요.'],records:['기록을 차근차근.','분석에 포함된 게시 기록의 메타데이터를 확인하세요.'],comparison:['두 기간, 나란히.','같은 조건에서 게시량과 관측 게시율의 변화를 살펴보세요.'],quality:['분석의 시작은, 데이터 이해.','수집 범위와 식별 가능 여부를 먼저 확인하세요.'],reports:['발견을 오래 간직하세요.','차트, 집계, 분석 조건을 재현 가능한 보고서로 저장하세요.'],settings:['나에게 맞는 작업공간.','데이터 보존과 계정 연결을 관리하세요.']};
const types={text:'텍스트',document:'문서',photo:'사진',video:'영상',audio:'오디오',other:'기타',unknown:'미확인'};
const state={workspace:null,workspaces:[],analysis:null,applied:null,page:'dashboard',heat:'count',cursor:0,history:[],upload:null,connection:null,authStage:'code',chatOffset:0,loading:0,importJob:null,poller:null};
const supplemental=document.createElement('div');supplemental.className='two-column supplemental-charts';supplemental.innerHTML='<article class="card chart-card"><div class="card-heading"><div><h3>자료 유형별 분포</h3><p>본문을 읽지 않고 메타데이터로 분류합니다.</p></div></div><div id="types-chart" class="plot compact-plot"></div></article><article class="card chart-card"><div class="card-heading"><div><h3>연속 게시 묶음 길이</h3><p id="sessions-caption">가까이 이어진 게시 묶음의 길이 분포</p></div></div><div id="sessions-chart" class="plot compact-plot"></div></article>';$('.bottom-note').before(supplemental);

async function api(path,options={}){
  const headers={'X-PostScope-Token':$('meta[name="postscope-token"]').content,...options.headers};
  if(options.body&&!(options.body instanceof FormData)){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body)}
  const response=await fetch(path,{...options,headers,credentials:'same-origin'});
  if(!response.ok){let data;try{data=await response.json()}catch{data={detail:'요청을 처리하지 못했습니다. 서버 연결을 확인하세요.'}}throw new Error(typeof data.detail==='string'?data.detail:'입력값을 확인하세요.')}
  return response.json();
}
function toast(message,kind=''){const node=document.createElement('div');node.className='toast '+kind;node.textContent=message;$('#toast-region').append(node);setTimeout(()=>node.remove(),6500)}
async function action(button,fn){if(button?.disabled)return;const old=button?.textContent;if(button){button.disabled=true;button.textContent='처리 중…'}try{return await fn()}catch(error){toast(error.message,'error')}finally{if(button){button.disabled=false;button.textContent=old}}}
function loading(value){state.loading+=value?1:-1;$('#loading-overlay').hidden=state.loading<=0}
function option(value,label){return `<option value="${esc(value)}">${esc(label)}</option>`}
function duration(seconds){if(seconds==null)return '계산 불가';if(seconds<60)return `${fmt(seconds)}<small>초</small>`;if(seconds<3600)return `${fmt(seconds/60)}<small>분</small>`;return `${fmt(seconds/3600)}<small>시간</small>`}
function localDate(value,timezone='Asia/Seoul'){return new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value))}
function timestamp(value,zone){return value?new Intl.DateTimeFormat('ko-KR',{timeZone:zone||'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'—'}
function readFilters(){const f=new FormData($('#filters'));return {workspace_id:state.workspace.id,chat_key:f.get('chat_key'),sender:f.get('sender'),start:f.get('start'),end:f.get('end'),timezone:f.get('timezone'),content:f.get('content'),unit:f.get('unit'),include_forwarded:$('#filters [name="include_forwarded"]').checked,session_minutes:Number(f.get('session_minutes'))}}
function dirty(){if(!state.applied||!state.workspace)return;const current=readFilters();$('#dirty-indicator').hidden=Object.keys(current).every(key=>current[key]===state.applied[key])}
function fillSenders(){const chat=$('#chat-select').value;const current=$('#sender-select').value;$('#sender-select').innerHTML=option('all','전체 작성자')+state.workspace.senders.filter(x=>x.chat_key===chat).map(x=>option(x.sender_key,x.label+(x.kind==='signature'?' · 개인 식별 정보 아님':''))).join('');if($$('#sender-select option').some(x=>x.value===current))$('#sender-select').value=current;const data=state.workspace.chats.find(x=>x.chat_key===chat);$('#album-option').disabled=!data?.album_supported;if(!data?.album_supported)$('#unit-select').value='messages'}

async function loadWorkspaces(preferred){
  state.workspaces=await api('/api/workspaces');
  if(!state.workspaces.length){const demo=await api('/api/demo',{method:'POST'});state.workspaces=await api('/api/workspaces');preferred=demo.workspace_id}
  $('#workspace-select').innerHTML=state.workspaces.map(x=>option(x.id,x.title+(x.is_demo?' · 데모':''))).join('');
  const id=preferred||localStorage.getItem('postscope-workspace');
  await selectWorkspace(state.workspaces.some(x=>x.id===id)?id:state.workspaces[0].id);
}
async function selectWorkspace(id){
  state.workspace=await api('/api/workspaces/'+encodeURIComponent(id));state.analysis=null;state.applied=null;state.cursor=0;state.history=[];
  localStorage.setItem('postscope-workspace',id);$('#workspace-select').value=id;
  $('#demo-banner').hidden=!state.workspace.is_demo;
  $('#chat-select').innerHTML=state.workspace.chats.map(x=>option(x.chat_key,x.title)).join('');
  $('#filters').reset();$('#timezone-select').value=state.workspace.timezone;fillSenders();
  const chat=state.workspace.chats[0];
  $('#empty-state').hidden=!!chat;$('#filter-section').hidden=!chat;$('#quality-strip').hidden=!chat;$('#page-dashboard').hidden=!chat||state.page!=='dashboard';
  if(chat){const start=localDate(chat.first_message||new Date().toISOString(),state.workspace.timezone);const end=localDate(chat.last_message||new Date().toISOString(),state.workspace.timezone);$('#filter-start').value=state.workspace.is_demo?'2026-10-01':start;$('#filter-end').value=end;await applyFilters()}
  renderQuality();navigate(state.page);
}
async function applyFilters(extra={}){
  if(!state.workspace?.chats.length)return;
  const config={...readFilters(),...extra};if(!config.start||!config.end){toast('시작일과 종료일을 입력하세요.','error');return}
  loading(true);
  try{const result=await api('/api/analyses',{method:'POST',body:config});if(state.workspace.id!==config.workspace_id)return;state.analysis=result;state.applied=config;state.cursor=0;state.history=[];dirty();renderDashboard();renderQuality();renderComparison();renderReport();if(state.page==='records')await loadRecords();toast('분석 조건을 적용했습니다.','success')}
  catch(error){toast(error.message,'error')}
  finally{loading(false)}
}

const plotBase={paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'Nanum, sans-serif',size:9,color:'#9ba8bd'},margin:{t:10,r:12,b:30,l:38},xaxis:{showgrid:false,zeroline:false,tickfont:{size:8},tickcolor:'transparent',fixedrange:false},yaxis:{gridcolor:'#f0f3f8',zeroline:false,rangemode:'tozero',tickfont:{size:8}},showlegend:false,hovermode:'x unified',hoverlabel:{bgcolor:'white',bordercolor:'#e2e9f7',font:{family:'Nanum',size:10,color:'#516584'}},bargap:.55};
const plotOptions={responsive:true,displayModeBar:false,scrollZoom:false};
function plot(id,traces,layout={}){Plotly.react(id,traces,{...plotBase,...layout},plotOptions)}
function renderDashboard(){
  const d=state.analysis;if(!d)return;const m=d.metrics,unit=d.config.unit==='messages'?'건':'묶음';
  $('#metric-total').innerHTML=`${nf.format(m.total)}<small>${unit}</small>`;
  $('#metric-days').innerHTML=`${nf.format(m.active_days)}<small>일</small>`;
  $('#metric-interval').innerHTML=duration(m.median_interval);
  $('#metric-peak').innerHTML=m.peak_hours.length?`${m.peak_hours[0]}:00<small>${m.peak_hours.length>1?`외 ${m.peak_hours.length-1}개 동률`:'– '+((m.peak_hours[0]+1)%24)+':00'}</small>`:'계산 불가';
  $('#peak-footnote').textContent=m.peak_hour_share==null?'비중 계산 불가':`전체 게시의 ${(m.peak_hour_share*100).toFixed(1)}% · ${d.config.timezone}`;
  $('#total-footnote').textContent=d.config.unit==='messages'?'선택한 조건의 고유 메시지':'소스에서 확인된 앨범은 1묶음으로 집계';
  $('#days-footnote').textContent=m.daily_average==null?'게시가 1건 이상 관측된 날짜':`24시간 환산 평균 ${fmt(m.daily_average)}${unit} · 범위 확인`;
  $('#record-badge').textContent=nf.format(m.total);
  $('#analysis-range').textContent=d.config.start.replaceAll('-','.')+' – '+d.config.end.replaceAll('-','.');
  $('#snapshot-time').textContent='스냅샷 '+timestamp(d.created_at,d.config.timezone);
  $('#coverage-status').textContent=d.quality.coverage;$('#coverage-dot').style.background=m.full_coverage?'#27b58a':'#cda25c';
  $('#coverage-status').style.color=m.full_coverage?'#5c967e':'#b49361';
  $('#quality-brief').textContent=`작성자 ID 식별률 ${d.quality.identified_ratio==null?'계산 불가':(d.quality.identified_ratio*100).toFixed(1)+'%'} · 격리 ${nf.format(d.quality.quarantined)}건${d.small_sample?' · 짧은 기간·적은 표본':''}`;
  $('#insight-peak').textContent=m.peak_hours.length?m.peak_hours.map(h=>h+'시').join(' · '):'기록 없음';
  $('#insight-text').textContent=m.peak_hour_share==null?'선택한 조건에 해당하는 기록이 없습니다.':`해당 시간대에 전체 관측 게시의 ${(m.peak_hour_share*100).toFixed(1)}%가 모였어요. ${d.config.timezone} 기준입니다.`;
  $('#session-count').textContent=nf.format(m.session_count)+'개';$('#session-size').textContent=fmt(m.session_median_size)+(m.session_median_size==null?'':'건');
  $('#daily-caption').textContent=m.full_coverage?'7일 이동평균은 조회 범위가 확인된 연속 7일에만 표시합니다.':'범위 미확인 구간은 0건을 의미하지 않습니다. 일평균과 이동평균 표시가 제한됩니다.';
  $('#hours-caption').textContent=d.config.timezone+' 기준 · 0~23시';
  const daily=d.daily,tooltip=`%{x}<br>%{y} ${unit}<br>${d.config.timezone}<extra></extra>`;
  plot('daily-chart',[{x:daily.map(x=>x.date),y:daily.map(x=>x.status==='unknown'&&x.count===0?null:x.count),type:'scatter',mode:'lines',name:'관측 게시량',line:{color:'#5077ed',width:2.4,shape:'linear'},fill:'tozeroy',fillcolor:'rgba(75,113,230,.065)',hovertemplate:tooltip},{x:daily.map(x=>x.date),y:daily.map(x=>x.moving_average),type:'scatter',mode:'lines',name:'7일 이동평균',connectgaps:false,line:{color:'#6bbeb5',width:1.5,dash:'dot'},hovertemplate:`7일 평균 %{y:.1f} ${unit}<extra></extra>`}],{xaxis:{...plotBase.xaxis,type:'date',nticks:9,tickformat:'%m.%d'},yaxis:{...plotBase.yaxis,nticks:5}});
  renderWeekday();
  plot('hourly-chart',[{x:Array.from({length:24},(_,i)=>i),y:d.hours,type:'bar',marker:{color:d.hours.map((_,h)=>m.peak_hours.includes(h)?'#4168e9':'#b6c9f9'),cornerradius:3},hovertemplate:`%{x}시 · ${d.config.timezone}<br>%{y} ${unit}<extra></extra>`}],{xaxis:{...plotBase.xaxis,tickmode:'linear',dtick:3,ticksuffix:'시'},bargap:.35});
  plot('interval-chart',[{x:['1분 미만','1~5분','5~10분','10분~1시간','1~6시간','6시간 이상'],y:d.interval_histogram,type:'bar',marker:{color:'#9cb7f4',cornerradius:4},hovertemplate:'%{x}<br>%{y}개 관측 간격<extra></extra>'}],{bargap:.55});
  plot('types-chart',[{x:Object.keys(d.types).map(t=>types[t]||(t==='mixed'?'복합 유형':t)),y:Object.values(d.types),type:'bar',marker:{color:'#87a6f1',cornerradius:4},hovertemplate:'%{x}<br>%{y}건<extra></extra>'}]);
  plot('sessions-chart',[{x:['0초','5분 미만','5~10분','10~30분','30~60분','1시간 이상'],y:d.session_length_histogram||[0,0,0,0,0,0],type:'bar',marker:{color:'#a59bdc',cornerradius:4},hovertemplate:'%{x}<br>%{y}묶음<extra></extra>'}]);
  $('#sessions-caption').textContent=`인접 간격 ${d.config.session_minutes}분 이하 · 한 건짜리 묶음 포함 · 로그인 세션 아님`;
  renderHeat();
  setTimeout(resizeCharts,100);
}
function renderWeekday(){const d=state.analysis;if(!d)return;const average=$('#weekday-mode').value==='average';plot('weekday-chart',[{x:['월','화','수','목','금','토','일'],y:average?d.weekday_average:d.weekday,type:'bar',marker:{color:d.weekday.map(v=>v===Math.max(...d.weekday)?'#5379ed':'#c2d2f8'),cornerradius:4},hovertemplate:`%{x}요일 · ${d.config.timezone}<br>%{y} ${average?'건/확인된 날짜':'건'}<extra></extra>`}],{margin:{t:15,r:5,b:28,l:30},bargap:.58,annotations:average&&!d.metrics.full_coverage?[{text:'조회 범위 미확인 · 평균 계산 제한',showarrow:false,xref:'paper',yref:'paper',x:.5,y:.5,font:{size:10,color:'#a2adbf'}}]:[]})}
function renderHeat(){
  const d=state.analysis;if(!d)return;const rate=state.heat==='rate';const cells=rate?d.rates:d.heatmap;const maximum=Math.max(1,...cells.flat().filter(v=>v!=null));
  let html='<span></span>'+Array.from({length:24},(_,h)=>`<span class="heat-label">${h%3===0?String(h).padStart(2,'0'):''}</span>`).join('');
  for(let w=0;w<7;w++){html+=`<span class="heat-label">${['월','화','수','목','금','토','일'][w]}</span>`;for(let h=0;h<24;h++){const value=cells[w][h],known=d.exposure[w][h]>0;const intensity=value==null?0:value/maximum;const color=`hsl(223 ${Math.round(60+intensity*16)}% ${Math.round(96-intensity*41)}%)`;const label=`${['월','화','수','목','금','토','일'][w]}요일 ${h}~${h+1}시 · ${d.config.timezone} · ${value==null?'관측 시간 없음':fmt(value)+(rate?'건/시간':'건')} · 분모 ${fmt(d.exposure[w][h])}시간${!known?' · 조회 범위 미확인':''}`;html+=`<button class="heat-cell ${known?'':'unknown'}" style="background-color:${color}" data-label="${esc(label)}" aria-label="${esc(label)}" title="${esc(label)}"></button>`}}
  $('#heatmap').innerHTML=html;
  $('#heatmap').hidden=state.heat==='table';$('#heat-table').hidden=state.heat!=='table';
  $('#heat-subtitle').textContent=rate?'확인된 조회 시간을 분모로 한 게시율 · 분모 0은 계산 불가':'미확인 구간은 사선으로 표시 · 실제 관측 0건과 구분';
  if(state.heat==='table')$('#heat-table').innerHTML='<table><thead><tr><th>요일 / 시</th>'+Array.from({length:24},(_,h)=>`<th>${h}</th>`).join('')+'</tr></thead><tbody>'+d.heatmap.map((row,w)=>`<tr><th>${['월','화','수','목','금','토','일'][w]}</th>${row.map((v,h)=>`<td>${v}${d.exposure[w][h]?'':' (범위 미확인)'}</td>`).join('')}</tr>`).join('')+'</tbody></table>';
}
function resizeCharts(){$$('.plot').filter(x=>x.offsetParent&&x.data).forEach(x=>Plotly.Plots.resize(x))}

function navigate(page){
  state.page=names[page]?page:'dashboard';
  $$('.page-panel').forEach(p=>p.hidden=p.id!=='page-'+state.page||(!state.workspace?.chats.length&&['dashboard','records','comparison','reports'].includes(state.page)));
  $$('.nav-link').forEach(a=>{a.classList.toggle('active',a.dataset.page===state.page);if(a.dataset.page===state.page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
  $('#breadcrumb-page').textContent=names[state.page];$('#page-title').textContent=titles[state.page][0];$('#page-subtitle').textContent=titles[state.page][1];
  const hasChat=!!state.workspace?.chats.length;$('#filter-section').hidden=!hasChat||!['dashboard','records','comparison'].includes(state.page);$('#quality-strip').hidden=!hasChat||state.page==='settings';$('#empty-state').hidden=hasChat||['settings','quality'].includes(state.page);
  $('#sidebar').classList.remove('open');
  if(state.page==='records'&&state.analysis)loadRecords().catch(e=>toast(e.message,'error'));
  if(state.page==='quality')refreshQuality().catch(e=>toast(e.message,'error'));
  if(state.page==='settings')loadSettings().catch(e=>toast(e.message,'error'));
  if(state.page==='reports')renderReport();
  if(state.page==='comparison')renderComparison();
  if(state.page==='dashboard')setTimeout(resizeCharts,50);
}
async function loadRecords(){if(!state.analysis)return;const data=await api(`/api/analyses/${state.analysis.id}/records?cursor=${state.cursor}&limit=50`);$('#records-body').innerHTML=data.items.length?data.items.map(r=>`<tr><td>${esc(r.message_id)}</td><td><span class="badge">${esc(r.sender_label)}</span>${r.sender_kind==='signature'?'<small> 개인 식별 아님</small>':''}</td><td>${esc(timestamp(r.sent_at_utc,state.analysis.config.timezone))}</td><td>${esc(types[r.content_kind]||r.content_kind)}</td><td>${r.members}건</td><td>${r.is_forwarded?'전달':'일반'}</td></tr>`).join(''):'<tr><td colspan="6">선택한 조건에 해당하는 기록이 없습니다.</td></tr>';$('#records-total').textContent=nf.format(data.total)+'건';$('#records-position').textContent=`${data.total?state.cursor+1:0}–${Math.min(state.cursor+50,data.total)} / ${nf.format(data.total)}`;$('#records-prev').disabled=!state.history.length;$('#records-next').disabled=data.next_cursor==null;$('#records-next').dataset.cursor=data.next_cursor??''}

async function refreshQuality(){if(!state.workspace)return;state.workspace=await api('/api/workspaces/'+state.workspace.id);renderQuality()}
function renderQuality(){
  const d=state.analysis;if(d){const q=d.quality;$('#quality-badge').textContent=q.coverage;const pairs=[['저장된 메시지',fmt(q.total_stored)+'건'],['작성자 ID 식별률',q.identified_ratio==null?'계산 불가':(q.identified_ratio*100).toFixed(1)+'%'],['서비스 이벤트 제외',fmt(q.service_excluded)+'건'],['서명만 있는 비율',q.signature_ratio==null?'계산 불가':(q.signature_ratio*100).toFixed(1)+'%'],['중복 / 격리',fmt(q.duplicates)+' / '+fmt(q.quarantined)+'건'],['필드 충돌',fmt(q.conflicts)+'건'],['앨범 식별자',q.album_supported?'소스에서 제공':'미제공 · 묶음 전환 제한'],['불완전 앨범 제외',fmt(d.incomplete_albums)+'묶음'],['첫 관측 메시지',timestamp(q.first_message,d.config.timezone)],['마지막 관측 메시지',timestamp(q.last_message,d.config.timezone)],['마지막 성공 갱신',timestamp(q.last_updated,d.config.timezone)],['확인된 조회 시간',fmt(d.metrics.observed_hours)+'시간']];$('#quality-details').innerHTML='<div class="detail-grid">'+pairs.map(([k,v])=>`<div class="detail-item"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')+'</div><div class="notice">접근 가능한 기록의 조회 완료는 과거의 모든 기록이 보존되었다는 뜻이 아닙니다. ID의 빈 번호로 삭제나 누락을 추정하지 않습니다. 원본 날짜 충돌은 Unix 시각을 우선하며 충돌 건수를 공개합니다.</div>'}
  else{$('#quality-details').innerHTML='<p class="muted">데이터를 가져오면 식별률과 조회 범위를 확인할 수 있습니다.</p>';$('#quality-badge').textContent='아직 데이터 없음'}
  const states={queued:'대기열',running:'수집 중',waiting:'재시도 대기',paused:'일시정지',completed:'완료',failed:'실패',cancelled:'취소'};
  $('#jobs-list').innerHTML=(state.workspace?.jobs||[]).map(j=>`<div class="job-item"><span class="badge">${esc(states[j.state]||j.state)}</span><div class="job-info">${j.kind==='json'?'JSON 가져오기':'API 기록 수집'} · ${nf.format(j.processed)}건 처리<small>${esc(j.error||'')}${j.next_retry_at?' · 재개 가능 '+esc(timestamp(j.next_retry_at)):''}</small>${j.stats.same_file?'<small>동일 파일입니다. 이전 결과를 유지했습니다.</small>':''}</div>${j.kind==='api'&&!['completed','cancelled','failed'].includes(j.state)?`<button class="button" data-job="${j.id}" data-action="${(j.state==='paused'||!j.worker_active)?'resume':'pause'}">${(j.state==='paused'||!j.worker_active)?'재개':'일시정지'}</button><button class="button" data-job="${j.id}" data-action="cancel">취소</button>`:''}</div>`).join('')||'<p class="muted small">수집 작업이 없습니다.</p>';
  const rows=state.workspace?.quarantine||[];$('#quarantine-list').innerHTML=rows.length?'<table><thead><tr><th>소스 행 번호</th><th>격리 사유</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${r.row_number}</td><td>${esc(r.reason)}</td></tr>`).join('')+'</tbody></table>':'<p class="muted small">격리된 행이 없습니다.</p>';
}
function renderComparison(){const d=state.analysis;if(!d?.comparison){$('#comparison-result').innerHTML='<p class="muted">기준 기간을 선택하면 건수 차이와 조회 범위가 확인된 게시율을 비교합니다.</p>';if(d){const start=new Date(d.config.start+'T12:00:00Z'),end=new Date(d.config.end+'T12:00:00Z');const length=Math.round((end-start)/86400000)+1;$('#compare-end').value=new Date(start-86400000).toISOString().slice(0,10);$('#compare-start').value=new Date(start-length*86400000).toISOString().slice(0,10)}return}const c=d.comparison;$('#comparison-result').innerHTML=`<div class="comparison-metrics"><div><span>기준 기간 게시량</span><strong>${nf.format(c.baseline.total)}</strong></div><div><span>현재 기간 게시량</span><strong>${nf.format(d.metrics.total)}</strong></div><div><span>건수 차이</span><strong>${c.difference>0?'+':''}${nf.format(c.difference)}</strong></div></div><div class="notice">증감률: ${c.percent==null?'기준값 0으로 계산 불가':c.percent.toFixed(1)+'%'}<br>${c.same_length?'동일한 기간 길이입니다.':'기간 길이가 달라 총량 비교에 주의하세요.'}<br>${d.metrics.full_coverage&&c.baseline.full_coverage?`확인된 게시율: 기준 ${fmt(c.baseline.hourly_rate)} → 현재 ${fmt(d.metrics.hourly_rate)}건/시간`:'일부 조회 범위가 미확인되어 게시율 비교를 표시하지 않습니다.'}${d.small_sample?'<br>짧은 기간·적은 표본: 통계적 신뢰도를 보증하지 않습니다.':''}</div>`}
function renderReport(){const d=state.analysis;$('#report-summary').innerHTML=d?`<b>${esc(d.chat_title)}</b> · ${nf.format(d.metrics.total)}${d.config.unit==='messages'?'건':'묶음'}<br>${esc(d.config.start)} ~ ${esc(d.config.end)} · ${esc(d.config.timezone)} · ${esc(d.quality.coverage)}<br><span class="small">${esc(d.algorithm)} · 생성 ${esc(timestamp(d.created_at,d.config.timezone))}</span>`:'먼저 데이터와 분석 조건을 적용하세요.'}
async function exportReport(format,button){if(!state.analysis){toast('먼저 분석을 실행하세요.','error');return}await action(button,async()=>{const data=await api('/api/reports',{method:'POST',body:{analysis_id:state.analysis.id,format,include_ids:$('#include-ids').checked}});const a=document.createElement('a');a.href=data.download_url;a.download=data.filename;document.body.append(a);a.click();a.remove();toast('보고서를 저장했습니다.','success')})}

async function loadSettings(){const settings=await api('/api/settings');$('#retention-days').value=settings.retention_days;const connections=await api('/api/connections');$('#connection-status').innerHTML=connections.length?connections.map(c=>`<div class="job-item"><span class="badge">${esc(c.state)}</span><span class="small muted">이 실행에서만 유지되는 메모리 세션</span><button class="button" data-disconnect="${esc(c.id)}">연결 해제</button></div>`).join(''):'<p class="muted small">연결된 계정이 없습니다.</p>'}
async function createWorkspace(title){const data=await api('/api/workspaces',{method:'POST',body:{title,timezone:'Asia/Seoul'}});await loadWorkspaces(data.id);return data}
async function openImport(tab='json'){try{if(!state.workspace||state.workspace.is_demo)await createWorkspace('내 게시 기록');$('#import-dialog').showModal();switchImportTab(tab)}catch(e){toast(e.message,'error')}}
function switchImportTab(tab){$$('[data-import-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.importTab===tab));$('#json-import-panel').hidden=tab!=='json';$('#api-import-panel').hidden=tab!=='api';if(tab==='api'){const now=new Date();$('#api-end').value=localDate(now.toISOString());$('#api-start').value=localDate(new Date(now-7*86400000).toISOString())}}
async function previewFile(file){if(!file)return;$('#import-preview').hidden=true;state.upload=null;$('#import-progress').hidden=false;$('#import-progress').textContent='파일 구조를 검사하고 있습니다. 본문은 저장하지 않습니다.';const form=new FormData();form.append('file',file);try{state.upload=await api('/api/imports/preview',{method:'POST',body:form});$('#file-summary').textContent=`${state.upload.filename} · ${(state.upload.bytes/1024).toFixed(1)} KiB · ${state.upload.preview_type}`;$('#import-chat').innerHTML=state.upload.schema.chats.map(c=>option(c.index,`${c.name||'이름 없는 대화'} · ${nf.format(c.count||0)}행 · ${c.type||'유형 미확인'}`)).join('');$('#import-preview').hidden=false;$('#import-progress').hidden=true}catch(e){$('#import-progress').textContent=e.message;toast(e.message,'error')}}
async function pollJob(id){
  const job=await api('/api/jobs/'+id);const stats=job.stats||{};
  $('#import-progress').hidden=false;$('#import-progress').textContent=`${job.kind==='json'?'가져오기':'수집'} ${job.state} · ${nf.format(job.processed)}건 처리${job.next_retry_at?' · 재개 '+timestamp(job.next_retry_at):''}${job.error?' · '+job.error:''}`;
  await refreshQuality();
  if(['completed','failed','cancelled','paused'].includes(job.state)){
    clearInterval(state.poller);state.poller=null;
    if(job.state==='completed'){toast(`완료 · 신규 ${stats.inserted||0}건 · 중복 ${stats.duplicate||0}건 · 격리 ${stats.quarantined||0}건${stats.same_file?' · 동일 파일 재가져오기':''}`,'success');await selectWorkspace(state.workspace.id);$('#import-dialog').close();location.hash='quality'}
    else if(job.state==='failed')toast(job.error||'처리를 완료하지 못했습니다.','error');
  }
}
function startPolling(id){if(state.poller)clearInterval(state.poller);let busy=false;state.poller=setInterval(async()=>{if(busy)return;busy=true;try{await pollJob(id)}catch(e){clearInterval(state.poller);toast(e.message,'error')}finally{busy=false}},2000);pollJob(id).catch(e=>toast(e.message,'error'))}

$('#workspace-select').addEventListener('change',e=>selectWorkspace(e.target.value).catch(e=>toast(e.message,'error')));
$('#filters').addEventListener('submit',e=>{e.preventDefault();applyFilters()});$('#filters').addEventListener('input',dirty);$('#filters').addEventListener('change',dirty);
$('#chat-select').addEventListener('change',()=>{fillSenders();dirty()});
$('#reset-filters').onclick=()=>{$('#filters').reset();fillSenders();const c=state.workspace.chats.find(c=>c.chat_key===$('#chat-select').value);if(c){$('#filter-start').value=localDate(c.first_message||new Date().toISOString());$('#filter-end').value=localDate(c.last_message||new Date().toISOString())}dirty()};
$('#quality-link').onclick=()=>location.hash='quality';$('#header-export').onclick=()=>location.hash='reports';
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)||'dashboard'));
window.addEventListener('resize',resizeCharts);
$('#menu-toggle').onclick=()=>$('#sidebar').classList.toggle('open');
$('#weekday-mode').onchange=renderWeekday;
$$('[data-heat]').forEach(b=>b.onclick=()=>{state.heat=b.dataset.heat;$$('[data-heat]').forEach(x=>x.classList.toggle('selected',x===b));renderHeat()});
for(const type of ['focusin','mouseover'])$('#heatmap').addEventListener(type,e=>{if(e.target.dataset.label)$('#heat-value').textContent=e.target.dataset.label});
$('#heatmap').addEventListener('keydown',e=>{const cells=$$('.heat-cell');const index=cells.indexOf(document.activeElement);const offset={ArrowLeft:-1,ArrowRight:1,ArrowUp:-24,ArrowDown:24}[e.key];if(offset!=null&&index>=0){e.preventDefault();cells[Math.max(0,Math.min(cells.length-1,index+offset))].focus()}});
$('#records-next').onclick=()=>{state.history.push(state.cursor);state.cursor=Number($('#records-next').dataset.cursor);loadRecords().catch(e=>toast(e.message,'error'))};$('#records-prev').onclick=()=>{state.cursor=state.history.pop()||0;loadRecords().catch(e=>toast(e.message,'error'))};
$('#comparison-form').onsubmit=e=>{e.preventDefault();applyFilters({compare_start:$('#compare-start').value,compare_end:$('#compare-end').value})};$('#compare-clear').onclick=()=>applyFilters();
$$('[data-export]').forEach(b=>b.onclick=()=>exportReport(b.dataset.export,b));
$('#new-workspace').onclick=()=>$('#workspace-dialog').showModal();$('#start-own').onclick=()=>openImport();$('#load-demo').onclick=()=>action($('#load-demo'),async()=>{const data=await api('/api/demo',{method:'POST'});await loadWorkspaces(data.workspace_id);location.hash='dashboard'});
$('#workspace-form').onsubmit=e=>{e.preventDefault();action($('#workspace-form button'),async()=>{await createWorkspace(new FormData(e.target).get('title'));$('#workspace-dialog').close();e.target.reset();location.hash='dashboard'})};
$$('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());$$('[data-import]').forEach(b=>b.onclick=()=>openImport());$$('[data-import-tab]').forEach(b=>b.onclick=()=>switchImportTab(b.dataset.importTab));
$('#json-file').onchange=e=>previewFile(e.target.files[0]);
for(const event of ['dragover','dragenter'])$('#dropzone').addEventListener(event,e=>{e.preventDefault();$('#dropzone').classList.add('dragover')});
$('#dropzone').addEventListener('dragleave',()=>$('#dropzone').classList.remove('dragover'));$('#dropzone').addEventListener('drop',e=>{e.preventDefault();$('#dropzone').classList.remove('dragover');previewFile(e.dataTransfer.files[0])});
$('#confirm-range').onchange=e=>$('#import-range').hidden=!e.target.checked;
$('#import-submit').onclick=()=>action($('#import-submit'),async()=>{if(!state.upload)throw new Error('먼저 파일을 선택하세요.');const confirmed=$('#confirm-range').checked;const job=await api('/api/imports',{method:'POST',body:{workspace_id:state.workspace.id,upload_id:state.upload.id,chat_index:Number($('#import-chat').value),source_timezone:$('#source-timezone').value||null,confirmed_start:confirmed?$('#import-start').value:null,confirmed_end:confirmed?$('#import-end').value:null,accept_partial:$('#accept-partial').checked}});$('#import-preview').hidden=true;state.upload=null;startPolling(job.id)});
$('#login-form').onsubmit=e=>{e.preventDefault();action($('#login-form button'),async()=>{const f=new FormData(e.target);try{const connection=await api('/api/connections/login',{method:'POST',body:{api_id:Number(f.get('api_id')),api_hash:f.get('api_hash'),phone:f.get('phone')}});state.connection=connection.id;state.authStage='code';$('#login-form').hidden=true;$('#verify-form').hidden=false;$('#verify-label').innerHTML='로그인 코드<input name="credential" type="text" autocomplete="one-time-code" required>'}finally{e.target.reset()}})};
$('#verify-form').onsubmit=e=>{e.preventDefault();action($('#verify-form button'),async()=>{const input=$('#verify-form input');const credential=input.value;input.value='';const data=await api('/api/connections/verify',{method:'POST',body:{connection_id:state.connection,[state.authStage==='password'?'password':'code']:credential}});if(data.state==='password'){state.authStage='password';$('#verify-label').innerHTML='2단계 인증 비밀번호<input name="credential" type="password" autocomplete="off" required>';toast('2단계 인증 비밀번호를 입력하세요.')}else{$('#verify-form').hidden=true;$('#api-collection').hidden=false;toast('계정이 연결되었습니다.','success')}})};
async function fetchChats(append=false){const data=await api(`/api/chats?connection_id=${encodeURIComponent(state.connection)}&offset=${append?state.chatOffset:0}`);if(!append){$('#api-chat').innerHTML='';state.chatOffset=0}$('#api-chat').insertAdjacentHTML('beforeend',data.items.map(c=>option(c.chat_key,c.title+' · '+c.kind)).join(''));state.chatOffset+=data.items.length;$('#more-chats').hidden=!data.has_more}
$('#fetch-chats').onclick=()=>action($('#fetch-chats'),()=>fetchChats());$('#more-chats').onclick=()=>action($('#more-chats'),()=>fetchChats(true));
const sampleButton=document.createElement('button');sampleButton.className='button full-width';sampleButton.id='sample-check';sampleButton.textContent='선택한 기간 표본 점검';$('#collect-start').before(sampleButton);
const sampleResult=document.createElement('div');sampleResult.className='notice';sampleResult.hidden=true;sampleButton.after(sampleResult);$('#collect-start').disabled=true;
function collectionConfig(){return {workspace_id:state.workspace.id,connection_id:state.connection,chat_key:$('#api-chat').value,start:$('#api-start').value,end:$('#api-end').value,timezone:$('#api-timezone').value}}
sampleButton.onclick=()=>action(sampleButton,async()=>{const sample=await api('/api/collections/sample',{method:'POST',body:collectionConfig()});sampleResult.hidden=false;sampleResult.textContent=`검사 ${sample.inspected}건 · 기간 내 ${sample.sample_count}건 · 작성자 ID 확인 ${sample.identified}건 · 앨범 식별자 ${sample.album_supported?'있음':'표본에 없음'}. ${sample.note}`;$('#collect-start').disabled=false});
$('#api-collection').addEventListener('change',()=>{$('#collect-start').disabled=true;sampleResult.hidden=true});
$('#collect-start').onclick=()=>action($('#collect-start'),async()=>{const job=await api('/api/collections',{method:'POST',body:collectionConfig()});$('#import-dialog').close();startPolling(job.id);location.hash='quality'});
$('#jobs-list').onclick=e=>{const b=e.target.closest('[data-job]');if(b)action(b,async()=>{await api(`/api/jobs/${b.dataset.job}/actions`,{method:'POST',body:{action:b.dataset.action,connection_id:state.connection}});await refreshQuality();if(b.dataset.action==='resume')startPolling(b.dataset.job)})};
$('#settings-form').onsubmit=e=>{e.preventDefault();action($('#settings-form button'),async()=>{await api('/api/settings',{method:'PUT',body:{retention_days:Number($('#retention-days').value)}});toast('보존 기간을 저장했습니다.','success')})};
$('#connect-settings').onclick=()=>openImport('api');$('#connection-status').onclick=e=>{const b=e.target.closest('[data-disconnect]');if(b)action(b,async()=>{const result=await api('/api/connections/'+b.dataset.disconnect,{method:'DELETE'});state.connection=null;$('#login-form').hidden=false;$('#api-collection').hidden=true;$('#verify-form').hidden=true;await loadSettings();toast(result.remote_revoked?'로컬 세션과 원격 앱 세션을 해제했습니다.':'로컬 세션을 삭제했습니다. 원격 해제는 확인되지 않았습니다.','success')})};
$('#delete-workspace').onclick=()=>{$('#delete-confirm').value='';$('#delete-dialog').showModal()};$('#delete-form').onsubmit=e=>{e.preventDefault();action($('#delete-form button'),async()=>{await api('/api/workspaces/'+state.workspace.id+'?confirm='+encodeURIComponent($('#delete-confirm').value),{method:'DELETE'});$('#delete-dialog').close();state.analysis=null;localStorage.removeItem('postscope-workspace');await loadWorkspaces();toast('작업공간을 삭제했습니다.','success')})};

loading(true);loadWorkspaces().then(()=>navigate(location.hash.slice(1)||'dashboard')).catch(e=>toast(e.message,'error')).finally(()=>loading(false));
