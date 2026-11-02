"""Streaming Desktop JSON adapter. Neither text nor attachment bytes leave this module."""
import json
import ijson
from ..storage import now, uid, upsert
from ..services.normalizer import normalize


def inspect_file(path):
    with open(path, "rb") as handle:
        shape = None
        for prefix, event, value in ijson.parse(handle):
            if prefix == "messages" and event == "start_array":
                shape = "single"
                break
            if prefix == "chats.list" and event == "start_array":
                shape = "multi"
                break
            if prefix == "chats" and event == "start_array":
                shape = "array"
                break
    if not shape:
        raise ValueError("지원하지 않는 JSON입니다. Telegram Desktop의 JSON 형식으로 내보내세요.")
    base = "" if shape == "single" else ("chats.list.item" if shape == "multi" else "chats.item")
    chats, current = [], {}
    with open(path, "rb") as handle:
        for prefix, event, value in ijson.parse(handle):
            if prefix == base and event == "start_map":
                current = {}
            if prefix in {base+"."+k if base else k for k in ("id", "name", "type")} and event in {"string", "number", "null"}:
                current[prefix.split(".")[-1]] = str(value) if value is not None else None
            if prefix == (base+".messages.item" if base else "messages.item") and event == "start_map":
                current["count"] = current.get("count", 0)+1
            if prefix == base and event == "end_map":
                chats.append({**current, "index": len(chats)})
    # Single root start/end events are prefix ''.
    if not chats:
        raise ValueError("대화 정보를 읽을 수 없습니다.")
    return {"shape": shape, "chats": chats}


def iter_messages(path, shape, index):
    prefix = {"single": "messages.item", "multi": "chats.list.item.messages.item", "array": "chats.item.messages.item"}[shape]
    if shape == "single":
        with open(path, "rb") as handle:
            yield from ijson.items(handle, prefix)
        return
    # Track chat array positions without materializing a whole chat.
    base = "chats.list.item" if shape == "multi" else "chats.item"
    with open(path, "rb") as handle:
        events = iter(ijson.parse(handle))
        position = -1
        for p, event, value in events:
            if p == base and event == "start_map":
                position += 1
            if position == index and p == prefix and event == "start_map":
                builder = ijson.ObjectBuilder()
                builder.event(event, value)
                depth = 1
                for _, ev, val in events:
                    builder.event(ev, val)
                    if ev in {"start_map", "start_array"}:
                        depth += 1
                    if ev in {"end_map", "end_array"}:
                        depth -= 1
                    if depth == 0:
                        break
                yield builder.value
            if position > index:
                return


def chat_key(chat, source_hash):
    kind = chat.get("type", "")
    peer_type = {"personal_chat": "user", "private_group": "chat", "private_supergroup": "channel", "public_supergroup": "channel", "public_channel": "channel", "private_channel": "channel"}.get(kind)
    if peer_type and chat.get("id"):
        return peer_type+":"+str(chat["id"])
    return "source:"+source_hash+":"+str(chat["index"])


def import_file(store, job_id):
    job = store.job(job_id)
    config = job["config"]
    path = store.directory / "uploads" / config["upload_id"]
    stats = {"inserted": 0, "duplicate": 0, "updated": 0, "conflict": 0, "quarantined": 0, "time_conflicts": 0, "services": 0}
    store.update_job(job_id, state="running")
    try:
        with store.connect() as db:
            upload = db.execute("SELECT * FROM uploads WHERE id=?", (config["upload_id"],)).fetchone()
            if not upload:
                raise ValueError("임시 파일이 만료되었습니다. 파일을 다시 선택하세요.")
            schema = json.loads(upload["schema"])
            chat = schema["chats"][config["chat_index"]]
            key = chat_key(chat, upload["hash"])
            source = db.execute("SELECT id FROM sources WHERE workspace_id=? AND hash=?", (job["workspace_id"], upload["hash"])).fetchone()
            source_id = source[0] if source else uid()
            if not source:
                db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)", (source_id, job["workspace_id"], "json", upload["hash"], upload["filename"], upload["bytes"], now()))
            existing = db.execute("SELECT stats FROM imports WHERE workspace_id=? AND source_id=? AND chat_key=?", (job["workspace_id"], source_id, key)).fetchone()
            if existing:
                stats = json.loads(existing[0])
                stats["same_file"] = True
            else:
                db.execute("INSERT OR IGNORE INTO chats VALUES(?,?,?,?,0)", (job["workspace_id"], key, chat.get("name") or "가져온 대화", chat.get("type") or "unknown"))
                # One transaction makes malformed/truncated JSON an all-or-nothing import.
                for row_number, raw in enumerate(iter_messages(path, schema["shape"], config["chat_index"]), 1):
                    try:
                        message, conflict = normalize(raw, config.get("source_timezone"))
                        stats[upsert(db, job["workspace_id"], key, message, job_id, row_number)] += 1
                        stats["time_conflicts"] += int(conflict)
                        stats["services"] += message["is_service"]
                        if message["album_key"]:
                            db.execute("UPDATE chats SET album_supported=1 WHERE workspace_id=? AND chat_key=?", (job["workspace_id"], key))
                    except (ValueError, TypeError, OverflowError, OSError) as exc:
                        stats["quarantined"] += 1
                        reason = "메타데이터 형식 오류" if not isinstance(exc, ValueError) else str(exc)[:150]
                        db.execute("INSERT INTO quarantine VALUES(?,?,?,?,?)", (uid(), job["workspace_id"], job_id, row_number, reason))
                valid = stats["inserted"]+stats["duplicate"]+stats["updated"]+stats["conflict"]
                if valid == 0:
                    raise ValueError("유효한 메시지가 없습니다. ID·게시 시각·원본 시간대를 확인하세요.")
                if config.get("confirmed_start") and config.get("confirmed_end"):
                    from ..services.analysis import date_bounds
                    start, end = date_bounds(config["confirmed_start"], config["confirmed_end"], config["source_timezone"])
                    db.execute("INSERT INTO observation_windows VALUES(?,?,?,?,?,?)", (uid(), job["workspace_id"], key, start.isoformat(), end.isoformat(), "user-confirmed export"))
                db.execute("INSERT INTO imports VALUES(?,?,?,?,?,?)", (job_id, job["workspace_id"], source_id, key, now(), json.dumps(stats)))
                db.execute("UPDATE workspaces SET revision=revision+1 WHERE id=?", (job["workspace_id"],))
            stats["chat_key"] = key
            db.execute("UPDATE collection_runs SET state='completed',stats=?,processed=?,updated_at=? WHERE id=?", (json.dumps(stats), sum(stats.get(k,0) for k in ("inserted","duplicate","updated","conflict","quarantined")), now(), job_id))
    except Exception as exc:
        store.update_job(job_id, state="failed", error=str(exc)[:200] if isinstance(exc, ValueError) else "JSON 처리 실패. 파일 형식·디스크 공간을 확인하세요.")
    finally:
        path.unlink(missing_ok=True)
        with store.connect() as db:
            db.execute("DELETE FROM uploads WHERE id=?", (config["upload_id"],))
