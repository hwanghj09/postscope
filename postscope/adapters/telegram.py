"""Only this adapter owns Telethon clients. Sessions/OTP/passwords stay in memory."""
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, types, errors
from telethon.sessions import MemorySession
from ..storage import uid, now, upsert
from ..services.normalizer import normalize


def message_metadata(message):
    sender = getattr(message,"from_id",None)
    if isinstance(sender, types.PeerUser):
        from_id = "user"+str(sender.user_id)
    elif isinstance(sender, types.PeerChannel):
        from_id = "channel"+str(sender.channel_id)
    elif getattr(message,"post",False) and isinstance(message.peer_id,types.PeerChannel):
        from_id = "channel"+str(message.peer_id.channel_id)
    else:
        from_id = None
    raw = {"id": str(message.id), "date": message.date.isoformat(), "from_id": from_id,
           "post_author": getattr(message,"post_author",None), "grouped_id": str(message.grouped_id) if getattr(message,"grouped_id",None) else None,
           "fwd_from": bool(getattr(message,"fwd_from",None)), "ttl_period": getattr(message,"ttl_period",None),
           "edited": message.edit_date.isoformat() if getattr(message,"edit_date",None) else None}
    if isinstance(message, types.MessageService):
        raw["type"] = "service"
    elif message.photo:
        raw["photo"] = True
    elif message.document:
        raw["file"] = True
        raw["mime_type"] = message.document.mime_type
        if any(isinstance(a, types.DocumentAttributeVideo) for a in message.document.attributes):
            raw["media_type"] = "video_file"
        elif any(isinstance(a, types.DocumentAttributeAudio) for a in message.document.attributes):
            raw["media_type"] = "audio_file"
        elif any(isinstance(a, (types.DocumentAttributeSticker,types.DocumentAttributeAnimated)) for a in message.document.attributes):
            raw["media_type"] = "sticker"
    elif message.media:
        raw["media_type"] = "other"
    else:
        raw["text"] = ""  # Presence only. Never read message.message.
    return normalize(raw)[0]


def entity_key(entity):
    if isinstance(entity, types.User):
        return "user:"+str(entity.id)
    if isinstance(entity, types.Channel):
        return "channel:"+str(entity.id)
    if isinstance(entity, types.Chat):
        return "chat:"+str(entity.id)
    raise ValueError("지원되지 않는 대화 유형입니다.")


class TelegramAdapter:
    def __init__(self, store):
        self.store = store
        self.connections = {}

    async def login(self, api_id, api_hash, phone):
        if len(self.connections)>=3:
            raise ValueError("열린 연결을 먼저 해제하세요. 연결은 최대 3개입니다.")
        client = TelegramClient(MemorySession(), api_id, api_hash, flood_sleep_threshold=0, request_retries=0, connection_retries=1, timeout=30)
        try:
            await asyncio.wait_for(client.connect(), 30)
            result = await client.send_code_request(phone)
        except BaseException:
            await client.disconnect()
            raise
        connection_id = uid()
        self.connections[connection_id] = {"client":client,"phone":phone,"phone_code_hash":result.phone_code_hash,
                                             "state":"code","created":datetime.now(timezone.utc),"account_id":None,"entities":{}}
        return {"id":connection_id,"state":"code"}

    def get(self, connection_id, authorized=True):
        connection = self.connections.get(connection_id)
        if not connection:
            raise ValueError("메모리 연결이 종료되었습니다. 계정을 다시 연결하세요.")
        if authorized and connection["state"]!="connected":
            raise ValueError("계정 인증을 완료하세요.")
        return connection

    async def verify(self, connection_id, code=None, password=None):
        connection = self.get(connection_id, False)
        if datetime.now(timezone.utc)-connection["created"] > timedelta(minutes=10) and connection["state"]!="connected":
            await self.disconnect(connection_id)
            raise ValueError("로그인 입력 시간이 만료되었습니다. 다시 연결하세요.")
        client = connection["client"]
        try:
            if password:
                await client.sign_in(password=password)
            else:
                await client.sign_in(phone=connection["phone"], code=code, phone_code_hash=connection["phone_code_hash"])
        except errors.SessionPasswordNeededError:
            connection["state"] = "password"
            return {"id":connection_id,"state":"password"}
        me = await client.get_me()
        connection.update(state="connected",account_id=str(me.id))
        connection.pop("phone",None)
        connection.pop("phone_code_hash",None)
        self._events(connection_id)
        return {"id":connection_id,"state":"connected"}

    async def chats(self, connection_id, offset=0, limit=50):
        connection = self.get(connection_id)
        output = []
        async for dialog in connection["client"].iter_dialogs(limit=offset+limit):
            connection["entities"][entity_key(dialog.entity)] = dialog.entity
            output.append({"chat_key":entity_key(dialog.entity),"title":dialog.name,"kind":type(dialog.entity).__name__})
        return {"items":output[offset:],"has_more":len(output)==offset+limit}

    async def page(self, connection_id, chat_key, end, cursor):
        connection = self.get(connection_id)
        entity = connection["entities"].get(chat_key)
        if entity is None:
            async for dialog in connection["client"].iter_dialogs():
                if entity_key(dialog.entity)==chat_key:
                    entity=dialog.entity
                    connection["entities"][chat_key]=entity
                    break
        if entity is None:
            raise ValueError("연결 계정에서 이 대화에 접근할 수 없습니다.")
        # General history, without claiming author-search completeness.
        messages = await asyncio.wait_for(connection["client"].get_messages(entity,limit=100,offset_id=int(cursor or 0),offset_date=end if not cursor else None),30)
        return [message_metadata(m) for m in messages if not isinstance(m,types.MessageEmpty)]

    async def disconnect(self, connection_id):
        connection=self.connections.pop(connection_id,None)
        remote=False
        if connection:
            try:
                remote=bool(await asyncio.wait_for(connection["client"].log_out(),10))
            except Exception:
                pass
            finally:
                await connection["client"].disconnect()
        return {"local_deleted":True,"remote_revoked":remote}

    async def close(self):
        # Do not keep a plaintext session file, including on ordinary shutdown.
        for connection in list(self.connections.values()):
            await connection["client"].disconnect()
        self.connections.clear()

    def _events(self, connection_id):
        connection=self.get(connection_id)
        client=connection["client"]

        async def changed(event):
            if not event.message or not event.message.date:
                return
            try:
                entity=await event.get_chat()
                key=entity_key(entity)
                normalized=message_metadata(event.message)
                with self.store.connect() as db:
                    workspaces=db.execute("SELECT DISTINCT workspace_id FROM collection_runs WHERE kind='api' AND json_extract(config,'$.account_id')=? AND json_extract(config,'$.chat_key')=?",(connection["account_id"],key)).fetchall()
                    for w in workspaces:
                        # Updates only affect previously observed messages. New arrivals are manual refreshes.
                        if db.execute("SELECT 1 FROM messages WHERE workspace_id=? AND chat_key=? AND message_id=?",(w[0],key,normalized["message_id"])).fetchone():
                            upsert(db,w[0],key,normalized,"event:"+uid(),0)
                            db.execute("UPDATE workspaces SET revision=revision+1 WHERE id=?",(w[0],))
            except Exception:
                return  # Never log event payloads or credentials.

        async def deleted(event):
            # Non-channel delete updates may not identify a chat; do not guess one.
            if not event.chat_id or not getattr(event,"is_channel",False):
                return
            key="channel:"+str(abs(event.chat_id)-1000000000000)
            with self.store.connect() as db:
                owners=db.execute("SELECT DISTINCT workspace_id FROM collection_runs WHERE kind='api' AND json_extract(config,'$.account_id')=? AND json_extract(config,'$.chat_key')=?",(connection["account_id"],key)).fetchall()
                for w in owners:
                    for message_id in event.deleted_ids:
                        db.execute("DELETE FROM messages WHERE workspace_id=? AND chat_key=? AND message_id=?",(w[0],key,str(message_id)))
                        db.execute("DELETE FROM message_provenance WHERE workspace_id=? AND chat_key=? AND message_id=?",(w[0],key,str(message_id)))
                    db.execute("UPDATE workspaces SET revision=revision+1 WHERE id=?",(w[0],))

        client.add_event_handler(changed,events.MessageEdited())
        client.add_event_handler(deleted,events.MessageDeleted())
