# Ansys Mechanical

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: dds-live에 Mechanical 어댑터가 없다(PROTOCOL.md·live-modeling 스킬에 TODO로만 있다).
  - Mechanical을 gRPC로 띄운다(`ansys-mechanical -g --port 10000`, 버전별 옵션 확인).
  - 단계마다 `dds-cae.run_script(tool="ansys_python")`를 실행한다. 스크립트 안에서 `connect_to_mechanical(port=10000)` → `run_python_script(...)`를 한다.
- 병목
  - **단계마다 파이썬 시작과 gRPC 연결**
  - AI가 Mechanical 스크립트(ExtAPI)를 매번 새로 쓴다.

## 2. 빠른 툴 대응 (Mechanical 안 도우미 + dds-live 안 연결)
- dds-live가 `app = ansys.mechanical.core.connect_to_mechanical(port=10000)`을 한 번 만들어 들고 있는다.
- 도우미 스크립트(`dds_mech_ops.py`)는 처음에 `app.run_python_script(<도우미 전체>)`로 한 번 올린다.
  - 이후에는 `app.run_python_script("dds_ops_set_params('<json>')")` 한 줄씩 보낸다.
  - 실행 사이에 Mechanical 쪽 전역 변수가 유지되는지는 확인이 필요하다. 안 되면 매번 도우미와 호출을 같이 보낸다. 그래도 왕복은 1번이다.

| 도우미 | Mechanical 스크립팅 API | 상태 |
|---|---|---|
| `summary` | `Model = ExtAPI.DataModel.Project.Model`: 바디(이름·두께), `Model.Mesh.ElementSize`, 해석별 하중(이름·크기) | 확인 필요(버전별) |
| `set_params` 두께 | 면 바디 `body.Thickness = Quantity("3 [mm]")` | 확인 필요 |
| `set_params` 메쉬 크기 | `Model.Mesh.ElementSize = Quantity("2 [mm]")`, 재메쉬 `Model.Mesh.GenerateMesh()`(요청할 때만) | 확인 필요 |
| `set_params` 하중 | `force.Magnitude.Output.DiscreteValues = [Quantity("1000 [N]")]` | 확인 필요 |
| `undo` | 기억한 이전 값 다시 쓰기 | — |
| `view` | `Graphics.Camera.SetSpecificViewOrientation(ViewOrientationType.Front)`, `Graphics.Camera.SetFit()` | 확인 필요 |
| `capture` | `Graphics.ExportImage(path, GraphicsImageExportFormat.PNG, settings)` | 확인 필요 |

## 3. values 이름 규칙
| 이름 예 | 뜻 |
|---|---|
| `body:Plate/thickness` | 면 바디 두께(mm) |
| `mesh/element_size` | 전체 요소 크기(mm) |
| `load:Force/magnitude` | 하중 크기(N). 해석이 여러 개면 `load:Static Structural/Force/magnitude` |
- 형상 치수 변경은 Mechanical이 아니라 CAD(NX·SpaceClaim 등) 쪽 Live Link나 Workbench 파라미터로 한다.

## 4. 연결·실행 개선
- gRPC 연결을 dds-live에서 계속 유지한다. 끊기면 다시 연결하고, `live_status`에 연결 상태를 보여 준다.
- 값을 여러 개 바꿀 때는 한 번의 `run_python_script`로 보낸다.
- 재메쉬와 해석은 오래 걸린다. 재메쉬는 요청할 때만 하고, 해석은 배치(`dds-cae`)로 한다.

## 5. API 확인 (기록 기능)
- Mechanical 스크립팅 창의 기록 기능(버전에 따라 있음)이나 Help의 Mechanical Scripting 예제로 확인한다.
- 객체·속성 이름은 스크립팅 창에서 트리 객체를 선택하고 `Tree.FirstActiveObject`의 속성을 출력해 확인한다.

## 6. 주의
- Mechanical 스크립트는 버전에 따라 IronPython 2.7이다. 도우미에 f-string을 쓰지 않는다.
- 단위는 `Quantity("값 [단위]")`로 명시한다. 숫자만 넣으면 프로젝트 단위로 해석된다.
- 사용자가 GUI에서 해석을 돌리는 중에는 호출이 막힌다. timeout으로 알린다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "바꿀 수 있는 두께·메쉬 크기·하중을 보여 줘" | `live_info` | 이름·값·단위 |
| "Plate 두께 3mm, 하중 1500N으로" | `live_set_params` | 트리 값, 두 번째 호출부터 연결 시간 0 |
| "요소 크기 2mm로 다시 메쉬해 줘" | `live_set_params` | 재메쉬 시간 |
| "정면에서 맞춰 보여 주고 화면 올려 줘" | `live_view` + `capture=true` | 이미지 |

## 8. 지침에 넣을 예
- `live_set_params(tool="ansys_mechanical", values={"body:Plate/thickness": 3, "load:Force/magnitude": 1500})`
- `live_view(tool="ansys_mechanical", orient="front", capture=true)`
