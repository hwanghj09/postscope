"""A browser-worker API over the same metadata store and analysis engine.

No HTTP server, remote uploads, or Telegram credentials are involved here.
"""
import base64
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo
from . import __version__
from .storage import Store, uid, now
from .demo import create_demo
from .adapters.desktop_json import inspect_file, import_file
from .services.analysis import date_bounds, run_analysis


class BrowserEngine:
    def __init__(self, directory='/data'):
        path=Path(directory)/'postscope.sqlite3'
        if path.exists():
            with sqlite3.connect(path) as db:
                # A bundled synthetic example is always usable, regardless of build age.
                db.execute('UPDATE messages SET last_seen_at=? WHERE workspace_id IN (SELECT id FROM workspaces WHERE is_demo=1)',(now(),))
        self.store=Store(directory)
        with self.store.connect() as db:
            # Generated downloads are in-memory files for this visit only.
            db.execute('DELETE FROM report_snapshots')
            db.execute('DELETE FROM uploads')

    def preview(self, body):
        path=Path(body['file_path'])
        if not path.is_file() or path.stat().st_size>100*1024*1024:
            raise ValueError('100 MiB 이하의 JSON 파일을 선택하세요.')
        if not body['filename'].lower().endswith('.json'):
            path.unlink(missing_ok=True)
            raise ValueError('JSON 파일을 선택하세요.')
        upload_id=uid()
        target=self.store.directory/'uploads'/upload_id
        try:
            # Retain at most one unprocessed file, in worker memory only.
            with self.store.connect() as db:
                for row in db.execute('SELECT id FROM uploads'):
                    (self.store.directory/'uploads'/row[0]).unlink(missing_ok=True)
                db.execute('DELETE FROM uploads')
            path.replace(target)
            digest=hashlib.sha256()
            with target.open('rb') as handle:
                while chunk:=handle.read(1024*1024):digest.update(chunk)
            schema=inspect_file(target)
            filename=Path(body['filename'].replace('\\','/')).name[:150]
            size=target.stat().st_size
            with self.store.connect() as db:
                db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?)',(upload_id,digest.hexdigest(),filename,size,now(),json.dumps(schema)))
            return {'id':upload_id,'filename':filename,'bytes':size,'hash':digest.hexdigest(),'schema':schema}
        except Exception:
            target.unlink(missing_ok=True)
            raise ValueError('JSON 구조를 읽지 못했습니다. Telegram Desktop 내보내기 파일을 확인하세요.') from None

    def workspace(self, workspace_id):
        result=self.store.workspace(workspace_id)
        with self.store.connect() as db:
            result['chats']=[dict(r) for r in db.execute('SELECT c.*,COUNT(m.message_id) AS count,MIN(m.sent_at_utc) AS first_message,MAX(m.sent_at_utc) AS last_message FROM chats c LEFT JOIN messages m ON m.workspace_id=c.workspace_id AND m.chat_key=c.chat_key WHERE c.workspace_id=? GROUP BY c.chat_key',(workspace_id,))]
            result['senders']=[dict(r) for r in db.execute('SELECT * FROM sender_labels WHERE workspace_id=? ORDER BY kind,sender_key',(workspace_id,))]
            result['jobs']=[{**dict(r),'stats':json.loads(r['stats']),'worker_active':False} for r in db.execute('SELECT id,kind,state,processed,next_retry_at,error,updated_at,stats FROM collection_runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 30',(workspace_id,))]
            result['quarantine']=[dict(r) for r in db.execute('SELECT row_number,reason FROM quarantine WHERE workspace_id=? LIMIT 200',(workspace_id,))]
        return result

    def request(self, url, method='GET', body=None):
        body=body or {}
        parsed=urlsplit(url);path=parsed.path;query=parse_qs(parsed.query)
        if path=='/api/health':return {'status':'ok','version':__version__,'mode':'browser'}
        if path=='/api/workspaces':
            if method=='GET':
                with self.store.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM workspaces ORDER BY is_demo,created_at DESC')]
            title=str(body.get('title','')).strip()
            if not title or len(title)>100:raise ValueError('작업공간 이름을 입력하세요.')
            zone=body.get('timezone','Asia/Seoul');ZoneInfo(zone);workspace_id=uid()
            with self.store.connect() as db:db.execute('INSERT INTO workspaces(id,title,timezone,created_at) VALUES(?,?,?,?)',(workspace_id,title,zone,now()))
            return self.store.workspace(workspace_id)
        if path=='/api/demo':return {'workspace_id':create_demo(self.store)}
        if path.startswith('/api/workspaces/'):
            workspace_id=path.split('/')[3]
            if method=='GET':return self.workspace(workspace_id)
            if method=='DELETE':
                self.store.workspace(workspace_id)
                if query.get('confirm')!=['DELETE']:raise ValueError('삭제 확인값이 필요합니다.')
                with self.store.connect() as db:
                    files=[r[0] for r in db.execute('SELECT filename FROM report_snapshots WHERE workspace_id=?',(workspace_id,))]
                    db.execute('DELETE FROM workspaces WHERE id=?',(workspace_id,))
                for filename in files:(self.store.directory/'reports'/filename).unlink(missing_ok=True)
                return {'deleted':True}
        if path=='/api/imports/preview':return self.preview(body)
        if path=='/api/imports':
            self.store.workspace(body['workspace_id'])
            if not body.get('accept_partial'):raise ValueError('오류 행 제외 처리를 확인하세요.')
            if body.get('source_timezone'):ZoneInfo(body['source_timezone'])
            if bool(body.get('confirmed_start'))!=bool(body.get('confirmed_end')):raise ValueError('확인한 기간을 모두 입력하세요.')
            if body.get('confirmed_start'):
                if not body.get('source_timezone'):raise ValueError('원본 시간대를 지정하세요.')
                date_bounds(body['confirmed_start'],body['confirmed_end'],body['source_timezone'])
            with self.store.connect() as db:
                upload=db.execute('SELECT schema FROM uploads WHERE id=?',(body['upload_id'],)).fetchone()
                if not upload or not 0<=int(body['chat_index'])<len(json.loads(upload[0])['chats']):raise ValueError('파일을 다시 선택하세요.')
                job_id=uid()
                db.execute("INSERT INTO collection_runs(id,workspace_id,kind,state,config,created_at,updated_at) VALUES(?,?,'json','queued',?,?,?)",(job_id,body['workspace_id'],json.dumps(body),now(),now()))
            import_file(self.store,job_id)
            return {'id':job_id,'state':self.store.job(job_id)['state']}
        if path.startswith('/api/jobs/'):
            result=self.store.job(path.split('/')[3]);result.pop('config',None);return result
        if path=='/api/analyses':
            config={'timezone':'Asia/Seoul','sender':'all','content':'all','unit':'messages','include_forwarded':True,'session_minutes':10,**body}
            if not 1<=int(config['session_minutes'])<=60:raise ValueError('연속 게시 기준은 1~60분입니다.')
            if bool(config.get('compare_start'))!=bool(config.get('compare_end')):raise ValueError('비교 기간을 모두 입력하세요.')
            result=run_analysis(self.store,config)
            return {k:v for k,v in result.items() if k not in {'intervals','sessions'}}
        if path.startswith('/api/analyses/') and path.endswith('/records'):
            cursor=int(query.get('cursor',['0'])[0]);limit=int(query.get('limit',['50'])[0])
            if cursor<0 or not 1<=limit<=200:raise ValueError('잘못된 페이지 범위입니다.')
            with self.store.connect() as db:row=db.execute('SELECT records FROM analysis_runs WHERE id=?',(path.split('/')[3],)).fetchone()
            if not row:raise ValueError('분석을 찾을 수 없습니다.')
            values=json.loads(row[0]);page=[{k:m.get(k) for k in ('message_id','sender_label','sender_kind','sent_at_utc','content_kind','is_forwarded','members')} for m in values[cursor:cursor+limit]]
            return {'items':page,'next_cursor':cursor+limit if cursor+limit<len(values) else None,'total':len(values)}
        if path=='/api/settings':
            if method=='PUT':
                days=int(body['retention_days'])
                if not 1<=days<=3650:raise ValueError('보존 기간은 1~3650일입니다.')
                with self.store.connect() as db:db.execute("UPDATE settings SET value=? WHERE key='retention_days'",(str(days),))
                self.store.cleanup()
            with self.store.connect() as db:return {r['key']:r['value'] for r in db.execute('SELECT * FROM settings')}
        if path=='/api/connections':return []
        if path=='/api/report-snapshot':
            with self.store.connect() as db:row=db.execute('SELECT result FROM analysis_runs WHERE id=?',(body['analysis_id'],)).fetchone()
            if not row:raise ValueError('분석을 찾을 수 없습니다.')
            return json.loads(row[0])
        if path=='/api/reports':
            from .services import browser_reports
            images={k:base64.b64decode(v) for k,v in body.get('pngs',{}).items()}
            browser_reports.PNG_IMAGES=images
            try:
                result=browser_reports.create_report(self.store,body['analysis_id'],body['format'],body.get('include_ids',False))
                with self.store.connect() as db:filename=db.execute('SELECT filename FROM report_snapshots WHERE id=?',(result['id'],)).fetchone()[0]
                report_path=self.store.directory/'reports'/filename
                content=report_path.read_bytes();report_path.unlink(missing_ok=True)
                return {**result,'bytes_base64':base64.b64encode(content).decode()}
            finally:browser_reports.PNG_IMAGES={}
        raise ValueError('계정 연결·수집은 로컬 앱에서 사용할 수 있습니다.')

    def request_json(self, url, method, body_json):
        try:return json.dumps({'ok':True,'data':self.request(url,method,json.loads(body_json))},ensure_ascii=False)
        except Exception as error:
            message=str(error) if isinstance(error,(ValueError,KeyError)) else '분석을 처리하지 못했습니다. 파일 형식을 확인하세요.'
            return json.dumps({'ok':False,'error':message[:250]},ensure_ascii=False)
