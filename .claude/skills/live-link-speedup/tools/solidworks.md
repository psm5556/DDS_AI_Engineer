# SOLIDWORKS

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: dds-live에 SOLIDWORKS 어댑터가 없다.
  - 단계마다 `run_script(tool="solidworks_com", args=["attach=1", ...])`로 `sw_param_update.py`를 실행한다.
  - 실행할 때마다 파이썬을 띄우고 `GetActiveObject("SldWorks.Application")`로 붙는다.
- 병목
  - **단계마다 프로세스 시작과 COM 연결**
  - AI가 치수 이름(`D1@Sketch1`)을 매번 확인한다.
  - 이미지 저장이 방향을 iso로 바꾸고, Save As로 파일을 쓴다.

## 2. 빠른 툴 대응 (dds-live 안 어댑터 + `references/com_worker.py`)
| 도우미 | SOLIDWORKS API | 상태 |
|---|---|---|
| `summary` | `sw_param_update.list_dimensions(model)`, `list_equations(model)` 재사용 + 활성 문서·구성 | 배치 스크립트와 같음 |
| `set_params` | `apply_params(model, values, strict, length_scale, angle_names)` → `rebuild(model)`(`ForceRebuild3`) | 배치 스크립트와 같음 |
| ↳ 실패 시 | `ForceRebuild3`가 False → 이전 값을 다시 넣고 재생성 | — |
| `undo` | `model.EditUndo2(n)` 또는 기억한 이전 값 다시 쓰기 | `EditUndo2` 확인 필요 |
| `view` 방향 | `model.ShowNamedView2("*Front", 1)`(swStandardViews: Front 1·Back 2·Left 3·Right 4·Top 5·Bottom 6·Isometric 7·Trimetric 8) | `*Isometric`,7은 스크립트에서 사용, 나머지 번호 확인 |
| `view` 맞추기 | `model.ViewZoomtofit2()` | 스크립트에서 사용 |
| `view` 표시 방식 | `model.ViewDisplayShaded()` / `ViewDisplayWireframe()` / `ViewDisplayHiddenremoved()` | 확인 필요 |
| `view` 숨기기 | 파트 바디 `body.HideBody(True)`, 조립체 부품 `Component2.Visible` | 확인 필요 |
| `capture` | `model.SaveBMP(path, 0, 0)` 또는 창 캡처. **방향을 바꾸지 않는다** | 확인 필요 |

## 3. values 이름 규칙
- 치수는 `D1@Sketch1` 형식(FullName), 전역변수는 이름(`Thickness`)을 쓴다.
- 값은 **mm·도** 단위로 받는다. API의 `SystemValue`는 m·rad이다. 도우미가 바꾼다(배치 스크립트 `length_scale`·`angle_names`).
- 사용자가 설계변수를 전역변수로 정리해 두면 이름이 안정적이다. 처음에 이렇게 하도록 안내한다.

## 4. 연결·실행 개선
- dds-live 프로세스가 `ComWorker("SldWorks.Application")` 하나로 연결을 계속 들고 있는다.
- 배치 스크립트를 import해서 함수만 부른다.
- 값을 여러 개 바꿀 때는 재생성을 한 번만 한다.
- 화면 갱신을 잠시 끄면 빨라질 수 있다(`model.ActiveView.EnableGraphicsUpdate = False` → 끝나고 `True`). 확인 필요.

## 5. API 확인 (기록 기능)
- Tools > Macro > Record로 같은 동작을 한 번 하고, 기록된 VBA(.swp)에서 메서드 이름을 가져온다.

## 6. 주의
- `SystemValue`는 **활성 구성**에만 적용될 수 있다. 모든 구성에 적용하려면 `SetSystemValue3`를 쓴다(확인 필요). 어느 쪽인지 결과에 적는다.
- 사용자가 스케치·피처를 편집 중이면 호출이 실패하거나 편집이 취소된다.
  - `model.SketchManager.ActiveSketch`로 미리 확인할 수 있다(확인 필요).
  - 확인이 안 되면 오류를 잡아 안내한다.
- attach에서는 문서를 열거나 닫거나 저장하지 않는다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "열린 모델의 치수와 전역변수를 보여 줘" | `live_info` | 이름·값(mm) |
| "Thickness 4mm, D1@Sketch1 50mm로" | `live_set_params` | 재생성 1번, 단위 |
| "Thickness를 0으로" | `live_set_params` | 이전 값으로 복구 |
| "되돌려 줘" | `live_undo` | 이전 값 |
| "정면에서 와이어로 보여 주고 화면 올려 줘" | `live_view` + `capture=true` | 방향·표시·이미지 |

## 8. 지침에 넣을 예
- `live_set_params(tool="solidworks", values={"Thickness": 4, "D1@Sketch1": 50})`
- `live_view(tool="solidworks", orient="front", style="wire")`
