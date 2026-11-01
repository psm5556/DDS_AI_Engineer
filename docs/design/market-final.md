# DDS Conversa 마켓플레이스 최종 설계

작성일은 2026-10-31이다. 저장소 파일은 고치지 않았고 서버, 실제 AI CLI, 로그인은 쓰지 않았다. 함수는 이름으로만 적는다.

**표시 규칙**
- `[확인]`: 이번에 코드를 읽거나 직접 돌려서 본 것
- `[인용]`: 조사 문서에 있고 이번에 다시 보지 않은 것
- `[판단]`: 내 결정

---

## 1. 세 설계 평가

5점 만점이고 점수는 모두 내 판단이다.

| 기준 | 사용자 우선 | 관리 통제 | MVP |
|---|---|---|---|
| 요구 충족(선택·설치·쉬운 등록) | 5 | 3 | 3 |
| 회수·승인 통제 | 3 | 5 | 3 |
| 크기(작을수록 높음) | 2 | 1 | 5 |
| 기존 코드·테스트와의 호환 | 4 | 4 | 5 |
| 근거(실측·코드 확인) | 3 | 4 | 5 |

**가져온 것**
- **MVP**: 뼈대 전체, 시제품, "빈 마켓이면 오늘과 같다"는 조건, 스킬 복사, 버전을 저장하지 않고 계산하는 방식, 첫 판에 pip를 쓰지 않는 것.
- **사용자 우선**: "같은 대화의 다음 요청부터"라는 약속, PC 맞춤 추천, 대화 등록, `내 것` 등급, 프로젝트 값이 남았는지 검사.
- **관리 통제**: 토큰 구멍을 먼저 막는 0단계, 턴 시작 관문, "항상 허용" 제거, 의심 항목을 확인해야 승인되는 절차.

**고치거나 버린 것**

| 원래 안 | 문제 | 최종 |
|---|---|---|
| MVP: `_source_stamp() + market.stamp()` | 설치 한 번에 프로젝트마다 스킬 33개, 파일 468개를 다시 복사한다. `sync_project_context`는 지문이 다르면 전부 복사한다 `[확인]` | 마켓 스킬은 지문과 복사를 따로 둔다 |
| MVP: MCP를 켠 사람에게만 입구 제공 | 첫 MCP 설치는 "새 대화부터"가 된다. AI 제안과 대화 등록도 입구가 없으면 불가능하다 | 서버 스위치로 입구를 켜고, 켜면 모두에게 제공한다 |
| 사용자 우선·관리 통제: `market_skill`로 본문 전달 | 도구 하나가 더 필요하고, 결정 B와 비슷해 보인다 | 프로젝트로 복사하고 경로를 알려 준다 |
| `settings_message`에 마켓 값 추가 | `test_agent_settings`가 메시지 전체를 같음 비교한다 `[확인]` | 별도 메시지 `market.sync`를 쓴다 |
| 관리 통제: `rev`와 `epoch` 표 | 표 하나와 복원 예외 처리가 더 든다 | `rev`는 그 사용자의 적용 목록 해시로 계산한다 |
| 관리 통제: 권한 선언, 해시 사슬, 감사 훅, 24시간 유예, 회수 3단계 | 등록자 부담과 코드가 늘고, 사내 위협에 비해 과하다 | 자동 탐지만 쓰고 회수는 2단계로 한다. 나머지는 수요가 생기면 검토한다 |

---

## 2. 개념 (열 줄)

1. 사내 서버에 "마켓" 화면이 하나 생긴다. 부서가 만든 스킬과 도구가 카드로 보인다.
2. 스킬은 AI에게 주는 작업 방법이고, 도구(MCP)는 AI가 실행하는 기능이다.
3. 카드에서 설치를 누르면 PC Agent가 내 PC에 받아 둔다. 터미널 명령은 없다.
4. 재시작 없이 지금 대화의 다음 요청부터 AI가 그것을 쓴다.
5. PC가 꺼져 있어도 누를 수 있고, 켜지면 설치된다.
6. 내가 만든 것은 폴더를 끌어다 놓거나 대화에서 "이거 마켓에 올려 줘"라고 하면 올라간다.
7. 서버가 내용을 읽어 양식을 채우고 자동으로 검사한다. 나만 쓸 때는 바로 쓸 수 있다.
8. 다른 사람에게 공개하려면 관리부서가 내용을 읽고 승인해야 한다.
9. 문제가 생기면 관리부서가 회수한다. 모든 PC가 다음 요청 전에 그것을 쓰지 못하게 된다.
10. 내 PC에서 도는 항목은 내 권한을 그대로 가지므로, 화면에 그 사실을 숨기지 않고 알린다.

---

## 3. 항목 종류와 매니페스트

### 종류

| 내부 이름 | 화면 표시 | 도는 곳 | PC로 가는 것 | 단계 |
|---|---|---|---|---|
| `skill_text` | 스킬 | AI가 읽음 | 문서 | 1 |
| `skill_code` | 스킬 · 코드 있음 | 내 PC, 내 권한 | 문서, 스크립트 | 1 |
| `pc_tool` | 도구 · 내 PC에서 실행 | 내 PC, 입구의 자식 프로세스 | 코드 | 2 |
| `server_tool` | 도구 · 서버에서 실행 | 사내 서버 | 없음 | 3 |

- 한 항목에 스킬과 도구를 함께 담을 수 있다.
- **받지 않는 것**: exe, npm, sse, 사내 외부 URL, hooks, sampling·resources·prompts를 쓰는 MCP.
- **올린 코드는 서버에서 실행하지 않는다.** `server_tool`은 저장소에 넣어 배포한 코드만 쓴다. 결정 C(비파라미터 형상부터)를 담는 자리다.

### 폴더

```text
<name>/
  plugin.json             # 없어도 된다. 서버가 만든다
  skills/<skill>/SKILL.md, references/, scripts/
  server/server.py        # pc_tool. @mcp.tool()로 판별
  server/vendor/          # 순수 파이썬만
```

폴더 최상위에 `SKILL.md`만 있어도 받는다.

### 매니페스트 (서버가 `MarketVersion.manifest`에 저장)

```json
{
  "name": "weld-fatigue",
  "version": "1.2.0",
  "displayName": "용접부 피로 평가",
  "description": "해석 결과에서 용접부 피로 수명을 계산합니다",
  "author": "관리부서",
  "category": "후처리",
  "extensions": { "dds.conversa": {
    "kinds": ["skill_code", "pc_tool"],
    "runsOn": "pc",
    "entry": "server/server.py",
    "skills": ["weld-fatigue"],
    "requiresTools": ["abaqus"],
    "needsTool": false,
    "examples": ["이 odb로 용접부 피로 수명을 계산해 줘"],
    "toolList": [{"name": "calc_life", "label": "피로 수명 계산", "description": "...", "inputNames": ["odb"]}],
    "capabilities": ["runs_code", "starts_programs"],
    "minAgentVersion": "0.3.0",
    "related": ["abaqus-automation"],
    "overrides": null,
    "provenance": {"source": "web|cli|chat|capture", "projectId": null, "captureId": null}
  }}
}
```

| 값 | 채우는 방법 | 규칙 |
|---|---|---|
| `name` | 폴더 이름이나 frontmatter. 한글뿐이면 `item-<번호>` | 소문자·숫자·하이픈, 40자 이내. `dds-*`, `claude-`, `anthropic-`, 기본 스킬 33개는 예약어 |
| `version` | 자동으로 올린다. 같은 내용이면 "바뀐 내용이 없습니다" | 정확한 x.y.z. 게시 후 변경 불가 |
| `description` | `SKILL.md` 설명의 첫 문장을 100자로 자른 초안 | 33개 설명이 모두 100자를 넘어 자르는 과정이 필요하다 `[인용]` |
| `SKILL.md` 설명 | 등록자가 쓴 것 | 한 줄, 1024자 이내. `_skill_index`의 정규식이 한 줄만 읽는다 `[확인]` |
| `capabilities` | AST와 본문 검사 | 등록자는 쓰지 않는다 |
| `toolList` | AST 초안, 시작 확인으로 덮어쓴다 | 지문은 이름, 설명, 입력 값 이름 목록으로 만든다 |
| `needsTool` | 초안을 등록자가 고친다 | `RESULT_KINDS`처럼 툴 없이 파이썬으로 도는 경우 때문에 둔다 |

**사람이 적는 것은 폴더와 한 줄 설명뿐이다.** 공통 필드 이름은 공개 형식에 맞춰 뒀으므로 CLI 형식 내보내기는 나중에 붙일 수 있다. 내보내기가 실제로 되는지는 시험 전이다.

---

## 4. 설치와 활성화

### 핵심 결정 `[판단]`
- 세션의 MCP 목록은 바꾸지 않는다. 입구 `dds-market` 뒤의 목록만 바꾼다.
- 스킬은 프로젝트 폴더로 복사하고, 다음 프롬프트에 경로를 알려 준다.
- 사용자 전역 설정(`~/.claude`, `~/.codex`, `~/.gemini`)은 고치지 않는다.

### 근거

| 사실 | 표시 |
|---|---|
| 실행 중인 세션에 MCP를 더하는 ACP 공통 방법이 없다. 목록을 바꾸면 Claude는 다시 띄우고, Gemini는 세션을 버린 뒤 다시 만들고, Codex는 무시한다 | `[인용]` |
| 시제품을 다시 돌려 봤다. 입구 시작 1.01초. 세션을 유지한 채 목록 파일을 바꾸면 다음 호출에서 항목을 찾았다(첫 호출 0.96초, 둘째 0.003초). 항목의 환경에 토큰이 없었다. 지문이 다르면 거부했고, 3초 걸리는 도구는 작업 번호를 먼저 돌려준 뒤 이어서 결과를 받았으며, 회수하면 같은 세션의 다음 호출이 막혔다 | `[확인]` |
| 항목이 일반 예외를 던지면 문구가 사라지고 "Error executing tool boom"만 남는다 | `[확인]` |
| Gemini의 `startConfiguredMcpServers`가 `Promise.all`로 MCP를 함께 띄운다. 입구 하나가 시작 시간을 직렬로 늘리지는 않을 것으로 본다 | `[확인]` 이 PC 판의 소스, 실측은 아님 |
| 입구가 `_common`을 불러오면 시작은 약 1.4초다(`_common` 3회 측정 1.35~1.55초). 사내 보안 검사가 있으면 더 길다 | `[인용]` |

시제품은 내가 만든 stdio 클라이언트로 돌렸다. 실제 CLI에서는 아직 시험하지 않았다.

### 순서

1. 사용자가 설치를 누른다. `PUT /api/market/installs/{name}`은 JWT 전용이므로 AI는 호출할 수 없다.
2. 서버가 `market.sync`를 보낸다. 꺼진 PC에는 연결될 때 `record_instance`가 보낸다.
3. Agent는 `updater.prepare_update`와 같은 흐름으로 처리한다.
   - 받기 → sha256 비교 → 임시 폴더에 풀며 경로 재검사 → 이름 바꾸기 → `.installed` 기록 → `lock.json`을 `os.replace`로 교체
   - 위치는 `~/.dds-agent/market/items/<이름>/<버전>/`이다. `DDS_AGENT_CONFIG` 폴더를 따르므로 `versions/` 밖이고, Agent 업데이트 뒤에도 남는다.
   - `self.installing`을 올려 받는 동안 Agent 업데이트를 미룬다. `market.`은 `WORK_MSGS`에 넣지 않아 동기화가 유휴 판단을 깨지 않는다.
4. `market.state`로 결과를 알린다. 화면 버튼은 이때 비로소 "설치됨"이 된다.
5. 사용자가 다음 요청을 보내면 `hub._run_turn`의 관문이 돈다.
   - `rev`(그 사용자의 적용 목록 해시)가 이 세션이 알던 값과 같으면 그냥 지나간다.
   - 다르면 `conn.request("market.apply", {rev, session_id}, timeout=15)`를 보낸다.
   - Agent는 `session_project[sid]`로 프로젝트 폴더를 찾아 마켓 스킬만 맞추고, 실제로 켜진 목록을 답한다.
   - `_ensure_session`은 살아 있는 세션을 Agent에게 묻지 않고 재사용하므로 `[확인]` 이 관문이 꼭 필요하다.
6. 프롬프트 앞에 안내를 붙인다. `_run_turn`의 `text`는 CLI로만 가고 대화 기록에는 남지 않는다 `[확인]`.

```text
[새로 설치됨] 용접부 피로 평가: 해석 결과에서 용접부 피로 수명을 계산합니다
스킬: <프로젝트>/.claude/skills/weld-fatigue/SKILL.md
도구: dds-market.market_describe("weld-fatigue")로 확인
```

세션의 첫 턴에는 켜진 항목 전체를 한 줄씩 붙인다. 서버가 재시작해 기억을 잃어도 다시 붙이면 되므로 무해하다.

### 스킬 연결 (`context.py`)

- `sync_project_context(project_dir)`의 서명은 그대로 둔다. `test_session_keep`이 `lambda d: False`로 바꿔 쓰기 때문이다 `[확인]`.
- 기존 복사가 끝난 뒤 새 함수 `_sync_market(project_dir)`를 부른다. 지문과 목록은 `.dds-market-stamp`, `.dds-market.json`으로 따로 둔다.
- 마켓 스킬은 지우고 다시 복사한다. 버전을 바꿀 때 옛 파일이 남지 않게 하려는 것이다.
- 규칙 파일은 지금과 같은 식(`INSTRUCTIONS.md` + `_skill_index`)으로 다시 쓴다. 마켓이 꺼져 있고 비어 있으면 결과가 같다.
- `.dds-skills.json`에도 `.dds-market.json`에도 없는 같은 이름의 스킬은 사용자가 만든 것이다. 덮어쓰지 않고 `conflict`로 알린다.
- 회수 때는 `index.json`의 프로젝트 전체에서 그 스킬 복사본을 지운다.

### MCP 연결

```python
names = MCP_SERVERS + (("dds_market",) if market.gateway_on() else ())
```

- `gateway_on()`은 모듈 함수이고 `lock.json`을 읽는다. 파일이 없으면 False다.
- `test_hidden_windows`의 가짜 객체(`s`만 있음)로 불러도 깨지지 않는다 `[확인]`.
- 켜고 끄는 스위치는 서버의 마켓 설정에 둔다. 현장 관문을 통과하기 전이나 문제가 있을 때 끌 수 있다.
- 꺼져 있으면 오늘과 같은 4개다.
- 입구를 켠 뒤 처음 여는 세션부터 5개가 된다. 이미 떠 있는 세션은 다음에 세션을 새로 열거나 불러올 때 입구를 받는다. 그 전에는 "새 대화에서 쓸 수 있습니다"라고 안내한다.
- **CLI 규칙** `[인용]`: stdio 정의에 `type`을 넣지 않고, `env`는 항상 배열로 넣고, 이름은 `dds-` 접두사를 쓴다.

### 사건별 동작

| 사건 | 진행 중인 호출 | 같은 대화의 다음 요청 | CLI 재시작 |
|---|---|---|---|
| 스킬 또는 도구 설치 | 영향 없음 | 쓸 수 있음. 안내가 붙음 | 없음 |
| 끄기, 제거 | 끝까지 진행 | 호출 거부, 스킬 삭제, 안내 | 없음 |
| 새 버전(권한과 도구가 같음) | 옛 버전으로 끝남 | 자식이 쉬면 새 버전 | 없음 |
| 새 버전(권한이나 도구가 늘어남) | 옛 버전으로 끝남 | 동의 전까지 옛 버전. 카드에 "다시 확인 필요" | 없음 |
| 내림 | 영향 없음 | 그대로. 새 설치만 막음 | 없음 |
| 회수 | 입구가 `lock.json` 수정 시각을 5초마다 보고 자식을 끝낸다 | 호출 거부, 스킬 삭제, `[회수됨: 이름, 쓰지 마세요]` | 없음 |
| Agent 업데이트 | 기존 규칙(작업 없고 5분 조용할 때) | `_load_session`으로 이어짐. `lock.json`이 남아 있어 항목 유지 | 있음(기존과 같음) |
| 입구를 처음 받는 Agent 업데이트 | 위와 같음 | 다음 세션부터 입구가 들어감 | 업데이트 때 한 번 |

회수 안내에는 등록자가 쓴 설명을 넣지 않는다. 작성자의 글이 지시문으로 들어가는 것을 막기 위해서다.

### 관문 실패 처리

| 상황 | 처리 |
|---|---|
| `hello`에 `market_api`가 없는 옛 Agent | 통과. 마켓 코드가 없다. 화면에는 "PC Agent를 업데이트하면 설치됩니다"를 보여 준다 |
| 시간 초과 + 아직 적용하지 않은 회수가 있음 | 턴을 시작하지 않는다. "PC Agent가 관리부서의 회수 조치를 받지 못했습니다 → 트레이에서 Agent를 다시 시작하세요" |
| 그 밖의 시간 초과 | 턴을 진행하고 다음 턴에 다시 시도한다 |
| 받는 중 | 이미 설치된 것만 답한다. 버튼이 아직 "설치 중"이라 약속을 어기지 않는다 |

### 입구 규칙 (`mcp_servers/dds_market.py`)

- 호출마다 `lock.json`을 읽는다.
- 자식은 처음 부를 때 띄우고 10분 쓰지 않으면 끝낸다. 망가진 항목이 세션 시작을 막지 않는다.
- 자식 환경에는 안전 목록, `PYTHONPATH=<항목>/server;<항목>/server/vendor;market_sdk`, `PYTHONIOENCODING`만 준다.
- 자식 작업 폴더는 프로젝트 폴더다.
- `DDS_MCP_WAIT_LIMIT_SEC`(Codex 50초, Gemini 540초 `[인용]`)를 넘기면 작업 번호를 돌려준다.
- 첫 판의 항목은 서버 API를 직접 부르지 않는다. 파일이나 값을 돌려주면 AI가 `dds-flow`로 게시한다.
- `_common`을 쓰지 않는 시작 틀 `market_sdk`를 준다. 오류 문구를 살리기 위해 `ToolError`를 쓰도록 만든다.
- async 도구는 `DdsMCP`가 감싸지 않으므로 `[확인]` 입구에서 직접 `ToolError`로 바꾼다.

### 실행 카드와 권한

- `hub._on_permission`과 `_on_event`에서 `market_call`을 항목 이름으로 풀어 보여 준다. 예: "용접부 피로 평가 · 피로 수명 계산".
- `tool_call_update`는 `rawInput`을 합치지 않는다고 조사됐다 `[인용]`. 합치는 코드를 더한다.
- `labels.ts`의 `canonical`에 `dds-market`을 더한다.
- `market_call`에서는 CLI의 "항상 허용" 선택지를 **뺀다**. 남기면 설치한 항목 전체가 함께 허용될 수 있다.
- 대신 "이 항목은 항상 허용"을 `MarketInstall.always_allow`에 저장하고 hub가 대신 답한다. 권한이 늘어난 새 버전에서는 초기화한다.
- `rawInput`을 알 수 없으면 "설치한 항목 실행"으로 표시하고 이번만 허용한다.

---

## 5. 등록

입구는 `/market`의 **등록** 버튼 하나와 대화 한 문장이다. 모든 경로가 `POST /api/market/submissions`로 들어간다.

| 경로 | 방식 | 단계 |
|---|---|---|
| 웹 업로드 | zip 끌어다 놓기 | 1 |
| 폴더 | 웹에서 폴더 끌어다 놓기(`webkitdirectory`, Agent 불필요) | 1 |
| 폴더(관리부서용) | `scripts/market_publish.py <폴더> [--official]`. `build_agent_bundle.py --publish`처럼 PIN 로그인 | 1 |
| 대화 | "방금 만든 것 올려 줘" → 공통 스킬 `market-publish` → `market_submit` | 2 |
| 자산화 설계 | 같은 API + `provenance.source=capture` + `evidence` | 3 |

**대화 등록 흐름**
1. AI가 대화와 `scripts/`, 최근 `runs/`를 보고 `<프로젝트>/market-drafts/<이름>/`에 초안을 만든다.
2. 입구가 MCP가 있으면 이 PC에서 `tools/list`를 시험한다.
3. 입구가 묶어서 올린다. 결과는 언제나 `draft`다.
4. 대화에 초안 카드가 뜬다. `[나만 쓰기] [부서에 검토 요청] [고치기]`
5. 카드를 누르는 것은 사람이다. 이 API는 JWT 전용이다.

숨김 폴더는 고르기 어렵다(결정 A). 그래서 대화 경로가 그 경우를 맡는다. 3단계에서 "이 PC에서 찾은 내 스킬" 목록을 더한다.

**서버 처리**: 코드를 실행하지 않고 메모리에서 처리한다.
1. `__pycache__`, `.git`, `.venv`를 걸러내고 최상위 폴더 하나를 벗긴다.
2. `check_bundle`을 그대로 쓴다(`recipe.py`, 20 MB·파일당 5 MB·200개). 스킬 33개 모두 통과했다 `[인용]`.
3. `_recipe_member`는 `dds_study.py`에 있어 건드리지 않는다 `[확인]`. 레시피의 지침 파일 차단은 넓어지지 않는다.
4. 형식을 판별하고 마켓 규칙을 적용한다.
5. 초안과 검사 결과를 몇 초 안에 돌려준다(시간은 목표).

| 처리 | 내용 |
|---|---|
| **거절** | 경로 이탈, 링크, 암호 zip, 압축 폭탄, 비밀값, 실행·컴파일 파일(`check_bundle`) |
| | 이름 규칙, 예약어, 여러 줄 설명, 구문 오류인 `.py`, hooks, `bin/`, `npx`, `uvx`, `latest`, sse |
| | `_common`이나 `agent.dds_agent` import, 허용 목록 밖 import, `@mcp.tool()` 없는 `pc_tool`, 같은 버전 재등록 |
| **표시(검토자가 항목마다 확인해야 승인 가능)** | `subprocess`, 소켓, `ctypes`, `winreg`, 사내가 아닌 URL, 원격 스크립트 받기 문구, 보이지 않는 유니코드, 큰 base64 |
| | `.dds-agent`, `~/.claude`, `agent.toml`, `DDS_TOKEN` 언급, "이전 지시 무시" 같은 지시 문구, 비슷한 이름 |
| | `_detached_popens` 규칙, 없는 `dds-*.도구` 언급, Live Link 금지 문구 |
| | 절대 경로, `P0012_`, 프로젝트 이름, 케이스 번호("이 프로젝트 값이 남아 있습니다") |

- 테스트에만 있던 규칙은 서버 검사기로 옮긴다.
- 화면에는 "자동 검사 통과"까지만 쓴다. "안전함"은 쓰지 않는다.

**시작 확인(`pc_tool`만)**
- 서버는 코드를 실행할 수 없다. 그래서 PC Agent가 `market.try`로 받아 `tools/list`를 돌리고 지문을 돌려준다.
- 승인 값으로 쓰는 지문은 **승인자 쪽 PC에서 돌린 것**만 인정한다. 등록자가 보낸 값은 참고로만 쓴다.
- 이 시험도 코드 실행이다. 화면에 "코드를 먼저 읽고 시험하세요"를 적는다.

---

## 6. 등급과 승인

등급은 패키지가 아니라 **버전**에 붙는다. 새 버전은 다시 검토한다.

| 등급 | 카드 표시 | 보는 사람 | 조건 | 승인 |
|---|---|---|---|---|
| 기본 포함 | 기본 포함 | 전체 | 기존 스킬 33개, MCP 4개 | Agent 배포 절차. 설치 버튼 없음 |
| 내 것 | 내 것 · 나만 | 본인, 관리자 | 거절 검사만 통과 | 없음 |
| 검토 완료 | 검토 완료 | 전체 | 사람이 본문과 코드를 읽음 | 관리자 + PIN |
| 관리부서 제공 | 관리부서 제공 | 전체 | 위 조건 + 유지 책임 | 관리자 + PIN |

- **실제 툴 확인**은 등급과 따로 표시한다. 예: `Abaqus 2024에서 확인 · 2026-10`. `pc_verify` 결과나 자산화 검증 결과를 `evidence`로 받는다.
- 기록이 없으면 "실제 툴 시험 기록 없음"이라고 적는다. 스킬 33개 중 31개에 `TODO(사내검증)`가 있으므로 `[인용]` "검토 완료"가 "돌려 봤다"로 읽히면 안 된다.
- 승인은 JWT + PIN이다. 반려는 사유만 받는다.
- `is_admin`을 관리부서로 본다. 부서 모델이 없고 `is_admin` 하나뿐이다 `[확인]`.
- 관리자는 등록 화면에서 바로 승인으로 넘어갈 수 있다. 이것이 관리부서의 "쉬운 등록"이다.
- 자기 코드 항목의 자기 승인은 12절 결정 사항이다.

---

## 7. 보안: 막는 것과 못 막는 것

### 먼저 막을 구멍 (0단계)

**문제 `[확인]`**
- `dds_` 토큰도 `user_from_token`으로 사용자를 그대로 돌려준다.
- `require_admin`은 `is_admin`만 본다. 관리자 PC의 토큰으로 관리자 API를 쓸 수 있다.
- `require_member`도 `is_admin`이면 통과한다.
- `create_token`은 `get_current_user`만 요구한다. 토큰으로 새 토큰을 만들 수 있다.
- MCP 환경에 `DDS_TOKEN`이 있고 `agent.toml`은 평문이다.

마켓 항목이 관리자 PC에서 돌면 승인 절차와 Agent 배포까지 우회될 수 있다.

**처리**
- `ApiToken.scope`를 더한다(NULL과 `agent`는 관리자 권한 없음, `devtest`는 `/api/devtest/*`만).
- 관리자 쓰기 API와 `create_token`은 JWT 전용으로 바꾼다.

**영향 `[확인]`**
- 설치 프로그램은 JWT로 `/api/tokens`를 부른다.
- `SettingsPage`는 웹 로그인으로 부른다.
- `pc_verify.py`는 DB에 직접 넣으므로 `devtest` 범위로 고친다.
- `mcp_servers`와 `agent/dds_agent`에서 `require_admin`이나 `/api/users`, `/api/agents` 호출은 문자열 검색 기준 없다. 경로를 조합해 부르는 경우가 있을 수 있어 테스트로 확정한다.
- 변화: 관리자의 AI가 자기가 멤버가 아닌 프로젝트에 접근하는 일이 막힌다.

### 막는 것

| 상황 | 수단 |
|---|---|
| 검토 없는 코드가 다른 PC로 가는 것 | 승인본만 받을 수 있음(다운로드 권한 검사) |
| 승인 뒤 몰래 바뀌는 것 | 불변 버전, sha256, 도구 지문 비교 |
| 좋던 패키지가 뒤 버전에서 나빠지는 것 | 새 버전 재검토, 차이 표시, 권한 증가 시 재동의 |
| AI가 스스로 설치·승인·공개하는 것 | JWT 전용 API. 입구에 설치 도구가 없음 |
| 망가진 항목이 대화를 막는 것 | 입구 뒤 자식 프로세스 |
| 회수가 안 닿는 것 | 연결 시 + 변경 시 + 턴 시작 관문 |
| 도구 설명 오염이 시작 프롬프트에 닿는 것 | 입구 도구 6개 고정. 항목 설명은 검토본만, 부를 때만 |

### 못 막는 것

- **PC에서 도는 항목은 내 권한을 그대로 갖는다.** 환경변수에서 토큰을 빼도 `agent.toml`과 CLI 인증 파일을 읽을 수 있다. 격리가 아니고 실수 방지다. 실제 장치는 사람의 검토, 불변 버전, 회수, 설치 기록이다. 그래서 설치 창에 "내 PC에서 내 권한으로 실행됩니다"를 적는다.
- **스킬은 코드 없이도 AI를 움직인다.** 자동 검사로는 못 잡는다. 사람이 읽는 단계는 뺄 수 없다.
- **자동 검사는 탐지일 뿐이다.** 특정 조건에서만 나쁘게 동작하는 코드는 지나간다.
- **회수는 복사본에 닿지 않는다.** AI가 스크립트를 `scripts/`나 레시피로 복사한 것은 남는다. 이미 읽은 스킬 내용도 CLI 기억에 남을 수 있다.
- **이미 새 나간 토큰.** 회수해도 되돌려지지 않는다. 토큰 재발급은 사람이 한다(`create_token`, 폐기 화면).
- **사용자가 CLI에 직접 MCP를 넣는 것.** 마켓은 배포 창구이고 강제 수단이 아니다.
- **서버 탈취.** 서버는 이미 Agent 업데이트로 모든 PC에 코드를 보낼 수 있다. 서명을 넣는다면 Agent 업데이트와 함께 넣어야 효과가 있다.
- **평문 구간 변조.** `deploy/linux`에 TLS 설정이 없다고 조사됐다 `[인용]`. HTTP면 토큰과 sha256이 평문으로 오간다. `pc_tool`을 전사에 공개하기 전에 사내 HTTPS를 권한다.

---

## 8. 인터넷 없는 의존성

| 단계 | 방식 | 이유 |
|---|---|---|
| 1~3단계 | 표준 모듈 + `requirements-agent.txt`의 직접 의존성 + `server/vendor/`의 순수 파이썬 | 공용 site-packages를 건드리지 않는다. 항목의 버전 상승이 Agent를 깨는 일이 없다 |
| 4단계 | 서버가 사내 미러에서 wheel을 받아 해시와 함께 보관하고, PC는 항목 폴더에 설치 | 컴파일된 패키지가 필요할 때 |

**1단계 허용 목록**
- 표준 모듈은 `sys.stdlib_module_names`로 판단한다.
- 직접 의존성은 `httpx`, `websockets`, `mcp`, `numpy`, `scipy`, `pyNastran`, `lasso-python`, `h5py`, `matplotlib`, `pywin32`, `pystray`, `pillow`다 `[확인]`.
- 이름이 다른 것(pillow → PIL, pywin32 → win32com)은 작은 표로 처리한다.
- `pandas`는 간접 의존성이라 PC마다 다를 수 있어 빼다 `[인용]`.
- 벗어나면 패키지 이름과 함께 거절한다: "pandas는 일부 PC에 없습니다 → 빼거나 관리부서에 추가를 요청하세요".

**4단계 명령**
- 서버: `pip download --platform win_amd64 --python-version 3.12 --only-binary=:all:`
- PC: `pip install --no-index --require-hashes --no-deps --target <항목>/_deps`
- wheel만 받으므로 설치 때 코드가 실행되지 않는다.
- 컴파일 패키지는 등록 때 Agent 고정 목록(`numpy==1.26.4` 등)과 대조한다.
- 사내 Linux 서버에서 이 명령이 되는지는 **미확인**이다.

---

## 9. 기존 번들을 핵심 + 패키지로 옮기기

**결론: 파일을 번들에서 빼지 않는다.** 번들은 689 KB로 이미 작고 `[인용]`, 빼면 `test_tool_catalog_consistent` 등 다수 테스트가 깨지며, 첫 실행에 서버 전달이 필요해진다. 나누는 것은 번들이 아니라 **복사 범위**다.

| 구분 | 항목 | 처리 |
|---|---|---|
| 핵심 | MCP 4개 + 입구, 공통 스킬 4개, `INSTRUCTIONS.md` | 항상. 번들 |
| 기본 포함 | 툴 스킬 29개 | 번들. 마켓 화면에 표시만 |
| 등록 항목 | 새로 올라오는 것 | `~/.dds-agent/market` |

| 시점 | 할 일 | 깨지는 것 |
|---|---|---|
| 1~2단계 | 아무것도 옮기지 않는다. 화면에 "기본 스킬 33종은 이미 들어 있습니다"만 둔다 | 없음 |
| 3단계 | **덮어쓰기**: 관리부서가 `overrides: abaqus-automation`인 항목을 게시하면 기본 스킬 대신 쓴다. 제거하면 번들본으로 돌아간다 | 없음(테스트는 번들을 본다) |
| 4단계 | `skills_scope: all(기본) | active`를 둔다. `active`는 공통 + 이 PC의 툴 + `scan_results`가 켠 것만 복사한다. CLI 하나에만 복사하면 468개 → 약 19개(추정 `[인용]`) | `all`이면 없음 |
| 4단계 | 현장 복사 시간을 재고, 이득이 크면 `active`를 기본으로 한다 | 측정 후 판단 |

**덮어쓰기를 3단계 첫 항목으로 두는 이유 `[판단]`**
- 지금은 스킬 하나를 고쳐도 Agent 전체를 배포하고 재시작해야 한다 `[인용]`.
- 현장에서 `TODO(사내검증)` 635곳을 고쳐 나갈 때 이 비용이 가장 크다 `[인용]`.

**`scan_results`가 스킬을 켜는 것**(4단계 `active`)은 번들 안에서 켜는 것이다. 서버 전달이 없고 보호 목적도 아니므로 결정 B와 다르다.

---

## 10. 화면

**깊이는 페이지 1 + 오른쪽 서랍 1이다.** 마법사 화면은 만들지 않는다. 작업대 규칙과 토큰을 따르고 스타일은 `modern.css` 끝에 더한다. 빈 화면만 무대 규칙을 쓴다.

### `/market` (사이드바 메뉴와 접힌 레일)

```text
마켓                                           [등록]
[검색: 하고 싶은 일이나 툴 이름 (예: 피로, Abaqus 보고서)]
[전체] [스킬] [도구] [설치됨] [내가 등록]

이 PC의 Abaqus·NX에 맞는 추천      ← 툴을 못 찾거나 Agent가 꺼져 있으면 줄 자체를 숨김
[카드][카드][카드]

후처리 · 모델링 · 해석 · 최적화 · 보고서 · 데이터   ← 분류별 카드
기본 스킬 33종은 이미 들어 있습니다 → CAD·CAE 툴
```

- **카드**: 이름, 한 줄 설명, 등급, `Abaqus 있음` 또는 `이 PC에 Abaqus 없음`, 버튼 하나.
- **버튼**: 설치 / 설치 중 / 설치됨 / 다시 확인 필요 / 쓸 수 없음(이유).
- **검색**: 받아 둔 목록을 브라우저에서 거른다(`ToolsPage`와 같은 방식). 서버에 LLM이 없으므로 의미 검색은 하지 않는다.
- **추천**: `requiresTools ⊂ tools_ready`이면 위로 올린다. 관리부서 제공 → 검토 완료 → 설치 수 순서다. 설치한 것은 뺀다.
- `hello.tools`는 툴 감지 결과가 아니다 `[인용]`. `hello`에 `tools_ready`를 새로 넣는다.
- **첫 표기**: "도구(MCP, 에이전트가 쓰는 기능)", "스킬(에이전트의 작업 방법)".

### 서랍 (`ToolsPage`의 `.drawer` 재사용)

항목 상세와 등록 초안을 같은 서랍에서 처리한다.
- **상세**: 할 수 있는 일 / 이렇게 말해 보세요(누르면 입력창 채움) / 필요한 툴과 이 PC 상태 / 내 PC에서 하는 일 / 시험 기록 / 변경 내역 / 만든 사람 / 끄기·제거.
- **등록**: 폴더를 놓으면 왼쪽에 자동 채운 값, 오른쪽에 검사 결과. 버튼은 `나만 쓰기`, `부서에 검토 요청`. 관리자에게는 `바로 공개`.

### 문구

| 상태 | 문구 |
|---|---|
| 지침뿐인 항목 | 확인 창 없이 설치 |
| 코드 있는 항목 | "내 PC에서 내 권한으로 실행됩니다. 자동 검사로 찾은 일: 파일 읽기·쓰기, 다른 프로그램 실행" |
| 설치 완료 | "설치했습니다. 다음 요청부터 바로 씁니다. 대화는 그대로입니다." |
| Agent 꺼짐 | "설치 예약됨 → PC Agent를 켜면 자동으로 설치됩니다" |
| 옛 Agent | "PC Agent를 업데이트하면 설치됩니다 → 트레이에서 다시 시작하세요" |
| sha256 불일치 | "내려받은 파일이 등록본과 다릅니다 → 다시 시도하고, 반복되면 관리부서에 알려 주세요" |
| 이름 겹침 | "프로젝트에 같은 이름의 스킬이 있어 덮어쓰지 않았습니다 → 내 스킬 이름을 바꾸세요" |
| 회수 | "관리부서가 회수했습니다. 사유: … → 대체: X" |

입구가 없는 세션이나 입구 스위치가 꺼진 동안의 설치 완료 문구는 "새 대화에서 바로 쓸 수 있습니다"로 바꾼다. 화면에는 언제나 맞는 약속만 적는다.

### 대화 카드 (2단계 초안, 3단계 제안)

- **등록 초안**: 이름, 한 줄 설명, 파일 목록, 검사 결과. 5절의 버튼.
- **설치 제안**: 이름, AI가 적은 이유, 등급, 권한 요약. `[설치하고 이어서] [지금은 안 함]`. 누른 뒤 `AGENT_SEND`로 이어 간다. 거절한 항목은 그 대화에서 다시 제안하지 못한다. 전체 공개 항목만 제안할 수 있다.

### 관리 > `마켓` 탭 (`AdminPage` 탭 하나)

- **검토 대기**: 코드 많은 것부터, 오래된 것부터. 파일 전체, 직전 버전과의 차이, 도구 설명 전문, 표시 항목별 확인 체크, 시작 확인 결과.
- **공개 항목**: 등급, 설치 수, 호출 수, 내림·회수.
- **회수 현황**: PC별 적용 상태(`적용 34 / 꺼져 있는 PC 6`), 영향 보기, 입구 스위치.

---

## 11. 데이터, API, 메시지, MCP 도구

### 표 (`create_all`과 `ensure_columns`로 충분 `[인용]`)

| 표 | 핵심 필드 | 단계 |
|---|---|---|
| `ApiToken` + | `scope` | 0 |
| `MarketPackage` | `name`(유일), `display_name`, `description`, `owner_id`, `category`, `official` | 1 |
| `MarketVersion`(불변) | `package_id`, `version`, `status`, `sha256`, `size`, `manifest`, `capabilities`, `check_report`, `acks`, `tools_fp`, `evidence`, `provenance`, `notes`, `submitted_by`, `reviewed_by`, `reviewed_at`, `review_note`, `recall_reason`, `replacement_id`. 유일 `(package_id, version)` | 1 |
| `MarketInstall` | `user_id`, `package_id`, `enabled`, `version_pin`, `consent_caps`, `always_allow`. 유일 `(user_id, package_id)` | 1 |
| `AgentInstance` + | `market`(JSON: 항목별 version, state, error) | 1 |
| `MarketEvent`(추가만) | `actor_id`, `via`, `kind`, 대상, `detail` | 1 |
| `MarketSetting` | `gateway_on`, `enabled` | 2 |
| `MarketCall` | 사용자, PC, 프로젝트, 항목, 버전, 도구, 결과, 시간. **인자 내용은 저장하지 않는다** | 2 |
| `MarketProjectItem` | `project_id`, `package_id`, `pinned_version` | 3 |

- **상태**: `draft → private | review → approved | rejected`, `approved → deprecated | withdrawn`
- **파일**: `data_dir/market/<name>/<version>.zip`
- **설치 버전은 저장하지 않고 계산한다.** 고정 버전이 있으면 그것, 없으면 동의 범위 안의 최고 승인 버전이다. 최신을 회수하면 자동으로 이전 버전으로 돌아간다.
- **`rev`** = 그 사용자 적용 목록의 해시. 카운터 표가 없어 DB를 복원해도 예외 처리가 필요 없다.

### API (`routers/market.py`, `schemas.py`, 테스트 동반)

`[J]`는 JWT 전용, `[J+P]`는 JWT + PIN이다. 표시가 없으면 agent 토큰도 된다.

| 메서드 | 경로 | 비고 | 단계 |
|---|---|---|---|
| GET | `/api/market/packages?q=&kind=&mine=` | 내 상태·추천 포함 | 1 |
| GET | `/api/market/packages/{name}` | 보이는 범위만 | 1 |
| GET | `/api/market/packages/{name}/versions/{v}/files[/{path}]` | 파일 보기 | 1 |
| GET | `.../versions/{v}/download` | 승인본 또는 본인 것. sha256 헤더 | 1 |
| POST | `/api/market/submissions` | zip 또는 폴더 파일들. 결과는 항상 `draft` | 1 |
| POST | `.../versions/{v}/private` · `/request-review` `[J]` | 나만 쓰기, 검토 요청 | 1 |
| GET | `/api/market/review` `[J]` | 관리자 | 1 |
| POST | `.../versions/{v}/approve` · `/withdraw` `[J+P]` | 관리자 | 1 |
| POST | `.../versions/{v}/reject` · `/deprecate` `[J]` | 관리자 | 1 |
| GET | `/api/market/installs/mine` | | 1 |
| PUT, DELETE | `/api/market/installs/{name}` `[J]` | AI가 설치하지 못하게 하는 지점 | 1 |
| POST | `.../versions/{v}/try` `[J]` | `market.try` 지시 | 2 |
| POST | `/api/market/calls` | 입구가 기록. 인자 없음, 실패는 무시 | 2 |
| PUT | `/api/market/settings` `[J+P]` | 입구 스위치, 마켓 전체 끄기 | 2 |
| GET | `/api/market/packages/{name}/impact` `[J]` | 설치·호출 사용자, PC, 프로젝트 | 2 |
| POST | `/api/market/proposals` | 제안 카드 | 3 |
| PUT | `/api/market/packages/{name}/default` `[J+P]` | 부서 기본 | 3 |
| PUT, DELETE | `/api/projects/{id}/market/{name}` `[J]` | master | 3 |
| GET, POST | `/api/market/hosted/{name}/tools` · `/call` | 설치 사용자 + 프로젝트 멤버 | 3 |

### Agent 메시지 (양쪽 짝, `__version__` 올림, `link.py` 설명 갱신)

| 메시지 | 방향 | 내용 |
|---|---|---|
| `hello` + | Agent→서버 | `market_api`, `market_rev`, `tools_ready` |
| `market.sync` | 서버→Agent, 응답 없음 | `{rev, gateway_on, items:[{name, version, sha256, skills, has_tool, tools_fp, display_name, description, min_agent}], recalled:[{name, reason, replacement}]}`. 연결 때와 변경 때 |
| `market.state` | Agent→서버, `_reliable` | `{rev, items:[{name, version, state, error}]}`. state: `installed`, `failed`, `conflict`, `agent_too_old` |
| `market.apply` | 서버→Agent, 응답 필요 | `{rev, session_id}` → `{rev, active:[{name, version, skill_paths}]}` |
| `market.try` | 서버→Agent, 응답 필요 | 항목을 임시 폴더에서 `tools/list`. 지문과 도구 목록 반환 |

- 서버는 `hello`의 `market_api`가 있을 때만 `market.*`를 보낸다.
- 전체 목록을 맞추는 방식이라 메시지를 잃어도 다음 연결에서 복구된다.
- `test_session_hub`, `test_agent_settings`의 가짜 Agent는 `market_api`가 없으므로 메시지가 늘지 않는다.

### 입구 MCP 도구 (6개 고정)

| 도구 | 하는 일 | 권한 카드 |
|---|---|---|
| `market_search(query, tool?)` | 설치된 것과 설치 가능한 공개 항목 찾기 | 묻지 않음을 목표로 한다. CLI별 동작 확인 필요 |
| `market_describe(name)` | 설명, 도구 목록, 입력 형식 | 위와 같음 |
| `market_call(name, tool, args)` | 실행(항목별 허용 검사, 시간 제한, 호출 기록) | 항목 이름으로 표시 |
| `market_status(job, wait_sec)` | 긴 작업 이어 보기 | 위와 같음 |
| `market_submit(folder, display_name, summary, examples, change_note)` | 등록 초안 제출 | 표시함 |
| `market_propose(name, reason)` | 설치 카드 요청(3단계. 2단계에는 "아직 지원하지 않습니다"가 아니라 등록하지 않음) | 표시함 |

`mcp = DdsMCP(`를 써야 `test_every_mcp_server_keeps_failure_text`를 통과한다 `[인용]`. `_mcp_tools`는 `dds_*.py`를 자동으로 잡는다 `[확인]`.

### 자산화 설계와의 접점 (재설계하지 않는다)

| 접점 | 내용 |
|---|---|
| 입구 | `POST /api/market/submissions` + `provenance.source=capture`, `captureId`, `evidence` |
| 승인 | 자동 공개 경로 없음. 검토 대기열 공유 |
| 측정 | `MarketCall`, `MarketEvent` 읽기 |
| 레시피 | `requires:[{name, version}]`. 복원 때 없으면 설치 카드 |

**자산화 설계 문서는 조사 폴더에서 찾지 못했다**(`자산`, `capture` 검색 결과 결정 메모뿐 `[확인]`). 위 접점은 그쪽과 대조가 필요하다.

---

## 12. 시험과 통과 기준

**전 단계 공통**: `.venv\Scripts\python -m pytest server/tests agent/tests -q`가 모두 passed. **기존 테스트를 고치지 않고 통과하는 것이 조건이다.** `recipe.py`의 `.claude/skills` 차단도 유지한다.

| 파일 | 통과 기준 |
|---|---|
| `test_token_scope.py` (0단계) | agent 토큰으로 `upload_release`, `set_latest`, `update_user`, `create_token`, 승인·회수 호출 시 전부 403. 관리자의 토큰도 같다. `devtest` 토큰은 `/api/devtest/*`만 된다. 관리자 토큰이 남의 프로젝트에서 403 |
| `test_market_empty.py` | 빈 마켓 + 스위치 끔: `mcp_servers()` 4개, 규칙 파일 바이트 동일, 지문 동일, 재복사 없음 |
| `test_market_context.py` | 설치: 3개 폴더 복사, 기존 스킬 파일 수정 시각 불변(**전체 재복사 없음**). 제거: 삭제, 내 스킬 유지. 이름 겹침: 덮지 않고 `conflict`. 버전 교체: 옛 파일 없음. 회수: `index.json`의 모든 프로젝트에서 삭제 |
| `test_market_check.py` | 스킬 33개 통과(예약어만 끔). 거절 사례 zip 각각 거절. 표시 사례 각각 표시. **서버가 올린 코드를 실행하거나 디스크에 풀지 않음** |
| `test_market_api.py` | 등급별 가시성과 다운로드 차단, 소유자만 새 버전, 버전 불변, PIN 오류 시 승인·회수 실패, 토큰으로 설치·승인 403, 표시 항목 미확인 시 승인 불가, 회수 시 sync, 이전 버전으로 복귀 |
| `test_market_agent.py` | sha 불일치 시 멈추고 이전 버전 유지, 중간 실패 뒤 흔적 없음, 꺼진 동안 눌러도 연결 후 설치, Agent 버전이 바뀌어도 유지 |
| `test_market_hub.py` | `rev`가 같으면 왕복 없음, 다르면 `market.apply` 후 프롬프트 전송, 안내 1회, 안내가 대화 기록에 없음, 옛 Agent 통과, 시간 초과 + 미적용 회수 시 턴 차단, 이름 풀기(`rawInput` 늦게 와도), "항상 허용" 제거, 항목별 허용 |
| `test_market_gateway.py` | **세션 유지 중 설치·회수 반영**, 자식 환경에 `DDS_TOKEN` 없음, 망가진 항목이 있어도 입구 시작, 지문 불일치 거부, 시간 초과 시 작업 번호, 회수 후 자식 종료, 오류 문구 유지 |
| `test_hidden_windows.py` 추가 | 입구 → 항목 → 그 아래 프로세스에서 보이는 창 0개 |
| `scripts/e2e_market.py` | 등록 → 시험 → 승인 → 다른 사용자 설치 → demo 대화 호출 → 회수 → 거부. **세션 id가 끝까지 같아야 통과** |
| 화면 | `npm run build`, `npm run smoke`, `npm run smoke -- --reduced`, 스크린샷 직접 확인, `test_design_tokens` 통과 |
| 기존 흐름 | `npm run smoke -- --demo`, `scripts/e2e_demo.py` |

**현장 관문**: 출시 전 1회, `scripts/pc_verify.py`로 Claude, Gemini, Codex 각각 시험한다.

| # | 확인 | 통과 기준 | 실패 시 |
|---|---|---|---|
| 1 | 입구 포함 새 세션 시작 | 시작 실패 0회. 입구 없을 때 대비 시작 시간 기록 | 그 CLI만 입구 끔 |
| 2 | 대화 중 설치 후 도구 사용 | 안내만 보고 같은 세션에서 `market_describe` → `market_call`까지 감 | 3번 대안 |
| 3 | 대화 중 설치 후 스킬 사용 | 다음 요청에서 `SKILL.md`를 읽음. 숨김 폴더 포함 | 안내 문구 강화, 그래도 안 되면 그 CLI는 "새 대화부터" |
| 4 | 권한 카드 | 제목과 입력값이 항목 이름으로 보임 | 범용 문구 + 이번만 허용 |
| 5 | 사내 보안 프로그램 | `~/.dds-agent/market`의 Python 실행이 막히지 않음 | 위치 변경 |

**대안**: AI가 입구를 안정적으로 못 쓰는 CLI에는 새 세션에서 `dds_market.py --package X`로 항목별 프록시를 직접 노출한다. 같은 검사를 지나므로 통제는 유지되고, 그 CLI만 "새 대화부터"로 약속을 낮춘다.

---

## 13. 단계와 크기

줄 수는 모두 판단이고 테스트는 별도다.

| 단계 | 범위 | 크기 | 끝난 상태 |
|---|---|---|---|
| **0** | 토큰 범위, JWT 전용, PIN 재확인 공통 함수 | 서버 100줄, `pc_verify` 약간 | 마켓과 무관하게 구멍이 막힘 |
| **1** | 표, API, 검사, Agent 설치, 스킬 복사, 턴 관문, 안내, `/market`, 등록 서랍, 검토 탭, `market_publish.py` | 서버 600, Agent 250, 화면 600 | 스킬을 올리고 승인하고 설치하면 다음 요청부터 씀. 회수됨 |
| **2** | 입구, `pc_tool`, 시작 확인, 카드 이름, 호출 기록, 영향 보기, 대화 등록(`market_submit`, `market-publish`), 시작 틀 | 입구 350, Agent 100, 서버 150, 화면 150, 문서 | **첫 출시**(0+1+2). 현장 관문 통과 후 스위치 켬 |
| **3** | 스킬 덮어쓰기, AI 제안 카드, 부서 기본, 프로젝트 필요 항목, 레시피 연결, 자산화 연결, `server_tool`(결정 C), 내 스킬 찾기 | 서버 400, 화면 200, 입구 50 | 항목별로 나눠 진행 |
| **4** | wheel 격리 설치, `skills_scope`, CLI 형식 내보내기, 범위 토큰 추가 세분화, 서명 | 수요와 측정값이 있을 때만 | |

**별도 권장**: 사내 HTTPS. `pc_tool`을 전사에 공개하기 전에 한다.

**함께 고칠 문서**: `AGENTS.md`(지도, 자주 하는 작업), `product-spec.md`, `skill-contract.md`(등록 규약: 오류는 `ToolError`, 긴 도구는 진행 중을 돌려주기), `local-data.md`(`market` 폴더), `docs/pc-verification.md`(현장 관문).

---

## 14. 킬러 기능과 아하 모먼트

### 킬러 기능 (주요 기능)

| 기능 | 왜 주요한가 | 상태 |
|---|---|---|
| 재시작 없이 같은 대화의 다음 요청부터 쓰는 설치 | 사용자 요구의 "선택/설치하면 사용"을 가장 직접 만족 | 시제품으로 원리 확인, 실제 CLI 미시험 |
| 폴더를 놓거나 말 한마디로 등록 | 사용자 요구의 "쉽게 등록". 매니페스트를 쓰지 않음 | 설계 단계 |
| 내 PC 툴에 맞는 항목이 먼저 보임 | 29개 툴 중 자기 것만 볼 수 있음 | 규칙 기반. 단순하지만 충분 |
| 현장에서 고친 스킬을 Agent 배포 없이 전 PC로 | 관리부서의 실제 부담을 줄임 | 3단계 |
| 회수와 영향 보기 | 관리부서가 문제를 수습할 수 있어야 공개를 맡을 수 있음 | 시제품으로 원리 확인 |

### 아하 모먼트 (진짜 사람이 할 수 없는 것)

**지금 확정할 수 있는 것은 없다.** 카탈로그, 설치, 승인, 회수는 모두 사람이 더 느리게 할 수 있는 일이다. 마켓은 빠르고 편하지만 그 자체로 사람의 한계를 넘지는 않는다.

**후보 하나**: 동료가 어제 만든 방법이 내 AI의 실력으로 다음 요청에서 바로 쓰이는 것. 사람은 새 방법을 배우는 데 시간이 걸리지만, AI는 읽고 바로 적용한다.

아하 모먼트라고 부르려면 아래가 필요하다.
1. 실제 CLI가 그 항목을 안내만 보고 찾아 쓴다.
2. 실제 툴에서 결과가 맞다는 기록이 있다. 지금은 스킬 31개에 `TODO(사내검증)`가 남아 있다.
3. 실제 업무에서 다른 사람의 항목을 처음 설치해 맞는 결과를 낸 사례를 잰다(`MarketCall`, `evidence`).

세 조건을 재기 전에는 쓰지 않는다. 사용자 작업을 자동으로 자산으로 만드는 쪽은 자산화 설계의 몫이고, 이 판단은 그쪽 결과를 함께 봐야 한다.

---

## 15. 확인한 것과 못 한 것

**이번에 직접 확인**
- `sync_project_context`, `_source_stamp`, `_skill_index`의 동작
- `mcp_servers`, `_runner_env`, `_prepare`, `_load_session`의 살아 있는 세션 처리
- `_ensure_session`, `_run_turn`, `_on_permission`
- `user_from_token`, `require_admin`, `require_member`
- `record_instance`, `settings_message`, `test_agent_settings`의 같음 비교
- `test_session_keep`, `test_hidden_windows`의 가짜 객체 사용
- `check_bundle` 한도, `_recipe_member` 위치, `DdsMCP`가 async를 감싸지 않음
- 설치 프로그램의 JWT 사용, `requirements-agent.txt`
- Gemini의 `Promise.all`(이 PC 판 소스)
- 시제품 재실행

**인용만 하고 다시 보지 않음**
- CLI 3종의 세션 동작 표, `mcpCapabilities`, 스킬 측정값 전부, 번들 크기, `TODO` 개수, 외부 형식과 사고 자료

**못 한 것**
- 실제 CLI에서의 전 과정. 12절 현장 관문으로 넘겼다.
- **AI가 안내만 보고 입구를 안정적으로 쓰는가. 이 설계의 가장 큰 불확실성이다.**
- 사내 PC의 시작 시간. Codex의 시작 제한 기본값(문서 기억으로 10초, 미확인). 다만 기존 MCP 4개에도 같은 위험이 있어 입구만의 새 위험은 아니다 `[판단]`.
- `pip download --platform win_amd64`의 사내 Linux 서버 동작.
- Windows 숨김 속성의 스킬 폴더를 CLI 3종이 찾는가(결정 A).
- 시제품은 입구의 원리만 시험했다. 자식 프로세스 묶음 종료와 `lock.json` 수정 시각 감시는 구현하지 않았다.

---

## 16. 사용자만 정할 수 있는 것

| # | 질문 | 권장 | 다르게 정하면 |
|---|---|---|---|
| 1 | "내가 만든 것을 등록"의 "나"는 누구인가 | 올리기와 본인 시험은 누구나, 전사 공개는 관리부서 승인 후 | 관리부서만이면 `내 것` 등급과 일반 사용자 등록 화면이 빠져 작아진다. 자산화 입구는 줄어든다 |
| 2 | 일반 사용자의 코드 항목도 `나만 쓰기`로 검토 없이 쓰게 할까 | 예. 자기 PC, 자기 권한이다 | 아니오면 시험하려 해도 승인을 기다려야 한다 |
| 3 | 동료에게 검토 없이 공유를 허용할까 | 첫 판은 아니오. 공유는 곧 검토 요청이다 | 예면 프로젝트 공유 등급이 첫 판에 들어온다 |
| 4 | 관리부서가 자기 코드를 스스로 승인해도 될까 | 지침은 허용, 코드는 다른 관리자. 관리자가 한 명이면 기록을 남기고 허용 | 관리자 수를 알려 주면 확정한다 |
| 5 | 부서 전체에 코드 항목을 기본으로 켤 수 있게 할까(3단계) | 예. 처음 켜질 때 알리고 끌 수 있게 한다. 관리부서는 이미 Agent 업데이트로 전 PC에 코드를 보낸다 | 아니오면 지침 항목만 기본으로 |
| 6 | 입구 프로세스 하나와 AI 호출 1회 증가를 받아들여 "같은 대화에서 바로"를 택할까 | 예 | 아니오면 "새 대화부터"로 약속이 낮아지고, AI 제안과 대화 등록이 빠진다 |
| 7 | 회수를 받지 못한 PC의 대화를 막아도 될까 | 예. 회수가 있고 15초 안에 답이 없을 때만 막는다 | 아니오면 회수 보장이 "연결되어 있으면"으로 낮아진다 |
| 8 | 기존 스킬 33개를 고르게 할까 | 아니오. "기본 포함"으로 두고, 나중에 PC 감지로 자동 조정한다. 이미 깔린 툴을 다시 고르지 않아도 된다 | 예면 4단계 일이 앞당겨지고 관련 테스트와 규칙 문서를 고쳐야 한다 |
| 9 | 스킬 덮어쓰기를 첫 출시에 넣을까 | 3단계 첫 항목. 현장 수정이 바로 시작되면 앞당긴다 | 앞당기면 첫 출시에 2~3일 정도 더 든다(추정) |
| 10 | 사내 HTTPS를 적용할 수 있는가 | `pc_tool` 전사 공개 전에 적용 | 안 되면 평문 구간 변조는 남는 위험으로 기록한다 |
| 11 | 시험 전용 계정과 PC를 둘 수 있는가 | 둔다. 없으면 코드를 읽은 뒤 검토자 PC에서만 시험 | 시험 코드가 관리자 PC에서 돈다 |
| 12 | 화면 이름 | "마켓" | 문구만 바뀐다 |

**0단계(토큰 범위)는 마켓을 만들지 않더라도 지금 있는 구멍이다.** 먼저 고칠지 정해 주면 나머지 범위와 별개로 진행할 수 있다.