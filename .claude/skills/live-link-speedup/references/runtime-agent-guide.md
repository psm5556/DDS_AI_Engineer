# 웹앱 에이전트 지침에 넣을 내용 (참고)

웹앱 안에서 CAD/CAE를 다루는 에이전트(Claude Code·Gemini·Codex를 ACP로 구동)가 읽는 지침이다.
- 빠른 툴을 만들어도 에이전트가 예전처럼 `live_exec`로 코드를 쓰고 단계마다 캡처하면 빨라지지 않는다. 툴과 지침을 함께 바꾼다.
- 넣을 곳(집 저장소 기준)
  - `agent-context/skills/live-modeling/SKILL.md`
  - `mcp_servers/dds_live.py`의 `MCPServer(instructions=...)`
  - COM·Creoson으로 붙는 툴은 툴 스킬의 "Live Link" 절(`catia-automation`·`solidworks-automation`·`creo-automation`·`lighttools-automation`)
- 지원하지 않는 툴에는 빠른 툴 안내를 넣지 않는다. 사내에서 만든 툴만 적는다.

## 1. MCP instructions 문구
```text
사용자가 열어 둔 CAD/CAE 화면에서 함께 모델링할 때 쓴다(형상 만들기, 재료·하중·경계조건·메쉬 설정, 해석 모델 준비).
시작할 때 live_info를 한 번 읽고, 이름은 거기서만 가져온다.
값 바꾸기는 live_set_params, 화면은 live_view, 되돌리기는 live_undo로 한다(코드를 쓰지 않아 빠르다).
live_exec는 이것들로 안 되는 일만, 하나의 명령 묶음으로 실행한다.
사용자가 툴 화면을 보고 있으므로 캡처는 큰 단계가 끝났을 때와 요청할 때만 한다(capture=true로 같은 호출에서).
해석 제출처럼 오래 걸리는 일은 Live Link가 아니라 dds-cae(배치)로 한다. 툴별 방법은 live-modeling 스킬을 읽는다.
```

## 2. 진행 원칙에서 바꿀 것
- 이전: "작은 단계씩 `live_exec`를 실행하고, 단계마다 `live_capture`로 화면을 게시한다."
- 이후
  - **하나의 논리 단위씩** 실행한다(예: 재료·단면·할당 → 스텝·하중·경계조건 → 메쉬).
  - 값 바꾸기, 화면 바꾸기, 되돌리기는 빠른 툴을 먼저 쓴다.
  - 사용자는 툴 화면을 직접 보고 있다. 캡처는 큰 단계가 끝났을 때·사용자가 요청할 때·보고서용으로만 한다.

## 3. 빠른 툴 쓰는 법 (모든 툴 공통)
1. 시작할 때 `live_info(tool)`를 한 번 읽는다. 바꿀 값과 화면 대상의 이름은 여기서만 가져온다(추측 금지).
2. 값: `live_set_params(tool, values={이름: 숫자, ...})`
   - 여러 개는 **한 호출에** 모은다. 재생성을 한 번만 한다.
   - 재생성이 실패하면 자동으로 바꾸기 전으로 되돌리고 오류를 돌려준다. 다른 값을 제안한다.
   - 느린 일(재메쉬 `fem=true`, 광선 추적 `trace=true`)은 사용자가 원할 때만 켠다.
3. 화면: `live_view(tool, orient=..., fit=..., style=..., show=[...], hide=[...], only=[...])`
   - orient: front·back·top·bottom·left·right·iso·trimetric
   - style: shaded·edges·wire·hidden
   - 툴이 지원하지 않는 항목은 오류로 돌아온다. 그대로 사용자에게 알린다.
4. 되돌리기: `live_undo(tool, steps=1)`
5. `live_exec`는 빠른 툴로 안 되는 일(새 피처 만들기 등)에만 쓴다. 하나의 명령 묶음으로 만든다.
6. 결과의 `ms`에 걸린 시간이 있다.
   - 툴 안 시간: update·fem·mesh·trace·capture
   - 바깥 시간: connect·wait·round_trip
   - 느리다는 말이 나오면 이 값으로 어디서 걸리는지 알려 준다.

## 4. 툴별 이름 규칙과 예 (사내에서 만든 툴만 남긴다)
| tool | values 이름 | 예 |
|---|---|---|
| `nx` | expression 이름 | `live_set_params(tool="nx", values={"thickness": 4, "rib_h": 12})` |
| `nx`(Simcenter 3D, 메쉬까지) | 같음 + `fem=true` | `live_set_params(tool="nx", values={"thickness": 4}, fem=true)` |
| `abaqus` | `종류:경로` | `live_set_params(tool="abaqus", values={"section:Shell-1/thickness": 2.5, "load:Load-1/magnitude": 1500})` |
| `hypermesh` | `prop:`·`mat:` | `live_set_params(tool="hypermesh", values={"prop:shell_2mm/thickness": 2.5})` |
| `catia` | 파라미터 이름 | `live_set_params(tool="catia", values={"Thickness": 4})` |
| `solidworks` | 치수 FullName·전역변수(mm) | `live_set_params(tool="solidworks", values={"Thickness": 4, "D1@Sketch1": 50})` |
| `creo` | 파라미터·치수 이름 | `live_set_params(tool="creo", values={"THICKNESS": 4})` |
| `lighttools` | lt_map.json 이름 | `live_set_params(tool="lighttools", values={"lens_z": 12.5}, trace=true)` |
| `ansys_mechanical` | `body:`·`mesh/`·`load:` | `live_set_params(tool="ansys_mechanical", values={"body:Plate/thickness": 3})` |
| `starccm` | Global Parameter 이름 | `live_set_params(tool="starccm", values={"inlet_velocity": 3.0})` |

## 5. 대화 예 (요청 1건 = 툴 호출 1번)
| 사용자 요청 | 에이전트가 부를 툴 |
|---|---|
| "두께를 4로 바꿔 줘" | `live_set_params(values={"thickness": 4})` |
| "두께 4, 리브 높이 12로 바꾸고 화면 올려 줘" | `live_set_params(values={"thickness": 4, "rib_h": 12}, capture=true)` |
| "정면에서 모서리까지 보이게" | `live_view(orient="front", style="edges")` |
| "커버만 숨겨 줘" | `live_view(hide=["COVER"])`(이름은 live_info에서) |
| "방금 것 취소" | `live_undo()` |
| "메쉬까지 다시 만들어 줘" | `live_set_params(values={...}, fem=true)` |
