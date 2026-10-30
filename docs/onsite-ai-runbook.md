# 사내 서버 준비 런북 (사내 AI가 그대로 따라 하는 절차)

목표: git으로 저장소만 받은 사내 서버에서 웹앱을 띄우고, 사용자들이 **설정 > 설치**에서 PC Agent를 내려받아 설치할 수 있게 만든다.
인터넷은 쓰지 않는다. 사내 pip·npm 미러와 서버에 설치된 Python·Node만 쓴다.

- 서버는 **Windows**와 **Linux** 모두 된다.
  - Windows 서버는 아래 0~3단계를 따른다.
  - Linux 서버는 **[L. Linux 서버](#l-linux-서버)** 절을 따른다.
- 사용자 PC는 Windows다. PC Agent 설치 프로그램과 포터블 런타임은 Windows용이라 Windows에서만 만들 수 있다.
  - Linux 서버는 이것을 Windows PC에서 묶음(zip) 하나로 만들어 옮긴다.
- 스킬(`agent-context/`)은 Agent 버전에 들어 있다.
  - 설치와 자동 업데이트 때 사용자 PC로 간다.
  - 프로젝트를 열 때마다 작업 폴더(`.claude/skills` 등)로 복사된다. 따로 설치하지 않는다.

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

## L. Linux 서버
0~3단계 대신 이 절을 따른다. 끝나면 4단계부터는 같다.

### L-0. 확인
```bash
hostname; python3.12 --version; node --version; npm --version
pip config list; npm config get registry
fc-list :lang=ko | head -3
```
- Python 3.12(3.11 이상)가 있다.
- Node 18 이상과 npm이 있다(웹 화면 빌드용).
  - 없으면 Windows PC에서 `cd frontend; npm ci; npm run build`로 만든 `frontend/dist` 폴더를 서버의 같은 위치에 복사한다.
- pip·npm이 사내 미러를 가리킨다.
- 한글 글꼴이 하나 이상 나온다. 보고서 그림에 쓴다.
  - 없으면 사내 패키지 미러에서 설치한다. Ubuntu·Debian은 `sudo apt install fonts-nanum`, RHEL 계열은 `sudo dnf install google-noto-sans-cjk-ttc-fonts`
  - 설치한 뒤 `rm -rf ~/.cache/matplotlib`로 글꼴 목록을 다시 만들게 한다.

### L-1. 코드와 의존성
```bash
git pull
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-server.txt
```
- 데이터 폴더는 저장소 루트의 `server.toml`에 `data_dir = '/var/lib/dds-conversa'`처럼 쓴다. 서버와 `prepare_onsite.py`가 같은 값을 읽는다.
- 서비스 계정이 그 폴더에 쓸 수 있어야 한다.

### L-2. Windows PC에서 설치 묶음 만들기 (이 단계만 Windows)
- 필요한 것: 저장소(서버와 같은 버전), python.org Python 3.12, Node, 사내 pip 미러가 있는 Windows PC 아무거나
```powershell
python scripts\prepare_onsite.py --pack agent_runtime_pack.zip
```
- 설치 프로그램(`DDS-Agent-Setup.exe`), 포터블 Python·Node, Agent 의존성 wheel을 zip 하나(약 200 MB)로 만든다.
- 이 PC의 서버 데이터 폴더는 건드리지 않는다.
- 만든 zip을 Linux 서버로 옮긴다(scp 등).

### L-3. 서버 실행 (systemd)
- `deploy/linux/dds-conversa.service`에서 경로(`/opt/dds-conversa`)와 사용자(`dds`)를 이 서버에 맞게 고친다. 그다음 등록한다.
```bash
sudo cp deploy/linux/dds-conversa.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dds-conversa
```
- 방화벽에서 7014 포트를 연다. 사용자 브라우저와 PC Agent가 이 포트로 접속한다.
- 로그: `journalctl -u dds-conversa -f`
- 처음 가입한 계정이 관리자가 된다.

### L-4. 설치 준비 마무리
서버를 켜 둔 채로 실행한다.
```bash
.venv/bin/python scripts/prepare_onsite.py --import-pack agent_runtime_pack.zip --publish --server http://127.0.0.1:7014 --user <관리자 아이디> --pin <PIN>
```
이 스크립트가 하는 일:
1. 묶음을 데이터 폴더의 `agent_runtime/`에 넣는다(sha256 확인, 예전 런타임 파일 정리).
2. `frontend/dist`가 없으면 웹 화면을 빌드한다.
3. 서버 코드로 Agent 첫 버전을 등록한다.
4. 준비 상태를 점검한다.

**확인**
- 마지막 줄이 `READY`다.
- 웹앱 **관리 > Agent 배포**의 "PC Agent 설치 준비" 세 항목이 모두 초록색이다.

### Linux 서버 업데이트
- 웹앱만 바뀐 경우
  - `git pull` → `frontend`에서 `npm run build` → `sudo systemctl restart dds-conversa`
  - 그다음 관리 > Agent 배포에서 "서버 코드로 첫 버전 배포"를 누른다(새 `__version__`일 때).
- 설치 프로그램이나 런타임을 바꿔야 할 때(드묾): L-2와 L-4의 `--import-pack`을 다시 한다.

## 4. 사용자 PC 설치 안내 (사람에게 전달)
1. 웹앱 **설정**에서 `DDS-Agent-Setup.exe`를 받아 실행한다. 관리자 권한은 필요 없다.
2. 웹앱 아이디·PIN을 입력한다. 토큰은 자동으로 발급된다.
3. 이 PC에 없는 AI(Claude Code·Gemini·Codex)만 고른 뒤 [설치 시작]을 누른다. 이미 있는 것은 "설치됨"으로 표시된다.
4. 설치가 끝나면 설치 창에서 각 AI의 [로그인]을 누른다. 사내 보안 환경에서는 몇 분 걸릴 수 있으니 열린 창을 닫지 말고 "로그인 완료"가 나올 때까지 기다린다.
5. 웹앱 사이드바 아래 **"PC Agent 연결됨 · <내 PC 이름>"**을 확인한다. 서버 이름이 보이면 잘못 연결된 것이다.
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
| `Linux 서버에서는 만들 수 없습니다` | Linux 서버에서 설치 프로그램·런타임을 만들려 함 | L-2(Windows PC에서 `--pack`) → L-4(`--import-pack`) |
| `파일이 손상됐습니다(sha256 불일치)` | 묶음을 옮기다 깨짐 | zip을 다시 옮긴다(scp는 바이너리 그대로 옮긴다) |
| 보고서 그림의 한글이 네모로 나옴 | Linux 서버에 한글 글꼴 없음 | L-0의 글꼴 설치 → `rm -rf ~/.cache/matplotlib` → 서버 재시작 |
| 다른 PC에서 `http://<서버>:7014`가 안 열림 | 방화벽 | 7014 포트 열기(`firewall-cmd`·`ufw` 등 서버 관리 방식대로) |

## 하지 말 것
- 외부 인터넷(GitHub·nodejs.org·PyPI)에서 내려받으려 하지 않는다.
- `data\`, 토큰, PIN, `server.toml`의 secret을 git에 커밋하지 않는다.
- 운영 서버 컴퓨터에 PC Agent를 설치해 두지 않는다. 확인용으로 잠깐 설치했다면 트레이 > 종료 후 설정 > 토큰에서 폐기한다.
