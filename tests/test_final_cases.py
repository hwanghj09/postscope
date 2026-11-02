import asyncio
import json
from datetime import datetime,timedelta,timezone
from telethon import types
from postscope.adapters.telegram import message_metadata
from postscope.storage import uid,now
from postscope.services.collector import Collector
from postscope.services.analysis import calculate
from postscope.services.normalizer import normalize
from test_collector import setup_job,MockAdapter,metadata


def test_channel_post_without_from_id_is_channel_account():
    message=types.Message(id=1,peer_id=types.PeerChannel(555),date=datetime.now(timezone.utc),post=True,message='MUST_NOT_READ')
    data=message_metadata(message)
    assert data['sender_key']=='channel:555' and data['sender_kind']=='channel'


def test_sticker_file_and_unverified_pdf_extension():
    base={'id':1,'date':'2026-11-02T03:00:00Z','file':'example.pdf'}
    data,_=normalize(base)
    assert data['content_kind']=='document' and data['document_mime'] is None
    sticker,_=normalize({**base,'media_type':'sticker'})
    assert sticker['content_kind']=='other'


def test_new_job_respects_persistent_account_wait(tmp_path):
    store,workspace,job=setup_job(tmp_path)
    deadline=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    with store.connect() as db:
        db.execute('INSERT INTO account_waits VALUES(?,?)',('1',deadline))
    adapter=MockAdapter([[]])
    async def check():
        collector=Collector(store,adapter)
        task=asyncio.create_task(collector.run(job))
        await asyncio.sleep(.03)
        assert adapter.calls==[]
        assert store.job(job)['next_retry_at']==deadline
        task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(check())


def test_api_today_only_observes_until_fixed_snapshot(tmp_path):
    store,workspace,job=setup_job(tmp_path)
    with store.connect() as db:
        config=json.loads(db.execute('SELECT config FROM collection_runs WHERE id=?',(job,)).fetchone()[0])
        config['snapshot_at']='2026-11-02T03:00:00+00:00'
        db.execute('UPDATE collection_runs SET config=? WHERE id=?',(json.dumps(config),job))
    adapter=MockAdapter([[]])
    asyncio.run(Collector(store,adapter).run(job))
    with store.connect() as db:
        assert db.execute('SELECT end_utc FROM observation_windows').fetchone()[0]=='2026-11-02T03:00:00+00:00'


def test_normalized_rate_excludes_posts_outside_verified_intervals():
    # Same weekday/hour, two weeks. Only the first week has confirmed observation.
    messages=[normalize({'id':1,'date':'2026-11-02T03:00:00Z','text':''})[0],normalize({'id':2,'date':'2026-11-09T03:00:00Z','text':''})[0]]
    windows=[{'start_utc':'2026-11-02T03:00:00+00:00','end_utc':'2026-11-02T04:00:00+00:00'}]
    d,_=calculate(messages,windows,{'start':'2026-11-02','end':'2026-11-09','timezone':'Asia/Seoul'})
    assert d['heatmap'][0][12]==2
    assert d['normalized_counts'][0][12]==1
    assert d['rates'][0][12]==1
