import csv
import io
import json
import zipfile
from datetime import datetime,timedelta,timezone
from fastapi.testclient import TestClient
from postscope.web import create_app
from postscope.services.reports import csv_bytes
from conftest import import_data,analyze


def test_token_origin_and_host_protection(client,workspace):
    assert client.post('/api/workspaces',headers={'x-postscope-token':'wrong'},json={'title':'x'}).status_code==403
    assert client.post('/api/workspaces',headers={'origin':'https://evil.example'},json={'title':'x'}).status_code==403
    assert client.get('/api/workspaces',headers={'host':'evil.example'}).status_code==400
    fresh=TestClient(client.app_instance,base_url='http://127.0.0.1')
    assert fresh.get('/api/workspaces').status_code==403
    assert fresh.get('/api/health').status_code==200


def test_secret_not_echoed_by_validation(client):
    secret='SENSITIVE_API_HASH'
    response=client.post('/api/connections/login',json={'api_id':1,'api_hash':secret,'phone':'+821012345678'})
    assert response.status_code==422
    assert secret not in response.text


def test_declared_and_chunked_upload_limits(client,monkeypatch):
    import postscope.web as web
    import httpx
    monkeypatch.setattr(web,'MAX_FILE',100)
    declared=client.post('/api/imports/preview',content=b'{}',headers={'content-length':str(2*1024*1024)})
    assert declared.status_code==413
    request=httpx.Request('POST','http://127.0.0.1/api/imports/preview',files={'file':('big.json',b'x'*(1024*1024+1000),'application/json')})
    body=request.read()
    headers={'content-type':request.headers['content-type']}
    def chunks():
        for i in range(0,len(body),65536):
            yield body[i:i+65536]
    chunked=client.post('/api/imports/preview',content=chunks(),headers=headers)
    assert chunked.status_code==413,chunked.text


def test_html_escape_and_default_pseudonyms(client,workspace,sample):
    import_data(client,workspace,sample,True)
    analysis=analyze(client,workspace,sender='user:9007199254740993').json()
    response=client.post('/api/reports',json={'analysis_id':analysis['id'],'format':'html'})
    assert response.status_code==200,response.text
    html=client.get(response.json()['download_url']).text
    assert '<script>window.bad=true</script>' not in html
    assert '&lt;script&gt;window.bad=true&lt;/script&gt;' in html
    assert 'user:9007199254740993' not in html
    assert 'channel:100' not in html
    assert 'NEVER_STORE_THIS_BODY' not in html
    assert 'cdn.plot.ly' not in html or 'plotly.js' in html  # bundled library can contain documentation strings
    assert '<script src=' not in html
    assert '생성 시점 스냅샷' in html


def test_csv_totals_bom_config_and_no_secrets(client,workspace,sample):
    import_data(client,workspace,sample,True)
    analysis=analyze(client,workspace).json()
    report=client.post('/api/reports',json={'analysis_id':analysis['id'],'format':'csv'}).json()
    response=client.get(report['download_url'])
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read('daily.csv').startswith(b'\xef\xbb\xbf')
        rows=list(csv.reader(io.StringIO(archive.read('daily.csv').decode('utf-8-sig'))))
        assert sum(int(row[1]) for row in rows[1:])==3
        heat=list(csv.reader(io.StringIO(archive.read('weekday_hour.csv').decode('utf-8-sig'))))
        assert sum(int(row[2]) for row in heat[1:])==3
        config=json.loads(archive.read('analysis_config.json'))
        assert config['snapshot_id']==analysis['snapshot_id']
        assert config['config']['chat_key']==sample['name']
        assert 'workspace_id' not in config['config']


def test_csv_formula_sanitized():
    text=csv_bytes([['=2+2','  +cmd','-formula','@SUM(A1)','safe']]).decode('utf-8-sig')
    assert text.startswith("'=2+2,'  +cmd,'-formula,'@SUM(A1),safe")


def test_export_explicit_identifiers(client,workspace,sample):
    import_data(client,workspace,sample)
    d=analyze(client,workspace,sender='user:9007199254740993').json()
    report=client.post('/api/reports',json={'analysis_id':d['id'],'format':'csv','include_ids':True}).json()
    with zipfile.ZipFile(io.BytesIO(client.get(report['download_url']).content)) as z:
        assert 'user:9007199254740993' in z.read('analysis_config.json').decode()


def test_png_and_full_bundle(client,workspace,sample):
    from PIL import Image
    import_data(client,workspace,sample,True)
    d=analyze(client,workspace).json()
    png=client.post('/api/reports',json={'analysis_id':d['id'],'format':'png'}).json()
    image=Image.open(io.BytesIO(client.get(png['download_url']).content))
    assert image.width>=1600
    bundle=client.post('/api/reports',json={'analysis_id':d['id'],'format':'zip'}).json()
    with zipfile.ZipFile(io.BytesIO(client.get(bundle['download_url']).content)) as archive:
        assert len([x for x in archive.namelist() if x.endswith('.png')])==4


def test_delete_cascades_and_removes_files(client,workspace,sample):
    import_data(client,workspace,sample)
    d=analyze(client,workspace).json()
    report=client.post('/api/reports',json={'analysis_id':d['id'],'format':'csv'}).json()
    assert client.delete('/api/workspaces/'+workspace+'?confirm=bad').status_code==400
    assert client.delete('/api/workspaces/'+workspace+'?confirm=DELETE').status_code==200
    store=client.app_instance.state.store
    with store.connect() as db:
        for table in ['messages','sources','imports','quarantine','analysis_runs','report_snapshots','chats','sender_labels','message_provenance','observation_windows','collection_runs']:
            assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0,table
    assert not list((store.directory/'reports').iterdir())
    assert client.get(report['download_url']).status_code==404


def test_expiry_and_retention_invalidate_cache(client,workspace,sample):
    import_data(client,workspace,sample,True)
    old=analyze(client,workspace).json()
    store=client.app_instance.state.store
    with store.connect() as db:
        db.execute("UPDATE messages SET expires_at_utc=? WHERE message_id='2'",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
    new=analyze(client,workspace).json()
    assert new['metrics']['total']==2 and new['id']!=old['id']
    with store.connect() as db:
        db.execute('UPDATE messages SET last_seen_at=?',((datetime.now(timezone.utc)-timedelta(days=100)).isoformat(),))
    cleaned=analyze(client,workspace).json()
    assert cleaned['metrics']['total']==0
    assert cleaned['quality']['coverage']=='내보내기 범위 미확인'


def test_analysis_uses_consistent_read_snapshot_during_write(client,workspace,sample,monkeypatch):
    from postscope.services import analysis
    from postscope.storage import upsert
    from postscope.services.normalizer import normalize
    import_data(client,workspace,sample)
    original=analysis.calculate
    changed=False
    def calculate_with_write(*args,**kwargs):
        nonlocal changed
        if not changed:
            changed=True
            with client.app_instance.state.store.connect() as db:
                m,_=normalize({'id':99,'date':'2026-11-02T07:00:00Z','text':''})
                upsert(db,workspace,'channel:100',m,'concurrent',0)
                db.execute('UPDATE workspaces SET revision=revision+1 WHERE id=?',(workspace,))
        return original(*args,**kwargs)
    monkeypatch.setattr(analysis,'calculate',calculate_with_write)
    first=analyze(client,workspace)
    assert first.status_code==200,first.text
    assert first.json()['metrics']['total']==3
    second=analyze(client,workspace).json()
    assert second['metrics']['total']==4
