/* Default view is a daily static JSON snapshot. No worker or API server on load. */
let snapshotPromise,engine;
const analyses=new Map();
async function snapshot(){
  snapshotPromise??=fetch(new URL('../snapshot.json',import.meta.url),{cache:'no-store'}).then(async response=>{
    if(!response.ok)throw new Error('게시 데이터를 불러오지 못했습니다. 새로고침하세요.');
    const data=await response.json();analyses.set(data.analysis.id,data.analysis);return data;
  });
  return snapshotPromise;
}
function sameConfig(left,right){return Object.keys(left).every(key=>(left[key]??null)===(right[key]??null))}
export async function request(path,options={}){
  const data=await snapshot(),method=options.method||'GET',body=options.body||{};
  if(path==='/api/workspaces'&&method==='GET')return [data.workspace];
  if(path==='/api/workspaces/'+data.workspace.id&&method==='GET')return data.workspace;
  if(path==='/api/analyses'){
    const saved=[...analyses.values()].find(analysis=>sameConfig(body,analysis.config));
    if(saved)return saved;
    engine??=import('./browser-api.js');const result=await (await engine).request(path,options);analyses.set(result.id,result);return result;
  }
  if(path.startsWith('/api/analyses/')&&path.includes('/records')){
    const url=new URL(path,location.origin),id=url.pathname.split('/')[3];
    if(id===data.analysis.id){
      const cursor=Number(url.searchParams.get('cursor')||0),limit=Number(url.searchParams.get('limit')||50);
      return {items:data.records.slice(cursor,cursor+limit),total:data.records.length,next_cursor:cursor+limit<data.records.length?cursor+limit:null};
    }
  }
  if(path==='/api/reports'||path.startsWith('/api/analyses/')){
    engine??=import('./browser-api.js');return (await engine).request(path,options);
  }
  if(path==='/api/connections')return [];
  throw new Error('이 웹사이트는 매일 수집된 데이터를 표시합니다.');
}
