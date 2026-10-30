# 개발 AI의 PC 원격 검증

사내에서는 **Linux 서버**에서 개발·배포하고, CAD/CAE 툴은 **사용자 PC(Windows)**에만 있다. 서버에서 개발하는 AI는 이 문서의 도구로 그 PC의 툴 연동을 **스스로** 시험하고 고친다. 사람에게 "이것 좀 눌러 보고 결과를 알려 주세요"라고 하지 않는다.

```
[서버: 개발 AI] scripts/pc_verify.py ──HTTP──> /api/devtest/run ──WebSocket──> [PC: PC Agent] 시험 스크립트 실행
        ^                                                                          │ 실제 NX·Abaqus…
        └──────────────── 출력·결과 파일·화면 캡처 ───────────────────────────────────┘
```

## 1. 처음 한 번
사람이 한 번만 하면 되는 일이다.
1. 시험할 PC에서 **관리자 계정**으로 PC Agent를 연결한다.
2. 그 계정으로 웹앱 **설정 > 내 PC Agent > "개발 AI가 이 PC에서 CAD·CAE 연동을 시험하도록 허용"**을 켠다.

이후는 개발 AI가 서버에서 한다.
```bash
.venv/bin/python scripts/pc_verify.py token     # 서버 DB로 관리자 토큰을 만들어 data/pc_verify.token에 저장 (git에 안 들어감)
python3 scripts/pc_verify.py targets            # 시험할 수 있는 PC 확인
```
- 서버 주소가 7014 기본값과 다르면 `--server http://...` 또는 `DDS_SERVER_URL`. 서버가 기본이 아닌 데이터 폴더를 쓰면 `token --data <폴더>`(개발용 실행은 `data/dev`). 토큰을 만든 뒤 서버에서 받는지 바로 확인한다.
- `targets`가 비어 있으면 위 1~2번이 안 된 것이다. 이때만 사람에게 그 두 가지를 부탁한다.

## 2. 고치고 시험하는 순서
```bash
# 1) 고친다. PC Agent·MCP·스킬을 바꿨으면 agent/dds_agent/__init__.py의 __version__을 올린다
.venv/bin/python -m pytest server/tests agent/tests -q        # 서버에서 되는 검증 먼저
# 2) 배포하고 PC가 새 버전으로 뜰 때까지 기다린다(유휴를 기다리지 않고 바로 업데이트)
python3 scripts/pc_verify.py deploy
# 3) PC에서 시험한다
python3 scripts/pc_verify.py suite --tool nx                  # 기본 점검: 툴 찾기 + Live Link 붙기·읽기·캡처
python3 scripts/pc_verify.py chat "열린 파트의 두께를 4로 바꿔 줘" --agent claude   # 사용자 요청 그대로
# 4) 실패하면 원인을 본다
python3 scripts/pc_verify.py logs
python3 scripts/pc_verify.py live nx info                     # 호출 하나만 재현
python3 scripts/pc_verify.py run my_check.py                  # 궁금한 것을 직접 확인하는 스크립트
# 5) 고치고 1)부터 반복. 끝나면 고친 곳의 TODO(사내검증) 표시를 지운다
```
받은 전문·파일·화면은 `data/pc_verify/<시각>/`에 있다. PNG는 파일을 직접 열어 눈으로 확인한다.

## 3. 명령
| 명령 | 하는 일 |
|---|---|
| `info` | Agent 버전, 코드 위치, 찾은 툴 경로, AI CLI 설치 여부 |
| `run 파일.py [인자] [--input 파일]` | PC의 Agent 파이썬으로 실행. `cae_adapters`·`agent.dds_agent`·`dds_live`를 그대로 불러 쓸 수 있다. `DDS_TEST_OUT` 폴더에 쓴 파일을 받아 온다 |
| `tool 키 파일 [인자] [--input 모델]` | 대화와 같은 명령 키(`tool_commands.py`)로 실제 툴 실행 |
| `live 툴 동작` | Live Link 호출 하나: `status`·`launch`·`info`·`capture`·`exec`·`set 이름=값`·`view 방향`·`undo` |
| `suite [--tool 툴]` | 기본 점검 묶음(`scripts/pc_checks/`) |
| `logs [--lines N]` | Agent·launcher 로그 |
| `fetch 경로` | PC 파일 받기. 시험·작업·Agent 폴더 안만, 토큰 파일은 안 된다 |
| `deploy` | 지금 코드 배포 → 바로 업데이트 → 다시 연결될 때까지 대기. 개발용 실행(`dev_live`)은 업데이트하지 않는다 |
| `chat "요청" --agent claude` | 웹앱과 똑같이 대화. 작업 흐름 승인·권한 허용까지 자동으로 하고 진행을 출력한다. **토큰 계정의 PC**에서 돈다 |

제한 시간은 `--timeout 초`(기본 600, 최대 7200), 대상 지정은 `--user-id`다. 옵션은 명령 앞에 쓴다.

## 4. 시험할 때 지킬 것
- **작은 모델로** 한다. 사용자가 PC를 함께 쓰고 있다. 열려 있는 모델을 바꾸는 시험(`live set`·`exec`)은 시험용 파일을 연 뒤에 한다.
- NX Live Link의 첫 시험은 **저장한 모델**로 한다. 세션 안 실행기(`dds_live_nx.py`)의 Windows 타이머는 실제 NX에서 아직 검증하지 못했다.
  - 이상하면 PC의 `~/.dds-agent/live/nx/oneshot` 파일을 만들어 타이머 없이(작업마다 실행기를 다시 띄우는 방식) 확인한다.
- 긴 해석은 `--timeout`을 늘리되, 먼저 작은 케이스로 시험한다.
- 시험 스크립트는 PC에서 돈다. Windows 경로와 Python 3.12를 기준으로 쓴다.

## 5. 안전장치
- PC 주인이 직접 켜야 하고, **7일 뒤 자동으로 꺼진다**. 서버와 PC Agent 양쪽에서 확인한다.
- **관리자 계정의 PC만** 시험 대상이 되고, 호출도 관리자만 할 수 있다. 일반 사용자의 PC는 대상이 되지 않는다.
- 실행 내역은 서버 `devtest_records`(설정 화면에 최근 시험 표시)와 PC `~/.dds-agent/devtest/history.jsonl`에 남는다. 시작하면 트레이 알림이 뜬다(1시간에 한 번).
- 시험은 `~/.dds-agent/devtest/<시각>/`에서 돈다. 폴더는 최근 30개만 남긴다. 받아 갈 수 있는 파일 위치를 제한하고 토큰 파일은 내주지 않는다.
- `run`은 임의 코드를 그 PC에서 실행한다. 이미 서버 관리자는 Agent 배포로 같은 일을 할 수 있어 권한이 새로 늘지는 않지만, 안 쓸 때는 끄는 것이 좋다.
