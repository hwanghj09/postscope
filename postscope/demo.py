import random
from datetime import datetime,timedelta,timezone
from .storage import uid,now,upsert
from .services.normalizer import normalize


def create_demo(store):
    with store.connect() as db:
        existing=db.execute("SELECT id FROM workspaces WHERE is_demo=1").fetchone()
        if existing:
            return existing[0]
        workspace=uid()
        db.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,1)",(workspace,"게시 기록 분석", "Asia/Seoul",now(),1))
        key="channel:10001"
        db.execute("INSERT INTO chats VALUES(?,?,?,?,1)",(workspace,key,"PostScope 리서치 그룹","private_supergroup"))
        source=uid()
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)",(source,workspace,"synthetic","demo-v1","합성 데모 · 실제 대화 아님",0,now()))
        rng=random.Random(42)
        message_id=0
        start=datetime(2026,8,31,15,tzinfo=timezone.utc)
        for day in range(61):
            count=rng.randint(28,90)+(18 if day%7 in (1,2,3) else 0)
            for j in range(count):
                hour=rng.choices(range(24),weights=[2,1,1,1,1,1,1,3,5,8,12,9,7,9,12,14,11,10,7,8,12,17,13,6])[0]
                stamp=start+timedelta(days=day,hours=hour,minutes=rng.randrange(60),seconds=rng.randrange(60))
                message_id+=1
                author=rng.choices([1,2,3,4,None],weights=[50,22,12,10,6])[0]
                raw={"id":message_id,"date_unixtime":str(int(stamp.timestamp())),"text":"","from_id":"user"+str(author) if author else None}
                choice=rng.random()
                if choice<.26:
                    raw.update(file=True,mime_type="application/pdf" if choice<.15 else "application/zip")
                elif choice<.48:
                    raw["photo"]=True
                elif choice<.56:
                    raw["media_type"]="video_file"
                if j%19==0:
                    raw["forwarded_from"]="synthetic"
                if j%33==0:
                    raw["grouped_id"]="demo-"+str(day)+"-"+str(j)
                m,_=normalize(raw)
                upsert(db,workspace,key,m,source,message_id)
                if raw.get("grouped_id"):
                    message_id+=1
                    raw.update(id=message_id,date_unixtime=str(int(stamp.timestamp())+2))
                    upsert(db,workspace,key,normalize(raw)[0],source,message_id)
        db.execute("INSERT INTO observation_windows VALUES(?,?,?,?,?,?)",(uid(),workspace,key,start.isoformat(),(start+timedelta(days=61)).isoformat(),"synthetic"))
        return workspace
