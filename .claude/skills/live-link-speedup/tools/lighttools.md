# LightTools

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: dds-live에 LightTools 어댑터가 없다.
  - 단계마다 `run_script`로 `lt_case.py attach=1`을 실행한다. 이때 COM `LightTools.LTAPI4`로 붙는다.
- 병목
  - **단계마다 프로세스 시작과 COM 연결**
  - **값을 바꿀 때마다 광선 추적까지 한다.** 가장 오래 걸린다.
  - AI가 데이터 키 문자열을 매번 만든다.

## 2. 빠른 툴 대응 (dds-live 안 어댑터 + `references/com_worker.py`)
| 도우미 | LTAPI | 상태 |
|---|---|---|
| `summary` | `lt_map.json`의 params 이름 → `db_get(key, prop)`로 현재 값, 수신기 목록(`db_list`) | 배치 스크립트와 같음 |
| `set_params` | `db_set(key, prop, value)` 재사용. **추적하지 않는다** | 배치 스크립트와 같음 |
| ↳ `trace=true` | 추적 명령(`cmd(lt, sim_cmd)`) → `lt_map.json` outputs를 `db_get`으로 | 배치 스크립트와 같음 |
| ↳ 실패 시 | 앞에서 바꾼 값을 이전 값으로 되돌린다 | — |
| `undo` | 기억한 이전 값 다시 쓰기 | — |
| `view` | 보기 방향·맞추기 명령 문자열(`lt.Cmd`) | 확인 필요 |
| `capture` | 창 캡처(PrintWindow) | MCP에 이미 있음 |

## 3. values 이름 규칙
- `lt_map.json` params의 이름(예: `lens_z`)을 쓴다. 키·속성 매핑은 그 파일에 있다.
- 매핑에 없는 값이 필요하면 사용자와 확인해 `lt_map.json`에 추가한다. 데이터 키를 대화에서 직접 만들지 않는다.

## 4. 연결·실행 개선
- dds-live 프로세스가 `ComWorker("LightTools.LTAPI4")` 하나로 연결을 계속 들고 있는다(ProgID는 사내 확인).
- 값 바꾸기와 광선 추적을 나눈다.
  - 위치·형상만 볼 때는 추적하지 않는다.
  - 결과(조도·균일도)가 필요할 때만 `trace=true`로 추적한다.
  - 결과에 `ms.trace`를 넣는다.
- 편집하는 동안 LightTools의 자동 재추적 설정이 켜져 있으면 값을 바꿀 때마다 추적한다. 끄는 방법을 확인한다.

## 5. API 확인 (기록 기능)
- GUI에서 동작했을 때 LightTools 명령 창(Command/Console)에 나오는 명령 문자열을 확인해 `lt.Cmd`에 쓴다. 버전에 따라 위치를 확인한다.
- 데이터 키와 속성 이름은 배치 스킬 `lighttools-automation`의 `inspect=1` 출력에서 가져온다.

## 6. 주의
- 광선 추적 결과에는 잡음이 있다. 같은 값으로 두 번 추적해 차이를 사용자에게 알려 준다.
- 대화상자(라이선스·저장 확인)가 뜨면 COM 호출이 멈출 수 있다. `ComWorker.call`의 timeout으로 알린다.
- attach에서는 모델을 열거나 닫거나 저장하지 않는다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "바꿀 수 있는 값과 수신기를 보여 줘" | `live_info` | lt_map 이름·현재 값 |
| "렌즈를 1 mm 앞으로" | `live_set_params` | 추적 없이 빠르게 반영 |
| "지금 상태로 추적해서 균일도 알려 줘" | `live_set_params(trace=true)` 또는 별도 추적 | `ms.trace` |
| "되돌려 줘" | `live_undo` | 이전 값 |

## 8. 지침에 넣을 예
- `live_set_params(tool="lighttools", values={"lens_z": 12.5})`
- 결과가 필요할 때: `live_set_params(tool="lighttools", values={"lens_z": 12.5}, trace=true)`
