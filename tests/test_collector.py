import asyncio
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from telethon import errors,types
from postscope.storage import Store,now,uid
from postscope.services.collector import Collector
from postscope.services.normalizer import normalize
from postscope.adapters.telegram import message_metadata


def setup_job(tmp_path,state='queued',next_retry_at=None):
    store=Store(tmp_path/'collector')
    workspace,job=uid(),uid()
    config={'workspace_id':workspace,'connection_id':'connection','account_id':'1','chat_key':'channel:100','start':'2026-11-02','end':'2026-11-02','timezone':'Asia/Seoul'}
    with store.connect() as db:
        db.execute('INSERT INTO workspaces(id,title,timezone,created_at) VALUES(?,?,?,?)',(workspace,'test','Asia/Seoul',now()))
        db.execute('INSERT INTO chats VALUES(?,?,?,?,1)',(workspace,'channel:100','test','channel'))
        db.execute('INSERT INTO collection_runs(id,workspace_id,kind,state,config,next_retry_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(job,workspace,'api',state,json.dumps(config),next_retry_at,now(),now()))
    return store,workspace,job


class MockAdapter:
    def __init__(self,pages):self.pages=iter(pages);self.calls=[];self.connections={'connection':{'account_id':'1','state':'connected'}}
    def get(self,key):return self.connections[key]
    async def page(self,connection,key,end,cursor):
        self.calls.append(cursor)
        item=next(self.pages)
        if isinstance(item,Exception):raise item
        return item


def metadata(message_id,stamp):return normalize({'id':message_id,'date':stamp,'from_id':'user1','text':''})[0]


def test_atomic_pages_resume_deduplicate_and_complete(tmp_path):
    store,workspace,job=setup_job(tmp_path)
    adapter=MockAdapter([[metadata(3,'2026-11-02T03:00:00Z'),metadata(2,'2026-11-01T15:00:00Z')],[]])
    asyncio.run(Collector(store,adapter).run(job))
    assert store.job(job)['state']=='completed'
    assert adapter.calls==[None,'2']
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM observation_windows').fetchone()[0]>=1


def test_restart_keeps_cursor_and_flood_wait_deadline(tmp_path):
    deadline=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    store,workspace,job=setup_job(tmp_path,'waiting',deadline)
    with store.connect() as db:db.execute("UPDATE collection_runs SET cursor='50' WHERE id=?",(job,))
    restarted=Store(store.directory)
    assert restarted.job(job)['next_retry_at']==deadline
    assert restarted.job(job)['cursor']=='50'
    adapter=MockAdapter([[]])
    async def check():
        task=asyncio.create_task(Collector(restarted,adapter).run(job))
        await asyncio.sleep(.03)
        assert not adapter.calls
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
    asyncio.run(check())


def test_flood_wait_persisted_without_early_retry(tmp_path):
    store,workspace,job=setup_job(tmp_path)
    adapter=MockAdapter([errors.FloodWaitError(request=None,capture=120),[]])
    async def check():
        task=asyncio.create_task(Collector(store,adapter).run(job))
        await asyncio.sleep(.03)
        status=store.job(job)
        assert status['state']=='waiting'
        assert datetime.fromisoformat(status['next_retry_at'])>datetime.now(timezone.utc)+timedelta(seconds=110)
        assert len(adapter.calls)==1
        store.update_job(job,state='cancelled')
        task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(check())


def test_auth_failure_is_terminal(tmp_path):
    store,workspace,job=setup_job(tmp_path)
    adapter=MockAdapter([errors.AuthKeyUnregisteredError(request=None),[]])
    asyncio.run(Collector(store,adapter).run(job))
    assert store.job(job)['state']=='failed'
    assert len(adapter.calls)==1
    assert adapter.connections['connection']['state']=='expired'


def test_restart_pauses_running_job(tmp_path):
    store,workspace,job=setup_job(tmp_path,'running')
    assert Store(store.directory).job(job)['state']=='paused'


def test_telethon_service_message_without_regular_fields():
    message=types.MessageService(id=1,peer_id=types.PeerChannel(100),date=datetime.now(timezone.utc),action=types.MessageActionPinMessage(),from_id=types.PeerUser(1))
    data=message_metadata(message)
    assert data['is_service']==1
