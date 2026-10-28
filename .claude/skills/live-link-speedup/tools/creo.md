# Creo Parametric

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: dds-live에 Creo 어댑터가 없다.
  - 단계마다 `run_script(tool="creo_python", args=["attach=1", ...])`로 `creo_creoson_case.py`를 실행한다.
  - 이 스크립트가 Creoson 서버(HTTP JSON, 기본 localhost:9056)에 붙는다.
- 병목
  - **단계마다 파이썬 시작과 Creoson 연결**(`creopyson.Client` → 연결)
  - AI가 인자와 params.json을 매번 만든다.
  - 이미지 저장(`interface_export_image`)을 파일로 하고 변환한다.

## 2. 빠른 툴 대응 (dds-live 안 어댑터)
| 도우미 | Creoson(creopyson) | 상태 |
|---|---|---|
| `summary` | `file_get_active()`, `parameter_list(file_=mdl)`, `dimension_list(file_=mdl)` | 확인 필요(반환 형식) |
| `set_params` | `creo_creoson_case.apply_params(c, mdl, values, strict)` → `regenerate(c, mdl)` 재사용 | 배치 스크립트와 같음 |
| ↳ 실패 시 | 이전 값을 다시 넣고 `file_regenerate` | — |
| `undo` | 기억한 이전 값 다시 쓰기 | — |
| `view` 방향 | `view_activate(name, file_=mdl)`(저장된 뷰: FRONT·TOP·RIGHT 등) | 확인 필요 |
| `view` 맞추기·표시 방식 | mapkey 문자열을 `interface_mapkey(...)`로 실행(맞추기·음영·와이어) | 확인 필요 |
| `capture` | `interface_export_image("JPEG", filename=..., ...)`(스크립트에서 사용) → PNG로 변환 | 배치 스크립트와 같음 |

## 3. values 이름 규칙
- 파라미터 이름(`THICKNESS`)은 그대로 쓴다. 치수는 `d12`처럼 쓰거나 사용자가 붙인 이름을 쓴다.
- 스크립트는 `parameter_set`에 `no_create=True`를 쓴다. 없는 이름으로 새 파라미터가 생기는 사고를 막기 위해서다.

## 4. 연결·실행 개선
- dds-live 프로세스가 `creopyson.Client` 하나를 들고 세션을 재사용한다. 세션이 끊기면(오류 응답) 다시 연결한다.
- HTTP라 COM 같은 스레드 제약은 없다. 다만 Creo는 요청을 하나씩 처리하므로 같은 작업이 겹치지 않게 잠금을 둔다.
- 값을 여러 개 바꿀 때는 `file_regenerate`를 한 번만 한다.

## 5. API 확인 (기록 기능)
- Creo 동작은 mapkey 기록(Tools > Mapkeys > Record)과 trail 파일(trail.txt.N)에 남는다.
- 맞추기·표시 방식 같은 화면 명령은 mapkey 문자열(`~ Command \`ProCmd...\``)을 기록해서 쓴다.
- Creoson 명령 이름과 인자는 Creoson 문서(서버와 함께 배포되는 doc)에서 확인한다.

## 6. 주의
- 사용자가 일반 바로가기로 띄운 Creo에는 Creoson이 붙지 않을 수 있다. `nitro_proe_remote.bat`로 띄우도록 안내한다(스킬의 TODO 참고).
- 재생성이 실패하면 모델이 실패 상태로 남는다. 반드시 이전 값으로 되돌린 뒤 다시 재생성한다.
- attach에서는 모델을 열거나 지우거나 저장하지 않는다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "열린 모델의 파라미터와 치수를 보여 줘" | `live_info` | 이름·값 |
| "THICKNESS 4, d12 50으로" | `live_set_params` | 재생성 1번, 두 번째 호출부터 `ms.connect` 0 |
| "THICKNESS 0으로" | `live_set_params` | 이전 값으로 복구 |
| "정면에서 보여 주고 화면 올려 줘" | `live_view` + `capture=true` | 방향·이미지 |

## 8. 지침에 넣을 예
- `live_set_params(tool="creo", values={"THICKNESS": 4})`
- `live_view(tool="creo", orient="front", capture=true)`
