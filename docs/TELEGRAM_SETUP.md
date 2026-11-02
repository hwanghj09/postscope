# 실제 텔레그램 데이터 연결하기

Windows PC에서 최초 한 번 진행합니다. 대상은 **유빈 아카이브(@yubin_MPGA), 전체 작성자**입니다. 연결 후 GitHub Actions가 매일 한국 시간 오전 9시 5분에 접근 가능한 게시 기록을 수집하고 정적 웹을 갱신합니다. 예약 실행은 GitHub 상황에 따라 지연될 수 있습니다.

현재 웹사이트: https://hwanghj09.github.io/postscope/

## 1. Telegram API 앱 만들기

1. https://my.telegram.org 에 본인 텔레그램 계정으로 로그인합니다.
2. **API development tools**를 엽니다.
3. 앱이 없으면 앱을 만듭니다. App title은 `PostScope`, Short name은 사용 가능한 영문 이름, Platform은 `Desktop`으로 설정하면 됩니다.
4. 발급된 **api_id**와 **api_hash**를 본인만 볼 수 있는 곳에서 확인합니다. 이미 앱이 있다면 기존 값을 사용합니다.

이 값은 봇 토큰이 아닙니다. API Hash·로그인 코드·비밀번호·세션을 채팅, 이슈, 소스 파일에 올리지 마세요.

## 2. PC에 도구 설치하기

시작 메뉴에서 **PowerShell**을 열고 아래 명령을 실행합니다. 이미 설치한 도구는 생략할 수 있습니다.

```powershell
winget install --id Git.Git -e
winget install --id GitHub.cli -e
winget install --id astral-sh.uv -e
```

설치가 끝나면 **PowerShell을 닫았다 다시 엽니다.** 설치 도구가 요청하는 확인은 화면 안내에 따라 진행합니다.

## 3. GitHub 로그인과 소스 준비

```powershell
gh auth login --web --git-protocol https
```

명령이 안내하는 GitHub 페이지에서 저장소 소유자인 **hwanghj09** 계정으로 로그인합니다. Git 인증 연결 질문이 나오면 승인합니다.

처음 소스를 받는 경우:

```powershell
gh repo clone hwanghj09/postscope
cd postscope
```

이미 받은 폴더가 있다면 새로 복제하지 말고 그 폴더로 이동한 뒤 갱신합니다.

```powershell
cd 기존-postscope-폴더-경로
git pull --ff-only
```

위 경로는 실제 폴더 경로로 바꿔 입력하세요.

## 4. Telegram 인증하고 Secrets 저장하기

저장소 폴더에서 실행합니다. uv가 Python 3.12와 필요한 의존성을 준비하므로 최초 실행은 시간이 걸릴 수 있습니다.

```powershell
uv run --frozen --no-dev --python 3.12 python scripts/configure_daily.py --repo hwanghj09/postscope --chat yubin_MPGA
```

화면에 요청되는 순서대로 입력합니다.

| 입력 | 넣을 값 |
| --- | --- |
| Telegram API ID | 1단계의 api_id 숫자 |
| Telegram API Hash | 1단계의 api_hash |
| 전화번호 | 국가 코드 포함 번호. 한국 번호는 `+82` 뒤에 맨 앞 `0`을 뺀 번호 |
| Telegram 로그인 코드 | Telegram이 본인에게 전달한 최신 로그인 코드 |
| 2단계 인증 비밀번호 | 해당 계정에 설정된 경우에만 입력 |

API Hash·전화번호·코드·비밀번호 입력은 **화면에 글자가 나타나지 않아도 정상**입니다. 입력 후 Enter를 누릅니다.

스크립트는 해당 계정의 채널 접근을 확인한 뒤 다음 GitHub Secrets를 직접 저장합니다. 세션 문자열을 직접 복사할 필요가 없습니다.

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION`
- `TELEGRAM_CHAT`

`GitHub Secrets 설정 완료`가 표시되면 인증 설정이 끝난 것입니다. 설정 상태는 https://github.com/hwanghj09/postscope/settings/secrets/actions 에서 **이름만** 확인할 수 있습니다. 기존 Secret 값은 GitHub 화면에서 다시 읽을 수 없습니다.

## 5. 첫 수집 실행하기

설정 스크립트는 인증값을 저장하며, 첫 수집은 별도로 실행합니다.

```powershell
gh workflow run pages.yml --repo hwanghj09/postscope --ref main -F collect=true
```

웹에서 실행하려면:

1. https://github.com/hwanghj09/postscope/actions/workflows/pages.yml 를 엽니다.
2. **Run workflow**를 누르고 브랜치는 `main`으로 둡니다.
3. **Fetch Telegram records before publishing**를 체크합니다.
4. **Run workflow**를 누릅니다.

해당 실행에서 **Update daily data → Build and test website → Deploy to GitHub Pages**가 모두 성공했는지 확인합니다. 코드만 배포하는 실행에서는 수집 단계가 생략될 수 있으므로, 실제 수집 실행의 결과를 확인하세요.

성공 후 웹사이트를 새로고침하면 실제 통계와 갱신 시각이 표시됩니다. 이후 예약 실행은 자동입니다. 채팅에 “설정 완료”라고 알려주면 Codex에 첫 수집 실행과 배포 확인을 맡길 수도 있습니다.

## 자주 막히는 부분

| 증상 | 해결 |
| --- | --- |
| `winget`을 찾을 수 없음 | Windows의 앱 설치 관리자를 설치·업데이트하거나 Git, GitHub CLI, uv를 각 공식 배포처에서 설치합니다. |
| `gh`, `git`, `uv`를 찾을 수 없음 | 설치 후 PowerShell을 완전히 닫았다 다시 엽니다. |
| 복제 폴더가 이미 존재함 | 기존 폴더로 이동해 `git pull --ff-only`를 실행합니다. |
| `scripts/configure_daily.py`가 없음 | 현재 위치가 저장소 폴더인지 확인하고 최신 소스를 받습니다. 오래된 릴리스 ZIP 대신 최신 main 소스를 사용하세요. |
| 인증 코드가 틀렸거나 만료됨 | 스크립트를 다시 실행하고 Telegram이 새로 전달한 코드를 입력합니다. |
| GitHub에서 `403` 또는 권한 오류 | `gh auth status`로 로그인 계정을 확인합니다. 저장소의 Secrets를 수정할 권한이 있는 계정으로 다시 로그인합니다. |
| Actions에 `GitHub Secrets 설정 필요`가 표시됨 | 4단계를 완료하고 위 네 Secret 이름을 확인한 뒤 수집을 다시 실행합니다. |
| 인증 만료·세션 오류 | 4단계를 다시 실행해 세션을 갱신합니다. |
| 채널 접근 오류 | 같은 Telegram 계정에서 대상 채널 기록을 열 수 있는지 확인합니다. 접근 권한을 우회하거나 자동 가입하지 않습니다. |
| Telegram 호출 제한 | 로그에 표시된 대기 시간을 지킵니다. 반복 실행으로 제한을 우회하지 마세요. |

수집 실패 시 마지막 정상 공개 데이터를 유지합니다. 최초 실제 수집 전에는 웹에 연결 대기 상태가 표시됩니다. 공개 데이터에는 게시 시각·자료 유형·가명 식별자 등 분석 메타데이터만 포함하며, 메시지 본문·첨부파일·인증 세션·원본 계정 ID는 넣지 않습니다.
