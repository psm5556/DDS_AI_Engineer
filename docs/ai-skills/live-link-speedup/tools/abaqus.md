# Abaqus/CAE

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: 툴 안 플러그인 `agent-context/live/abaqus/abaqus_plugins/ddsLive/`
  - `ddsLive_plugin.py`(GUI 쪽)
    - 127.0.0.1:47801 소켓으로 요청을 받는다.
    - GUI 타이머가 **0.2초마다** 큐를 확인한다.
    - `sendCommand()`로 커널에 실행을 넘긴다.
  - `ddslive_kernel.py`(커널 쪽)
    - 코드 파일을 exec한다.
    - 결과를 파일로 남기면 GUI가 읽어 응답한다.
- 병목
  - AI가 커널 Python(mdb·session)을 매번 새로 쓴다.
  - 모델 요약(`summary`)에 이름 목록만 있고 값이 없다. 그래서 값을 읽는 호출이 더 든다.
  - 요청마다 `sendCommand`를 두 번 한다(import + 실행).
  - 요청마다 `abaqus`·`abaqusConstants` 전체를 env에 복사한다.
  - 단계마다 캡처한다.

## 2. 빠른 툴 대응 (커널 쪽 도우미 `ddslive_kernel.py`에 함수 추가)
| 도우미 | Abaqus 커널 API | 상태 |
|---|---|---|
| `summary` | 모델별 parts·materials(탄성·밀도 값)·sections(두께)·loads(크기)·bcs·steps·sets + 메쉬 크기 | 목록은 [확인], 값 읽는 속성은 rpy로 확인 |
| `set_params` 돌출 깊이 등 | `p.features[f].setValues(depth=...)` → `p.regenerate()` → `m.rootAssembly.regenerate()` | 스킬 문서에 있음 |
| `set_params` 스케치 치수 | 스케치 Parameter가 있으면 `s.parameters[n].setValues(expression=...)` → `feat.setValues(sketch=s)` | 확인 필요(버전별) |
| `set_params` 재료 | `m.materials[n].elastic.setValues(table=((E, nu),))`, `.density.setValues(...)` | 스킬 문서에 있음 |
| `set_params` 쉘 두께 | `m.sections[n].setValues(thickness=...)` | 스킬 문서에 있음 |
| `set_params` 하중 | `m.loads[n].setValues(magnitude=...)` 또는 `cf1/cf2/cf3=` | 스킬 문서에 있음 |
| `set_params` 메쉬 크기 | `p.seedPart(size=...)` → `p.generateMesh()`(independent면 `a.seedPartInstance`·`a.generateMesh`) | 스킬 문서에 있음. 메쉬는 요청할 때만 |
| `undo` | Python Undo API 없음 → 도우미가 기억한 **이전 값을 다시 쓴다** | — |
| `view` 방향·맞추기 | `vp.view.setValues(session.views['Front'])`, `vp.view.fitView()` | [확인](odb 스킬과 같음) |
| `view` 표시 대상 | `vp.setValues(displayedObject=p 또는 a)` | [확인] |
| `view` 표시 방식 | `vp.partDisplay.setValues(renderStyle=SHADED/WIREFRAME/HIDDEN)`(조립체는 `assemblyDisplay`) | 확인 필요 |
| `view` 숨기기 | `displayGroupMdbToolset`의 `LeafFromPartInstance` → `vp.assemblyDisplay.displayGroup.remove(leaf=...)`/`.replace(...)` | 확인 필요 |
| `capture` | 지금 `screenshot()`(`session.printToFile(format=PNG, canvasObjects=(vp,))`) 그대로 | [확인] |

## 3. values 이름 규칙
Abaqus는 바꿀 값의 종류가 여러 가지다. `종류:경로` 형식의 이름을 쓰고, `summary()`가 이 이름 그대로 목록을 준다.
| 이름 예 | 뜻 |
|---|---|
| `feature:Bracket/Solid extrude-1/depth` | 파트 Bracket의 피처 깊이 |
| `sketch:Bracket/Solid extrude-1/L` | 스케치 Parameter `L` |
| `material:AL6061/E`, `material:AL6061/density` | 탄성계수·밀도 |
| `section:Shell-1/thickness` | 쉘 두께 |
| `load:Load-1/magnitude`, `load:Force-1/cf2` | 하중 크기 |
| `seed:Bracket/size` | 메쉬 크기(재메쉬, 느림) |
- 모델이 여러 개면 앞에 `Model-1:`을 붙인다. 하나면 생략한다.
- 같은 묶음 안의 재생성은 파트마다 한 번만 하고, 마지막에 조립체를 한 번 재생성한다.

## 4. 연결·실행 개선
- GUI 타이머 간격을 0.2초에서 0.05~0.1초로 줄인다.
- import는 처음 한 번만 `sendCommand`로 보낸다(이미 불렀는지 GUI 쪽에 기억). 이후에는 실행 한 줄만 보낸다.
- 커널 env는 한 번 만들어 재사용한다. 도우미 호출은 `ddslive_kernel.call("set_params", json_text)`처럼 한 줄로 한다.
- 결과 파일은 임시 이름으로 쓰고 이름을 바꾼다.
- `sendCommand`는 커널이 끝날 때까지 GUI를 멈춘다. 그래서 재메쉬처럼 긴 작업은 요청할 때만 한다.
- `ms`에 `update`(regenerate), `mesh`, `capture`를 나눠 넣는다.

## 5. API 확인 (기록 기능)
- 작업 폴더의 `abaqus.rpy`(리플레이 파일)에 GUI 동작이 Python으로 기록된다.
- 기록 전에 `session.journalOptions.setValues(replayGeometry=COORDINATE, recoverGeometry=COORDINATE)`을 실행하면 선택이 읽기 쉽게 기록된다.
- 자세한 방법은 `agent-context/skills/abaqus-automation/references/cae-model-api.md` 첫 부분에 있다.

## 6. 주의
- Abaqus 버전에 따라 Python 2.7(2023 이하)과 3(2024 이상)이 다르다. 도우미는 두 버전에서 모두 돌게 쓴다(f-string 금지).
- 스케치 치수 인덱스(`dimensions[i]`)는 모델마다 다르다. 이름으로 바꾸려면 사용자가 스케치에 Parameter를 만들어 두게 안내한다.
- 하중이 스텝마다 다르면 `setValuesInStep(stepName=...)`을 쓴다. 이름에 스텝을 넣는다: `load:Load-1/magnitude@Step-2`.
- 사용자가 다른 대화상자를 쓰는 중에 `sendCommand`가 실행되면 결과가 꼬일 수 있다. 오류를 잡아 "열린 대화상자를 닫아 주세요"라고 안내한다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "열린 모델에서 바꿀 수 있는 값을 보여 줘" | `live_info` | 이름 규칙·값 |
| "쉘 두께를 2.5로, 하중을 1500으로 바꿔 줘" | `live_set_params` | 한 번에 반영, 모델 트리 값 |
| "돌출 깊이를 0으로 바꿔 줘" | `live_set_params` | 재생성 실패 → 이전 값으로 복구 |
| "방금 것 되돌려 줘" | `live_undo` | 이전 값 |
| "조립체를 정면에서 보여 주고 커버 인스턴스는 숨겨 줘" | `live_view` | 방향·숨기기 |
| "메쉬 크기를 3으로 다시 메쉬해 줘" | `live_set_params` | 재메쉬 시간(`ms.mesh`) |

## 8. 지침에 넣을 예
- `live_set_params(tool="abaqus", values={"section:Shell-1/thickness": 2.5, "load:Load-1/magnitude": 1500})`
- `live_view(tool="abaqus", orient="iso", style="shaded", hide=["Cover-1"])`
