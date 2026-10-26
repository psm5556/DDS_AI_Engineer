---
name: ansys-automation
description: Ansys 해석 자동화. Workbench 저널(runwb2 -B)로 설계 파라미터 변경과 프로젝트 업데이트, PyMAPDL과 APDL 배치로 파라메트릭 모델을 해석, PyMechanical로 .mechdb의 하중·경계조건·재료·메쉬 변경, PyDPF로 .rst 결과(최대 von Mises, 변위, 반력)를 추출, 컨투어 PNG를 저장한다. 사용자가 Ansys Workbench, Mechanical, MAPDL/APDL 모델 자동화, DOE/최적화 케이스 실행, Ansys 결과 추출을 요청할 때 사용.
---

# Ansys 자동화 스킬

이 스킬은 `docs/skill-contract.md` 규약을 따른다. 스크립트는 케이스 폴더(cwd)에서 실행되고 `params.json`을 읽는다. 결과는 `results.json`(숫자, snake_case 키)과 `images/*.png`로 쓴다. 결과 추출에 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 끝낸다.

## 1. 언제 쓰나: 경로 선택

| 상황 | 경로 | tool 키 | 템플릿 |
|---|---|---|---|
| 기존 Workbench 프로젝트(SpaceClaim/DesignModeler 형상 + Mechanical)에 **입력·출력 파라미터가 이미 정의됨**. 형상 치수를 바꿔 DOE를 돈다 | (a) Workbench 저널 | `ansys_wb` | `scripts/wb_param_update.wbjn` |
| 형상이 단순해서 APDL로 직접 만들 수 있다. 빠른 파라메트릭 연구, 라이선스/GUI 없이 반복 | (b) PyMAPDL | `ansys_python` | `scripts/pymapdl_param_plate.py` |
| 기존 `.mechdb`에서 하중 크기·두께·메쉬 크기·재료만 바꾼다(형상 변경 없음) | (c) PyMechanical(임베딩) | `ansys_python` | `scripts/pymechanical_update.py` |
| 어떤 경로든 해석 후 `.rst`에서 결과를 뽑는다. 솔버와 무관한 후처리 | (d) PyDPF-Core | `ansys_python` | `scripts/dpf_extract_rst.py` |
| 사내에 검증된 APDL 입력파일(.inp/.mac)이 있다. Python 패키지 설치가 어려운 환경 | (e) MAPDL 클래식 배치 | `python`(드라이버), 단발 실행은 `mapdl` | `scripts/mapdl_batch_driver.py` + `scripts/plate_template.inp` |

선택 원칙은 다음과 같다.
- **형상(CAD 치수)이 바뀌면 (a)가 기본이다.** CAD 재생성, 메쉬, 해석, 출력 파라미터 갱신을 Workbench가 한 번에 처리한다. 출력 파라미터(최대 응력, 질량 등)는 Mechanical에서 미리 "P" 체크로 파라미터화해야 한다.
- 형상이 고정되고 조건만 바뀌면 (c)가 가볍다. 다만 PyMechanical API는 버전 의존성이 크다(2023 R2 이상 권장, `TODO(사내검증)`).
- 해석 모델 전체를 코드로 정의할 수 있으면 (b)가 가장 재현성이 좋다.
- 결과 추출은 (d)를 공통 추출기로 쓰면 경로와 무관하게 같은 코드로 쓸 수 있다.

전제 조건:
- Ansys 버전, 라이선스(Mechanical Enterprise/Pro 등), 설치 경로는 사내 환경에 따라 다르다. `config.toml`의 `[tools]` 경로를 확인한다.
- Workbench 저널은 **IronPython 2.7**에서 돈다. f-string을 쓰지 말고 py2 문법으로 작성한다. 파일 첫 줄에 `# encoding: utf-8`을 둔다.
- `ansys_python`은 PyAnsys 패키지(`ansys-mapdl-core`, `ansys-mechanical-core`, `ansys-dpf-core`)가 설치된 CPython 3.x다.

## 2. 실행 방법

| tool 키 | 명령 템플릿 | 비고 |
|---|---|---|
| `ansys_wb` | `{ansys_runwb2} -B -R {script}` | `-B`는 배치(GUI 없음), `-R`은 저널 실행. 인자 전달이 없으므로 저널은 cwd(또는 `DDS_CASE_DIR` 환경변수)를 케이스 폴더로 쓴다 |
| `ansys_python` | `{ansys_python} {script} {args}` | PyMAPDL/PyMechanical/PyDPF |
| `mapdl` | `{ansys_mapdl} -b -i {script} -o solve.out` | APDL 입력을 직접 실행. params.json을 읽지 못하므로 스터디에는 드라이버를 쓴다 |
| `python` | 플랫폼 venv 파이썬 | `mapdl_batch_driver.py`(APDL 템플릿 → 실행 → 파싱) |

스터디 어댑터 예시:
```json
{"tool": "ansys_wb", "script": "scripts/wb_param_update.wbjn", "args": [], "inputs": ["models/bracket.wbpz"], "outputs": ["max_mises", "mass"]}
{"tool": "ansys_python", "script": "scripts/pymapdl_param_plate.py", "args": ["--nproc", "2"], "inputs": [], "outputs": ["max_mises", "max_disp", "mass"]}
{"tool": "python", "script": "scripts/mapdl_batch_driver.py", "args": ["--template", "plate_template.inp", "--np", "2"], "inputs": ["scripts/plate_template.inp"], "outputs": ["max_mises"]}
```
Workbench 프로젝트는 `.wbpj`와 `_files` 폴더가 짝이므로 **`.wbpz` 아카이브 하나를 inputs로 복사**하고 저널에서 `Unarchive`로 푸는 것이 안전하다.

## 3. 파라미터(형상) 변경

### (a) Workbench 저널
```python
dp = Parameters.GetDesignPoint(Name="0")          # Current design point
p1 = Parameters.GetParameter(Name="P1")
dp.SetParameterExpression(Parameter=p1, Expression="12 [mm]")
Update()                                           # geometry -> mesh -> solve -> output parameter update
val = Parameters.GetParameter(Name="P3").Value     # Quantity (.Value is the number)
```
- `params.json` 키(`thickness`)와 WB 파라미터 이름(`P1`)을 연결하는 `INPUT_MAP`을 템플릿 상단에서 정의한다. 맵에 없는 키는 파라미터 표시 이름(DisplayText)으로 찾는다.
- 단위를 붙이지 않은 Expression의 처리는 버전에 따라 다를 수 있다. 템플릿은 `UNIT_MAP`으로 `"12 [mm]"` 형태를 만든다. `TODO(사내검증)`
- 정확한 호출은 GUI에서 **File > Scripting > Record Journal**로 녹화해 확인한다. 이 방법이 가장 확실하다.
- 자세한 내용: [references/workbench-journal.md](references/workbench-journal.md)

### (b) PyMAPDL
`params.json` 값을 파이썬 변수로 받아 `mapdl.block(...)`, `mapdl.esize(...)` 같은 명령에 넣는다. APDL 파라미터로도 넘길 수 있다(`mapdl.parameters["THK"] = 2.5`). 기존 APDL 매크로를 재사용하려면 `mapdl.parameters[...]`로 값을 넣은 뒤 `mapdl.input("model.mac")`를 호출한다. 자세한 내용: [references/pymapdl.md](references/pymapdl.md)

### (e) APDL 클래식
드라이버가 `params.json`을 `params.inp`(`THICKNESS=2.5` 형식)로 변환한다. 템플릿 입력파일은 첫머리에서 `/INPUT,params,inp`로 이를 읽는다. 파라미터 이름 규칙은 다음과 같다. 영문자로 시작하고, 32자 이하이며, APDL 명령어 이름(`FORCE`, `TYPE` 등)과 겹치지 않아야 한다.

## 4. 해석조건 변경(재료, 하중, 경계조건, 메쉬)

| 항목 | Workbench 저널 | PyMechanical(임베딩) | PyMAPDL/APDL |
|---|---|---|---|
| 하중 크기 | Mechanical에서 파라미터화한 뒤 `SetParameterExpression` | `force.Magnitude.Output.SetDiscreteValue(0, Quantity("1000 [N]"))` | `F,ALL,FZ,val` / `SF`, `SFA` |
| 판 두께 | 파라미터화(Geometry 두께 P 체크) | `body.Thickness = Quantity("2 [mm]")` | `SECTYPE/SECDATA` 또는 형상 치수 |
| 재료 상수 | Engineering Data에서 E, 밀도 등을 파라미터화 | 재료 XML Import, 바디에 할당 `TODO(사내검증)` | `MP,EX,1,val` |
| 메쉬 크기 | Mechanical에서 Sizing을 파라미터화 | `Model.Mesh.ElementSize = Quantity("2 [mm]")` | `ESIZE,val` |
| 경계조건 | GUI에서 미리 정의 | `analysis.AddFixedSupport()` + `Location` | `D,ALL,ALL` |

PyMechanical에서는 대상 객체를 **트리 이름으로** 찾는다(`DataModel.GetObjectsByName("DDS_Force")`). 엔지니어에게 GUI에서 객체 이름을 고유하게 바꿔 두라고 안내한다. 자세한 내용: [references/pymechanical.md](references/pymechanical.md)

## 5. 해석 실행과 완료/실패 판정

| 경로 | 실행 | 성공 판정 |
|---|---|---|
| WB 저널 | `Update()` | 예외 없이 끝나고 출력 파라미터 값을 float로 읽을 수 있음. 실패 시 Update가 예외를 던지거나 출력값이 이전 값으로 남는다. 템플릿은 출력 파라미터 읽기 실패 시 results.json을 쓰지 않는다 |
| PyMAPDL | `mapdl.solve()` | 예외가 없음. `mapdl.post_processing`으로 결과를 읽을 수 있음. MAPDL 에러는 `MapdlRuntimeError` 예외로 올라온다 |
| PyMechanical | `analysis.Solution.Solve(True)` | `str(analysis.Solution.Status)`가 `Done`. 결과 객체의 `Maximum`을 읽을 수 있음 `TODO(사내검증)` |
| APDL 배치 | `ANSYSxxx -b -i in.inp -o solve.out` | `solve.out`에 `*** ERROR ***`이 없고, `.rst`와 결과 txt가 생성됨. 배치 종료코드는 버전에 따라 다르므로 단독 근거로 쓰지 않는다 `TODO(사내검증)` |

## 6. 결과 추출 → results.json

- **WB**: 출력 파라미터 → `OUTPUT_MAP`(`{"max_mises": "P3"}`). 보조 기록으로 `Parameters.ExportAllDesignPointsData(FilePath=...)` CSV도 남긴다.
- **PyMAPDL**: 
  - `mapdl.post_processing.nodal_eqv_stress()`: 절점 von Mises
  - `nodal_displacement("NORM")`: 변위 크기
  - 반력: `NSEL` + `ESLN` + `FSUM` 후 `mapdl.get("RZ", "FSUM", 0, "ITEM", "FZ")`
  - 질량: `ETABLE,EVOL,VOLU` + `SSUM`으로 부피를 구한 뒤 밀도를 곱한다
- **PyMechanical**: 결과 객체의 `.Maximum.Value`. 질량은 바디의 `.Mass`.
- **PyDPF**(공통): `dpf.Model(rst)`, `model.results.stress()`, `operators.invariant.von_mises_eqv_fc`, `operators.min_max.min_max_fc`. 자세한 내용: [references/pydpf.md](references/pydpf.md)

주의할 점:
- 단위계를 명시한다. 템플릿은 MPA 단위계(mm, N, t, MPa)를 쓴다. 이때 밀도 7.85e-9 t/mm³의 질량은 톤 단위다. results.json 키나 스터디 설명에 단위를 기록한다.
- 노드 평균(nodal averaged) 응력은 요소 응력보다 낮을 수 있다. 또 Mechanical의 표시값과 DPF/MAPDL 값은 평균 방식이 다르면 약간 다르다. 같은 스터디 안에서는 한 가지 방식만 쓴다.
- 응력 특이점(고정단 모서리, 집중하중점)의 최대값은 메쉬에 따라 발산한다. 관심 영역의 named selection으로 제한하거나 사용자에게 경고한다.

## 7. 이미지 저장

| 경로 | 방법 |
|---|---|
| PyMAPDL | `mapdl.post_processing.plot_nodal_eqv_stress(off_screen=True, savefig="images/mises.png", cpos="iso")` |
| PyDPF | `field.plot(off_screen=True, notebook=False, screenshot="images/mises.png")` |
| PyMechanical | `stress.Activate()`; `Graphics.Camera.SetFit()`; `Graphics.ExportImage(path, GraphicsImageExportFormat.PNG, settings)` `TODO(사내검증)` |
| APDL | `/SHOW,PNG`, `/GFILE,1200`, `/VIEW,1,1,1,1`, `PLNSOL,S,EQV`, `/SHOW,CLOSE`. `jobname000.png`, `001`, ... 순으로 생성되며 드라이버가 `images/`로 옮긴다 |
| WB 저널 | `model.SendCommand(Language="Python", Command=...)`로 Mechanical 스크립트를 실행해 ExportImage `TODO(사내검증)`. 대안은 저장된 `.rst`를 PyDPF로 그리는 것 |

PyVista 오프스크린 렌더링은 원격/서비스 세션에서 OpenGL이 없으면 실패할 수 있다. 실패해도 숫자 결과는 쓰도록 이미지 저장은 try로 감싼다(템플릿이 이미 그렇게 되어 있음).

## 8. 자주 하는 실수와 디버깅

- **WB 저널의 경로**: 저널은 상대경로를 cwd 기준으로 해석하지 않을 수 있다. 템플릿처럼 `os.path.join(CASE_DIR, ...)`로 절대경로를 쓰고 `/`로 통일한다.
- **IronPython 2.7**: f-string, `pathlib`, `print(..., file=)`를 쓸 수 없다. `json` 모듈은 쓸 수 있다.
- **WB 프로젝트 잠금**: 이전 실행이 비정상 종료하면 `.lock` 파일이 남는다. 케이스마다 `.wbpz`를 새로 풀면 피할 수 있다.
- **출력 파라미터가 갱신되지 않음**: Mechanical 결과의 Maximum 옆 "P" 체크가 빠졌거나 업데이트가 부분 실패한 경우다. `ExportAllDesignPointsData` CSV에서 값과 상태를 확인한다.
- **PyMAPDL 실행 실패**: `launch_mapdl`이 MAPDL 실행파일을 못 찾으면 `--exec-file`이나 환경변수 `PYMAPDL_MAPDL_EXEC`로 경로를 지정한다. 포트 충돌 시 이전 프로세스를 정리한다.
- **PyMAPDL 작업 폴더**: `run_location`을 케이스 폴더 하위로 지정해야 `.rst`와 `.err`가 케이스에 남는다. 에러는 `file.err`을 확인한다.
- **APDL 배치**: `solve.out`에서 `*** ERROR ***`을 검색한다. 매크로에서 정의되지 않은 파라미터는 0으로 읽히므로 `params.inp` 키 이름 오타에 주의한다.
- **PyMechanical**: `App(version=242)`의 버전 숫자는 설치 버전과 같아야 한다. 한 파이썬 프로세스에서 App은 하나만 만들 수 있다.
- **DPF**: `.rst` 파일이 열려 있는 동안(MAPDL 세션이 살아 있는 동안) 읽기가 실패하면 MAPDL을 종료한 뒤 읽는다.

## 9. 사내 검증 필요 항목(TODO(사내검증))

1. WB 저널의 Expression 단위 처리(`"12"`와 `"12 [mm]"`)와 `SetScriptVersion` 필요 여부. Record Journal로 확인한다.
2. `runwb2 -B -R` 실행 시 cwd와 실패 시 종료코드. 필요하면 플랫폼이 `DDS_CASE_DIR` 환경변수를 넘긴다.
3. WB 저널에서 Mechanical 이미지 export(`Edit`, `SendCommand`)가 배치 모드에서 동작하는지.
4. PyMechanical: 재료 할당 API, `Solution.Status` 값, `Graphics.ExportImage` 설정 속성, `app.helpers.export_image` 사용 가능 버전.
5. PyDPF: `reaction_force` 연산자와 해석 시 반력 출력 설정(`OUTRES,RSOL`), Mechanical 결과와의 평균 방식 차이.
6. APDL 배치 실행파일 이름(`ANSYS242.exe`/`MAPDL.exe`)과 종료코드 의미.

참고 문서:
- [references/workbench-journal.md](references/workbench-journal.md)
- [references/pymapdl.md](references/pymapdl.md)
- [references/pymechanical.md](references/pymechanical.md)
- [references/pydpf.md](references/pydpf.md)
- [references/mapdl-batch-apdl.md](references/mapdl-batch-apdl.md)
