import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from telethon import errors
from ..storage import now,uid,upsert
from .analysis import date_bounds


class Collector:
    def __init__(self,store,adapter):
        self.store,self.adapter=store,adapter
        self.tasks={}
        self.account_locks={}

    def launch(self,job_id):
        if job_id in self.tasks and not self.tasks[job_id].done():
            return
        self.tasks[job_id]=asyncio.create_task(self.run(job_id))

    async def run(self,job_id):
        job=self.store.job(job_id)
        config=job["config"]
        lock=self.account_locks.setdefault(config["account_id"],asyncio.Lock())
        async with lock:
            retries=0
            while True:
                job=self.store.job(job_id)
                config=job["config"]
                if job["state"] in {"paused","cancelled","completed","failed"}:
                    return
                with self.store.connect() as db:
                    cooldown=db.execute('SELECT next_retry_at FROM account_waits WHERE account_id=?',(config['account_id'],)).fetchone()
                if cooldown and datetime.fromisoformat(cooldown[0])>datetime.now(timezone.utc):
                    if job['state']!='waiting' or job['next_retry_at']!=cooldown[0]:
                        self.store.update_job(job_id,state='waiting',next_retry_at=cooldown[0],error='계정에 적용된 Telegram 호출 제한 대기')
                    await asyncio.sleep(1)
                    continue
                if job["next_retry_at"] and datetime.fromisoformat(job["next_retry_at"])>datetime.now(timezone.utc):
                    await asyncio.sleep(min(1,(datetime.fromisoformat(job["next_retry_at"])-datetime.now(timezone.utc)).total_seconds()))
                    continue
                start,end=date_bounds(config["start"],config["end"],config["timezone"])
                if config.get('snapshot_at'):
                    end=min(end,datetime.fromisoformat(config['snapshot_at']))
                try:
                    connection=self.adapter.get(config["connection_id"])
                    if connection["account_id"]!=config["account_id"]:
                        raise ValueError("원래 수집 계정으로 다시 연결하세요.")
                    self.store.update_job(job_id,state="running",next_retry_at=None,error=None)
                    page=await self.adapter.page(config["connection_id"],config["chat_key"],end,job["cursor"])
                    latest=self.store.job(job_id)
                    if latest["state"] in {"paused","cancelled"}:
                        return
                    reached_start=not page or min(datetime.fromisoformat(m["sent_at_utc"]) for m in page)<start
                    stats=job["stats"]
                    with self.store.connect() as db:
                        for index,m in enumerate(page):
                            moment=datetime.fromisoformat(m["sent_at_utc"])
                            # Read one full boundary page: enough context to avoid truncating ordinary albums.
                            if start-timedelta(days=1)<=moment<end+timedelta(days=1):
                                status=upsert(db,job["workspace_id"],config["chat_key"],m,job_id,index)
                                stats[status]=stats.get(status,0)+1
                            if m["album_key"]:
                                db.execute("UPDATE chats SET album_supported=1 WHERE workspace_id=? AND chat_key=?",(job["workspace_id"],config["chat_key"]))
                        cursor=str(min(int(m["message_id"]) for m in page)) if page else job["cursor"]
                        if page and cursor==job["cursor"]:
                            raise ValueError("API 커서가 진행되지 않았습니다. 저장 지점은 유지됩니다.")
                        db.execute("UPDATE collection_runs SET cursor=?,page_start_cursor=?,processed=processed+?,stats=?,updated_at=?,state=? WHERE id=?",(cursor,job['cursor'] or '0',len(page),json.dumps(stats),now(),"completed" if reached_start else "running",job_id))
                        db.execute("UPDATE workspaces SET revision=revision+1 WHERE id=?",(job["workspace_id"],))
                        if reached_start:
                            db.execute("INSERT INTO observation_windows VALUES(?,?,?,?,?,?)",(uid(),job["workspace_id"],config["chat_key"],start.isoformat(),end.isoformat(),"accessible API snapshot"))
                        elif page:
                            lower=max(start,min(datetime.fromisoformat(m["sent_at_utc"]) for m in page))
                            if lower<end:
                                db.execute("INSERT INTO observation_windows VALUES(?,?,?,?,?,?)",(uid(),job["workspace_id"],config["chat_key"],lower.isoformat(),end.isoformat(),"partial API history"))
                    retries=0
                    if reached_start:
                        return
                    await asyncio.sleep(.3)
                except errors.FloodWaitError as exc:
                    deadline=(datetime.now(timezone.utc)+timedelta(seconds=exc.seconds)).isoformat()
                    with self.store.connect() as db:
                        db.execute('INSERT INTO account_waits VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET next_retry_at=MAX(account_waits.next_retry_at,excluded.next_retry_at)',(config['account_id'],deadline))
                    self.store.update_job(job_id,state="waiting",next_retry_at=deadline,error="Telegram 호출 제한: 지정 시각까지 대기합니다.")
                except (OSError,asyncio.TimeoutError,ConnectionError):
                    retries+=1
                    if retries>5:
                        self.store.update_job(job_id,state="paused",error="네트워크 재시도 5회 실패. 연결을 확인한 후 재개하세요.")
                        return
                    self.store.update_job(job_id,state="waiting",next_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=2**retries+random.random())).isoformat(),error="네트워크 연결 재시도 대기")
                except (errors.UnauthorizedError,errors.ForbiddenError,errors.AuthKeyError):
                    connection=self.adapter.connections.get(config["connection_id"])
                    if connection:
                        connection["state"]="expired"
                    self.store.update_job(job_id,state="failed",error="인증 만료 또는 접근 권한 오류. 기존 기록은 분석할 수 있습니다.")
                    return
                except Exception as exc:
                    self.store.update_job(job_id,state="failed",error=str(exc)[:200] if isinstance(exc,ValueError) else "수집을 중단했습니다. 계정 접근·API 설정·저장 공간을 확인하세요.")
                    return

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
        with self.store.connect() as db:
            db.execute("UPDATE collection_runs SET state='paused' WHERE state IN ('running','queued')")
