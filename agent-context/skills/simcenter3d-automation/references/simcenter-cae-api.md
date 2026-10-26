# Simcenter 3D (NX CAE) NX Open Python API 노트

- **[확인]**: Journal 기록이나 공식 레퍼런스에서 확인되는 호출이다.
- **TODO(사내검증)**: 버전마다 다를 수 있다. Journal 기록으로 확인한 뒤에 쓴다.

> 원칙: CAE API는 Builder와 PropertyTable 속성 이름이 문자열(`"Magnitude"` 등)이라 추측하면 틀리기 쉽다.
> **반드시 GUI에서 같은 편집을 하며 Journal을 기록**하고, 기록된 문자열과 enum을 그대로 쓴다.

## 1. 파일 체인과 연관성(associativity)

```
bracket.prt (마스터 CAD, expression 보유)
   └─ bracket_fem1_i.prt (Idealized: 마스터 바디의 WAVE 링크 + 단순화)
         └─ bracket_fem1.fem (FEM: 메쉬, 재료, 물리속성)
               └─ bracket_sim1.sim (SIM: 하중, 구속, 솔루션)
```
- **CAD → FEM 연관 워크플로**: (1) .prt의 expression을 바꾸고 `DoUpdate`를 실행한다. (2) Idealized 파트를 업데이트한다(WAVE 링크 갱신). (3) FEM을 업데이트한다(`UpdateFemodel`). 이때 메쉬가 재생성되고 메쉬 컨트롤과 경계조건이 새 형상에 다시 연결된다. (4) SIM에서 해석한다.
- 하중과 구속이 **형상(면/모서리)에 걸려 있어야** 형상이 바뀌어도 따라온다. 절점이나 요소에 직접 걸린 조건은 재메쉬 후 깨진다.
- 형상 변화가 커서 면이 새로 생기거나 사라지면 연관이 끊길 수 있다. 결과가 이상하면 DOE의 극단 케이스부터 GUI로 열어 확인한다.
- 케이스 폴더로 **4개 파일 모두** 복사한다(`inputs`). 파일 이름을 바꾸지 않는다. 파일 간 참조가 이름 기반이기 때문이다.

## 2. 열기와 파트 전환

```python
session = NXOpen.Session.GetSession()                                   # [확인]
session.Parts.LoadOptions.ComponentLoadMethod = NXOpen.LoadOptions.LoadMethod.FromDirectory  # TODO(사내검증)
sim_part, st = session.Parts.OpenBaseDisplay(sim_path)                  # [확인]
sim_part = session.Parts.BaseWork                                        # [확인] NXOpen.CAE.SimPart
simulation = sim_part.Simulation                                         # [확인] NXOpen.CAE.SimSimulation
fem_part = sim_part.FemPart                                              # [확인] (어셈블리 FEM .afm 은 다름 - TODO(사내검증))
fe_model = fem_part.BaseFEModel                                          # [확인]
part, st = session.Parts.OpenBase(path)                                  # [확인] 표시 없이 열기
session.Parts.SetActiveDisplay(part, NXOpen.DisplayPartOption.AllowAdditional,
                               NXOpen.PartDisplayPartWorkPartOption.UseLast)  # [확인] 파트 전환
```
- 열고 나서 `session.Parts`의 `FullPath`를 모두 확인해 **케이스 폴더 밖 파일이 없는지** 검사한다. 템플릿이 이 검사를 한다.

## 3. 업데이트

```python
mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, "upd")
n_err = session.UpdateManager.DoUpdate(mark)          # [확인] CAD / Idealized / 각 파트 expression 변경 후
fe_model.UpdateFemodel()                               # [확인] FEM 업데이트(재메쉬 포함). 반환값 활용은 TODO(사내검증)
```
- Idealized 파트 업데이트를 별도로 해야 하는지, FEM 업데이트만으로 충분한지는 TODO(사내검증)다. 템플릿은 안전하게 둘 다 한다.
- 메쉬 실패 감지: 업데이트 뒤 해석에서 `SetupCheckOption.CompleteCheckAndOutputErrors`가 오류를 잡는다. 메쉬 개수나 요소 수를 직접 확인하는 API는 TODO(사내검증)다.

## 4. 메쉬 변경

```python
mm = fe_model.MeshManager                                              # [확인]
mesh = mm.FindObject("Mesh[3d_mesh(1)]")                                # 이름은 GUI 트리 기준
b = mm.CreateMesh3dTetBuilder(mesh)                                     # TODO(사내검증)
unit = fem_part.UnitCollection.FindObject("MilliMeter")
b.PropertyTable.SetBaseScalarWithDataPropertyValue("quad mesh overall edge size", "5", unit)  # TODO(사내검증) 속성 이름
b.CommitMesh(); b.Destroy()
```
- **권장 방법**: 메쉬 크기 필드에 FEM 파트 expression(예: `mesh_size`)을 넣어 두고 expression만 바꾼다. 그러면 `params.json`에 `mesh_size`를 넣는 것만으로 템플릿이 FEM 파트 expression을 찾아 반영한다. 필드에 expression을 쓸 수 있는지는 TODO(사내검증)다.
- 2D, 1D 메쉬와 Mapped Hex는 빌더가 다르다(`CreateMesh2dBuilder` 등). Journal 기록으로 확인한다.

## 5. 재료 / 물리속성

```python
ppt = fem_part.PhysicalPropertyTables.FindObject("PhysicalPropertyTable[PSOLID1]")   # TODO(사내검증) 이름 형식
mat = fem_part.MaterialManager.PhysicalMaterials.LoadFromNxlibrary("Steel")          # TODO(사내검증) 메서드명
ppt.PropertyTable.SetMaterialPropertyValue("material", False, mat)                   # TODO(사내검증)
# 쉘 두께 (PSHELL)
unit = fem_part.UnitCollection.FindObject("MilliMeter")
ppt.PropertyTable.SetBaseScalarWithDataPropertyValue("element thickness", "2.5", unit)  # TODO(사내검증) 속성 이름
```
- 쉘 두께도 메쉬 크기처럼 **expression 구동**을 우선 검토한다.
- 재료 물성값(E, ν, ρ)을 바꾸는 작업은 라이브러리 재료를 직접 고치지 않는다. **로컬 재료**를 만들어 할당한다. Journal 기록으로 확인한다(TODO(사내검증)).

## 6. 하중 / 구속 크기

```python
load = None
for bc in simulation.Loads:          # [확인] simulation.Constraints 도 동일
    if bc.Name == "Force(1)":
        load = bc
b = simulation.CreateBcBuilderForBc(load)                     # [확인 - Journal 기록 형태]
fe = b.PropertyTable.GetScalarFieldPropertyValue("Magnitude")  # TODO(사내검증) 속성 이름(대소문자)
fe.EditFieldExpression("1500", sim_part.UnitCollection.FindObject("Newton"), [], False)
b.PropertyTable.SetScalarFieldPropertyValue("Magnitude", fe)
b.CommitAddBc(); b.Destroy()                                   # TODO(사내검증) 편집 시에도 CommitAddBc 인지
```
- 템플릿 `sc_update_solve.py`의 `BC_PARAM_MAP`에 `"force_n": ("Force(1)", "Magnitude", "Newton")`을 넣으면 이 경로를 쓴다.
- **권장 방법**: GUI에서 하중 크기 필드에 SIM 파트 expression(`force_n`)을 입력해 둔다. 그러면 expression만 바꿔도 반영된다(TODO(사내검증)). 압력, 변위 구속, 온도도 같은 방식이다.

## 7. 솔루션 / 솔버 설정

```python
for s in simulation.Solutions:      # [확인]
    s.Name
# 솔버 옵션, 출력 요청, 스텝(서브케이스) 편집은 PropertyTable / ModelingObject 기반 - TODO(사내검증)
```
- 출력 요청(Output Requests)에서 **변위, 응력(von Mises), SPC 반력**이 OP2로 나가도록 GUI에서 한 번 설정하고 저장해 둔다. 응력 출력이 "Max Shear"로 되어 있으면 von Mises가 없다.
- 모달 해석의 모드 수(EIGRL ND), 비선형 스텝 설정은 Journal 기록으로 확인한다.
- 질량 출력이 필요하면 `PARAM,GRDPNT,0`을 추가한다(f06의 GRID POINT WEIGHT GENERATOR). TODO(사내검증)

## 8. 해석 실행

```python
ssm = NXOpen.CAE.SimSolveManager.GetSimSolveManager(session)           # [확인]
n_ok, n_fail, n_skip = ssm.SolveChainOfSolutions(
    [solution],
    NXOpen.CAE.SimSolution.SolveOption.Solve,
    NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors,
    NXOpen.CAE.SimSolution.SolveMode.Foreground)                         # [확인] Foreground = 완료까지 대기
```
- 결과 파일은 기본적으로 .sim과 같은 폴더에 생긴다. 이름은 `<sim이름>-<솔루션이름 소문자, 공백→_>.dat/.f06/.op2/.log` 형식이다. 예: `bracket_sim1-solution_1.f06`. 위치와 이름은 Customer Defaults에 따라 다를 수 있다(TODO(사내검증)). 템플릿은 이름 규칙을 먼저 적용하고, 못 찾으면 해석 시작 이후 생긴 최신 f06을 쓴다.
- **완료 판정**: (1) `n_fail == 0`이고 `n_ok >= 1`이다. (2) f06에 `FATAL`이 없다. (3) f06에 `END OF JOB`이 있다. (4) op2 파일이 있다.
- 실패 원인은 f06의 `USER FATAL MESSAGE` 번호와 `.log`에 나온다. 메모리와 디스크 부족은 `.log`에서 확인한다.
- 대안(NX 밖에서 실행): `.dat`만 내보내고 Simcenter Nastran을 직접 실행할 수도 있다(`nastran.exe model.dat`). 이 경우 `SolveOption.ExportOnly` 같은 옵션을 확인한다(TODO(사내검증)).

## 9. 결과 추출

### 9-1. 권장: op2 + pyNastran (NX 밖, 플랫폼 파이썬)
`references/nastran-results.md`와 `scripts/sc_run_case.py`를 참고한다. NX 내장 파이썬에는 pyNastran이 없으므로 플랫폼 venv에서 실행한다.

### 9-2. NX 내부 후처리 API (ResultManager) — TODO(사내검증)
```python
# 공식 레퍼런스상 존재: NXOpen.CAE.ResultManager, ResultParameters(SetGenericResultType), ResultAccess
rm = session.ResultManager
sol_result = rm.CreateReferenceResult(solution)             # TODO(사내검증) 메서드명
rp = rm.CreateResultParameters()                            # TODO(사내검증)
# rp.SetGenericResultType(...), rp.SetResultComponent(NXOpen.CAE.Result.Component.VonMises),
# rp.SetLoadcaseIteration(0, 0)  - 이름/인자 TODO(사내검증)
ra = rm.CreateResultAccess(sol_result, rp)                   # TODO(사내검증)
# ra.AskNodalResult(node_index) 등
```
- Post Processing Navigator에서 결과를 열고 컨투어를 표시하는 과정을 Journal로 기록하면 정확한 호출이 나온다. 단, 최대값 조회 API는 기록되지 않을 수 있다. 이 경로는 **NX에서 계산한 평균 절점 응력과 같은 값**이 필요할 때만 쓴다.

### 9-3. f06 직접 파싱
pyNastran을 쓸 수 없을 때 마지막 수단으로 쓴다. 변위 표(`D I S P L A C E M E N T   V E C T O R`)나 응력 표를 정규식으로 파싱할 수 있지만 형식이 요소 타입마다 달라 오류가 나기 쉽다.

## 10. 컨투어 이미지 — TODO(사내검증)
- Post view 생성 → 결과/성분 지정 → 이미지 내보내기 순서다. 대략 `session.Post.CreatePostviewForResult(...)`, `PostviewSetResult`, `PostviewUpdate`에 4절의 Image Export(UI)나 UF Disp.CreateImage를 이어 쓴다. 모두 Journal 기록으로 확인한다.
- **run_journal 배치에는 그래픽 창이 없어 이미지 캡처가 실패할 수 있다.** 이미지가 꼭 필요하면 다음 중 하나를 쓴다.
  1) 대화형 Simcenter에서 후처리 저널을 재생한다.
  2) 플랫폼 파이썬에서 pyNastran으로 op2와 bdf를 읽어 pyvista/matplotlib으로 그린다. 형상은 `read_bdf`로 읽는다.
- 이미지 실패는 결과 실패가 아니다. results.json은 정상적으로 쓴다.

## 11. Journal 기록 방법
NX에서 **Developer 탭 → Journal → Record**를 누른다(구버전: Tools → Journal → Record). 언어는 File → Preferences → User Interface → Tools → Journal에서 **Python**으로 설정한다.
기록할 만한 작업은 다음과 같다. 하중 크기 편집, 물리속성 재료 변경, 메쉬 크기 변경, 솔루션 편집(출력 요청), FEM Update, Solve, Post 컨투어 표시와 이미지 내보내기.
기록 결과에서 `FindObject("...")` 이름, PropertyTable 속성 문자열, enum을 그대로 옮긴다.
