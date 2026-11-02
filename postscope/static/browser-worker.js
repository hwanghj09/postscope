/* All imported files and metadata stay in this origin's worker and IndexedDB. */
importScripts(new URL('../vendor/pyodide/pyodide.js',self.location).href);
let python,engine;
const staticMode=new URL(self.location).searchParams.has('snapshot');
function database(){return new Promise((resolve,reject)=>{const request=indexedDB.open('postscope-browser-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('state');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})}
async function readState(){const db=await database();return new Promise((resolve,reject)=>{const tx=db.transaction('state','readonly'),request=tx.objectStore('state').get('database');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);tx.oncomplete=()=>db.close()})}
async function saveState(){
  if(staticMode)return;
  try{
    const bytes=python.FS.readFile('/data/postscope.sqlite3');
    const db=await database();
    await new Promise((resolve,reject)=>{const tx=db.transaction('state','readwrite');tx.objectStore('state').put(bytes,'database');tx.oncomplete=()=>{db.close();resolve()};tx.onerror=()=>{db.close();reject(tx.error)}});
  }catch(error){self.postMessage({type:'storage-warning',message:'브라우저 저장 공간을 사용할 수 없어 이번 탭에서만 유지됩니다.'})}
}
async function boot(){
  self.postMessage({type:'progress',message:'분석 엔진 준비 중'});
  python=await loadPyodide({indexURL:new URL('../vendor/pyodide/',self.location).href});
  await python.loadPackage('sqlite3');
  const response=await fetch(new URL('../engine.zip',self.location));
  if(!response.ok)throw new Error('분석 엔진을 불러오지 못했습니다. 새로고침하세요.');
  python.unpackArchive(new Uint8Array(await response.arrayBuffer()),'zip',{extractDir:'/app'});
  python.FS.mkdirTree('/data');
  let saved;
  if(!staticMode)try{saved=await readState()}catch(error){self.postMessage({type:'storage-warning',message:'이번 탭에서만 데이터가 유지됩니다.'})}
  if(saved)python.FS.writeFile('/data/postscope.sqlite3',saved);
  else{
    const demo=await fetch(new URL(staticMode?'../snapshot.sqlite3':'../demo.sqlite3',self.location));
    if(!demo.ok)throw new Error('데모 데이터를 불러오지 못했습니다.');
    python.FS.writeFile('/data/postscope.sqlite3',new Uint8Array(await demo.arrayBuffer()));
  }
  python.runPython("import sys; sys.path.insert(0,'/app'); from postscope.browser_engine import BrowserEngine; browser_engine=BrowserEngine('/data')");
  engine=python.globals.get('browser_engine');
  await saveState();
  self.postMessage({type:'ready'});
}
const ready=boot();
let queue=Promise.resolve();
self.onmessage=event=>{
  const message=event.data;
  queue=queue.catch(()=>{}).then(async()=>{
    try{
      await ready;
      if(message.file){
        const path='/tmp/postscope-upload-'+crypto.randomUUID()+'.json';
        python.FS.writeFile(path,new Uint8Array(message.file.bytes));
        message.body={filename:message.file.name,file_path:path};
      }
      const result=JSON.parse(engine.request_json(message.path,message.method,JSON.stringify(message.body||{})));
      if(result.ok&&message.method!=='GET'&&!['/api/imports/preview','/api/report-snapshot','/api/reports'].includes(message.path))await saveState();
      self.postMessage({id:message.id,...result});
    }catch(error){self.postMessage({id:message.id,ok:false,error:'분석 엔진을 실행하지 못했습니다. 브라우저를 새로고침하세요.'})}
  });
};
ready.catch(()=>self.postMessage({type:'boot-error',error:'분석 엔진을 불러오지 못했습니다. 새로고침하세요.'}));
