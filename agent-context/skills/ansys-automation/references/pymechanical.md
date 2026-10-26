# PyMechanical(ansys-mechanical-core) 레퍼런스: 버전 민감

> PyMechanical은 버전마다 API가 바뀐다. 여기의 코드는 ansys-mechanical-core 0.10 이후와 Mechanical 2023 R2 이후를 기준으로 한다. 사내 버전에서 먼저 확인한다. `TODO(사내검증)`

## 두 가지 모드
| 모드 | 특징 | 사용 |
|---|---|---|
| 임베딩 `App` | Mechanical을 파이썬 프로세스 안에 로드한다. 스크립팅 객체(Model, ExtAPI)를 직접 쓴다 | DDS 템플릿 기본(`pymechanical_update.py`) |
| 원격 `launch_mechanical` | 별도 Mechanical 프로세스와 gRPC로 통신한다. 문자열 스크립트를 `run_python_script`로 보낸다 | 임베딩이 안 되는 환경, GUI 세션 연결 |

### 임베딩
```python
from ansys.mechanical.core import App, global_variables
app = App(version=242)                       # installed version number
globals().update(global_variables(app))      # injects ExtAPI, DataModel, Model, Quantity, enums...
app.open(r"C:\work\case_0001\bracket.mechdb")
analysis = Model.Analyses[0]
```
### 원격
```python
import ansys.mechanical.core as pymechanical
mech = pymechanical.launch_mechanical()      # or CLI: ansys-mechanical -r 242 --port 10000 -g
mech.run_python_script('ExtAPI.DataModel.Project.Open(r"C:\\work\\bracket.mechdb")')
val = mech.run_python_script("Model.Analyses[0].Solution.Children[1].Maximum.ToString()")  # returns str
mech.exit()
```

## 자주 쓰는 스크립팅 호출(Mechanical scripting API)
```python
obj = DataModel.GetObjectsByName("DDS_Force")[0]              # rename objects in the GUI first
obj.Magnitude.Output.SetDiscreteValue(0, Quantity("1000 [N]"))  # force/pressure magnitude
body.Thickness = Quantity("2 [mm]")                           # surface body thickness
Model.Mesh.ElementSize = Quantity("3 [mm]")                   # global element size
sz = Model.Mesh.AddSizing(); sz.Location = ns; sz.ElementSize = Quantity("1 [mm]")
fs = analysis.AddFixedSupport(); fs.Location = DataModel.GetObjectsByName("NS_Fixed")[0]
pr = analysis.AddPressure(); pr.Location = ...; pr.Magnitude.Output.SetDiscreteValue(0, Quantity("1 [MPa]"))
Model.Mesh.GenerateMesh()
analysis.Solution.Solve(True)
st = analysis.Solution.AddEquivalentStress(); st.EvaluateAllResults(); st.Maximum   # Quantity
ExtAPI.Application.ActiveUnitSystem = MechanicalUnitSystem.StandardNMM
analysis.WorkingDir                                             # folder with file.rst
```
- 재료: `Model.Materials.Import(xml_path)` 후 바디에 `body.Material = "Name"`를 할당한다. `TODO(사내검증)`. Engineering Data 상수(E 등)를 바꾸는 작업은 Workbench 경로(a)에서 파라미터화하는 편이 확실하다.
- Named Selection은 `DataModel.GetObjectsByName("NS_...")`로 찾는다. 스크립트로 새로 만들 때는 `Model.AddNamedSelection()`과 Worksheet 기준을 쓰는데, 버전마다 달라 `TODO(사내검증)`이다.

## 이미지
```python
# newer PyMechanical helper  TODO(사내검증) availability
app.helpers.export_image(st, file_path="images/mises.png", width=1600, height=1000)
# Mechanical API (2021 R1+)
st.Activate(); Graphics.Camera.SetFit()
s = Ansys.Mechanical.Graphics.GraphicsImageExportSettings()
s.Resolution = GraphicsResolutionType.EnhancedResolution
s.Background = GraphicsBackgroundType.White
s.Capture = GraphicsCaptureType.ImageAndLegend
Graphics.ExportImage(r"C:\work\images\mises.png", GraphicsImageExportFormat.PNG, s)
```
- 뷰 방향: `Graphics.Camera.SetSpecificViewOrientation(ViewOrientationType.Iso)`. `TODO(사내검증)`

## 주의
- 한 프로세스에서 `App`은 하나만 만든다. 케이스마다 새 프로세스로 실행하는 플랫폼 구조와 맞는다.
- `Solution.Status` 문자열과 enum 값은 버전 의존이다. 실패 판정은 결과 `Maximum` 읽기 성공 여부와 함께 쓴다.
- 임베딩은 Windows에서 .NET(pythonnet)을 쓴다. 가상환경 파이썬 버전은 PyMechanical이 지원하는 범위여야 한다.
