"""One scheduled metadata-only Telegram read. Replace public data only after success."""
import asyncio
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telethon import TelegramClient,errors,types
from telethon.sessions import StringSession
from postscope.adapters.telegram import message_metadata
from postscope.public_snapshot import public_dataset

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'public'/'dataset.json'


async def collect():
    config_path=ROOT/'public'/'source.json'
    config=json.loads(config_path.read_text()) if config_path.exists() else {}
    target=os.environ.get('TELEGRAM_CHAT') or config.get('chat')
    if config.get('mode') in {'demo','synthetic'}:
        raise ValueError('합성 데이터 갱신은 비활성화됐습니다. 실제 Telegram 채널을 설정하세요.')
    if not target:raise ValueError('수집할 채널을 public/source.json 또는 TELEGRAM_CHAT에 설정하세요.')
    missing=[key for key in ['TELEGRAM_API_ID','TELEGRAM_API_HASH','TELEGRAM_SESSION'] if not os.environ.get(key)]
    if missing:raise ValueError('GitHub Secrets 설정 필요: '+', '.join(missing))
    days=int(os.environ.get('POSTSCOPE_LOOKBACK_DAYS',config.get('lookback_days',90)))
    if not 1<=days<=365:raise ValueError('조회 기간은 1~365일입니다.')
    end=datetime.now(timezone.utc);zone=ZoneInfo('Asia/Seoul')
    start=datetime.combine(end.astimezone(zone).date()-timedelta(days=days-1),datetime.min.time(),zone).astimezone(timezone.utc)
    client=TelegramClient(StringSession(os.environ['TELEGRAM_SESSION']),int(os.environ['TELEGRAM_API_ID']),os.environ['TELEGRAM_API_HASH'],flood_sleep_threshold=0,request_retries=0,connection_retries=1,timeout=30)
    messages=[];cursor=0;retries=0
    try:
        await asyncio.wait_for(client.connect(),30)
        if not await client.is_user_authorized():raise ValueError('Telegram 인증이 만료됐습니다. 세션을 다시 설정하세요.')
        entity=await client.get_entity(int(target) if str(target).lstrip('-').isdigit() else target)
        while True:
            try:
                page=await asyncio.wait_for(client.get_messages(entity,limit=100,offset_id=cursor,offset_date=end if not cursor else None),30)
                retries=0
            except errors.FloodWaitError as error:
                if error.seconds>120:raise ValueError(f'Telegram 호출 제한: {error.seconds}초 후에만 재시도할 수 있습니다.') from None
                await asyncio.sleep(error.seconds);continue
            except (OSError,asyncio.TimeoutError):
                retries+=1
                if retries>5:raise ValueError('네트워크 재시도에 실패했습니다. 이전 게시 데이터는 유지됩니다.') from None
                await asyncio.sleep(2**retries);continue
            if not page:break
            reached_start=False
            for message in page:
                if isinstance(message,types.MessageEmpty):continue
                if message.date<start:reached_start=True
                if start<=message.date<end:messages.append(message_metadata(message))
            if len(messages)>100000:raise ValueError('10만 건을 초과했습니다. 조회 기간을 줄이세요.')
            next_cursor=min(message.id for message in page)
            if next_cursor==cursor:raise ValueError('Telegram 페이지 조회가 진행되지 않았습니다.')
            cursor=next_cursor
            if reached_start:break
            await asyncio.sleep(.3)
    finally:await client.disconnect()
    dataset=public_dataset(messages,secret=os.environ['TELEGRAM_API_HASH'],title=config.get('label','게시 기록'),start=start.isoformat(),end=end.isoformat(),collected_at=end.isoformat())
    write_dataset(dataset)
    print(f'일일 수집 완료: {len(messages)}건. 공개 파일에는 본문·계정 원본 ID·세션을 포함하지 않습니다.')


def write_dataset(dataset):
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    temporary=OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(OUTPUT)


if __name__=='__main__':
    try:asyncio.run(collect())
    except Exception as error:
        print(str(error) if isinstance(error,ValueError) else 'Telegram 수집에 실패했습니다. 권한·인증·네트워크를 확인하세요.',file=sys.stderr)
        sys.exit(1)
