# CATIA V5

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: dds-live에 CATIA 어댑터가 없다.
  - 단계마다 `run_script(tool="catia_com", args=["attach=1", ...])`로 `catia_param_update.py`를 실행한다.
  - 실행할 때마다 파이썬을 띄우고 `GetActiveObject("CATIA.Application")`로 붙는다.
- 병목
  - **단계마다 프로세스 시작과 COM 연결**(수 초)
  - AI가 인자와 params.json을 매번 만든다.
  - 이미지 저장이 방향을 iso로 바꾼다. 사용자가 보던 화면이 바뀐다.

## 2. 빠른 툴 대응 (dds-live 안 어댑터 + `references/com_worker.py`)
| 도우미 | CATIA COM | 상태 |
|---|---|---|
| `summary` | `catia_param_update.list_parameters(part)` 재사용 + 활성 문서 이름·종류(Part/Product) | 배치 스크립트와 같음 |
| `set_params` | `apply_params(part, values, strict)` → `update_part(part)`(`Part.Update()`) | 배치 스크립트와 같음 |
| ↳ 실패 시 | 기억한 이전 값을 다시 넣고 `Update()` | — |
| `undo` | 기억한 이전 값을 다시 쓴다(COM에 Undo API 없음) | — |
| `view` 맞추기 | `ActiveWindow.ActiveViewer.Reframe()` | 배치 스크립트에서 사용 |
| `view` 방향 | `Viewer.Viewpoint3D.PutSightDirection` / `PutUpDirection` | 확인 필요(스크립트에 TODO) |
| `view` 표시 방식 | `Viewer3D.RenderingMode` | 확인 필요 |
| `view` 숨기기 | `doc.Selection.Add(obj)` → `Selection.VisProperties.SetShow(1)`(1=숨김) | 확인 필요 |
| `capture` | `ActiveViewer.CaptureToFile(4, bmp)`(BMP) → Pillow로 PNG. **방향을 바꾸지 않는다** | 배치 스크립트에서 사용 |

## 3. values 이름 규칙
- 파라미터 이름을 `list_parameters`가 준 그대로 쓴다.
  - 사용자 파라미터: `Thickness`
  - 전체 경로: `Part1\PartBody\Pad.1\FirstLimit\Length`
- 사용자가 설계변수를 사용자 파라미터로 정리해 두면 이름이 짧고 안정적이다. 처음에 이렇게 하도록 안내한다.
- 단위 처리는 배치 스크립트 `apply_params`를 그대로 따른다.

## 4. 연결·실행 개선
- dds-live 프로세스가 `ComWorker("CATIA.Application")` 하나로 연결을 계속 들고 있는다. 끊기면 다음 호출에서 다시 붙는다.
- 배치 스크립트(`catia_param_update.py`)를 import해서 함수만 부른다. 사내에서 고친 버전을 쓴다.
- 값을 여러 개 바꿀 때는 `Update()`를 한 번만 한다.
- 화면 갱신을 잠시 끄면 빨라질 수 있다(`CATIA.RefreshDisplay = False` → 끝나고 `True`). 확인 필요.
- 경고창이 COM을 막지 않게 `CATIA.DisplayFileAlerts = False`를 쓴다. 저장·열기를 하지 않으면 거의 필요 없다.

## 5. API 확인 (기록 기능)
- Tools > Macro > Start Recording(CATScript/VBScript)으로 같은 동작을 한 번 하고, 기록된 매크로에서 메서드 이름을 가져온다.

## 6. 주의
- 사용자가 스케치 편집 모드에 있으면 COM 호출이 막히거나 실패한다. 오류를 잡아 "편집을 끝내 주세요"라고 안내한다.
- attach에서는 문서를 열거나 닫거나 저장하지 않는다. 저장은 사용자가 정한다.
- 업데이트 오류는 COM 예외로 올라온다. 이전 값으로 되돌린 뒤 오류를 알린다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "열린 파트의 파라미터를 보여 줘" | `live_info` | 이름·값 |
| "Thickness 4, RibHeight 12로 바꿔 줘" | `live_set_params` | 업데이트 1번, `ms.connect`가 두 번째 호출부터 0 |
| "Thickness를 0으로" | `live_set_params` | 이전 값으로 복구 |
| "방금 것 되돌려 줘" | `live_undo` | 이전 값 |
| "화면에 맞추고 지금 화면 올려 줘" | `live_view` + `capture=true` | 보던 방향이 그대로인지 |

## 8. 지침에 넣을 예
- `live_set_params(tool="catia", values={"Thickness": 4, "RibHeight": 12})`
- `live_view(tool="catia", fit=true, capture=true)`
