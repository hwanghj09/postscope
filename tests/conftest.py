import json
import time
import pytest
from fastapi.testclient import TestClient
from postscope.web import create_app


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/"data")
    with TestClient(app,base_url="http://127.0.0.1:8765") as client:
        client.get("/")
        client.headers["x-postscope-token"]=app.state.csrf_token
        client.app_instance=app
        yield client


@pytest.fixture
def workspace(client):
    return client.post('/api/workspaces',json={'title':'검증 작업공간'}).json()['id']


@pytest.fixture
def sample():
    times=['2026-11-01T14:59:00Z','2026-11-01T15:00:00Z','2026-11-01T15:00:30Z','2026-11-02T03:00:00Z','2026-11-02T15:00:00Z']
    return {'id':100,'name':'<script>window.bad=true</script>','type':'private_supergroup','messages':[
        {'id':i+1,'date':stamp,'from_id':'user9007199254740993','from':'같은 이름','text':'NEVER_STORE_THIS_BODY',**({'file':'ignored.pdf','mime_type':'application/pdf','grouped_id':'G1'} if i in (1,2) else {'photo':True} if i==4 else {})}
        for i,stamp in enumerate(times)]}


def import_data(client,workspace,data,confirmed=False,**extra):
    content=json.dumps(data,ensure_ascii=False).encode()
    preview=client.post('/api/imports/preview',files={'file':('result.json',content,'application/json')})
    assert preview.status_code==200,preview.text
    body={'workspace_id':workspace,'upload_id':preview.json()['id'],'chat_index':0,'source_timezone':'Asia/Seoul','accept_partial':True,**extra}
    if confirmed:
        body.update(confirmed_start='2026-11-01',confirmed_end='2026-11-03')
    response=client.post('/api/imports',json=body)
    assert response.status_code==202,response.text
    job_id=response.json()['id']
    for _ in range(200):
        job=client.get('/api/jobs/'+job_id).json()
        if job['state'] in {'completed','failed'}:
            return job
        time.sleep(.01)
    raise AssertionError('import timeout')


def analyze(client,workspace,**extra):
    return client.post('/api/analyses',json={'workspace_id':workspace,'chat_key':'channel:100','start':'2026-11-02','end':'2026-11-02',**extra})
