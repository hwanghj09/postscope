"""Non-CI benchmark: uv run python tests/benchmark.py [100000]. No real data."""
import hashlib
import json
import resource
import sys
import tempfile
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from postscope.storage import Store,uid,now
from postscope.adapters.desktop_json import inspect_file,import_file
from postscope.services.analysis import run_analysis


def main():
    count=int(sys.argv[1]) if len(sys.argv)>1 else 100000
    with tempfile.TemporaryDirectory() as directory:
        store=Store(directory)
        workspace,upload,job=uid(),uid(),uid()
        path=store.directory/'uploads'/upload
        start=datetime(2026,8,1,tzinfo=timezone.utc)
        with path.open('w') as f:
            f.write('{"id":100,"type":"private_supergroup","name":"성능 합성 데이터","messages":[')
            for i in range(count):
                if i:f.write(',')
                f.write(json.dumps({'id':i+1,'date_unixtime':str(int((start+timedelta(minutes=i)).timestamp())),'from_id':'user1','text':'SYNTHETIC BODY NEVER STORED'},ensure_ascii=False))
            f.write(']}')
        before=time.perf_counter()
        schema=inspect_file(path)
        checksum=hashlib.sha256(path.read_bytes()).hexdigest()
        with store.connect() as db:
            db.execute('INSERT INTO workspaces(id,title,timezone,created_at) VALUES(?,?,?,?)',(workspace,'성능 검증','Asia/Seoul',now()))
            db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?)',(upload,checksum,'benchmark.json',path.stat().st_size,now(),json.dumps(schema)))
            config={'upload_id':upload,'chat_index':0,'source_timezone':'Asia/Seoul'}
            db.execute('INSERT INTO collection_runs(id,workspace_id,kind,state,config,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(job,workspace,'json','queued',json.dumps(config),now(),now()))
        import_file(store,job)
        import_seconds=time.perf_counter()-before
        before=time.perf_counter()
        config={'workspace_id':workspace,'chat_key':'channel:100','start':'2026-08-01','end':'2026-11-01','timezone':'Asia/Seoul','sender':'all','content':'all','unit':'messages','include_forwarded':True,'session_minutes':10}
        result=run_analysis(store,config)
        analysis_seconds=time.perf_counter()-before
        before=time.perf_counter();run_analysis(store,config);cached=time.perf_counter()-before
        output={'messages':count,'import_state':store.job(job)['state'],'import_seconds':round(import_seconds,3),'analysis_seconds':round(analysis_seconds,3),'cached_seconds':round(cached,3),'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),'aggregate_total':result['metrics']['total']}
        print(json.dumps(output,ensure_ascii=False,indent=2))
        assert output['aggregate_total']==count


if __name__=='__main__':main()
