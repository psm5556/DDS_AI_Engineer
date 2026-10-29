# PC 테스트 통로: 사내 AI가 서버에서 실제 CAD/CAE 툴로 스스로 테스트하기

> **상태:** 구현 전 개발 지침(2026-10-30 작성).
> - 사내 AI(Claude Code·Gemini CLI·Codex)가 이 문서대로 만든다.
> - 다 만들면 이 문서를 "쓰는 법" 문서로 고친다.
> - 개발 규칙은 [AGENTS.md](../AGENTS.md)를 따른다: 한국어 커밋, 검증 후 커밋, PC Agent를 바꾸면 `__version__`을 올린다, 비밀값과 `data/`는 커밋하지 않는다.
> - 이 문서와 실제 코드가 다르면 코드에 맞춘다. 바꾼 이유는 커밋 본문에 적는다.

## 1. 목표
- **지금:** 사용자가 직접 실제 CAD/CAE 툴과의 연동을 시험한다.
  1. Linux 서버에서 웹앱을 띄운다.
  2. 자기 PC 브라우저로 접속한다.
  3. 실제 툴과 잘 연동되는지 본다.
- **바꿀 것:** Linux 서버에서 개발하는 사내 AI가 이 시험을 사람 없이 반복한다.
  - 스킬을 고친 뒤 **사용자 PC의 실제 툴로 직접 실행한다.**
  - 결과와 오류를 받아 보고 다시 고친다.
- **고친 스킬은 요청에 실어 보낸다.** 그래서 버전 배포와 자동 업데이트를 기다리지 않는다. 요청마다 서버 저장소의 `agent-context` 전체를 압축해 보낸다.
- **첫 용도:** 스킬에 있는 `TODO(사내검증)` 약 560곳을 줄인다.
- **통로:** 이미 있는 PC Agent WebSocket을 쓴다. 원격 데스크톱이나 화면 클릭 자동화는 쓰지 않는다.

## 2. 지금 구조 (손댈 곳)
| 무엇 | 위치 |
|---|---|
| 서버 → PC Agent 요청 | `registry.runner(user.id).call("타입", {...}, timeout=초)` — `server/app/agent/link.py` |
| PC Agent 메시지 처리 | `agent/dds_agent/main.py`의 `handle()` (`_respond(rid, 함수, ...)` 패턴) |
| 툴 실행 명령 | `cae_adapters/tool_commands.py`의 `build_command`·`tool_env` (`python` 키는 PC Agent의 파이썬) |
| 케이스 실행(참고) | `cae_adapters/script.py`의 `ScriptAdapter.run_case` |
| 실행기 | `agent/dds_agent/job_runner.py`의 `LocalRunner.run` |
| 스킬 동기화 | `agent/dds_agent/context.py` — PC Agent **설치본**의 `agent-context`를 복사한다. 그래서 지금은 스킬을 고치면 버전 배포가 필요하다 |
| 창 캡처 | `mcp_servers/dds_live.py`의 `_find_window`·`_capture_window`(PrintWindow) |
| PC Agent 설정 | `agent/dds_agent/config.py`의 `AgentSettings` (`agent.toml`) |
| 트레이 메뉴 | `agent/dds_agent/tray.py` |
| 관리자 권한 | `server/app/deps.py`의 `require_admin`. API 토큰(웹앱 > 설정에서 발급)으로도 인증된다 |
| 가짜 PC Agent 테스트 | `server/tests/test_features2.py:61` (`registry.runner`를 FakeRunner로 바꾼다) |

- WebSocket 메시지는 최대 16 MB다(`main.py`의 `websockets.connect(max_size=...)`).
- `agent-context` 전체는 약 1.8 MB이고, 압축하면 더 작다.

## 3. 만들 것

### 3-1. PC Agent: 테스트 모드와 `dev.info`·`dev.run`

**테스트 모드**
- **켜고 끄기**
  - 기본은 꺼져 있다.
  - 트레이 메뉴 "개발 테스트 허용(3시간)"으로 켜고, 다시 누르면 끈다.
  - 정한 시간이 지나면 저절로 꺼진다.
- **표시**
  - 켜져 있으면 트레이 상태 줄에 "개발 테스트 허용 · HH:MM까지"를 보인다.
  - 테스트가 돌기 시작하면 트레이 알림 "개발 테스트 실행: <label>"을 띄운다.
- **`agent.toml` 설정**
  ```toml
  [dev_test]
  hours = 3        # 트레이에서 켰을 때 허용 시간
  always = false   # 전용 테스트 PC에서만 true(늘 허용)
  ```
- **저장 위치:** 켜짐 상태는 PC Agent 메모리에만 둔다. 다시 시작하면 꺼진다. `always = true`만 예외다.

**`dev.info`의 응답**
```json
{"dev_test": true, "until": "2026-10-30T18:00:00", "version": "0.2.x", "os": "Windows 11", "hostname": "PC-1234",
 "tools": {"nx": "C:\\Program Files\\Siemens\\NX2306\\...", "abaqus": "..."}, "busy": null}
```
- `tools`에는 지금 PC Agent가 쓰는 툴 경로(`self.s.tools`)를 담는다.
- `busy`에는 진행 중인 작업 이름을 담는다. 없으면 `null`이다.

**`dev.run` 요청**
```json
{
  "type": "dev.run", "rid": 1,
  "label": "abaqus 정적해석 스모크",
  "context_zip": "<base64>",
  "files": {"inputs/plate.inp": "<base64>"},
  "steps": [
    {"tool": "abaqus", "script": "skills/abaqus-automation/scripts/abq_case_run.py", "args": ["--inp", "inputs/plate.inp"]}
  ],
  "timeout_sec": 900,
  "capture": "Abaqus/CAE",
  "collect": ["results.json", "*.log", "*.msg", "*.sta"]
}
```
- `context_zip`: 서버 저장소의 `agent-context` 전체를 압축한 것.
- `files`: 테스트 입력. 작은 파일만 보낸다.
- `steps`: `ScriptAdapter`의 step과 같은 모양이다. `script`는 `agent-context` 안에서의 상대 경로다.
- `timeout_sec`: step 하나의 제한 시간.
- `capture`: 선택. 실행이 끝난 뒤 제목에 이 글자가 든 창을 캡처한다.
- `collect`: 돌려받을 파일. `work/` 기준 glob 패턴이다.

**PC의 테스트 폴더**
```
<workspace_dir>/_devtest/<YYYYMMDD-HHMMSS>_<label>/
  context/   ← context_zip을 푼 곳 (agent-context와 같은 구조: skills/, live/, INSTRUCTIONS.md)
  work/      ← files를 쓰고 명령을 실행하는 곳(cwd)
```

**동작 순서**
1. 테스트 모드가 아니면 바로 오류를 돌려준다.
   - 문구: "PC에서 개발 테스트가 꺼져 있습니다 → 트레이 아이콘 > 개발 테스트 허용"
2. 사용자가 작업 중이면 거부하고, 무엇이 진행 중인지 오류에 적는다. 거부하는 경우는 다음과 같다.
   - 대화 턴 진행 중
   - 케이스 실행 중
   - 다른 `dev.run` 실행 중
3. 위 폴더를 새로 만들고 zip과 `files`를 푼다.
   - **테스트 폴더 밖으로 나가는 경로는 거부한다.** 절대 경로, `..`, zip slip이 해당된다.
4. step마다 아래를 검사한 뒤 실행한다.
   - `tool`은 `tool_commands`에 있는 키만 받는다. 셸 문자열과 임의 실행 파일은 받지 않는다.
   - `script`는 `context/` 안의 파일만 받는다.
   - 스크립트는 그 자리에서 실행한다. 같은 스킬의 다른 스크립트를 불러올 수 있게 하기 위해서다.
   - 명령은 `build_command`·`tool_env`로 만든다. 케이스 실행과 같은 규칙이다.
   - 앞 step이 실패하면 멈춘다.
5. 시간을 넘기면 **프로세스 트리 전체를 끝낸다.**
   - Windows에서는 `taskkill /PID <pid> /T /F`를 쓴다.
   - 이유: 솔버의 자식 프로세스가 남아 라이선스를 계속 잡지 않게 하기 위해서다.
   - 지금 `LocalRunner.run`은 `subprocess.run(timeout=)`을 써서 바로 아래 자식만 끝낸다. 케이스 실행에도 같은 문제가 있으므로 같은 함수로 함께 고친다.
6. `capture`가 있으면 창을 찍는다.
   - `_capture_window`와 같은 방식(PrintWindow)으로 찍고, 긴 변 1600 px PNG로 줄인다.
   - 캡처 코드는 복사하지 말고 공용 함수로 옮겨 `dds_live.py`와 함께 쓴다.
7. 아래 모양으로 응답한다.
   ```json
   {"ok": true, "folder": "C:\\Users\\...\\_devtest\\20261030-141500_abaqus",
    "steps": [{"command": ["..."], "returncode": 0, "seconds": 41.2, "stdout_tail": "...", "stderr_tail": "..."}],
    "files": {"work/results.json": {"text": "..."}, "work/plate.odb": {"size": 123456}},
    "image": "<base64 png 또는 null>"}
   ```
   - 1 MB 이하 텍스트 파일은 내용을 담는다. 그보다 크거나 바이너리면 크기만 담는다.
   - 응답 전체는 8 MB 이하로 한다.
8. PC Agent 로그에 한 줄을 남긴다: 시각, label, 툴, 스크립트, 결과, 걸린 시간.
9. `_devtest`에는 최근 30개만 남기고 오래된 것부터 지운다. `_devtest` 밖은 절대 지우지 않는다.

### 3-2. 서버: `server/app/routers/devtest.py`
- **API**
  - `GET /api/dev/pc-info`는 `dev.info`를 보낸다.
  - `POST /api/dev/pc-run`은 `dev.run`을 보낸다.
- **권한:** `require_admin`.
  - **호출한 관리자 자신의 PC Agent에만** 보낸다(`registry.runner(admin.id)`).
  - 다른 사용자의 PC를 고르는 인자는 만들지 않는다.
- **켜는 조건:** 환경변수 `DDS_DEV_BRIDGE=1`일 때만 라우터를 등록한다.
  - 기본은 꺼짐이다.
  - 운영 서비스 파일(`deploy/linux/`)에는 넣지 않는다.
  - 운영 서버에서 켜야 하면 먼저 사용자에게 확인한다.
- **제한 시간:** `timeout_sec × step 수 + 120`초.
- **PC Agent가 연결되어 있지 않으면** 409를 돌려준다.
  - 문구: "PC Agent가 연결되어 있지 않습니다 → 내 PC에서 DDS Agent를 실행하고 이 서버에 연결하세요"
- 요청·응답 스키마는 `schemas.py`에 둔다.
- 서버 로그에 누가, 언제, 어떤 label·툴로 실행했고 결과가 어땠는지 남긴다.

### 3-3. 사내 AI가 쓰는 명령: `scripts/pc_probe.py`
- **인증:** 환경변수 두 개를 쓴다. 토큰은 파일이나 저장소에 쓰지 않는다.
  - `DDS_URL`: 기본값은 `http://127.0.0.1:7014`
  - `DDS_DEV_TOKEN`: 웹앱 > 설정에서 관리자 계정으로 발급한 토큰
- **`pc_probe.py info`**
  - 연결 상태, 테스트 모드, 툴 목록을 보여 준다.
- **`pc_probe.py run`**
  ```
  pc_probe.py run --tool abaqus --script skills/abaqus-automation/scripts/x.py
      [--args ...] [--input 파일 ...] [--capture 창제목] [--collect 패턴 ...] [--timeout 900] [--label 이름]
  ```
  - 매번 지금 저장소의 `agent-context`를 zip으로 묶어 보낸다. `__pycache__`는 뺀다.
  - 결과는 `data/devtest/<시각>_<label>/`에 푼다. `data/`는 git에 올라가지 않는다.
  - 화면에 요약을 출력한다.
    - step별 종료 코드와 걸린 시간
    - stderr 끝 30줄
    - results.json
    - 캡처 파일 경로
- **`pc_probe.py suite <툴 키>`**
  - `tests/onsite/<툴 키>.json`의 항목을 차례로 실행한다.
  - 결과표를 `data/devtest/suite_<툴>_<시각>.md`로 쓴다.
- **종료 코드**
  - 0: 통과
  - 1: 실패
  - 2: 연결·권한·테스트 모드 문제. 무엇을 하면 되는지 함께 출력한다.

### 3-4. 점검 목록: `tests/onsite/`
- **`tests/onsite/<툴 키>.json`**
  - `docs/onsite-validation.md`의 툴별 확인 항목을 `run` 인자 형식으로 옮긴다.
  - `expect`는 통과 조건이다.
  ```json
  [{"label": "nx 파트 정보", "steps": [], "inputs": ["models/nx_block.py"], "collect": ["results.json"],
    "expect": {"returncode": 0, "results_keys": ["mass"], "ranges": {"mass": [0.1, 10]}}}]
  ```
- **입력 모델**은 아주 작게 만들어 `tests/onsite/models/`에 둔다.
  - 솔버: 텍스트 입력 파일(inp, k, bdf 등)을 직접 만든다. 요소는 수십~수백 개면 된다.
  - CAD: 스크립트로 단순한 형상을 만드는 step을 앞에 둔다.
  - 사내 실제 모델이 필요하면 사용자에게 PC 경로를 묻는다. 그 경로는 스크립트 인자로 넘긴다.
  - 사내 모델은 저장소에 넣지 않는다.

### 3-5. 개발용 스킬과 AGENTS.md
- **`.claude/skills/onsite-pc-test/SKILL.md`를 만든다.**
  - 개발용이다. Agent 배포에는 들어가지 않는다. `live-link-speedup`과 같은 자리에 둔다.
  - **언제 쓰는가:** 스킬이나 툴 연동 코드를 고친 뒤, 그리고 `TODO(사내검증)`을 지울 때.
  - **순서**
    1. `info`로 연결과 툴을 확인한다.
    2. 작은 모델로 `run`을 한다.
    3. 실패하면 stderr와 로그를 읽고 고쳐서 다시 `run`을 한다.
    4. 통과하면 그 자리의 `TODO(사내검증)`을 지운다.
    5. 툴 단위로 `suite`를 돌린다.
    6. 결과를 `docs/onsite-validation.md`에 툴 버전과 함께 기록한다.
    7. 커밋한다.
  - **규칙**
    - 한 번에 툴 하나만 테스트한다.
    - 첫 실행은 작은 모델로 한다.
    - GUI 창이 뜨는 테스트는 사용자에게 먼저 알린다.
    - 결과 숫자가 물리적으로 맞는지는 AI가 판단하지 않는다. "사용자 확인 필요" 목록으로 남긴다.
    - 같은 오류로 3번 실패하면 멈추고 사용자에게 묻는다.
- **`AGENTS.md` 6절 표에 한 줄을 추가한다.**
  - 내용: "실제 툴로 스스로 테스트 | `.claude/skills/onsite-pc-test/SKILL.md`부터 읽는다(사용자 PC에서 개발 테스트를 허용해야 함)"
  - Gemini CLI와 Codex도 이 파일 경로를 직접 읽으면 된다.

## 4. 안전 규칙 (꼭 지킨다)
- 테스트 모드는 기본으로 꺼져 있다. 사용자가 PC 트레이에서 켤 때만, 정한 시간 동안만 동작한다.
- 호출한 사람 자신의 PC에서만 실행한다.
- 툴 실행 키와 `agent-context` 안의 스크립트만 실행한다. 셸 명령과 임의 실행 파일은 받지 않는다.
- 파일을 쓰고 지우는 일은 `_devtest` 폴더 안에서만 한다.
- 사용자가 대화하거나 케이스를 실행하는 중에는 실행하지 않는다.
- 토큰과 PIN을 로그, 결과, 커밋에 남기지 않는다.
- 이 기능은 서버가 개인 PC에서 **AI가 고친 코드**를 실행하는 구조다. 사내 보안 담당의 확인을 받기 전에는 사용자 본인 PC에서만 쓴다.

## 5. 테스트와 완료 조건
- **`agent/tests/test_devtest.py`(새로 만든다)**
  - 테스트 모드가 꺼져 있으면 거부한다. 허용 시간이 지나면 꺼진다.
  - 경로 탈출(절대 경로, `..`, zip slip)을 거부한다.
  - 모르는 툴 키와 `context/` 밖의 스크립트를 거부한다.
  - `python` 키로 가짜 툴 스크립트를 실행한다. 이 스크립트는 results.json을 쓰고 자식 프로세스를 하나 띄운다.
    - 결과 파일을 수집하는지 확인한다.
    - 시간을 넘기면 자식 프로세스까지 끝나는지 확인한다.
  - 다른 실행이 진행 중이면 거부한다.
  - 응답 크기 제한과 오래된 `_devtest` 정리가 동작한다.
- **`server/tests`**
  - `require_admin`이 적용된다.
  - `DDS_DEV_BRIDGE`가 꺼져 있으면 404를 돌려준다.
  - PC Agent가 연결되어 있지 않으면 409를 돌려준다.
  - FakeRunner로 `dev.run`이 그대로 전달되는지 확인한다.
- **AGENTS.md 4절 검증 명령이 모두 통과해야 한다.** 프론트엔드를 바꾸지 않았으면 빌드는 생략한다.
- **실제 확인**
  1. `agent/dds_agent/__init__.py`의 `__version__`을 올리고 관리 > Agent 배포 > "서버 코드로 첫 버전 배포"를 한다.
     - 사용자 PC가 업데이트되고 트레이에 새 메뉴가 보이는지 확인한다.
  2. 사용자가 트레이에서 "개발 테스트 허용"을 켠다.
  3. 서버에서 `pc_probe.py info`를 실행해 툴 목록이 보이는지 확인한다.
  4. `pc_probe.py run --tool python ...`(가짜 툴)이 통과하는지 확인한다.
  5. 사용자가 실제로 쓰는 툴 하나(예: NX)로 `suite`를 돌려 결과표를 만든다.
- **커밋:** 한국어로, 검증을 통과한 뒤에 한다. 푸시는 사용자가 요청할 때 한다.

## 6. 하지 말 것
- 원격 데스크톱이나 화면 클릭 자동화
- 다른 사용자의 PC에서 실행하는 기능
- 사용자의 실제 모델이나 결과 파일을 저장소에 커밋하는 일
- 이 기능을 위해 웹 화면을 바꾸는 일(이번 범위 밖)

## 7. 만든 뒤 첫 작업
1. 사용자가 실제로 쓰는 툴부터 차례로 `suite`를 돌린다.
2. 실패한 항목을 `run`으로 하나씩 고치고 `TODO(사내검증)`을 지운다.
3. 결과 숫자를 사람이 확인해야 하는 항목은 목록으로 만들어 사용자에게 보여 준다.
4. 스킬을 고쳤으면 `__version__`을 올려 배포한다. 그래야 사용자들의 실제 사용에 반영된다.
