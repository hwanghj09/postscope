"""Run on your own PC: put Telegram login secrets directly into GitHub Secrets."""
import argparse
import asyncio
import getpass
import shutil
import subprocess
from telethon import TelegramClient,errors
from telethon.sessions import StringSession


def save_secret(repo,name,value):
    subprocess.run(['gh','secret','set',name,'--repo',repo],input=value,text=True,check=True)


async def configure(repo,target=None):
    if not shutil.which('gh'):raise ValueError('GitHub CLI를 설치하고 gh auth login으로 로그인하세요.')
    api_id=int(input('Telegram API ID: ').strip())
    api_hash=getpass.getpass('Telegram API Hash: ').strip()
    phone=getpass.getpass('전화번호 (+국가코드): ').strip()
    target=target or input('수집할 채널 @아이디 또는 방 ID: ').strip()
    if not target:raise ValueError('수집할 방을 입력하세요.')
    client=TelegramClient(StringSession(),api_id,api_hash)
    try:
        await client.connect()
        sent=await client.send_code_request(phone)
        code=getpass.getpass('Telegram 로그인 코드: ').strip()
        try:await client.sign_in(phone=phone,code=code,phone_code_hash=sent.phone_code_hash)
        except errors.SessionPasswordNeededError:await client.sign_in(password=getpass.getpass('2단계 인증 비밀번호: '))
        # Verify access before configuring a scheduled job. Never auto-join a chat.
        await client.get_entity(int(target) if target.lstrip('-').isdigit() else target)
        for name,value in [('TELEGRAM_API_ID',str(api_id)),('TELEGRAM_API_HASH',api_hash),('TELEGRAM_SESSION',client.session.save()),('TELEGRAM_CHAT',target)]:
            save_secret(repo,name,value)
        print('GitHub Secrets 설정 완료. Deploy website를 collect=true로 실행하면 수집·배포합니다.')
    finally:await client.disconnect()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repo',default='hwanghj09/postscope');parser.add_argument('--chat');args=parser.parse_args()
    try:asyncio.run(configure(args.repo,args.chat))
    except Exception:
        raise SystemExit('설정하지 못했습니다. GitHub 로그인·권한 및 Telegram 인증·방 접근을 확인하세요.') from None
