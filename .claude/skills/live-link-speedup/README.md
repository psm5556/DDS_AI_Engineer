# 사내 AI에게 전달하는 방법 (Live Link 빠르게)

- 이 폴더는 DDS Conversa 코드를 고치는 AI 코딩 에이전트(Claude Code·Gemini CLI·Codex)에게 주는 **개발용 스킬**이다.
- 사내 코드는 집 저장소와 많이 다르다. 그래서 코드를 그대로 붙이지 않고 **절차·약속·참고 구현**을 주어 사내 코드에 맞춰 고치게 한다.
- 사내 구성: Linux 서버 한 대가 DDS Conversa 저장소이고, 거기서 개발하고, 웹앱을 돌리고, exe로 PC Agent를 배포한다.

## 1. 무엇을 가져가나
- 이 폴더(`live-link-speedup`) 전체를 zip으로 묶어 Linux 서버로 옮긴다.

| 파일 | 내용 |
|---|---|
| `SKILL.md` | 공통 절차: 현황 파악 → 측정 → 빠른 툴 5개 → 왕복·캡처·대기 줄이기 → 연결 점검 → 지침 → 검증·배포 |
| `tools/*.md` | 툴별 지금 방식·병목, API 대응표, 이름 규칙, 사내 확인 요청 (9개 툴) |
| `references/nx/` | NX 참고 구현(도우미·MCP 툴·테스트). 집에서 테스트를 통과한 코드 |
| `references/com_worker.py` | CATIA·SOLIDWORKS·LightTools용 COM 연결 유지 초안 |
| `references/runtime-agent-guide.md` | 웹앱 안 에이전트 지침에 넣을 문구 |

## 2. 어디에 두나 (Linux 서버의 저장소, Claude Code·Gemini CLI 공용)
```
<서버 저장소>/
├─ .claude/skills/live-link-speedup/   ← 이 폴더를 그대로 둔다(실제 파일, Claude Code가 읽음)
├─ .gemini/skills/live-link-speedup    ← 위 폴더를 가리키는 심볼릭 링크(Gemini CLI가 읽음)
└─ AGENTS.md                           ← 6절 "자주 하는 작업" 표에 한 줄 추가(두 AI 공통 안내)
```
- 서버 저장소에서 한 번 실행한다.
  ```bash
  mkdir -p .gemini/skills
  ln -s ../../.claude/skills/live-link-speedup .gemini/skills/live-link-speedup
  git add .claude/skills/live-link-speedup .gemini/skills/live-link-speedup AGENTS.md
  git commit -m "Live Link 속도 개선 개발 스킬 추가"
  ```
- 파일은 한 벌만 둔다. 링크라서 스킬을 고쳐도 두 AI가 같은 내용을 본다. git은 링크를 링크로 저장한다.
- `AGENTS.md` 6절 표에 아래 줄을 추가한다.
  ```text
  | Live Link 속도 개선 | `.claude/skills/live-link-speedup/SKILL.md`부터 읽는다(개발용 스킬, Agent 배포에는 들어가지 않음) |
  ```
  - `CLAUDE.md`·`GEMINI.md`가 `AGENTS.md`를 불러오므로 두 AI 모두 이 줄을 본다.
  - Gemini CLI가 스킬을 지원하지 않는 버전이어도 이 줄로 찾아간다.
- **확인**
  - Gemini CLI: 저장소에서 `gemini`를 실행하고 `/skills list`에 `live-link-speedup`이 나오는지 본다.
    - 처음 쓸 때 스킬을 켤지 묻는다. 허용한다.
    - 링크를 만든 뒤 이미 열려 있던 세션이면 `/skills reload`를 한다.
  - Claude Code: 저장소에서 `claude`를 실행하고 "쓸 수 있는 스킬 목록 보여 줘"라고 물어 `live-link-speedup`이 나오는지 본다.
- 아래 4절 요청 문장은 SKILL.md 경로를 직접 적으므로, 스킬 자동 인식이 안 돼도 두 AI 모두 그대로 쓸 수 있다.
- **사용자 PC에는 깔리지 않는다.** Agent 배포 zip에는 `agent/`·`mcp_servers/`·`cae_adapters/`·`agent-context/`만 들어간다. 이 스킬은 개발하는 AI만 쓰면 된다.
- `agent-context/skills/`에는 **넣지 않는다.**
  - 그곳은 사용자 프로젝트 폴더로 복사되는 웹앱 AI용 스킬 자리다.
  - 그곳에 두면 웹앱 AI가 "Live Link가 느리다"는 말에 이 스킬을 읽고 설치 폴더의 코드를 고치려 할 수 있다.

## 3. 사용자 PC로 가는 것은 무엇인가
- 개발 AI가 이 스킬대로 고친 **결과물**만 Agent 배포로 사용자 PC에 간다.

| 결과물(서버 저장소) | 사용자 PC에서 쓰이는 곳 |
|---|---|
| `mcp_servers/dds_live.py`(빠른 툴) | `%LOCALAPPDATA%\DDS-Agent\versions\<버전>\mcp_servers\` |
| `agent-context/live/<툴>/`(툴 안 도우미) | `...\versions\<버전>\agent-context\live\<툴>\` |
| `agent-context/skills/live-modeling/SKILL.md`(웹앱 AI 사용법) | 프로젝트를 열 때 작업 폴더의 `.claude\skills\live-modeling\` 등으로 복사 |

- 배포 순서
  1. `agent/dds_agent/__init__.py`의 `__version__`을 올린다.
  2. 관리 > Agent 배포에서 "서버 코드로 배포"를 누른다.
- 배포한 뒤
  - 새로 exe로 설치하는 PC와 이미 설치된 PC(자동 업데이트) 모두 새 버전을 받는다.
  - 시험 PC는 트레이 아이콘 > "업데이트 확인"으로 바로 받을 수 있다.

## 4. 무엇이라고 요청하나 (Linux 서버의 개발 AI에게, 그대로 붙여 넣기)
- 한 번에 툴 하나씩 진행한다. NX부터 권장한다(참고 구현이 있고 사내에서 가장 많이 쓴다).
- **1회차 — 현황 파악과 측정만**(코드는 아직 고치지 않음)
  ```text
  .claude/skills/live-link-speedup/SKILL.md와 tools/nx-simcenter.md를 읽어 줘.
  우리 코드의 NX Live Link에 대해 SKILL.md 1절(현황 파악) 표를 만들고, 2절대로 단계별 걸린 시간(ms)을 재는 코드를 넣어 줘.
  기준 요청 3개를 재는 방법을 알려 주고, 고칠 계획을 3~8절 순서로 제안해 줘. 이미 되어 있는 항목은 건너뛰어.
  여기는 Linux 서버이고, Live Link 코드는 사용자 PC(Windows)에서 돈다는 점(0-1절)을 지켜 줘.
  ```
- **2회차 — 계획대로 고치고 배포**
  ```text
  제안한 계획대로 NX 빠른 툴(live_info·live_set_params·live_view·live_undo, capture=true)을 만들어 줘.
  참고 구현은 references/nx/에 있어. 우리 코드 구조에 맞추고, 사내에서 검증된 nx_param_update·sc_update_solve 함수를 재사용해.
  웹앱 에이전트 지침은 references/runtime-agent-guide.md대로 고치고, SKILL.md 8절대로 테스트한 뒤 __version__을 올려 줘.
  ```
  - 테스트가 통과하면 관리 > Agent 배포에서 배포한다(사람이 누른다).
- **3회차 — 시험 PC에서 확인한 결과로 정리**
  - 시험 PC에서 할 일
    1. 트레이 아이콘 > "업데이트 확인"
    2. NX를 연 상태에서 웹앱 대화창에 `tools/nx-simcenter.md` 7절 요청을 보낸다.
  - 그 결과를 개발 AI에게 붙여 넣는다.
  ```text
  tools/nx-simcenter.md 7절 요청을 시험 PC의 실제 NX에서 해 본 결과야: (결과·오류·ms 붙여 넣기)
  실패한 API는 Journal 기록으로 확인해서 고치고, 확인된 곳의 TODO(사내검증)를 지워 줘. 고치기 전후 ms 표를 만들어 줘.
  ```
- 다른 툴은 `tools/nx-simcenter.md` 자리에 해당 파일을 넣어 같은 순서로 요청한다.
  - 예: `tools/abaqus.md`, `tools/catia.md`
  - COM 툴(CATIA·SOLIDWORKS·LightTools)은 공통 COM 연결(`references/com_worker.py`)을 처음 한 번 만들고 나머지가 같이 쓴다.

## 5. 결과로 받아야 할 것
- 툴마다 아래를 받는다.
  - 고치기 전후 `ms` 표: 요청 / AI 툴 호출 수 / round_trip / 툴 안 시간
  - 남은 `TODO(사내검증)` 목록
  - 다음 병목
- 배포한 버전에 문제가 생기면 관리 > Agent 배포에서 이전 버전을 최신으로 되돌린다.

## 6. 주의
- AI가 툴 API 이름을 지어내지 않게 한다. 모르는 것은 툴의 기록 기능(NX Journal, Abaqus rpy, HyperMesh command.tcl, CATIA·SOLIDWORKS 매크로 기록 등)으로 확인한다. 방법은 SKILL.md 6절과 각 툴 파일 5절에 있다.
- 첫 시험은 작은 모델로 하고, 바뀐 값을 사람이 확인한다.
- 배포한 버전은 모든 사용자 PC로 간다. 사용자가 적은 시간에 배포한다.
- 이 폴더의 기준은 집 저장소 커밋 bdf3181(PC Agent 0.2.5)이다. Linux 개발용 보완(0-1절)은 그 뒤에 더했다.
