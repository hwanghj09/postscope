import asyncio
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import FastAPI,Request,UploadFile,File,HTTPException
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,Field
from telethon import errors
from .storage import Store,uid,now
from .adapters.desktop_json import inspect_file,import_file
from .adapters.telegram import TelegramAdapter
from .services.collector import Collector
from .services.analysis import date_bounds,run_analysis
from .services.reports import create_report
from .demo import create_demo

ROOT=Path(__file__).resolve().parent
MAX_FILE=100*1024*1024


class RequestLimitExceeded(HTTPException):
    def __init__(self):
        super().__init__(413,'요청 크기가 제한을 초과했습니다. JSON은 최대 100 MiB입니다.')


class BodyLimitMiddleware:
    """Also bound chunked multipart bodies before Starlette spools them to disk."""
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        total=0
        started=False
        exceeded=False
        limit_body_sent=False
        limit_payload=json.dumps({'detail':'요청 크기가 제한을 초과했습니다. JSON은 최대 100 MiB입니다.'},ensure_ascii=False).encode()
        async def bounded_receive():
            nonlocal total,exceeded
            event=await receive()
            total+=len(event.get('body',b''))
            if total>MAX_FILE+1024*1024:
                exceeded=True
                raise RequestLimitExceeded()
            return event
        async def tracked_send(event):
            nonlocal started,limit_body_sent
            # FastAPI may translate a receive exception through a multipart task
            # group into HTTP 400. Preserve the actual limit status in that path.
            if exceeded and event['type']=='http.response.start':
                event={**event,'status':413,'headers':[(b'content-type',b'application/json; charset=utf-8'),(b'content-length',str(len(limit_payload)).encode())]}
            elif exceeded and event['type']=='http.response.body':
                event={**event,'body':b'' if limit_body_sent else limit_payload}
                limit_body_sent=True
            if event['type']=='http.response.start':
                started=True
            await send(event)
        def is_limit(error):
            return isinstance(error,RequestLimitExceeded) or any(is_limit(e) for e in getattr(error,'exceptions',()))
        try:
            await self.app(scope,bounded_receive,tracked_send)
        except BaseException as error:
            if started or not is_limit(error):
                raise
            response=JSONResponse({'detail':'요청 크기가 제한을 초과했습니다. JSON은 최대 100 MiB입니다.'},status_code=413)
            await response(scope,receive,send)


class WorkspaceInput(BaseModel):
    title:str=Field(min_length=1,max_length=100)
    timezone:str="Asia/Seoul"


class ImportInput(BaseModel):
    workspace_id:str
    upload_id:str
    chat_index:int=Field(ge=0)
    source_timezone:str|None=None
    confirmed_start:str|None=None
    confirmed_end:str|None=None
    accept_partial:bool=False


class AnalysisInput(BaseModel):
    workspace_id:str
    chat_key:str
    start:str
    end:str
    timezone:str="Asia/Seoul"
    sender:str="all"
    content:Literal["all","text","photo","document","pdf","video","audio","other","unknown"]="all"
    unit:Literal["messages","albums"]="messages"
    include_forwarded:bool=True
    session_minutes:int=Field(default=10,ge=1,le=60)
    compare_start:str|None=None
    compare_end:str|None=None


class LoginInput(BaseModel):
    api_id:int=Field(gt=0)
    api_hash:str=Field(pattern=r"^[a-fA-F0-9]{32}$")
    phone:str=Field(pattern=r"^\+[0-9]{7,15}$")


class VerifyInput(BaseModel):
    connection_id:str
    code:str|None=Field(default=None,max_length=12)
    password:str|None=Field(default=None,max_length=256)


class CollectionInput(BaseModel):
    workspace_id:str
    connection_id:str
    chat_key:str
    start:str
    end:str
    timezone:str="Asia/Seoul"


class ActionInput(BaseModel):
    action:Literal["pause","resume","cancel"]
    connection_id:str|None=None


class ReportInput(BaseModel):
    analysis_id:str
    format:Literal["html","csv","zip","png"]="html"
    include_ids:bool=False


def create_app(data_dir=None):
    store=Store(data_dir or os.environ.get("POSTSCOPE_DATA_DIR",str(Path.cwd()/"data")))
    adapter=TelegramAdapter(store)
    collector=Collector(store,adapter)
    token=secrets.token_urlsafe(32)
    tasks=set()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await collector.close()
        await adapter.close()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)

    app=FastAPI(title="PostScope",version="1.0.0",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store,app.state.adapter,app.state.collector=store,adapter,collector
    app.state.csrf_token=token
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","[::1]"])

    @app.middleware("http")
    async def protect(request,call_next):
        try:
            content_length=int(request.headers.get("content-length","0"))
        except ValueError:
            return JSONResponse({'detail':'잘못된 요청 크기입니다.'},status_code=400)
        if content_length>MAX_FILE+1024*1024:
            return JSONResponse({"detail":"JSON 크기 제한은 100 MiB입니다. 기간을 나눠 내보내세요."},status_code=413)
        if request.url.path.startswith("/api/") and request.url.path!="/api/health":
            if not secrets.compare_digest(request.cookies.get("postscope_session",""),token):
                return JSONResponse({"detail":"로컬 실행 세션이 필요합니다. 첫 화면을 새로고침하세요."},status_code=403)
            if request.method not in {"GET","HEAD"}:
                origin=request.headers.get("origin")
                if origin:
                    parsed=urlsplit(origin)
                    if parsed.scheme not in {"http","https"} or parsed.netloc!=request.url.netloc:
                        return JSONResponse({"detail":"다른 출처의 요청은 허용되지 않습니다."},status_code=403)
                if not secrets.compare_digest(request.headers.get("x-postscope-token",""),token):
                    return JSONResponse({"detail":"요청 보호 토큰이 일치하지 않습니다."},status_code=403)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/") or request.url.path=="/":
            response.headers["Cache-Control"]="no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request,exc):
        return JSONResponse({"detail":str(exc)[:250]},status_code=400)

    @app.exception_handler(ZoneInfoNotFoundError)
    async def zone_error(request,exc):
        return JSONResponse({"detail":"올바른 IANA 시간대를 입력하세요. 예: Asia/Seoul"},status_code=400)

    @app.exception_handler(errors.RPCError)
    async def telegram_error(request,exc):
        if isinstance(exc,errors.FloodWaitError):
            message=f"Telegram 호출 제한입니다. {exc.seconds}초 후 다시 시도하세요."
        elif isinstance(exc,(errors.PhoneCodeInvalidError,errors.PhoneCodeExpiredError)):
            message="로그인 코드가 올바르지 않거나 만료되었습니다."
        elif isinstance(exc,errors.PasswordHashInvalidError):
            message="2단계 인증 비밀번호가 올바르지 않습니다."
        else:
            message="Telegram 연결에 실패했습니다. 앱 정보·계정 인증·방 접근 권한을 확인하세요."
        return JSONResponse({"detail":message},status_code=400)

    # Pydantic errors normally echo input; remove it to keep login secrets out of responses.
    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse({"detail":"입력 형식을 확인하세요.","fields":[".".join(map(str,e["loc"][1:])) for e in exc.errors()]},status_code=422)

    templates=Jinja2Templates(directory=ROOT/"templates")
    app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
    app.mount("/assets",StaticFiles(directory=ROOT/"assets"),name="assets")

    @app.get("/")
    def index(request:Request):
        response=templates.TemplateResponse(request=request,name="index.html",context={"token":token})
        response.set_cookie("postscope_session",token,httponly=True,samesite="strict")
        return response

    @app.get("/api/health")
    def health():
        return {"status":"ok","version":"1.0.0"}

    @app.get("/api/workspaces")
    def workspaces():
        with store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM workspaces ORDER BY is_demo,created_at DESC")]

    @app.post("/api/workspaces")
    def workspace_create(body:WorkspaceInput):
        ZoneInfo(body.timezone)
        workspace=uid()
        with store.connect() as db:
            db.execute("INSERT INTO workspaces(id,title,timezone,created_at) VALUES(?,?,?,?)",(workspace,body.title.strip(),body.timezone,now()))
        return store.workspace(workspace)

    @app.post("/api/demo")
    def demo():
        return {"workspace_id":create_demo(store)}

    @app.get("/api/workspaces/{workspace_id}")
    def workspace_detail(workspace_id:str):
        workspace=store.workspace(workspace_id)
        with store.connect() as db:
            workspace["chats"]=[dict(r) for r in db.execute("SELECT c.*,COUNT(m.message_id) AS count,MIN(m.sent_at_utc) AS first_message,MAX(m.sent_at_utc) AS last_message FROM chats c LEFT JOIN messages m ON m.workspace_id=c.workspace_id AND m.chat_key=c.chat_key WHERE c.workspace_id=? GROUP BY c.chat_key",(workspace_id,))]
            workspace["senders"]=[dict(r) for r in db.execute("SELECT * FROM sender_labels WHERE workspace_id=? ORDER BY kind,sender_key",(workspace_id,))]
            workspace["jobs"]=[{**dict(r),"stats":json.loads(r["stats"])} for r in db.execute("SELECT id,kind,state,processed,next_retry_at,error,updated_at,stats FROM collection_runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 30",(workspace_id,))]
            for job in workspace["jobs"]:
                job["worker_active"]=job["id"] in collector.tasks and not collector.tasks[job["id"]].done()
            workspace["quarantine"]=[dict(r) for r in db.execute("SELECT row_number,reason FROM quarantine WHERE workspace_id=? LIMIT 200",(workspace_id,))]
        return workspace

    @app.post("/api/imports/preview")
    async def preview(file:UploadFile=File(...)):
        if not (file.filename or "").lower().endswith(".json"):
            raise ValueError("Telegram Desktop의 JSON 파일을 선택하세요. HTML은 지원하지 않습니다.")
        upload_id=uid()
        path=store.directory/"uploads"/upload_id
        size=0
        digest=hashlib.sha256()
        try:
            with path.open("wb") as output:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>MAX_FILE:
                        raise HTTPException(413,"JSON 크기 제한은 100 MiB입니다. 기간을 나눠 내보내세요.")
                    digest.update(chunk)
                    output.write(chunk)
            try:
                schema=await asyncio.to_thread(inspect_file,path)
            except Exception as exc:
                raise ValueError("JSON 구조를 읽지 못했습니다. Telegram Desktop의 완전한 JSON 파일인지 확인하세요.") from exc
            filename=Path(file.filename.replace("\\","/")).name[:150]
            with store.connect() as db:
                db.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?)",(upload_id,digest.hexdigest(),filename,size,now(),json.dumps(schema)))
            return {"id":upload_id,"filename":filename,"bytes":size,"hash":digest.hexdigest(),"schema":schema,"preview_type":"전체 구조 검사 · 본문 미보관"}
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post("/api/imports",status_code=202)
    async def import_start(body:ImportInput):
        store.workspace(body.workspace_id)
        if not body.accept_partial:
            raise ValueError("오류 행을 격리하고 정상 행만 가져오는 처리 방식을 확인하세요.")
        if body.source_timezone:
            ZoneInfo(body.source_timezone)
        if bool(body.confirmed_start)!=bool(body.confirmed_end):
            raise ValueError("조회 범위의 시작일과 종료일을 모두 입력하세요.")
        if body.confirmed_start:
            if not body.source_timezone:
                raise ValueError("조회 범위를 확인하려면 원본 시간대를 지정하세요.")
            date_bounds(body.confirmed_start,body.confirmed_end,body.source_timezone)
        with store.connect() as db:
            upload=db.execute("SELECT schema FROM uploads WHERE id=?",(body.upload_id,)).fetchone()
            if not upload or body.chat_index>=len(json.loads(upload[0])["chats"]):
                raise ValueError("선택한 대화 또는 임시 파일이 없습니다.")
            if db.execute("SELECT 1 FROM collection_runs WHERE kind='json' AND state IN ('queued','running') AND json_extract(config,'$.upload_id')=?",(body.upload_id,)).fetchone():
                raise ValueError("이 파일을 이미 처리 중입니다.")
            job_id=uid()
            db.execute("INSERT INTO collection_runs(id,workspace_id,kind,state,config,created_at,updated_at) VALUES(?,?,'json','queued',?,?,?)",(job_id,body.workspace_id,body.model_dump_json(),now(),now()))
        task=asyncio.create_task(asyncio.to_thread(import_file,store,job_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"id":job_id,"state":"queued"}

    @app.get("/api/connections")
    def connections():
        return [{"id":key,"state":c["state"]} for key,c in adapter.connections.items()]

    @app.post("/api/connections/login")
    async def login(body:LoginInput):
        try:
            return await adapter.login(body.api_id,body.api_hash,body.phone)
        except (OSError,asyncio.TimeoutError):
            raise ValueError("Telegram 네트워크 연결 시간이 초과되었습니다. PC의 네트워크를 확인하세요.")

    @app.post("/api/connections/verify")
    async def verify(body:VerifyInput):
        if not body.code and not body.password:
            raise ValueError("로그인 코드 또는 2단계 인증 비밀번호를 입력하세요.")
        return await adapter.verify(body.connection_id,body.code,body.password)

    @app.delete("/api/connections/{connection_id}")
    async def disconnect(connection_id:str):
        with store.connect() as db:
            db.execute("UPDATE collection_runs SET state='paused',error='계정 연결 해제로 일시정지됨' WHERE state IN ('running','waiting','queued') AND json_extract(config,'$.connection_id')=?",(connection_id,))
        return await adapter.disconnect(connection_id)

    @app.get("/api/chats")
    async def chats(connection_id:str,offset:int=0):
        if offset<0:
            raise ValueError("잘못된 목록 위치입니다.")
        return await adapter.chats(connection_id,offset)

    @app.post("/api/collections",status_code=202)
    async def collect(body:CollectionInput):
        from datetime import datetime,timezone
        store.workspace(body.workspace_id)
        start,_=date_bounds(body.start,body.end,body.timezone)
        if start>=datetime.now(timezone.utc):
            raise ValueError("미래 기간의 기록은 수집할 수 없습니다.")
        connection=adapter.get(body.connection_id)
        if (body.chat_key,body.start,body.end,body.timezone) not in connection.get("sampled",set()):
            raise ValueError("선택한 대화·기간의 표본 점검을 먼저 실행하세요.")
        entity=connection["entities"].get(body.chat_key)
        if not entity:
            raise ValueError("접근 가능한 방 목록에서 대화를 선택하세요.")
        config={**body.model_dump(),"account_id":connection["account_id"],"snapshot_at":now()}
        job_id=uid()
        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO chats VALUES(?,?,?,?,1)",(body.workspace_id,body.chat_key,getattr(entity,"title",None) or "API 대화",type(entity).__name__))
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)",(uid(),body.workspace_id,"api",job_id,"API 접근 가능 스냅샷",0,now()))
            db.execute("INSERT INTO collection_runs(id,workspace_id,kind,state,config,created_at,updated_at) VALUES(?,?,'api','queued',?,?,?)",(job_id,body.workspace_id,json.dumps(config),now(),now()))
        collector.launch(job_id)
        return {"id":job_id,"state":"queued"}

    @app.post("/api/collections/sample")
    async def sample(body:CollectionInput):
        from datetime import datetime
        store.workspace(body.workspace_id)
        start,end=date_bounds(body.start,body.end,body.timezone)
        messages=await adapter.page(body.connection_id,body.chat_key,end,None)
        selected=[m for m in messages if start<=datetime.fromisoformat(m['sent_at_utc'])<end]
        adapter.get(body.connection_id).setdefault("sampled",set()).add((body.chat_key,body.start,body.end,body.timezone))
        return {"sample_count":len(selected),"inspected":len(messages),"identified":sum(m['sender_kind'] in {'user','channel'} for m in selected),"album_supported":any(m['album_key'] for m in messages),"note":"직전 페이지 최대 100건의 표본입니다. 전체 기록의 확보를 뜻하지 않습니다."}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id:str):
        job=store.job(job_id)
        job.pop("config",None)
        return job

    @app.post("/api/jobs/{job_id}/actions")
    async def job_action(job_id:str,body:ActionInput):
        job=store.job(job_id)
        if job["kind"]!="api":
            raise ValueError("JSON 가져오기는 원자적으로 처리합니다. 완료 후 결과를 확인하세요.")
        if job["state"] in {"completed","failed","cancelled"}:
            raise ValueError("종료된 작업은 변경할 수 없습니다. 새 수집 작업을 만드세요.")
        if body.action=="resume":
            config=job["config"]
            if body.connection_id:
                connection=adapter.get(body.connection_id)
                if connection["account_id"]!=config["account_id"]:
                    raise ValueError("원래 수집 계정으로 다시 연결하세요.")
                config["connection_id"]=body.connection_id
                with store.connect() as db:
                    db.execute("UPDATE collection_runs SET config=? WHERE id=?",(json.dumps(config),job_id))
            adapter.get(config["connection_id"])
            with store.connect() as db:
                db.execute('UPDATE collection_runs SET cursor=COALESCE(page_start_cursor,cursor) WHERE id=?',(job_id,))
            store.update_job(job_id,state="waiting" if job["next_retry_at"] else "queued",error=None)
            collector.launch(job_id)
        else:
            store.update_job(job_id,state="paused" if body.action=="pause" else "cancelled")
        return job_detail(job_id)

    @app.post("/api/analyses")
    def analysis(body:AnalysisInput):
        if bool(body.compare_start)!=bool(body.compare_end):
            raise ValueError("비교 기간의 시작일과 종료일을 모두 입력하세요.")
        result=run_analysis(store,body.model_dump())
        # Only aggregates go to the dashboard; raw interval rows are for CSV export.
        return {k:v for k,v in result.items() if k not in {"intervals","sessions"}}

    @app.get("/api/analyses/{analysis_id}/records")
    def records(analysis_id:str,cursor:int=0,limit:int=50):
        if cursor<0 or not 1<=limit<=200:
            raise ValueError("페이지 크기는 1~200, 위치는 0 이상이어야 합니다.")
        with store.connect() as db:
            row=db.execute("SELECT records FROM analysis_runs WHERE id=?",(analysis_id,)).fetchone()
        if not row:
            raise HTTPException(404,"분석을 찾을 수 없습니다.")
        values=json.loads(row[0])
        # No real sender IDs leave the record endpoint by default.
        page=[{k:m.get(k) for k in ("message_id","sender_label","sender_kind","sent_at_utc","content_kind","is_forwarded","members")} for m in values[cursor:cursor+limit]]
        return {"items":page,"next_cursor":cursor+limit if cursor+limit<len(values) else None,"total":len(values)}

    @app.post("/api/reports")
    def report(body:ReportInput):
        return create_report(store,body.analysis_id,body.format,body.include_ids)

    @app.get("/api/reports/{report_id}/download")
    def download(report_id:str):
        with store.connect() as db:
            row=db.execute("SELECT filename FROM report_snapshots WHERE id=?",(report_id,)).fetchone()
        if not row or not (store.directory/"reports"/row[0]).is_file():
            raise HTTPException(404,"보고서를 찾을 수 없습니다.")
        return FileResponse(store.directory/"reports"/row[0],filename="PostScope-report."+row[0].rsplit(".",1)[-1])

    @app.get("/api/settings")
    def settings():
        with store.connect() as db:
            return {r["key"]:r["value"] for r in db.execute("SELECT * FROM settings")}

    class SettingsInput(BaseModel):
        retention_days:int=Field(ge=1,le=3650)

    @app.put("/api/settings")
    def settings_save(body:SettingsInput):
        with store.connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key='retention_days'",(str(body.retention_days),))
        store.cleanup()
        return settings()

    @app.delete("/api/workspaces/{workspace_id}")
    async def workspace_delete(workspace_id:str,confirm:str):
        store.workspace(workspace_id)
        if confirm!="DELETE":
            raise ValueError("삭제 확인값이 필요합니다.")
        with store.connect() as db:
            if db.execute("SELECT 1 FROM collection_runs WHERE workspace_id=? AND state IN ('running','queued','waiting')",(workspace_id,)).fetchone():
                raise ValueError("진행 중인 수집을 취소하거나 완료한 후 삭제하세요.")
            paths=[r[0] for r in db.execute("SELECT filename FROM report_snapshots WHERE workspace_id=?",(workspace_id,))]
            db.execute("DELETE FROM workspaces WHERE id=?",(workspace_id,))
        for filename in paths:
            (store.directory/"reports"/filename).unlink(missing_ok=True)
        return {"deleted":True}

    app.add_middleware(BodyLimitMiddleware)
    return app
