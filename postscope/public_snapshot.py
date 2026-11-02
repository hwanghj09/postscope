"""Public, pseudonymized daily snapshots; no account/session identifiers are emitted."""
import hashlib
import hmac
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .storage import Store,uid,now,upsert
from .services.analysis import run_analysis

FIELDS=('sent_at_utc','edited_at_utc','expires_at_utc','content_kind','document_mime','is_forwarded','is_service','lifecycle')


def publish_pending(output,title='텔레그램 게시 기록'):
    """Missing source is unknown data, never a successful zero or synthetic result."""
    snapshot={'schema_version':1,'status':'awaiting_source','updated_at':None,
              'workspace':{'id':'telegram-pending','title':title,'timezone':'Asia/Seoul',
                           'created_at':None,'revision':0,'is_demo':False,'connection_required':True,
                           'chats':[],'senders':[],'jobs':[],'quarantine':[]},
              'analysis':None,'records':[]}
    directory=Path(output);directory.mkdir(parents=True,exist_ok=True)
    (directory/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
    (directory/'snapshot.sqlite3').unlink(missing_ok=True)
    return snapshot


def pseudonym(value,secret,domain):
    digest=hmac.new(secret.encode(),(domain+'\0'+str(value)).encode(),hashlib.sha256).hexdigest()
    return str(int(digest[:15],16)),digest[:6].upper()


def public_dataset(messages,*,secret,title,start,end,collected_at=None,source='api',complete=True):
    stamp=collected_at or now();senders={};rows=[]
    for message in messages:
        if message.get('expires_at_utc') and message['expires_at_utc']<=stamp:continue
        sender=message.get('sender_key')
        if sender:
            identifier,label=pseudonym(sender,secret,'sender')
            sender_key=message['sender_kind']+':'+identifier
            prefix={'user':'계정','channel':'채널','signature':'서명'}.get(message['sender_kind'],'작성자')
            senders[sender_key]={'sender_key':sender_key,'kind':message['sender_kind'],'label':prefix+' '+label}
        else:sender_key=None
        identifier,_=pseudonym(message['message_id'],secret,'message')
        album=pseudonym(message['album_key'],secret,'album')[0] if message.get('album_key') else None
        rows.append({**{key:message.get(key) for key in FIELDS},'message_id':identifier,'sender_kind':message['sender_kind'],'sender_key':sender_key,'album_key':album})
    rows.sort(key=lambda row:(row['sent_at_utc'],int(row['message_id'])))
    return {'schema_version':1,'collected_at':stamp,'is_demo':False,'title':title,'timezone':'Asia/Seoul',
            'source':source,'requested_start_utc':start,'requested_end_utc':end,'complete':complete,
            'senders':sorted(senders.values(),key=lambda row:row['sender_key']),'messages':rows}


def materialize(dataset,directory):
    store=Store(directory);workspace_id=uid();chat='channel:1'
    with store.connect() as db:
        db.execute('INSERT INTO workspaces VALUES(?,?,?,?,?,?)',(workspace_id,'게시 기록','Asia/Seoul',dataset['collected_at'],1,int(dataset.get('is_demo',False))))
        album_supported=dataset['source']=='api' or any(m.get('album_key') for m in dataset['messages'])
        db.execute('INSERT INTO chats VALUES(?,?,?,?,?)',(workspace_id,chat,dataset['title'],dataset['source'],int(album_supported)))
        batch=uid();fingerprint=hashlib.sha256(json.dumps(dataset,sort_keys=True).encode()).hexdigest()
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)',(batch,workspace_id,dataset['source'],fingerprint,'일일 수집',0,dataset['collected_at']))
        for index,message in enumerate(dataset['messages']):
            upsert(db,workspace_id,chat,message,batch,index)
        db.execute('UPDATE messages SET first_seen_at=?,last_seen_at=? WHERE workspace_id=?',(dataset['collected_at'],dataset['collected_at'],workspace_id))
        for sender in dataset['senders']:
            db.execute('UPDATE sender_labels SET label=? WHERE workspace_id=? AND chat_key=? AND sender_key=?',(sender['label'],workspace_id,chat,sender['sender_key']))
        if dataset.get('complete'):
            db.execute('INSERT INTO observation_windows VALUES(?,?,?,?,?,?)',(uid(),workspace_id,chat,dataset['requested_start_utc'],dataset['requested_end_utc'],'daily accessible snapshot'))
        stats={'inserted':len(dataset['messages']),'quarantined':0,'duplicate':0,'conflict':0,'time_conflicts':0}
        db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?)',(batch,workspace_id,batch,chat,dataset['collected_at'],json.dumps(stats)))
        db.execute("INSERT INTO collection_runs(id,workspace_id,kind,state,config,processed,created_at,updated_at,stats) VALUES(?,?,'api','completed','{}',?,?,?,?)",(uid(),workspace_id,len(dataset['messages']),dataset['collected_at'],dataset['collected_at'],json.dumps(stats)))
    return store,workspace_id,chat


def publish_files(store,workspace_id,chat,output,period_start=None,period_end=None):
    from .browser_engine import BrowserEngine
    from zoneinfo import ZoneInfo
    engine=BrowserEngine.__new__(BrowserEngine);engine.store=store
    workspace=engine.workspace(workspace_id)
    info=workspace['chats'][0]
    zone=ZoneInfo('Asia/Seoul')
    start=period_start or datetime.fromisoformat(info['first_message']).astimezone(zone).date().isoformat()
    end=period_end or datetime.fromisoformat(info['last_message']).astimezone(zone).date().isoformat()
    if workspace['is_demo']:start='2026-10-01'
    info['period_start']=start
    info['period_start']=start;info['period_end']=end
    config={'workspace_id':workspace_id,'chat_key':chat,'start':start,'end':end,'timezone':'Asia/Seoul','sender':'all','content':'all','unit':'messages','include_forwarded':True,'session_minutes':10}
    analysis=run_analysis(store,config)
    with store.connect() as db:
        records=json.loads(db.execute('SELECT records FROM analysis_runs WHERE id=?',(analysis['id'],)).fetchone()[0])
    snapshot={'schema_version':1,'updated_at':workspace['created_at'],'workspace':workspace,
              'analysis':{key:value for key,value in analysis.items() if key not in {'intervals','sessions'}},
              'records':[{key:row.get(key) for key in ('message_id','sender_label','sender_kind','sent_at_utc','content_kind','is_forwarded','members')} for row in records]}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    import shutil
    shutil.copy2(store.path,output/'snapshot.sqlite3')
    return snapshot
