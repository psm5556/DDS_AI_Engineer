# Moldflow Synergy API 요약 (COM, Python pywin32)

Moldflow Synergy/Insight는 COM 서버 `synergy.Synergy`를 제공한다. 공식 예제는 VBScript(.vbs)이며, Python에서는 `win32com.client`로 같은 객체를 쓴다. 이 문서는 템플릿에서 쓰는 호출만 정리한다. 확인되지 않은 항목은 `TODO(사내검증)`로 표시했다.

## 가장 확실한 확인 방법: 매크로 녹화
Synergy에서 매크로 녹화(Tools/도구 → 매크로 기록. 메뉴 이름은 버전별로 다르다)를 켜고 GUI로 한 번 조작하면 `.vbs`가 생성된다. 속성 type/ID, 필드 ID, 플롯 이름, 메서드 인자를 이 파일에서 그대로 옮긴다. **ID 숫자를 추측하지 않는다.**

녹화 매크로의 머리 부분은 다음 형태다.
```vbscript
Set SynergyGetter = GetObject(CreateObject("WScript.Shell").ExpandEnvironmentStrings("%SAInstance%"))
Set Synergy = SynergyGetter.GetSASynergy
' 실행 중인 Synergy가 없으면: Set Synergy = CreateObject("synergy.Synergy")
Synergy.SetUnits "Metric"
```

## VBScript → Python 대응
| VBScript | Python (pywin32) |
|---|---|
| `Set Synergy = CreateObject("synergy.Synergy")` | `syn = win32com.client.Dispatch("synergy.Synergy")` |
| `Synergy.SetUnits "Metric"` | `syn.SetUnits("Metric")` |
| `Synergy.OpenProject "C:\p\proj.mpi"` | `syn.OpenProject(r"C:\p\proj.mpi")` |
| `Synergy.Project.OpenItemByName "Study1", "Study"` | `syn.Project.OpenItemByName("Study1", "Study")` |
| `Set StudyDoc = Synergy.StudyDoc` / `StudyDoc.Save` | `sd = syn.StudyDoc; sd.Save()` |
| `Set DVec = Synergy.CreateDoubleArray()` / `DVec.AddDouble 230` | `dv = syn.CreateDoubleArray(); dv.AddDouble(230.0)` |
| `DVec.Val(i)`, `DVec.Size` | `dv.Val(i)`, `dv.Size` |
| `Synergy.CreateIntegerArray()` | `syn.CreateIntegerArray()` |

- VBScript의 `Set x = ...`는 Python에서 그냥 대입이다. 인자 없는 메서드는 pywin32 동적 디스패치에서 속성처럼 읽힐 수 있다(`syn.StudyDoc`). 안 되면 `syn.StudyDoc()`로 호출한다.
- 경로는 항상 절대경로로 넘긴다(COM 서버의 현재 폴더는 케이스 폴더가 아니다).

## 속성(공정조건, 재료, 사출 위치 등) 편집: PropertyEditor
```python
ped = syn.PropertyEditor
prop = ped.FindProperty(type_id, prop_id)      # 녹화 매크로의 숫자 그대로
cur = prop.FieldValues(field_id)                # DoubleArray (읽기)
dv = syn.CreateDoubleArray(); dv.AddDouble(240.0)
prop.FieldValues(field_id, dv)                  # 쓰기 (VBS: Prop.FieldValues fid, DVec)
ped.CommitChanges("Process Conditions")         # 커밋하지 않으면 반영되지 않는다
```
- TODO(사내검증): pywin32에서 쓰기 호출이 `prop.FieldValues(fid, dv)`로 되는지, makepy(`gencache.EnsureDispatch`) 사용 시 `prop.SetFieldValues(fid, dv)`가 되는지. 템플릿 `set_field()`는 둘 다 시도한다.
- 순회: `ped.GetFirstProperty(type_id)`, `ped.GetNextProperty(prop)`. 필드 순회 `prop.GetFirstField()`, `prop.GetNextField(fid)`, `prop.FieldDescription(fid)`. TODO(사내검증): 필드 순회 함수 존재 여부(템플릿 `--dump-prop`에서 실패해도 속성 목록은 나온다).
- 공정조건의 프로파일(사출 속도/보압 프로파일)은 `[x1, y1, x2, y2, ...]` 같은 평탄 배열이다. 배열 구조는 녹화 매크로에서 확인하고 `PROP_MAP`의 `index`로 지정한다.

## 결과 플롯: PlotManager / Viewer
```python
pm = syn.PlotManager
plot = pm.FindPlotByName("Fill time")           # UI 표시 이름(언어 설정 영향)
ds = plot.GetDataID()
indp = syn.CreateDoubleArray(); pm.GetIndpValues(ds, indp)   # 시간 등 독립변수 목록
sel = syn.CreateDoubleArray()                    # 시간 의존 결과면 sel.AddDouble(t)
ent = syn.CreateIntegerArray(); val = syn.CreateDoubleArray()
pm.GetScalarData(ds, sel, ent, val)              # 엔티티 ID, 값
pm.GetDataType(ds)                               # "NDDT"(노드) / "ELDT"(요소) 등
syn.Viewer.ShowPlot(plot)
syn.Viewer.SaveImage(r"C:\case\images\fill_time.png")
```
- 플롯 순회: `pm.GetFirstPlot()`, `pm.GetNextPlot(plot)`, `plot.GetName()`. 템플릿 `mf_synergy_post.py --list-plots`가 이름 목록을 `mf_plots.txt`로 쓴다.
- TODO(사내검증): 벡터 결과(변형 벡터) 추출 함수 이름, `SaveImage`의 해상도 지정 변형 함수, 지원 이미지 형식.
- 대표 플롯 이름(영문 UI): `Fill time`, `Pressure at V/P switchover`, `Temperature at flow front`, `Weld lines`, `Air traps`, `Volumetric shrinkage at ejection`, `Clamp force:XY Plot`, `Deflection, all effects:Deflection`. TODO(사내검증): 버전, 해석 종류(Midplane/Dual Domain/3D), 한글 UI에서의 이름.

## 메쉬 엔티티 (필드 추출용)
- `sd.GetFirstNode()`, `sd.GetNextNode(n)`, `sd.GetNodeCoord(n)`(→ `.X .Y .Z`), `sd.GetEntityID(n)`, `sd.GetFirstTri()`, `sd.GetNextTri(t)`, `sd.GetElemNodes(t)`(→ `EntList`, `.Entity(i)`, `.Size`).
- TODO(사내검증): 함수 이름과 반환형 전부. 노드 수가 많으면 COM 왕복 때문에 느리다(10만 노드에 수 분).

## 해석 실행 (COM 대신 배치 권장)
- 템플릿은 COM으로 수정·저장한 뒤 `runstudy.exe`로 해석한다. COM 해석 함수(`StudyDoc.AnalyzeNow` 계열)는 인자와 대기 방식이 버전마다 달라 쓰지 않는다. TODO(사내검증)
- 해석 순서(Fill+Pack+Cool+Warp) 변경, 사출 위치(게이트) 노드 지정, CAD 재임포트·메쉬 생성은 녹화 매크로의 호출을 그대로 옮긴다. 이 문서에 시그니처를 적지 않는다.
