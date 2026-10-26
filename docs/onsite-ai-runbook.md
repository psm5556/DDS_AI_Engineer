# 사내 서버 준비 런북 (사내 AI가 그대로 따라 하는 절차)

목표: git으로 저장소만 받은 사내 서버에서 웹앱을 띄우고, 사용자들이 **설정 > 설치**에서 PC Agent를 내려받아 설치할 수 있게 만든다.
인터넷은 쓰지 않는다. 사내 pip·npm 미러와 서버에 설치된 Python·Node만 쓴다.

> 사람이 할 일: AI에게 "docs/onsite-ai-runbook.md대로 서버를 준비해 줘. 관리자 아이디는 ○○, PIN은 ○○○○○○야"라고 요청한다.
> AI는 각 단계의 **확인** 결과를 보고하고, 실패하면 아래 "문제 해결"을 보고 고친 뒤 다시 시도한다.

---

## 0. 지금 어디인지 확인
```powershell
hostname; python --version; node --version; npm --version
pip config list; npm config get registry
```
**확인**
- 이 컴퓨터가 웹앱을 띄울 **서버**다(사용자 PC가 아니다).
- Python 3.12(3.11 이상)가 있다. python.org 설치본이어야 하며, Microsoft Store판은 안 된다.
- Node 18 이상과 npm이 있다.
- pip·npm이 사내 미러를 가리킨다.

## 1. 코드와 의존성
```powershell
git pull
python -m venv .venv
.venv\Scripts\pip install -r requirements-server.txt
```
**확인:** 오류 없이 끝난다. 개발도 할 서버라면 `requirements-dev.txt`를 설치한다.

## 2. 서버 실행
```powershell
.venv\Scripts\python -m uvicorn server.app.main:app --host 0.0.0.0 --port 7014
```
- 데이터 폴더를 바꾸려면 저장소 루트의 `server.toml`에 `data_dir = 'D:\DDS\data'`를 쓴다(기본은 저장소의 `data\`).
- 상시 실행은 NSSM 또는 작업 스케줄러에 등록한다.
- 서버 컴퓨터에서는 `scripts\dev_live.py`를 쓰지 않는다. 이 스크립트는 PC Agent까지 서버에 띄워, AI 작업이 서버에서 돌게 된다.

**확인**
- 브라우저에서 `http://<서버>:7014`가 열린다.
- 처음 가입한 계정이 관리자가 된다. 아직 계정이 없으면 사람이 가입하게 한다.

## 3. PC Agent 설치 파일 준비 (한 번에)
서버를 켜 둔 채로, 다른 창에서 실행한다.
```powershell
.venv\Scripts\python scripts\prepare_onsite.py --publish --server http://127.0.0.1:7014 --user <관리자 아이디> --pin <PIN>
```
이 스크립트가 하는 일:
1. `frontend\dist`가 없으면 웹 화면을 빌드한다(`npm ci` → `npm run build`).
2. `DDS-Agent-Setup.exe`를 빌드한다(Windows 기본 .NET Framework csc).
3. 포터블 런타임을 만든다. 서버의 Python·Node를 묶고, Agent 의존성 wheel은 사내 pip 미러에서 받는다. 수 분 걸린다.
4. 서버 코드로 Agent 첫 버전을 등록한다.
5. 준비 상태를 점검한다.

**확인**
- 마지막 줄이 `READY`다.
- 웹앱 **관리 > Agent 배포**의 "PC Agent 설치 준비" 세 항목이 모두 초록색이다.

## 4. 사용자 PC 설치 안내 (사람에게 전달)
1. 웹앱 **설정 > 설치**에서 `DDS-Agent-Setup.exe`를 받고 **토큰 발급**을 누른다.
2. 설치 파일을 실행해 토큰을 붙여 넣고, 쓸 AI(Claude Code·Gemini·Codex)를 고른 뒤 [설치]를 누른다. 관리자 권한은 필요 없다.
3. 처음 한 번은 새 터미널을 열고 각 AI CLI에 로그인한다: `claude` → `/login` · `gemini` · `codex login`
4. 웹앱 사이드바 아래 **"PC Agent 연결됨 · <내 PC 이름>"**을 확인한다. 서버 이름이 보이면 잘못 연결된 것이다.
5. **CAD·CAE 툴** 화면에서 내 PC에 설치된 툴을 확인한다. 기본 위치가 아니면 툴을 눌러 경로 설정 방법을 본다.

## 5. 문제 해결
| 증상 | 원인 | 해결 |
|---|---|---|
| 설치 파일을 받을 때 "사이트에서 사용할 수 없는 파일" | 서버에 설치 파일이 없음 | 3단계 실행, 관리 > Agent 배포 점검표 확인 |
| `csc.exe가 없습니다` | .NET Framework 4.x 없음 | Windows 기능에서 .NET Framework 4.8 켜기, 또는 다른 PC에서 `scripts\build_setup_exe.ps1`로 만든 exe를 `<data_dir>\agent_runtime\`에 복사 |
| `Microsoft Store용 Python은 묶을 수 없습니다` | 서버 Python이 Store판 | python.org 설치본으로 서버를 다시 설치·실행 |
| `Node.js(npm 포함)를 찾을 수 없습니다` | PATH에 node 없음 | Node 설치 또는 PATH 추가 후 새 창에서 다시 실행 |
| `wheel을 받지 못했습니다` (경고) | pip 미러에 일부 패키지 없음 | 그대로 진행해도 된다. 사용자 PC 설치 때 사내 pip 미러에서 직접 받는다. 미러 관리자에게 `requirements-agent.txt` 패키지 추가를 요청한다 |
| `npm 패키지 설치 실패` | npm 미러 설정 | `npm config set registry <사내 미러>` 후 다시 실행 |
| 관리자 로그인 실패 | 아이디·PIN 오류 또는 잠김(5회) | 사람에게 확인, 잠기면 10분 뒤 |
| 사용자 PC 설치 중 Node 압축 해제 실패 | 경로가 너무 김(260자) | 기본 설치 위치(`%LOCALAPPDATA%\DDS-Agent`)를 쓴다 |
| 런타임을 새로 만들고 싶다 | — | `prepare_onsite.py --force-runtime` |

## 하지 말 것
- 외부 인터넷(GitHub·nodejs.org·PyPI)에서 내려받으려 하지 않는다.
- `data\`, 토큰, PIN, `server.toml`의 secret을 git에 커밋하지 않는다.
- 운영 서버 컴퓨터에 PC Agent를 설치해 두지 않는다. 확인용으로 잠깐 설치했다면 트레이 > 종료 후 설정 > 토큰에서 폐기한다.
