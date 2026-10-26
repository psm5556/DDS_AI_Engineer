# NX Open (Python) CAD API 노트

이 문서는 `scripts/` 템플릿에서 쓰는 호출과 자주 필요한 추가 호출을 정리한다.
- **[확인]**: Journal 기록 결과나 공식 레퍼런스에서 흔히 보이는 호출이다.
- **TODO(사내검증)**: 버전에 따라 이름이나 시그니처가 다를 수 있다. 사용 전에 Journal 기록으로 확인한다.

공식 레퍼런스: Siemens Support Center → NX Open Python Reference (`NXOpen.*` 클래스).
사내 NX 버전과 같은 버전의 문서를 본다.

---

## 0. 세션과 파트

```python
import NXOpen
session = NXOpen.Session.GetSession()                     # [확인]
part, load_status = session.Parts.OpenBaseDisplay(path)   # [확인] Python에서는 튜플 반환
work = session.Parts.Work                                  # [확인] 워크 파트
disp = session.Parts.Display                               # [확인] 디스플레이 파트
# 이미 열린 파트 찾기
for p in session.Parts:                                    # [확인] PartCollection 은 반복 가능
    print(p.Leaf, p.FullPath)
# 저장
work.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.FalseValue)  # [확인]
work.SaveAs(new_full_path)                                 # [확인] PartSaveStatus 반환
# 모두 닫기
session.Parts.CloseAll(NXOpen.BasePart.CloseModified.CloseModified, None)   # [확인]
```

**Load Options**: 어셈블리나 CAE 체인을 케이스 폴더로 복사해서 열 때, 기본 설정(As Saved)으로는 **원본 폴더의 파일**이 열릴 수 있다.
```python
session.Parts.LoadOptions.ComponentLoadMethod = NXOpen.LoadOptions.LoadMethod.FromDirectory
# TODO(사내검증): 열고 난 뒤 session.Parts 를 돌며 FullPath 가 케이스 폴더인지 꼭 확인
```

## 1. Expression (파라미터)

```python
for e in work.Expressions:              # [확인]
    e.Name, e.RightHandSide, e.Type      # Type: "Number", "Integer", "String", "Boolean", ...
    e.Value                              # Number 일 때 숫자값(파트 기본단위가 아닌 expression 단위)
    e.Units                              # NXOpen.Unit 또는 None
expr = work.Expressions.FindObject("rib_height")       # [확인] 없으면 예외 발생 → try/except
work.Expressions.EditWithUnits(expr, expr.Units, "12.5")  # [확인] Journal 기록 형태
expr.RightHandSide = "12.5"                             # [확인] 대안
# 새 expression 만들기
unit_mm = work.UnitCollection.FindObject("MilliMeter")  # [확인]
work.Expressions.CreateWithUnits("thk=2.5", unit_mm)    # [확인] "이름=식" 문자열
```
- 문자열 expression이면 RHS에 따옴표를 넣는다: `'"ABC"'`.
- 수식으로 구동되는 expression(예: `p5 = thk*2`)을 덮어쓰면 수식 관계가 끊긴다. **최상위 독립 변수만** 설계변수로 쓴다.
- Interpart expression(어셈블리에서 다른 파트를 참조하는 식)은 **원천 파트**의 expression을 바꾼다.
- 스프레드시트나 Part Family로 관리되는 모델은 expression을 직접 바꾸면 덮어써질 수 있다. 사내 모델 관리 방식을 확인한다.

## 2. 업데이트와 실패 감지

```python
mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, "update")  # [확인]
n_err = session.UpdateManager.DoUpdate(mark)                                   # [확인] 오류 개수 반환
```
- `n_err > 0`이거나 예외(`NXOpen.NXException`)가 나면 **실패**로 처리한다. results.json을 쓰지 않는다.
- 오류 상세: `session.UpdateManager.ErrorList` → `.Length`, `.GetErrorInfo(i)` 순서로 조회한다. TODO(사내검증) 속성 이름을 확인한다.
- 실패 후 되돌리기: `session.UndoToMark(mark, None)`를 쓴다. TODO(사내검증)
- 업데이트에는 성공했어도 피처가 경고 상태(빨간 노드)일 수 있다. 솔리드 바디 수나 부피가 0이 아닌지도 함께 점검한다. 템플릿은 바디 수를 검사한다.

## 3. 질량특성 / 측정

```python
uc = work.UnitCollection
units = [uc.FindObject("SquareMilliMeter"), uc.FindObject("CubicMilliMeter"),
         uc.FindObject("Kilogram"), uc.FindObject("MilliMeter"), uc.FindObject("Newton")]  # [확인] 면적,부피,질량,길이,무게
bodies = [b for b in work.Bodies if b.IsSolidBody]                                      # [확인]
mb = work.MeasureManager.NewMassProperties(units, 0.99, bodies)                         # [확인]
mb.Mass, mb.Volume, mb.Area, mb.Weight, mb.Centroid.X                                    # [확인]
```
- **밀도 주의**: 재료나 밀도를 지정하지 않은 바디는 NX 기본 밀도로 계산된다. 강철 근사값 7.83e-6 kg/mm³가 쓰이지만 사내 설정에 따라 다를 수 있다. 질량이 설계 목적이면 파트에 재료를 할당했는지 확인하거나, `부피 × 사내 밀도`로 직접 계산한다.
- 거리 측정: `work.MeasureManager.NewDistance(unit, NXOpen.MeasureManager.MeasureType.Minimum, obj1, obj2).Value`를 쓴다. TODO(사내검증) 오버로드를 확인한다. 측정할 객체는 이름(Name 속성)이나 attribute로 찾는 방식이 안정적이다. 예를 들어 GUI에서 면에 이름을 붙이고 바디의 `GetFaces()`에서 `f.Name`으로 찾는다.
- Bounding box: `NXOpen.UF.UFSession.GetUFSession().Modl.AskBoundingBox(body.Tag)`를 쓴다. TODO(사내검증) 반환 형태를 확인한다.

## 4. 내보내기 (DexManager)

STEP:
```python
sc = session.DexManager.CreateStepCreator()                               # [확인]
sc.ExportAs = NXOpen.StepCreator.ExportAsOption.Ap214                      # Ap203/Ap214/Ap242 - TODO(사내검증)
sc.ExportFrom = NXOpen.StepCreator.ExportFromOption.ExistingPart
sc.InputFile = work.FullPath
sc.OutputFile = r"...\export\part.stp"
sc.FileSaveFlag = False
sc.ExportSelectionBlock.SelectionScope = NXOpen.ObjectSelector.Scope.EntirePart
sc.Commit(); sc.Destroy()
```
- `SettingsFile`(ugstep214.def / ugstep242.def)의 기본 경로는 NX 버전마다 다르다. 기록한 저널에 경로가 나오면 그대로 쓴다. TODO(사내검증)
- IGES는 `CreateIgesCreator()`로 같은 패턴을 따른다. TODO(사내검증)

Parasolid (x_t):
```python
ufs = NXOpen.UF.UFSession.GetUFSession()
ufs.Ps.ExportData([b.Tag for b in bodies], r"...\export\part.x_t")   # TODO(사내검증) UF_PS_export_data 래퍼 시그니처
```
대안: File → Export → Parasolid을 Journal로 기록해서 확인한다.

STL: `session.DexManager.CreateStlCreator()`를 쓴다. 속성은 `OutputFile`, `ChordalTol`, `AdjacencyTol`, `ExportSelectionBlock.Add(...)`이다. TODO(사내검증) 대안은 UF_STD STL 함수다.

## 5. 뷰 / 이미지

```python
work.ModelingViews.WorkView.Orient(NXOpen.View.Canned.Isometric, NXOpen.View.ScaleAdjustment.Fit)  # [확인]
work.ModelingViews.WorkView.Fit()                                                                  # [확인]
# Canned: Top, Front, Right, Back, Bottom, Left, Isometric, Trimetric
```
이미지 파일 출력(File → Export → Image 기록 형태):
```python
ui = NXOpen.UI.GetUI()
b = ui.CreateImageExportBuilder()
b.FileFormat = NXOpen.Gateway.ImageExportBuilder.FileFormats.Png
b.FileName = png_path
b.Commit(); b.Destroy()
```
- **run_journal 배치에는 그래픽 창이 없다.** 이미지 출력이 실패하거나 빈 이미지가 나올 수 있다. TODO(사내검증)
  - 대안 1: `NXOpen.UF.UFSession.GetUFSession().Disp.CreateImage(path, format, background)`를 쓴다(UF_DISP_create_image).
  - 대안 2: 대화형 NX에서 Tools → Journal → Play로 같은 저널을 실행한다. 이미지가 꼭 필요한 케이스에만 쓴다.
  - 대안 3: STL로 내보낸 뒤 플랫폼 파이썬(tool `python`)에서 렌더링한다(예: pyvista, matplotlib).
- Studio(Ray Traced) 이미지 캡처는 대화형 세션이 전제다. 배치 자동화에는 권장하지 않는다. TODO(사내검증)
- 이미지 실패는 **결과 실패가 아니다.** 템플릿은 경고만 출력한다.

## 6. 어셈블리 기초

```python
root = work.ComponentAssembly.RootComponent        # [확인] 단품이면 None
for c in root.GetChildren():                         # [확인]
    c.DisplayName, c.Name
    proto = c.Prototype                              # [확인] 컴포넌트의 원본 파트(미로드 시 None 가능)
```
- **컴포넌트 파트의 expression 변경**: `proto.Expressions`에서 FindObject와 EditWithUnits를 호출한 뒤 `session.UpdateManager.DoUpdate(mark)`를 실행한다. 반영이 안 되면 `session.Parts.SetWork(proto)`로 워크 파트를 바꾼 뒤 변경과 업데이트를 하고, `session.Parts.SetWork(work)`로 되돌린다. TODO(사내검증)
- 어셈블리 질량: 각 컴포넌트 바디의 occurrence를 모아 `NewMassProperties`에 넘긴다. `c.FindOccurrence(body)`를 쓴다. TODO(사내검증) 더 간단한 방법은 각 파트 질량을 구해서 합하는 것이다. 단, 같은 파트가 여러 번 들어간 경우 개수만큼 곱해야 한다.
- 컴포넌트 억제(suppress)와 위치 변경은 Journal 기록으로 확인한다. TODO(사내검증)

## 7. Journal 기록으로 API 확인하기 (가장 중요)

1. NX에서 **Developer 탭 → Journal → Record**를 누른다. 버전에 따라 메뉴 → Tools → Journal → Record에 있다. Developer 탭이 안 보이면 File → Preferences → User Interface → Tools → Journal에서 언어를 **Python**으로 설정하고 Developer 리본을 켠다.
2. GUI에서 원하는 작업을 한 번만 한다. 예: expression 편집, STEP 내보내기, 재료 지정.
3. **Stop Recording**을 누른 뒤 생성된 `.py`를 열어 클래스, 메서드, enum 이름을 그대로 가져온다.
4. 기록된 코드에는 `FindObject("...")`처럼 GUI 객체 이름이 하드코딩되어 있다. 템플릿에서는 이름을 인자나 params로 받거나 반복문으로 찾도록 바꾼다.
5. 기록된 `theSession.SetUndoMark(...)` 같은 Undo 관련 줄은 대부분 생략해도 된다.
