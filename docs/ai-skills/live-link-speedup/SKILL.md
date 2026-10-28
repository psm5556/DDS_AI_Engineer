---
name: live-link-speedup
description: DDS Conversa(이전 이름 DDS AI Engineer)의 Live Link(사용자가 열어 둔 CAD/CAE 화면에서 웹앱 에이전트가 설계 수정·화면 조작을 하는 기능)를 빠르게 만드는 개발 절차. 툴마다 같은 '빠른 툴'(live_info·live_set_params·live_view·live_undo·live_capture)을 만들고, 단계별 걸린 시간을 재고, 왕복·캡처·대기·연결 비용을 줄인다. 대상은 NX·Simcenter 3D, Abaqus/CAE, HyperMesh, CATIA V5, SOLIDWORKS, Creo, LightTools, Ansys Mechanical, STAR-CCM+. "Live Link가 느리다", "열린 CAD에서 설계 수정·화면 조작을 빠르게", "dds_live 개선" 요청에 사용.
---

# Live Link 빠르게 만들기 (개발용 스킬)

- 이 스킬은 웹앱을 **개발하는** AI 코딩 에이전트용이다.
  - 웹앱 안에서 CAD를 다루는 에이전트의 스킬(`agent-context/skills/live-modeling`)과는 다르다. 그 스킬은 7절에서 함께 고친다.
- 공통 절차는 이 파일에 있다. 툴별 내용은 `tools/<툴>.md`에 있다.
  - 한 번에 **툴 하나씩** 진행한다.
  - 진행 순서: 1절(현황 파악) → 2절(측정) → `tools/<툴>.md` → 3~8절.

| 툴 | 파일 | 연결 방식 | 참고 구현 |
|---|---|---|---|
| NX · Simcenter 3D | `tools/nx-simcenter.md` | 툴 안 실행기(대기열) | 있음 `references/nx/` |
| Abaqus/CAE | `tools/abaqus.md` | 툴 안 플러그인(소켓 → GUI 타이머 → 커널) | 표·설계만 |
| HyperMesh | `tools/hypermesh.md` | 툴 안 플러그인(Tcl 소켓) | 표·설계만 |
| CATIA V5 | `tools/catia.md` | COM 붙기 | `references/com_worker.py` |
| SOLIDWORKS | `tools/solidworks.md` | COM 붙기 | `references/com_worker.py` |
| LightTools | `tools/lighttools.md` | COM 붙기 | `references/com_worker.py` |
| Creo Parametric | `tools/creo.md` | Creoson(HTTP) | 표·설계만 |
| Ansys Mechanical | `tools/ansys-mechanical.md` | PyMechanical(gRPC) | 표·설계만 |
| STAR-CCM+ | `tools/starccm.md` | 클라이언트-서버 매크로 | 표·설계만 |

- "참고 구현 있음"은 집에서 테스트까지 한 코드다. "표·설계만"은 API 대응표와 설계다. API 이름은 6절 방법으로 사내에서 확인한 뒤 쓴다.

## 0. 배경
- **사내 상황**
  - 배치 자동화(run_journal 등)는 사내에서 잘 동작한다.
  - Live Link는 느리다.
  - 사람이 단계마다 승인하던 문제는 이미 고쳤다.
- **사내 코드는 집 저장소와 많이 다르다.** 이 문서와 `references/`의 코드는 참고용이다. 사내 코드 구조에 맞춰 적용한다.
- **느린 원인은 대개 툴 바깥에 있다.**

  | 원인 | 왜 느린가 |
  |---|---|
  | AI가 매번 툴 API 코드를 새로 쓴다 | 생성 시간이 들고, 틀린 API를 고치느라 다시 시도한다 |
  | 이름을 몰라 탐색 호출을 한다 | 왕복이 1~3번 더 생긴다 |
  | 단계마다 캡처하고 게시한다 | 왕복이 늘고 큰 이미지를 올린다 |
  | 단계마다 프로세스를 새로 띄우고 다시 연결한다(run_script, COM 붙기, 매크로 클라이언트) | 한 번에 수 초가 든다 |
  | 결과 확인 간격(1초 폴링 등) | 단계마다 평균 0.5초 |
  | 실행기 부가 작업(작업마다 창 열기, Undo 마크, 재생성, 다시 그리기) | 읽기만 하는 작업에도 비용이 든다 |
  | 툴 자체의 재생성·재메쉬·광선 추적 | 모델 크기에 비례한다. 줄이기 어려우니 필요할 때만 한다 |

## 1. 현황 파악 (고치기 전에)
- **읽을 곳.** 사내에서 이름이 바뀌었으면 비슷한 것을 찾는다.
  - `mcp_servers/dds_live.py`: `live_exec`·`live_info`·`live_capture`가 툴에 코드를 보내고 결과를 받는 방법
  - `agent-context/live/<툴>/`: 툴 안 플러그인·실행기
  - 툴 스킬 `agent-context/skills/<툴>-automation/`: 사내에서 검증된 배치 스크립트와 Live Link 절
  - 웹앱 에이전트 지침: `agent-context/skills/live-modeling/SKILL.md`, dds-live의 `MCPServer(instructions=...)`
  - 대화 기록: 서버 `server/app/agent/hub.py`가 남기는 툴 호출 이벤트와 시각
- **사용자에게 보여 줄 표**(툴마다)
  1. 연결 방식: 툴 안 플러그인 / COM 붙기 / 서버·API 연결 / 단계마다 프로세스 실행
  2. 실행 스레드. 툴 API는 대부분 툴의 메인(GUI) 스레드나 COM을 만든 스레드에서만 안전하다.
  3. 결과를 돌려받는 방식과 확인 간격
  4. 작업 한 건마다 하는 부가 작업
  5. 빠른 툴(값만 받는 툴)이 있는지. 없으면 3절이 가장 효과가 크다.
  6. 3~6절 항목 중 이미 된 것. 된 것은 건너뛴다.

## 2. 먼저 잰다 (측정 없이 고치지 않는다)
- **결과에 걸린 시간 `ms`를 넣는다.**

  | 이름 | 재는 곳 |
  |---|---|
  | `ai` | AI가 툴 호출을 만들기까지. 서버 대화 이벤트의 시각 차이로 잰다 |
  | `connect` | 연결·프로세스 시작(있으면) |
  | `wait` | 보낸 뒤 툴이 실행을 시작할 때까지 |
  | `update` / `fem` / `trace` / `capture` | 툴 안에서 재생성, 재메쉬, 광선 추적, 화면 저장 |
  | `round_trip` | MCP 툴 호출 전체 |

- **기준 요청 3개.** 툴마다 `tools/<툴>.md`의 예를 쓴다. 고치기 전과 후에 같은 모델로 잰다.
  1. 값 하나 바꾸기
  2. 보는 방향 바꾸기
  3. 값 두 개를 바꾸고 화면 올리기
- **기록 표:** 요청 / AI 툴 호출 수 / `ai` 합 / `round_trip` / 툴 안 시간 / 체감 시간
- **목표(예시)**
  - 요청 1건에 AI 툴 호출 1번
  - 툴 바깥 지연(`connect`+`wait`+전달) 0.3초 이하
  - 캡처 1초 이하
  - 나머지는 툴 자체 시간과 AI 응답 시간이어야 한다.

## 3. 빠른 툴: 모든 툴에 같은 이름, 같은 약속
- 원리: AI는 **값만** 넘긴다. 미리 만들어 둔 도우미가 검증된 API로 실행한다.
  - 웹앱 에이전트는 툴이 바뀌어도 같은 다섯 툴만 알면 된다.

  | MCP 툴 | 도우미 함수 | 하는 일 |
  |---|---|---|
  | `live_info(tool)` | `summary()` | 바꿀 수 있는 값 목록, 모델 구조, 지금 화면 |
  | `live_set_params(tool, values, ...)` | `set_params(values, ...)` | 여러 값을 한 번에 바꾸고, 재생성은 1번, 실패하면 되돌린다 |
  | `live_view(tool, orient, fit, style, show, hide, only)` | `view(...)` | 방향·맞추기·표시 방식·보이기/숨기기 |
  | `live_undo(tool, steps)` | `undo(steps)` | 도우미가 바꾼 것을 최근 것부터 되돌린다 |
  | `live_capture(tool)` 또는 `capture=true` | `capture(path)` | 뷰포트만 이미지로 저장한다 |

- **공통 약속**
  - 결과는 JSON이다: `{"ok", "error", "how"(사용자가 할 일), "warnings", "ms", ...}`
  - `summary()`가 돌려주는 값 목록은 `params: [{"name", "value", "unit", "kind"}]` 형식이다(NX 참고 구현은 `expressions`).
    - 사용자가 이름 붙인 것만 기본으로 준다. 자동 이름은 개수만 준다. 출력이 길면 AI가 매번 읽느라 느려진다.
  - `values`의 키는 `summary()`가 준 이름 그대로 쓴다. 모르는 이름이면 아무것도 바꾸지 않고 실패한다("live_info로 확인하세요").
  - 숫자만 받는다. 단위는 `summary()`의 단위를 따른다. 내부 단위가 다른 툴(SOLIDWORKS는 m)은 도우미가 바꾼다.
  - 바꾸기 전 값을 기억한다. 툴에 Undo API가 없으면 `undo`는 이전 값을 다시 쓴다.
  - 툴에 Undo 기록이 있으면 묶음 하나에 이름 있는 마크 하나를 남긴다(예: "DDS: thickness 3→4").
  - 재생성(업데이트)은 묶음마다 한 번만 한다. 실패하면 바꾸기 전으로 되돌리고 오류를 돌려준다.
  - 지원하지 않는 기능은 "이 툴은 live_view의 style을 지원하지 않습니다"처럼 분명하게 거절한다. 조용히 무시하지 않는다.
- **도우미 위치는 두 가지다.**
  - **툴 안 도우미**(NX, Abaqus, HyperMesh, Ansys Mechanical)
    - 툴 세션에 모듈을 한 번 올려 두고, MCP는 호출 코드 2~4줄만 보낸다.
    - 파일이 바뀌면(PC Agent 업데이트) 수정 시각을 보고 다시 불러온다.
  - **MCP 안 어댑터**(CATIA, SOLIDWORKS, LightTools는 COM / Creo는 HTTP / STAR-CCM+는 클라이언트)
    - dds-live 프로세스가 연결을 계속 들고 있다. 단계마다 프로세스를 띄우지 않는다.
- **검증된 함수를 다시 쓴다.**
  - 툴 스킬의 배치 스크립트(사내에서 검증·수정한 버전)를 import해서 쓴다. 새로 짜지 않는다.
- **참고 구현(NX):** `references/nx/`
  - `ddslive_nx_ops.py`: 도우미
  - `mcp_fast_tools.py`: MCP 툴(발췌)
  - 테스트 2개
- **MCP 툴 구현 요령**
  - 값을 코드에 넣을 때는 `json.loads(<repr(json.dumps(values))>)` 형태로 넣는다. 따옴표와 특수문자에 안전하다.
  - 툴 안 도우미 호출은 **기존 실행 경로**(`live_exec`)를 그대로 탄다. 그래야 사내 실행기 수정분과 함께 동작한다.
  - `result`가 비면 실패로 처리한다.
  - `capture=true`면 같은 실행에서 이미지 경로를 넘긴다. 받은 파일은 줄여서 게시한다.
  - 툴 설명(docstring)은 짧게 쓰고 예시는 1줄로 한다. 설명이 길면 AI가 매 턴 읽느라 느려진다.

## 4. 왕복·캡처·대기 줄이기
| 항목 | 확인 | 고치는 법 |
|---|---|---|
| 결과 확인 간격 | 폴링 간격 | 0.1초 이하. 가능하면 소켓·이벤트로 바로 응답 |
| 작업 파일 넘기기 | 다 쓰기 전에 읽힐 수 있는가 | 임시 이름으로 쓰고 `os.replace`로 바꾼다. 집 테스트에서 반쯤 쓴 코드가 실행되는 일이 실제로 있었다 |
| 결과 파일 읽기 | 쓰는 중에 읽으면 JSON 오류 | 잠깐 뒤 다시 읽는다 |
| 작업마다 부가 작업 | 창 열기·Undo 마크·재생성·다시 그리기 | 도우미가 필요할 때만 한다. 읽기 작업에는 하지 않는다 |
| 캡처 | 창 전체를 찍는가, 창을 앞으로 가져오는가 | 툴의 뷰포트 이미지 API로 찍고, 안 되면 창 캡처(PrintWindow). 긴 변 1600px로 줄여 게시 |
| 캡처 빈도 | 지침이 "단계마다"인가 | 큰 단계가 끝났을 때·요청할 때·보고서용만(7절) |
| 모듈·연결 캐시 | 단계마다 import·연결·로그인을 하는가 | 한 번 하고 재사용. 끊기면 다시 연결 |

## 5. 연결·실행 방식 점검
- **툴 API는 정해진 스레드에서만 부른다.**
  - 툴 안 플러그인: 받기는 배경 스레드에서 하고, 실행은 툴의 GUI 이벤트 루프·타이머로 넘긴다.
  - COM: 연결을 만든 스레드(STA)에서만 부른다. 전용 스레드 하나와 작업 큐를 둔다(`pythoncom.CoInitialize`).
    - MCP 툴 함수가 다른 스레드에서 불릴 수 있다. 그래서 COM 객체를 직접 공유하지 않는다.
- **사용자가 툴에서 편집 중일 때를 처리한다.**
  - 편집 중이면(스케치 편집, 명령 대화상자) 호출이 막히거나 실패한다.
  - 미리 확인할 수 있으면 확인하고, 안 되면 오류를 잡아 알린다: "툴에서 편집 중인 명령을 끝내 주세요".
- **같은 작업이 겹쳐 실행되지 않게 한다**(재진입 방지).
- **`live_status`가 연결 상태를 바로 알게 한다**(ping·하트비트). 연결이 없으면 오래 기다리지 않고 시작 방법을 안내한다.
- **긴 작업은 Live Link로 하지 않는다**(해석 제출, 대량 반복). GUI가 멈춘다. 배치(`dds-cae`)로 한다.

## 6. API 이름은 지어내지 않는다
- 툴의 **기록 기능**으로 사람이 같은 동작을 한 번 하고, 기록된 코드에서 이름을 가져온다.
- 툴별 기록 방법은 `tools/<툴>.md`의 "API 확인" 절에 있다.
- 확인한 곳의 `TODO(사내검증)` 표시를 지운다. 확인하지 못한 기능은 빠른 툴에서 빼고, 거절 메시지를 낸다.

## 7. 웹앱 에이전트 지침 고치기
- 툴을 만들어도 에이전트가 예전처럼 `live_exec`로 코드를 쓰고 단계마다 캡처하면 빨라지지 않는다.
- `references/runtime-agent-guide.md`를 `live-modeling` 스킬과 MCP instructions에 반영한다.
  - 툴 절마다 `values` 이름 규칙과 예를 넣는다(`tools/<툴>.md`의 "지침에 넣을 예").

## 8. 검증
- **단위 테스트**(라이선스 없이)
  - 툴 API를 흉내 내는 가짜 모듈로 도우미 논리를 확인한다: 한꺼번에 바꾸기, 재생성 1번, 실패 시 되돌리기, 모르는 이름.
    - 참고: `references/nx/ref_test_live_nx_ops.py`
  - 가짜 실행기로 MCP가 만든 호출 코드가 실제로 실행되는지 확인한다.
    - 참고: `references/nx/ref_test_live_fast.py`
  - 두 테스트는 집 저장소의 `agent/tests/test_live_*.py`를 복사했다(실수로 실행되지 않게 이름 앞에 `ref_`). 경로는 `agent/tests/` 기준이다. 사내 구조에 맞게 옮기고 이름을 바꾼다.
- **실제 툴 확인**
  - `tools/<툴>.md`의 확인 요청을 대화창에 그대로 보낸다.
  - 실패 케이스(형상이 깨지는 값)와 되돌리기를 포함한다.
  - `ms` 기록 표를 고치기 전후로 비교한다.
- **저장소 규칙**
  - AGENTS.md 4절 검증을 통과한다.
  - `agent/dds_agent/__init__.py`의 `__version__`을 올린다.
  - 관리 > Agent 배포로 배포한다.
- 사용자에게 고치기 전후 `ms` 표, 남은 `TODO(사내검증)`, 다음 병목을 보고한다.

## 9. 그다음 (선택)
- **AI 없이 직접 조작**
  - 웹앱 오른쪽 패널에 값 슬라이더와 뷰 버튼을 둔다. 서버 → PC Agent → 같은 도우미를 바로 부른다. AI 응답 시간이 빠지므로 1초 안에 끝난다.
  - 3D Simulator 화면의 슬라이더 패턴을 다시 쓴다.
  - PC Agent 메시지는 `agent/dds_agent/main.py`의 `handle()`과 서버의 `registry.runner(user.id).call()`을 짝으로 맞춘다.
- **AI 응답 시간이 대부분이면**
  - 대화를 짧게 유지하거나 새 대화를 시작한다.
  - 툴 설명과 지침을 짧게 한다.
  - 쓸 수 있는 빠른 모델을 고른다.
