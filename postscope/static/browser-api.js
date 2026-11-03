let worker;
const pending=new Map();
let sequence=0,bootError=null,storageWarned=false;
function ensureWorker(){
if(worker)return worker;
const url=new URL('./browser-worker.js',import.meta.url);
url.search=new URL(import.meta.url).search;
if(document.querySelector('meta[name="postscope-mode"]')?.content==='static')url.searchParams.set('snapshot','1');
worker=new Worker(url);
worker.onmessage=event=>{
  const message=event.data;
  if(message.type==='progress'){
    const label=document.querySelector('#loading-overlay>span:last-child');if(label)label.textContent=message.message;
  }else if(message.type==='boot-error'){
    bootError=message.error;
    for(const {reject} of pending.values())reject(new Error(bootError));pending.clear();
  }else if(message.type==='storage-warning'){
    if(!storageWarned){storageWarned=true;const node=document.createElement('div');node.className='toast error';node.textContent=message.message;document.querySelector('#toast-region')?.append(node);setTimeout(()=>node.remove(),10000)}
  }else if(message.id){
    const callbacks=pending.get(message.id);if(!callbacks)return;pending.delete(message.id);
    message.ok?callbacks.resolve(message.data):callbacks.reject(new Error(message.error));
  }
};
worker.onerror=()=>{
  bootError='브라우저 분석 엔진을 시작하지 못했습니다.';
  for(const {reject} of pending.values())reject(new Error(bootError));pending.clear();
};
return worker;
}
async function call(path,method='GET',body={},file=null){
  if(bootError)throw new Error(bootError);
  const id=++sequence;
  ensureWorker();
  const result=new Promise((resolve,reject)=>pending.set(id,{resolve,reject}));
  worker.postMessage({id,path,method,body,file},file?[file.bytes]:[]);
  return result;
}

let fontData;
async function chartImages(data,onlyHeatmap=false){
  await document.fonts.ready;
  fontData??=fetch(new URL('../assets/NanumGothic-Regular.ttf',import.meta.url)).then(response=>response.arrayBuffer()).then(buffer=>{
    let binary='';for(const chunk of new Uint8Array(buffer))binary+=String.fromCharCode(chunk);return btoa(binary);
  });
  const font=await fontData;
  const blue='#4267e9',days=data.daily;
  const charts={
    daily:[{x:days.map(d=>d.date),y:days.map(d=>d.status==='unknown'&&d.count===0?null:d.count),type:'scatter',mode:'lines',line:{color:blue}}],
    weekday:[{x:['월','화','수','목','금','토','일'],y:data.weekday,type:'bar',marker:{color:blue}}],
    hourly:[{x:Array.from({length:24},(_,i)=>i),y:data.hours,type:'bar',marker:{color:blue}}],
    heatmap:[{x:Array.from({length:24},(_,i)=>String((i+5)%24).padStart(2,'0')),y:['월','화','수','목','금','토','일'],z:data.heatmap.map((row,w)=>Array.from({length:24},(_,i)=>data.heatmap[(w+((i+5)%24<5?1:0))%7][(i+5)%24])),type:'heatmap',colorscale:[[0,'#f0f4ff'],[1,blue]]}],
  };
  const titles={daily:'날짜별 관측 게시량',weekday:'요일별 게시 분포',hourly:'시간대별 게시 분포',heatmap:'요일 × 시간 · 05시–다음날 04시'};
  const images={};
  for(const name of onlyHeatmap?['heatmap']:Object.keys(charts)){
    const div=document.createElement('div');div.style.cssText='position:fixed;left:-20000px;top:0;width:1760px;height:660px';document.body.append(div);
    try{
      await Plotly.newPlot(div,charts[name],{width:1760,height:660,title:{text:titles[name]+' · '+data.config.timezone},font:{family:'Nanum',size:18,color:'#43526b'},paper_bgcolor:'white',plot_bgcolor:'white',showlegend:false,margin:{t:90,b:70,l:90,r:60},xaxis:name==='heatmap'?{type:'category',categoryorder:'array',categoryarray:Array.from({length:24},(_,i)=>String((i+5)%24).padStart(2,'0'))}:{},yaxis:{autorange:name==='heatmap'?'reversed':true,rangemode:'tozero',gridcolor:'#edf1f7'}},{staticPlot:true});
      const svgUrl=await Plotly.toImage(div,{format:'svg',width:1760,height:660});
      let svg=decodeURIComponent(svgUrl.slice(svgUrl.indexOf(',')+1));
      svg=svg.replace(/(<svg\b[^>]*>)/,`$1<defs><style>@font-face{font-family:Nanum;src:url(data:font/ttf;base64,${font}) format('truetype')}</style></defs>`);
      const blob=URL.createObjectURL(new Blob([svg],{type:'image/svg+xml;charset=utf-8'}));
      try{
        const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;image.src=blob});
        const canvas=document.createElement('canvas');canvas.width=1760;canvas.height=660;canvas.getContext('2d').drawImage(image,0,0);
        images[name]=canvas.toDataURL('image/png').split(',')[1];
      }finally{URL.revokeObjectURL(blob)}
    }finally{Plotly.purge(div);div.remove()}
  }
  return images;
}

export async function request(path,options={}){
  const method=options.method||'GET';
  let body=options.body||{};
  if(body instanceof FormData){
    const file=body.get('file');
    return call(path,method,{}, {name:file.name,bytes:await file.arrayBuffer()});
  }
  if(path==='/api/reports'){
    if(['png','zip'].includes(body.format)){
      const snapshot=await call('/api/report-snapshot','POST',{analysis_id:body.analysis_id});
      body={...body,pngs:await chartImages(snapshot,body.format==='png')};
    }
    const report=await call(path,method,body);
    const binary=atob(report.bytes_base64),bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
    const types={html:'text/html;charset=utf-8',png:'image/png',csv:'application/zip',zip:'application/zip'};
    const url=URL.createObjectURL(new Blob([bytes],{type:types[body.format]}));
    setTimeout(()=>URL.revokeObjectURL(url),300000);
    delete report.bytes_base64;return {...report,download_url:url};
  }
  return call(path,method,body);
}
