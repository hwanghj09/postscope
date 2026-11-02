"""SQLite transactions and versioned schema. Only normalized metadata is persisted."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.directory.chmod(0o700)
        for name in ("uploads", "reports"):
            (self.directory / name).mkdir(exist_ok=True, mode=0o700)
        self.path = self.directory / "postscope.sqlite3"
        self.migrate()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
            CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT);
            CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,title TEXT NOT NULL,timezone TEXT NOT NULL,
                created_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,is_demo INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                kind TEXT,hash TEXT,filename TEXT,bytes INTEGER,created_at TEXT,UNIQUE(workspace_id,hash));
            CREATE TABLE IF NOT EXISTS chats(workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                chat_key TEXT,title TEXT,kind TEXT,album_supported INTEGER DEFAULT 0,PRIMARY KEY(workspace_id,chat_key));
            CREATE TABLE IF NOT EXISTS sender_labels(workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                chat_key TEXT,sender_key TEXT,kind TEXT,label TEXT,PRIMARY KEY(workspace_id,chat_key,sender_key));
            CREATE TABLE IF NOT EXISTS messages(workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                chat_key TEXT,message_id TEXT,sender_kind TEXT,sender_key TEXT,sent_at_utc TEXT,edited_at_utc TEXT,
                expires_at_utc TEXT,album_key TEXT,content_kind TEXT,document_mime TEXT,is_forwarded INTEGER,
                is_service INTEGER,lifecycle TEXT,first_seen_at TEXT,last_seen_at TEXT,
                PRIMARY KEY(workspace_id,chat_key,message_id));
            CREATE INDEX IF NOT EXISTS messages_time ON messages(workspace_id,chat_key,sent_at_utc);
            CREATE INDEX IF NOT EXISTS messages_sender ON messages(workspace_id,chat_key,sender_key,sent_at_utc);
            CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,chat_key TEXT,created_at TEXT,stats TEXT);
            CREATE TABLE IF NOT EXISTS message_provenance(workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                chat_key TEXT,message_id TEXT,batch_id TEXT,row_number INTEGER,PRIMARY KEY(workspace_id,chat_key,message_id,batch_id));
            CREATE TABLE IF NOT EXISTS quarantine(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                batch_id TEXT,row_number INTEGER,reason TEXT);
            CREATE TABLE IF NOT EXISTS collection_runs(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                kind TEXT,state TEXT,config TEXT,cursor TEXT,processed INTEGER DEFAULT 0,next_retry_at TEXT,error TEXT,
                created_at TEXT,updated_at TEXT,stats TEXT DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS observation_windows(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                chat_key TEXT,start_utc TEXT,end_utc TEXT,evidence TEXT);
            CREATE TABLE IF NOT EXISTS analysis_runs(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                config TEXT,revision INTEGER,algorithm TEXT,created_at TEXT,result TEXT,records TEXT);
            CREATE TABLE IF NOT EXISTS report_snapshots(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
                analysis_id TEXT REFERENCES analysis_runs(id) ON DELETE CASCADE,format TEXT,created_at TEXT,filename TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,hash TEXT,filename TEXT,bytes INTEGER,created_at TEXT,schema TEXT);
            CREATE TABLE IF NOT EXISTS account_waits(account_id TEXT PRIMARY KEY,next_retry_at TEXT);
            ''')
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1,?)", (now(),))
            columns={r[1] for r in db.execute('PRAGMA table_info(collection_runs)')}
            if 'page_start_cursor' not in columns:
                db.execute('ALTER TABLE collection_runs ADD COLUMN page_start_cursor TEXT')
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2,?)", (now(),))
            db.execute("INSERT OR IGNORE INTO settings VALUES('retention_days','90')")
            # A killed worker never claims successful completion. Saved wait deadlines survive.
            db.execute("UPDATE collection_runs SET state='paused',error='앱 종료로 일시정지됨. 연결 후 재개하세요.' WHERE state IN ('running','queued')")
        self.cleanup()

    def cleanup(self):
        with self.connect() as db:
            days = int(db.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0])
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            retention_chats = db.execute("SELECT DISTINCT workspace_id,chat_key FROM messages WHERE last_seen_at<?",(cutoff,)).fetchall()
            affected = db.execute("SELECT DISTINCT workspace_id FROM messages WHERE last_seen_at<? OR (expires_at_utc IS NOT NULL AND expires_at_utc<=?)", (cutoff, now())).fetchall()
            db.execute("DELETE FROM message_provenance WHERE EXISTS(SELECT 1 FROM messages m WHERE m.workspace_id=message_provenance.workspace_id AND m.chat_key=message_provenance.chat_key AND m.message_id=message_provenance.message_id AND (m.last_seen_at<? OR (m.expires_at_utc IS NOT NULL AND m.expires_at_utc<=?)))", (cutoff, now()))
            db.execute("DELETE FROM messages WHERE last_seen_at<? OR (expires_at_utc IS NOT NULL AND expires_at_utc<=?)", (cutoff, now()))
            for row in affected:
                db.execute("UPDATE workspaces SET revision=revision+1 WHERE id=?", (row[0],))
                db.execute("UPDATE analysis_runs SET records='[]' WHERE workspace_id=?", (row[0],))
            for row in retention_chats:
                # Purged metadata no longer supports a zero-count observation claim.
                db.execute("DELETE FROM observation_windows WHERE workspace_id=? AND chat_key=?",(row[0],row[1]))
            stale = db.execute("SELECT id FROM uploads WHERE created_at<?", ((datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),)).fetchall()
            for row in stale:
                (self.directory / 'uploads' / row[0]).unlink(missing_ok=True)
                db.execute("DELETE FROM uploads WHERE id=?", (row[0],))
        # Orphan files can remain after a process dies between filesystem and DB work.
        for directory, table, field in (("reports", "report_snapshots", "filename"), ("uploads", "uploads", "id")):
            with self.connect() as db:
                retained = {r[0] for r in db.execute(f"SELECT {field} FROM {table}")}
            for path in (self.directory / directory).iterdir():
                if path.name not in retained and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < datetime.now(timezone.utc)-timedelta(hours=2):
                    path.unlink(missing_ok=True)

    def workspace(self, workspace_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not row:
            raise ValueError("작업공간을 찾을 수 없습니다.")
        return dict(row)

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM collection_runs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("작업을 찾을 수 없습니다.")
        result = dict(row)
        result["config"] = json.loads(result["config"])
        result["stats"] = json.loads(result["stats"])
        return result

    def update_job(self, job_id, **kwargs):
        allowed = {"state", "cursor", "processed", "next_retry_at", "error", "stats"}
        assert set(kwargs) <= allowed
        kwargs["updated_at"] = now()
        with self.connect() as db:
            db.execute("UPDATE collection_runs SET "+",".join(k+"=?" for k in kwargs)+" WHERE id=?", (*[json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v for v in kwargs.values()], job_id))


def upsert(db, workspace_id, chat_key, message, batch_id, row_number):
    """Atomic message, provenance and label update. Returns inserted/duplicate/updated/conflict."""
    old = db.execute("SELECT * FROM messages WHERE workspace_id=? AND chat_key=? AND message_id=?", (workspace_id, chat_key, message["message_id"])).fetchone()
    timestamp = now()
    status = "inserted"
    if old:
        changes = any(old[k] != message[k] for k in message if k not in {"time_conflict"})
        status = "duplicate" if not changes else "updated"
        if changes and not (message.get("edited_at_utc") and (not old["edited_at_utc"] or message["edited_at_utc"] >= old["edited_at_utc"])):
            if old["edited_at_utc"] and (not message.get("edited_at_utc") or message["edited_at_utc"] < old["edited_at_utc"]):
                message = {k: old[k] for k in message}
            else:
                status = "conflict"
    fields = list(message)
    db.execute("INSERT INTO messages(workspace_id,chat_key,"+",".join(fields)+",first_seen_at,last_seen_at) VALUES("+",".join("?" for _ in range(len(fields)+4))+") ON CONFLICT(workspace_id,chat_key,message_id) DO UPDATE SET "+",".join(k+"=excluded."+k for k in fields if k!="message_id")+",last_seen_at=excluded.last_seen_at", (workspace_id, chat_key, *message.values(), timestamp, timestamp))
    db.execute("INSERT OR IGNORE INTO message_provenance VALUES(?,?,?,?,?)", (workspace_id, chat_key, message["message_id"], batch_id, row_number))
    key = message["sender_key"] or "unknown"
    label = db.execute("SELECT label FROM sender_labels WHERE workspace_id=? AND chat_key=? AND sender_key=?", (workspace_id, chat_key, key)).fetchone()
    if not label:
        kind = message["sender_kind"]
        index = db.execute("SELECT COUNT(*) FROM sender_labels WHERE workspace_id=? AND chat_key=? AND kind=?", (workspace_id, chat_key, kind)).fetchone()[0]
        prefix = {"user": "계정", "channel": "채널", "signature": "서명"}.get(kind, "식별 불가")
        suffix = chr(65+index) if index < 26 else str(index+1)
        db.execute("INSERT INTO sender_labels VALUES(?,?,?,?,?)", (workspace_id, chat_key, key, kind, prefix+(" "+suffix if kind!="unknown" else "")))
    return status
