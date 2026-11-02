import json
from postscope.services.normalizer import normalize
from conftest import import_data,analyze


def test_reimport_idempotent(client,workspace,sample):
    import_data(client,workspace,sample)
    again=import_data(client,workspace,sample)
    assert again['stats']['same_file']
    with client.app_instance.state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==5


def test_minimal_metadata_and_big_ids(client,workspace,sample):
    sample['messages'][0]['id']=9007199254740993123
    import_data(client,workspace,sample)
    store=client.app_instance.state.store
    with store.connect() as db:
        row=db.execute('SELECT message_id,sender_key FROM messages ORDER BY sent_at_utc LIMIT 1').fetchone()
        assert row[0]=='9007199254740993123' and row[1]=='user:9007199254740993'
        data=''.join(str(tuple(r)) for r in db.execute('SELECT * FROM messages'))
        assert 'NEVER_STORE_THIS_BODY' not in data
        assert 'ignored.pdf' not in data
    assert list((store.directory/'uploads').iterdir())==[]


def test_multi_chat_only_selection(client,workspace,sample):
    another={**sample,'id':101,'name':'다른 방','messages':[{'id':1,'date':'2026-11-02T03:00:00Z','text':''}]}
    job=import_data(client,workspace,{'chats':{'list':[sample,another]}},chat_index=1)
    assert job['state']=='completed',job
    details=client.get('/api/workspaces/'+workspace).json()
    assert len(details['chats'])==1 and details['chats'][0]['count']==1


def test_mixed_bad_rows_and_service(client,workspace,sample):
    sample['messages'] += [{'id':6,'date':'bad'}, {'date':'2026-11-02T03:00:00Z'},None,
        {'id':7,'date':'2026-11-02T03:00:00Z','type':'service','action':'pin_message'}]
    job=import_data(client,workspace,sample)
    assert job['stats']['quarantined']==3
    assert analyze(client,workspace).json()['metrics']['total']==3
    assert analyze(client,workspace).json()['quality']['service_excluded']==1


def test_naive_time_requires_zone(client,workspace,sample):
    sample['messages'][0]['date']='2026-11-01T14:59:00'
    job=import_data(client,workspace,sample,source_timezone=None)
    assert job['stats']['quarantined']==1


def test_names_not_identity_and_peer_collision(client,workspace,sample):
    sample['messages'][1].update(from_id='user2',from_='같은 이름')
    sample['messages'][2].pop('from_id');sample['messages'][2]['from']='같은 이름'
    sample['messages'][3]['from_id']='channel2'
    sample['messages'][4].pop('from_id');sample['messages'][4]['author']='서명 이름'
    import_data(client,workspace,sample)
    labels=client.get('/api/workspaces/'+workspace).json()['senders']
    assert {r['kind'] for r in labels}=={'user','channel','unknown','signature'}
    assert 'user:2' in {r['sender_key'] for r in labels}
    assert 'channel:2' in {r['sender_key'] for r in labels}
    assert len(labels)==5


def test_edit_updates_original_time_not_new_post(client,workspace,sample):
    import_data(client,workspace,sample)
    sample['messages'][1].update(edited='2026-11-03T03:00:00Z',mime_type='application/zip')
    job=import_data(client,workspace,sample)
    assert job['stats']['updated']==1
    d=analyze(client,workspace).json()
    assert d['metrics']['total']==3
    assert analyze(client,workspace,content='pdf').json()['metrics']['total']==1


def test_time_conflict_exposed(client,workspace,sample):
    sample['messages'][0]['date_unixtime']='1793545140'
    sample['messages'][0]['date']='2026-11-01T13:00:00Z'
    job=import_data(client,workspace,sample)
    assert job['stats']['time_conflicts']==1


def test_invalid_json_and_html_rejected(client):
    for name,content in [('result.json',b'{"messages":[{'),('result.html',b'<html>')]:
        response=client.post('/api/imports/preview',files={'file':(name,content)})
        assert response.status_code==400


def test_later_export_does_not_delete_old_records(client,workspace,sample):
    import_data(client,workspace,sample)
    sample['messages']=sample['messages'][1:]
    import_data(client,workspace,sample)
    with client.app_instance.state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==5
