"""Deterministic statistics on a single immutable SQLite read snapshot."""
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from ..storage import now, uid
from .gaps import analyze_gaps

ALGORITHM = "postscope-1.1.0"
UTC = timezone.utc
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def date_bounds(start, end, zone):
    tz = ZoneInfo(zone)
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if (b-a).days > 3660:
        raise ValueError("한 번의 분석 기간은 최대 10년입니다.")
    return datetime.combine(a, time(), tz).astimezone(UTC), datetime.combine(b+timedelta(days=1), time(), tz).astimezone(UTC)


def union_windows(rows, start, end):
    clipped = sorted((max(start, datetime.fromisoformat(r["start_utc"])), min(end, datetime.fromisoformat(r["end_utc"]))) for r in rows)
    merged = []
    for a, b in clipped:
        if a >= b:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def exposure(windows, tz):
    cells = [[0.0]*24 for _ in range(7)]
    daily = defaultdict(float)
    for start, end in windows:
        cursor = start
        while cursor < end:
            local = cursor.astimezone(tz)
            # Split at UTC and local hour boundaries (including fractional timezone offsets).
            seconds = min(3600-local.minute*60-local.second-local.microsecond/1e6,
                          3600-cursor.minute*60-cursor.second-cursor.microsecond/1e6)
            boundary = min(end, cursor+timedelta(seconds=max(seconds, 0.000001)))
            hours = (boundary-cursor).total_seconds()/3600
            cells[local.weekday()][local.hour] += hours
            daily[local.date().isoformat()] += hours
            cursor = boundary
    return cells, daily


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*p
    low = int(position)
    return values[low]+(values[min(low+1,len(values)-1)]-values[low])*(position-low)


def calculate(messages, windows_raw, config, album_supported=True):
    tz = ZoneInfo(config["timezone"])
    start, end = date_bounds(config["start"], config["end"], config["timezone"])
    windows = union_windows(windows_raw, start, end)
    cell_exposure, day_exposure = exposure(windows, tz)
    observed_hours = sum(sum(row) for row in cell_exposure)
    full_coverage = abs(observed_hours-(end-start).total_seconds()/3600) < 1e-6
    unit = config.get("unit", "messages")
    if unit == "albums" and not album_supported:
        raise ValueError("앨범 식별자가 없는 소스에서는 게시 묶음 집계를 사용할 수 없습니다.")
    sender = config.get("sender", "all")
    candidates = [m for m in messages if not m["is_service"] and m["lifecycle"]=="active"
                  and (sender=="all" or (m["sender_key"] or "unknown")==sender)]
    groups = defaultdict(list)
    for m in candidates:
        key = (m["sender_key"], "album:"+m["album_key"]) if unit=="albums" and m["album_key"] else (None, "message:"+m["message_id"])
        groups[key].append(m)
    selected, incomplete = [], 0
    content = config.get("content", "all")
    for group in groups.values():
        group.sort(key=lambda m: (m["sent_at_utc"], int(m["message_id"])))
        first = group[0]
        moment = datetime.fromisoformat(first["sent_at_utc"])
        if not start <= moment < end:
            continue
        if content != "all" and not any((m["document_mime"]=="application/pdf" if content=="pdf" else m["content_kind"]==content) for m in group):
            continue
        if not config.get("include_forwarded", True) and any(m["is_forwarded"] for m in group):
            continue
        if unit=="albums" and first["album_key"]:
            # Require evidence around an album, not just records surviving the date filter.
            left = moment-timedelta(seconds=1)
            right = datetime.fromisoformat(group[-1]["sent_at_utc"])+timedelta(seconds=1)
            evidence = union_windows(windows_raw, left, right)
            if not evidence or evidence[0][0] > left or evidence[0][1] < right:
                incomplete += 1
                continue
        selected.append({**first, "members": len(group), "content_kinds": sorted({m["content_kind"] for m in group})})
    selected.sort(key=lambda m:(m["sent_at_utc"], int(m["message_id"])))
    n = len(selected)
    daily_counts, weekday, hours, types = Counter(), [0]*7, [0]*24, Counter()
    heat = [[0]*24 for _ in range(7)]
    covered_heat = [[0]*24 for _ in range(7)]
    for m in selected:
        local = datetime.fromisoformat(m["sent_at_utc"]).astimezone(tz)
        daily_counts[local.date().isoformat()] += 1
        weekday[local.weekday()] += 1
        hours[local.hour] += 1
        heat[local.weekday()][local.hour] += 1
        stamp=datetime.fromisoformat(m['sent_at_utc'])
        if any(a<=stamp<b for a,b in windows):
            covered_heat[local.weekday()][local.hour] += 1
        types[m["content_kind"] if len(m["content_kinds"])==1 else "mixed"] += 1
    daily, occurrences = [], [0]*7
    cursor = date.fromisoformat(config["start"])
    last = date.fromisoformat(config["end"])
    while cursor <= last:
        key = cursor.isoformat()
        day_start, day_end = date_bounds(key, key, config["timezone"])
        expected = (day_end-day_start).total_seconds()/3600
        observed = day_exposure.get(key, 0)
        known = abs(observed-expected) < 1e-6
        if known:
            occurrences[cursor.weekday()] += 1
        row = {"date": key, "count": daily_counts[key], "hours": observed,
               "status": "observed" if known else ("partial" if observed else "unknown"), "moving_average": None}
        daily.append(row)
        if len(daily)>=7 and all(d["status"]=="observed" for d in daily[-7:]):
            row["moving_average"] = sum(d["count"] for d in daily[-7:])/7
        cursor += timedelta(days=1)
    intervals, accepted, sessions = [], [], []
    threshold = config.get("session_minutes", 10)*60
    current = None
    for index, m in enumerate(selected):
        stamp = datetime.fromisoformat(m["sent_at_utc"])
        previous = datetime.fromisoformat(selected[index-1]["sent_at_utc"]) if index else None
        delta = (stamp-previous).total_seconds() if previous else None
        crosses = bool(previous and windows_raw and not any(a<=previous and stamp<=b for a,b in windows))
        if previous:
            intervals.append({"seconds": delta, "included": not crosses, "reason": "확인된 조회 구간 사이 공백" if crosses else ""})
            if not crosses:
                accepted.append(delta)
        if current is None or crosses or delta > threshold:
            current = {"start": m["sent_at_utc"], "end": m["sent_at_utc"], "count": 1, "seconds": 0}
            sessions.append(current)
        else:
            current["end"] = m["sent_at_utc"]
            current["count"] += 1
            current["seconds"] = (stamp-datetime.fromisoformat(current["start"])).total_seconds()
    peak = max(hours) if n else None
    peak_hours = [h for h,c in enumerate(hours) if c==peak] if n else []
    peak_count = max(daily_counts.values(), default=0)
    metrics = {"total": n, "active_days": len(daily_counts), "daily_average": n/(observed_hours/24) if full_coverage and observed_hours else None,
               "hourly_rate": n/observed_hours if full_coverage and observed_hours else None,
               "median_interval": statistics.median(accepted) if accepted else None,
               "q25_interval": percentile(accepted,.25), "q75_interval": percentile(accepted,.75),
               "min_interval": min(accepted) if accepted else None, "max_interval": max(accepted) if accepted else None,
               "peak_hours": peak_hours, "peak_hour_share": peak/n if n else None,
               "peak_days": sorted(k for k,v in daily_counts.items() if v==peak_count),
               "session_count": len(sessions), "session_median_size": statistics.median([s["count"] for s in sessions]) if sessions else None,
               "observed_hours": observed_hours, "full_coverage": full_coverage}
    histogram = [0]*6
    for value in accepted:
        slot = next((i for i,maximum in enumerate([60,300,600,3600,21600]) if value<maximum),5)
        histogram[slot] += 1
    result = {"metrics": metrics, "daily": daily, "weekday": weekday, "weekday_occurrences": occurrences,
              "weekday_average": [weekday[i]/occurrences[i] if full_coverage and occurrences[i] else None for i in range(7)],
              "hours": hours, "heatmap": heat, "exposure": cell_exposure,
              "rates": [[covered_heat[w][h]/cell_exposure[w][h] if cell_exposure[w][h] else None for h in range(24)] for w in range(7)],
              "normalized_counts": covered_heat,
              "types": dict(types), "intervals": intervals, "interval_histogram": histogram, "sessions": sessions,
              "incomplete_albums": incomplete, "small_sample": n<30 or observed_hours<14*24,
              "insight": None, "gaps": analyze_gaps(daily)}
    session_lengths=[s["seconds"] for s in sessions]
    result["session_length_histogram"]=[sum(1 for s in session_lengths if a<=s<b) for a,b in ((0,1),(1,300),(300,600),(600,1800),(1800,3600),(3600,float('inf')))]
    if n:
        result["insight"] = f"관측된 게시물 중 {', '.join(str(h)+'시' for h in peak_hours)} 게시 비중이 각각 {peak/n*100:.1f}%로 가장 높았습니다. 시간대: {config['timezone']}, 집계: {'메시지 수' if unit=='messages' else '게시 묶음 수'}."
    return result, selected


def run_analysis(store, config):
    store.cleanup()
    workspace = store.workspace(config["workspace_id"])
    canonical = json.dumps(config, sort_keys=True, ensure_ascii=False)
    with store.connect() as db:
        db.execute("BEGIN")
        revision = db.execute("SELECT revision FROM workspaces WHERE id=?", (workspace["id"],)).fetchone()[0]
        cached = db.execute("SELECT result FROM analysis_runs WHERE workspace_id=? AND revision=? AND config=? AND algorithm=?", (workspace["id"],revision,canonical,ALGORITHM)).fetchone()
        if cached:
            return json.loads(cached[0])
        chat = db.execute("SELECT * FROM chats WHERE workspace_id=? AND chat_key=?", (workspace["id"],config["chat_key"])).fetchone()
        if not chat:
            raise ValueError("분석할 대화를 선택하세요.")
        messages = [dict(row) for row in db.execute("SELECT * FROM messages WHERE workspace_id=? AND chat_key=?", (workspace["id"],config["chat_key"]))]
        windows = [dict(row) for row in db.execute("SELECT * FROM observation_windows WHERE workspace_id=? AND chat_key=?", (workspace["id"],config["chat_key"]))]
        labels = {r["sender_key"]:{"label":r["label"],"kind":r["kind"]} for r in db.execute("SELECT * FROM sender_labels WHERE workspace_id=? AND chat_key=?", (workspace["id"],config["chat_key"]))}
        imports = [json.loads(r[0]) for r in db.execute("SELECT stats FROM imports WHERE workspace_id=? AND chat_key=?", (workspace["id"],config["chat_key"]))]
        sources = [dict(r) for r in db.execute("SELECT DISTINCT s.kind,s.hash,s.created_at FROM sources s LEFT JOIN imports i ON i.source_id=s.id WHERE s.workspace_id=? AND (i.chat_key=? OR s.kind='synthetic' OR (s.kind='api' AND EXISTS(SELECT 1 FROM collection_runs r WHERE r.id=s.hash AND json_extract(r.config,'$.chat_key')=?)))", (workspace["id"],config['chat_key'],config['chat_key']))]
        result, selected = calculate(messages, windows, config, bool(chat["album_supported"]))
        for m in selected:
            m["sender_label"] = labels.get(m["sender_key"] or "unknown",{}).get("label","식별 불가")
        quality = {"total_stored": len(messages), "service_excluded": sum(m["is_service"] for m in messages),
                   "identified_ratio": sum(m["sender_kind"] in {"user","channel"} for m in messages)/len(messages) if messages else None,
                   "signature_ratio": sum(m["sender_kind"]=="signature" for m in messages)/len(messages) if messages else None,
                   "quarantined": sum(s.get("quarantined",0) for s in imports), "duplicates": sum(s.get("duplicate",0) for s in imports),
                   "conflicts": sum(s.get("conflict",0)+s.get("time_conflicts",0) for s in imports),
                   "album_supported": bool(chat["album_supported"]), "source_kinds": sorted({r["kind"] for r in sources}),
                   "sources":sources,"parser_version":"desktop-json-1.0.0",
                   "coverage": "조회 범위 확인" if result["metrics"]["full_coverage"] else "일부 구간 미조회" if windows else "내보내기 범위 미확인",
                   "first_message": min((m["sent_at_utc"] for m in messages),default=None), "last_message": max((m["sent_at_utc"] for m in messages),default=None),
                   "last_updated": max((m["last_seen_at"] for m in messages),default=None), "windows": windows}
        result.update(id=uid(), snapshot_id=uid(), created_at=now(), algorithm=ALGORITHM, revision=revision,
                      config=config, quality=quality, chat_title=chat["title"], workspace_title=workspace["title"], is_demo=bool(workspace["is_demo"]))
        result["sender_label"]="전체 작성자" if config.get("sender","all")=="all" else labels.get(config["sender"],{}).get("label","선택한 범주")
        result["comparison"] = None
        if config.get("compare_start") and config.get("compare_end"):
            a,b = date_bounds(config["compare_start"],config["compare_end"],config["timezone"])
            c,d = date_bounds(config["start"],config["end"],config["timezone"])
            if a<d and c<b:
                raise ValueError("비교 기간이 현재 분석 기간과 겹칩니다. 기간을 수정하세요.")
            baseline,_ = calculate(messages,windows,{**config,"start":config["compare_start"],"end":config["compare_end"]},bool(chat["album_supported"]))
            old,new = baseline["metrics"]["total"],result["metrics"]["total"]
            result["comparison"] = {"baseline": baseline["metrics"], "difference": new-old,"percent": (new-old)/old*100 if old else None,
                                     "same_length": (date.fromisoformat(config["end"])-date.fromisoformat(config["start"])) == (date.fromisoformat(config["compare_end"])-date.fromisoformat(config["compare_start"]))}
    # End the read transaction before saving. A collector can commit during computation;
    # promoting that old WAL read snapshot to a writer would raise SQLITE_BUSY_SNAPSHOT.
    with store.connect() as db:
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?)", (result["id"],workspace["id"],canonical,revision,ALGORITHM,result["created_at"],json.dumps(result,ensure_ascii=False),json.dumps(selected,ensure_ascii=False)))
    return result
