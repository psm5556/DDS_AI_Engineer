# 사내 AI에게 전달하는 방법 (Live Link 빠르게)

이 폴더는 사내에서 DDS Conversa 코드를 고치는 AI 코딩 에이전트(Claude Code·Gemini CLI·Codex)에게 주는 개발 스킬이다.
사내 코드는 집 저장소와 많이 달라서, 코드를 그대로 붙이지 않고 **절차·약속·참고 구현**을 주고 사내 코드에 맞춰 고치게 한다.

## 1. 무엇을 가져가나
- 이 폴더(`live-link-speedup`) 전체. zip으로 묶어 사내로 옮긴다.

| 파일 | 내용 |
|---|---|
| `SKILL.md` | 공통 절차: 현황 파악 → 측정 → 빠른 툴 5개 → 왕복·캡처·대기 줄이기 → 연결 점검 → 지침 → 검증 |
| `tools/*.md` | 툴별 지금 방식·병목, API 대응표, 이름 규칙, 사내 확인 요청 (9개 툴) |
| `references/nx/` | NX 참고 구현(도우미·MCP 툴·테스트). 집에서 테스트를 통과한 코드 |
| `references/com_worker.py` | CATIA·SOLIDWORKS·LightTools용 COM 연결 유지 초안 |
| `references/runtime-agent-guide.md` | 웹앱 안 에이전트 지침에 넣을 문구 |

## 2. 어디에 두나
- 사내 저장소에서 아래 위치 중 하나에 둔다.
  - Claude Code: `<저장소>/.claude/skills/live-link-speedup/` — 대화에서 관련 요청을 하면 스스로 읽는다.
  - 그 밖의 AI: `<저장소>/docs/ai-skills/live-link-speedup/` — 첫 요청에서 이 파일을 읽으라고 지시한다.
- 웹앱이 CAD 에이전트에게 주는 `agent-context/skills/`에는 **넣지 않는다**. 그곳은 웹앱 안 에이전트용이다.

## 3. 무엇이라고 요청하나 (그대로 붙여 넣기)
- 한 번에 툴 하나씩 진행한다. 순서는 NX부터 권장한다(참고 구현이 있고 사내에서 가장 많이 쓴다).
- 아래 경로는 폴더를 둔 곳에 맞춰 바꾼다.
- **1회차 — 현황 파악과 측정만**(코드는 아직 고치지 않음)
  ```text
  docs/ai-skills/live-link-speedup/SKILL.md와 tools/nx-simcenter.md를 읽어 줘.
  우리 코드의 NX Live Link에 대해 SKILL.md 1절(현황 파악) 표를 만들고, 2절대로 단계별 걸린 시간(ms)을 재는 코드를 넣어 줘.
  기준 요청 3개를 재는 방법을 알려 주고, 고칠 계획을 3~8절 순서로 제안해 줘. 이미 되어 있는 항목은 건너뛰어.
  ```
- **2회차 — 계획대로 고치기**
  ```text
  제안한 계획대로 NX 빠른 툴(live_info·live_set_params·live_view·live_undo, capture=true)을 만들어 줘.
  참고 구현은 references/nx/에 있어. 우리 코드 구조에 맞추고, 사내에서 검증된 nx_param_update·sc_update_solve 함수를 재사용해.
  웹앱 에이전트 지침은 references/runtime-agent-guide.md대로 고치고, SKILL.md 8절대로 테스트까지 해 줘.
  ```
- **3회차 — 실제 NX에서 확인 후 정리**
  ```text
  tools/nx-simcenter.md 7절 요청을 실제 NX에서 해 본 결과야: (결과·오류·ms 붙여 넣기)
  실패한 API는 Journal 기록으로 확인해서 고치고, 확인된 곳의 TODO(사내검증)를 지워 줘. 고치기 전후 ms 표를 만들어 줘.
  ```
- 다른 툴은 `tools/nx-simcenter.md` 자리에 해당 파일을 넣어 같은 순서로 요청한다.
  - 예: `tools/abaqus.md`, `tools/catia.md`
  - COM 툴(CATIA·SOLIDWORKS·LightTools)은 공통 COM 연결(`references/com_worker.py`)을 처음 한 번 만들고 나머지가 같이 쓴다.

## 4. 결과로 받아야 할 것
- 툴마다 아래 세 가지를 받는다.
  - 고치기 전후 `ms` 표: 요청 / AI 툴 호출 수 / round_trip / 툴 안 시간
  - 남은 `TODO(사내검증)` 목록
  - 다음 병목
- 저장소 규칙(AGENTS.md)대로 테스트를 통과하고 `__version__`을 올렸는지 확인한다. 그다음 관리 > Agent 배포로 배포한다.

## 5. 주의
- AI가 툴 API 이름을 지어내지 않게 한다. 모르는 것은 툴의 기록 기능(NX Journal, Abaqus rpy, HyperMesh command.tcl, CATIA·SOLIDWORKS 매크로 기록 등)으로 확인한다. 이 방법은 SKILL.md 6절과 각 툴 파일 5절에 있다.
- 첫 시험은 작은 모델로 하고, 바뀐 값을 사람이 확인한다.
- 이 폴더의 기준은 집 저장소 커밋 bdf3181(PC Agent 0.2.5)이다.
