import hashlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def identifier(value):
    if value is None or isinstance(value, bool):
        raise ValueError("메시지 ID 누락")
    result = str(value)
    if not re.fullmatch(r"-?\d+", result):
        raise ValueError("잘못된 메시지 ID")
    return str(int(result))


def parse_time(value, source_timezone=None):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(value)):
        return datetime.fromtimestamp(float(value), UTC)
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("올바르지 않은 날짜 형식") from None
    if result.tzinfo is None:
        if not source_timezone:
            raise ValueError("시간대 없는 날짜: 원본 시간대 지정 필요")
        tz = ZoneInfo(source_timezone)
        a, b = result.replace(tzinfo=tz, fold=0), result.replace(tzinfo=tz, fold=1)
        if a.utcoffset() != b.utcoffset():
            raise ValueError("일광절약시간 전환의 모호하거나 존재하지 않는 시각: UTC 시각 필요")
        if a.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != result:
            raise ValueError("존재하지 않는 로컬 시각")
        result = a
    return result.astimezone(UTC)


def peer(value):
    if isinstance(value, dict):
        for kind in ("user", "channel", "chat"):
            if value.get(kind+"_id") is not None:
                return kind, kind+":"+identifier(value[kind+"_id"])
    if isinstance(value, str):
        match = re.fullmatch(r"(user|channel|chat)[:]?(-?\d+)", value)
        if match:
            return match[1], match[1]+":"+identifier(match[2])
    return "unknown", None


def normalize(raw, source_timezone=None):
    if not isinstance(raw, dict):
        raise ValueError("메시지 행이 객체가 아님")
    message_id = identifier(raw.get("id"))
    unix = parse_time(raw.get("date_unixtime"))
    iso = None
    conflict = False
    if raw.get("date"):
        try:
            iso = parse_time(raw["date"], source_timezone)
        except ValueError:
            if unix is None:
                raise
    sent = unix or iso
    if sent is None:
        raise ValueError("게시 시각 누락")
    if unix and iso and abs((unix-iso).total_seconds()) > 1:
        conflict = True
    if not 1970 <= sent.year <= 2100:
        raise ValueError("게시 시각 범위 오류")
    kind, sender = peer(raw.get("from_id", raw.get("sender_id")))
    if kind == "chat":
        kind, sender = "unknown", None
    signature = raw.get("author", raw.get("post_author"))
    if sender is None and signature:
        kind, sender = "signature", "signature:"+hashlib.sha256(str(signature).encode()).hexdigest()[:24]
    media = str(raw.get("media_type", ""))
    mime = raw.get("mime_type")
    if raw.get("photo") or media == "photo":
        content = "photo"
    elif media in {"video_file", "video_message", "video"}:
        content = "video"
    elif media in {"audio_file", "voice_message", "audio"}:
        content = "audio"
    elif media in {"sticker", "animation", "contact", "location", "poll", "other"}:
        content = "other"
    elif raw.get("file") or mime:
        content = "document"
    elif "text" in raw:
        content = "text"
    else:
        content = "unknown"
    edited = parse_time(raw.get("edited_unixtime") or raw.get("edited"), source_timezone)
    expires = parse_time(raw.get("expires_at_utc"), source_timezone)
    if not expires and raw.get("ttl_period"):
        expires = sent+timedelta(seconds=int(raw["ttl_period"]))
    album = raw.get("grouped_id", raw.get("media_group_id", raw.get("album_id")))
    return {
        "message_id": message_id, "sender_kind": kind, "sender_key": sender,
        "sent_at_utc": sent.isoformat(), "edited_at_utc": edited.isoformat() if edited else None,
        "expires_at_utc": expires.isoformat() if expires else None,
        "album_key": str(album) if album is not None else None,
        "content_kind": content, "document_mime": str(mime) if mime else None,
        "is_forwarded": int(bool(raw.get("forwarded_from") or raw.get("fwd_from"))),
        "is_service": int(raw.get("type") == "service" or bool(raw.get("action"))),
        "lifecycle": "active",
    }, conflict
