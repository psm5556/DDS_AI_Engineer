# NX · Simcenter 3D

참고 구현이 있다: `references/nx/`. 집 저장소 커밋 bdf3181(PC Agent 0.2.5)에서 가져왔다.

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: 툴 안 실행기 `agent-context/live/nx/dds_live_nx.py`
  - MCP가 `%USERPROFILE%\.dds-agent\live\nx\queue\job_*.py`를 쓴다.
  - NX 안에서 실행하고 `job_*.result.json`을 남긴다.
  - 사람이 버튼을 누르던 부분은 사내에서 이미 고쳤다.
- 병목
  - AI가 NXOpen 코드를 매번 새로 쓴다.
  - `live_info`가 파트 이름과 바디만 준다.
  - 단계마다 캡처한다.
  - 결과 확인 간격이 1초다.
  - 실행기가 작업마다 리스팅 창을 열고, Visible Undo 마크와 DoUpdate를 한다.

## 2. 빠른 툴 대응
| 도우미 | NXOpen | 상태 |
|---|---|---|
| `summary` | `part.Expressions`(이름·RHS·Value·Units), `Features`(GetFeatureName·FeatureType·Suppressed), `Bodies` | 대부분 [확인], `Suppressed` 확인 필요 |
| `set_params` | `Expressions.FindObject` → `EditWithUnits` → `UpdateManager.DoUpdate(mark)` | [확인] (배치 스크립트와 같음) |
| ↳ 다른 파트 | `Parts.SetActiveDisplay`(sc_update_solve `display`) | [확인] |
| ↳ 재메쉬 `fem=true` | Idealized `DoUpdate` → `fem_part.BaseFEModel.UpdateFemodel()` | 확인 필요 |
| ↳ 실패 시 되돌리기 | `SetUndoMark(Visible)` → `UndoToMark(mark, None)` | 확인 필요 |
| `view` 방향·맞추기 | `ModelingViews.WorkView.Orient(View.Canned.Front, ScaleAdjustment.Fit)`, `.Fit()` | [확인] |
| `view` 표시 방식 | `WorkView.RenderingStyle = View.RenderingStyleType.ShadedWithEdges` | 확인 필요 |
| `view` 숨기기 | `DisplayManager.BlankObjects` / `UnblankObjects` | 확인 필요 |
| `view` CAE 파트 | `.fem/.sim`에서 `ModelingViews` 대신 `Views.WorkView` | 확인 필요 |
| `capture` | `UI.CreateImageExportBuilder()` (대화형 세션) | 확인 필요 |

## 3. values 이름 규칙
- expression 이름을 그대로 쓴다(대소문자 구분). 예: `{"thickness": 4, "rib_h": 12}`
- 찾는 순서: 워크 파트 → 디스플레이 파트 → 열린 CAD → SIM → FEM → Idealized. 배치 `sc_update_solve`의 라우팅과 같다.
- 수식으로 구동되는 expression을 덮어쓰면 경고한다.
- Integer expression은 반올림한다.

## 4. 연결·실행 개선
- 실행기는 NX 메인 스레드에서 실행한다. 사내 수정본이 배경 스레드에서 NXOpen을 직접 부르면 멈춤·충돌 위험이 있다.
- 대기열을 쓰면
  - 작업 파일은 임시 이름으로 쓰고 `os.replace`로 바꾼다.
  - 결과 확인 간격은 0.1초로 한다.
- 실행기가 작업마다 하는 일을 줄인다.
  - 리스팅 창 대신 상태줄에 표시한다.
  - 도우미 호출에는 실행기 쪽 Undo 마크와 DoUpdate를 하지 않는다. 도우미가 직접 한다.
- 사용자가 NX 명령 대화상자를 쓰는 중이면 실행을 미룬다. `UF_UI_lock_ug_access`로 판단할 수 있는지 확인이 필요하다.

## 5. API 확인 (기록 기능)
- Developer(또는 Tools) > Journal > Record로 같은 동작을 GUI에서 한 번 한다. 기록된 .py에서 메서드와 enum 이름을 가져온다.

## 6. 주의
- `FindObject`는 없으면 예외를 던진다(None이 아니다).
- Simcenter에서 CAD 값만 바꾸면 메쉬는 그대로다. 메쉬까지 필요할 때만 `fem=true`를 쓴다(느리다).
- 다른 파트를 고치느라 화면 파트를 바꿨으면 끝나고 원래 화면으로 돌아온다.
- NX 템플릿 규약: f-string을 쓰지 않는다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "열린 파트에서 바꿀 수 있는 치수를 보여 줘" | `live_info` | 이름·값·단위 |
| "두께를 4, 리브 높이를 12로 바꿔 줘" | `live_set_params` | 업데이트 1번, Undo 목록에 "DDS: …" 1개 |
| "정면에서 모서리까지 보이게 하고 커버는 숨겨 줘" | `live_view` | 방향·표시·숨기기 |
| "방금 것 되돌려 줘" | `live_undo` | 이전 값 |
| "두께를 0으로 바꿔 줘" | `live_set_params` | 자동으로 되돌리고 오류 안내 |
| (sim을 연 상태) "두께 4로 바꾸고 메쉬까지 업데이트해 줘" | `live_set_params(fem=true)` | 재메쉬 후 원래 화면 |
| "지금 화면을 결과에 올려 줘" | `live_capture` | 뷰포트만, NX 창이 앞으로 튀어나오지 않음 |

## 8. 지침에 넣을 예
- `live_set_params(tool="nx", values={"thickness": 4, "rib_h": 12})`
- `live_view(tool="nx", orient="front", style="edges", hide=["EXTRUDE(3)"])`
- Simcenter 3D 메쉬까지: `live_set_params(tool="nx", values={...}, fem=true)`
