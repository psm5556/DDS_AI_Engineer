# CATIA V5 COM 자동화 API 노트 (Python/pywin32)

`scripts/` 템플릿에서 쓰는 호출과 자주 필요한 추가 호출을 정리한다.
- **[확인]**: 매크로 기록이나 `V5Automation.chm`에서 흔히 보이는 호출이다.
- **TODO(사내검증)**: 버전·설정에 따라 다를 수 있다. 사용 전에 매크로 기록으로 확인한다.

공식 레퍼런스: CATIA 설치 폴더 `...\win_b64\code\bin\V5Automation.chm`. 사내 릴리스(R번호)와 같은 문서를 본다.
참고 라이브러리: `pycatia`(pywin32 래퍼, 오픈소스). 사용 여부는 사내 정책을 따른다.

## 0. 연결과 문서
```python
import win32com.client
catia = win32com.client.GetActiveObject("CATIA.Application")  # [확인] 실행 중 세션에 붙기
catia = win32com.client.Dispatch("CATIA.Application")         # [확인] 없으면 새로 시작
catia.Visible = False                                         # [확인]
catia.DisplayFileAlerts = False                               # [확인] 경고 대화상자 억제
catia.RefreshDisplay = False                                  # [확인] 대량 변경 시 화면 갱신 끄기
doc = catia.Documents.Open(r"C:\case\bracket.CATPart")         # [확인]
doc = catia.ActiveDocument                                    # [확인] Live Link
part = doc.Part                                               # [확인] PartDocument 만
doc.Save(); doc.SaveAs(path); doc.Close()                     # [확인]
catia.Quit()                                                  # [확인]
```
- COM 컬렉션 인덱스는 **1부터** 시작한다(`Item(1)`).
- pywin32 late binding에서 ByRef 출력 인자(배열)는 직접 받기 어렵다. `SystemService.Evaluate`로 VBScript를 실행한다(아래 3절).

## 1. 파라미터와 관계식
```python
params = part.Parameters                       # [확인]
p = params.Item("thickness")                   # [확인] 이름 또는 전체 경로
p.Value = 2.5                                  # [확인] Length=mm, Angle=deg
p.ValuateFromString("2.5mm")                   # [확인] 단위 포함 문자열
p.ValueAsString()                              # [확인]
p.ReadOnly; p.Hidden                           # [확인]
p.OptionalRelation                             # TODO(사내검증) 구동 Relation 또는 None
# 사용자 파라미터 생성
params.CreateDimension("thk", "LENGTH", 2.5)   # [확인]
params.CreateReal("ratio", 0.3)                # [확인]
# 관계식
rels = part.Relations                          # [확인]
rels.CreateFormula("f_thk", "", part.Parameters.Item("Pad.1\\FirstLimit\\Length"), "thk*2")  # [확인]
part.Update()                                  # [확인] 실패 시 COM 예외
part.UpdateObject(obj)                         # [확인] 특정 피처만
part.IsUpToDate(obj)                           # TODO(사내검증) 인자로 Part 자신 허용 여부
```
- 디자인 테이블: `rels.Item("DesignTable.1").Configuration = 3`처럼 행을 고른다. TODO(사내검증)
- 파라미터 이름 목록은 `inspect=1`의 `model_info.json`에서 확인한다.

## 2. 형상 접근
```python
body = part.MainBody                   # [확인] PartBody
body.Shapes.Count                      # [확인]
part.Bodies.Item("Body.2")             # [확인]
part.HybridBodies.Item("Geometrical Set.1").HybridShapes  # [확인]
ref = part.CreateReferenceFromObject(body)                # [확인]
```

## 3. 측정
```python
an = doc.Product.Analyze               # [확인] Mass, Volume, WetArea
an.Mass, an.Volume, an.WetArea         # TODO(사내검증) 단위 kg, m^3, m^2
# ByRef 배열 → VBScript 경유
code = """
Public Function dds_cog(analyze)
    Dim c(2)
    analyze.GetGravityCenter c
    dds_cog = c
End Function
"""
cog = catia.SystemService.Evaluate(code, 0, "dds_cog", [an])   # TODO(사내검증) 0 = CATVBScriptLanguage
# GetInertia(9개 배열)도 같은 방식

spa = doc.GetWorkbench("SPAWorkbench")                         # [확인]
meas = spa.GetMeasurable(ref)                                  # [확인]
meas.Volume; meas.Area; meas.Length                            # [확인] 대상 형상에 따라
meas.GetMinimumDistance(ref2)                                  # [확인] 두 형상 최소거리
# GetCOG(arr), GetPoint(arr) 등 배열 출력은 Evaluate 경유
```
- 측정 대상 면·모서리는 GUI에서 Publication(Tools → Publication)으로 이름을 붙여 두고 `part.Publications`나 `CreateReferenceFromName`으로 찾는다. TODO(사내검증)
- 관성 측정(Measure Inertia)을 피처로 저장한 모델은 그 파라미터(`Inertia.1\Mass` 등)를 `Parameters.Item`으로 읽을 수도 있다.

## 4. 내보내기
```python
doc.ExportData(r"C:\case\export\bracket.stp", "stp")   # [확인]
doc.ExportData(r"C:\case\export\bracket.stl", "stl")   # [확인] Part 문서
doc.ExportData(r"C:\case\export\bracket.igs", "igs")   # [확인]
```
- STEP AP·STL 공차는 Tools → Options 설정을 따른다. `catia.SettingControllers.Item("CATSdeStepSettingCtrl")` 등으로 바꿀 수 있으나 컨트롤러 이름과 속성은 버전마다 다르다. TODO(사내검증)

## 5. 이미지
```python
viewer = catia.ActiveWindow.ActiveViewer      # [확인]
viewer.Reframe(); viewer.Update()             # [확인]
vp = viewer.Viewpoint3D                       # [확인]
vp.PutSightDirection([-1, -1, -1])            # TODO(사내검증) 배열 인자 전달
vp.PutUpDirection([-0.408, -0.408, 0.816])
viewer.CaptureToFile(4, r"C:\case\images\iso.bmp")  # [확인] 4=BMP, 5=JPEG (PNG 없음)
catia.StartCommand("Fit All In")              # 명령 이름은 UI 언어에 따라 다르다 → 비권장
```

## 6. 배치 매크로(CNEXT -batch -macro)
- `CNEXT.exe -batch -macro script.CATScript`는 GUI 없이 매크로의 `CATMain()`을 실행한다. 사내 환경에 따라 `-env <환경> -direnv <폴더>`가 필요하다. TODO(사내검증)
- CATScript는 VBScript 계열 BasicScript 문법이다. JSON 파서가 없으므로 템플릿은 `VBScript.RegExp`로 평탄 dict만 읽는다.
- 인자를 넘길 수 없다. 케이스 폴더는 현재 폴더 또는 `DDS_CASE_DIR` 환경변수로 찾는다.
- 오류 시 종료코드가 0일 수 있다. 판정은 results.json 존재 여부로 한다.

## 7. 디버깅 요령
- **Tools → Macro → Start Recording**으로 GUI 작업을 기록한다. 기록된 CATScript/VBA의 객체 경로를 Python으로 옮긴다(`Set x = ...` → `x = ...`, 인자 괄호 추가).
- COM 예외 메시지는 `pywintypes.com_error`의 `excepinfo[2]`에 있다.
