---
name: solidworks-automation
description: SOLIDWORKS 자동화 — COM API(win32com, SldWorks.Application)로 파트/어셈블리 열기(OpenDoc6), 치수(D1@Sketch1)·전역변수(Equations) 변경, ForceRebuild3 재생성 실패 감지, 질량·부피·면적·무게중심·관성 측정, STEP/STL/Parasolid 내보내기, 아이소메트릭 이미지 저장을 한다. 사용자가 SolidWorks 모델 치수나 방정식 변수를 바꾸거나, SolidWorks 형상으로 DOE·최적화 케이스를 만들거나, SLDPRT 질량특성·STEP/STL을 뽑거나, 열어 둔 SolidWorks에서 함께 작업하자고 할 때 사용.
---

# SOLIDWORKS 자동화 (COM API)

## 1. 언제 쓰나 / 전제
- 파라메트릭 SLDPRT/SLDASM의 치수나 전역변수를 바꿔 재생성하고, 질량특성이나 형상 파일을 얻을 때 쓴다. PIDO 흐름의 **형상 단계**다.
- 전제
  - SOLIDWORKS 2018 이상을 권장한다. 설치되고 라이선스가 활성이어야 한다. COM 서버 `SldWorks.Application`은 설치 시 등록된다(버전별 `SldWorks.Application.<n>`도 있다).
  - COM은 **로그인한 대화형 데스크톱 세션**에서만 안정적이다. 서비스나 로그오프 상태 스케줄러에서는 실패한다.
  - 플랫폼 venv 파이썬에 `pywin32`가 있어야 한다. JPG → PNG 변환에 Pillow를 쓴다.
  - 숨김 실행: 스크립트가 새로 띄운 SolidWorks는 `Visible=False`다. 이미지가 필요하면(`image=1`) 창을 띄운다.
  - **좀비 프로세스**: 스크립트가 띄운 SolidWorks만 `ExitApp()`한다. 실패하면 시작 시 새로 생긴 `SLDWORKS.exe` PID만 `taskkill`한다. 사용자 세션은 닫지 않는다. 시작 직후 라이선스·업데이트 대화상자가 뜨면 멈추므로, 처음 한 번은 `visible=1`로 확인한다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `solidworks_com` | `{python} {script} {args}` | pywin32로 SolidWorks COM 제어. 실행 중 세션 attach 가능 |

| 템플릿 | 용도 |
|---|---|
| `scripts/sw_param_update.py` | 케이스 실행. `inspect=1`이면 치수·방정식 목록만 `model_info.json`에 쓴다(**먼저 실행**) |
| `scripts/stl_to_fields.py` | STL → `fields/*.npz` 변환과 STL 기반 형상량(순수 파이썬+numpy) |

인자는 `key=value`다: `part=`, `inspect`, `attach`, `strict`, `length_unit`, `angle_params`, `visible`, `export_step`, `export_stl`, `export_x_t`, `fields`, `stl_scale`, `save_as`, `image`. 모델 파일은 `part=` → `case.json`의 `inputs` → 케이스 폴더의 `*.SLDPRT|*.SLDASM` 1개 순으로 찾는다.

```
run_script(tool="solidworks_com", script=<sw_param_update.py 내용>, filename="sw_param_update.py", args=["part=bracket.SLDPRT", "inspect=1"])
```
스터디 어댑터:
```json
{"tool": "solidworks_com", "script": "scripts/sw_param_update.py",
 "args": ["part=bracket.SLDPRT", "export_step=1", "export_stl=1", "fields=1"],
 "inputs": ["models/bracket.SLDPRT"],
 "outputs": ["mass", "volume_mm3", "cog_z"]}
```
- 케이스를 **병렬로 돌리지 않는다**. `Dispatch`는 실행 중인 SolidWorks 하나에 붙는다. 스터디 병렬도는 1로 둔다.
- 어셈블리는 참조 파트도 `inputs`로 케이스 폴더에 복사한다. 원본 경로의 파트가 열리지 않는지 SolidWorks의 참조 검색 순서를 확인한다. TODO(사내검증)

## 3. 파라미터 변경
1. `inspect=1`로 `model_info.json`을 만든다. `dimensions[].name`(예: `D1@Sketch1`)과 `equations[]`(전역변수)를 보고 설계변수를 사용자와 확정한다.
2. params.json 키 규칙:
   - `@`가 있으면 **치수 전체 이름**이다: `model.Parameter("D1@Sketch1").SystemValue = 값`. SystemValue는 **SI(m, rad)**다. 템플릿은 `length_unit=mm`(기본)이면 /1000 한다. 각도 치수는 `angle_params=D3@Sketch1`로 지정하면 deg → rad로 바꾼다.
   - `@`가 없으면 **전역변수**다: `GetEquationMgr()`에서 `"thk" = ...` 식을 찾아 바꾸고 `EvaluateAll()`한다. 값은 문서 단위로 해석된다.
3. 전역변수 방식을 권장한다. 이름이 안정적이고(피처 이름이 바뀌어도 유지) 사용자가 GUI에서 관리하기 쉽다.
4. 주의
   - 인덱스 있는 속성 쓰기(`Equation(i) = text`)는 pywin32에서 `PROPERTYPUT` 직접 호출(`com_put`)로 한다. TODO(사내검증)
   - `SystemValue`는 **활성 구성**에만 적용될 수 있다. 여러 구성이 있으면 `SetSystemValue3(v, 2, None)`(2 = 모든 구성)을 쓴다. TODO(사내검증)
   - 방정식으로 구동되는 치수를 직접 바꾸면 재생성 때 덮어써진다. 원천 변수를 바꾼다.
   - Design Table이 있는 모델은 테이블이 우선한다.
   - 없는 키는 기본(`strict=1`) 실패다.

## 4. 모델 업데이트와 실패 감지
- `ForceRebuild3(False)`가 False를 돌려주면 실패다.
- 보조: `Extension.GetWhatsWrongCount()`가 0보다 크면 실패다(What's Wrong 목록의 오류·경고 수). TODO(사내검증): 경고만 있는 모델은 정상인데 실패로 판정될 수 있다. 그 경우 이 검사를 경고로 낮춘다.
- `CreateMassProperty` 부피가 0이면(솔리드 바디 없음) 실패다.
- 실패 시 results.json을 쓰지 않는다. 템플릿은 시작할 때 이전 results.json을 지운다.

## 5. 측정(질량특성) → results.json
```json
{"mass": 0.412, "volume_mm3": 52600.0, "area_mm2": 18800.0,
 "cog_x": 0.0, "cog_y": 12.3, "cog_z": 40.1, "ixx": 1.2e-4, "iyy": 1.1e-4, "izz": 2.0e-4,
 "n_params_applied": 2}
```
- `model.Extension.CreateMassProperty()`의 `Mass`[kg], `Volume`[m^3], `SurfaceArea`[m^2], `CenterOfMass`[m]를 mm 단위로 바꿔 쓴다. 관성 `GetMomentOfInertia(0)`는 kg·m^2 그대로다. TODO(사내검증): 인자 0의 의미(무게중심 기준)
- 재료가 없으면 기본 밀도(1000 kg/m^3)로 계산된다. 질량이 목적이면 재료 지정 여부를 확인한다.
- 신버전에는 `CreateMassProperty2`(IMassProperty2)도 있다. 사용 여부는 사내 버전 문서로 확인한다.

## 6. 형상 내보내기(STEP/STL/Parasolid)
- `model.Extension.SaveAs(path, 0, 1|2, None, errors, warnings)`. 확장자(`.step`, `.stl`, `.x_t`)로 포맷이 정해진다. 옵션 2(Copy)로 활성 문서 이름을 유지한다.
  - ByRef 인자(errors, warnings)는 `VARIANT(VT_BYREF|VT_I4)`, ExportData는 `VARIANT(VT_DISPATCH, None)`으로 넘긴다.
- STEP AP214/AP203, STL 해상도·**단위**·바이너리 여부는 시스템 옵션(Tools → Options → Export)을 따른다. 코드로 바꾸려면 `sw.SetUserPreferenceIntegerValue(swExportStlUnits...)` 같은 enum이 필요하다. TODO(사내검증)
- **3D 형상 학습**: `fields=1`이면 STL을 `stl_to_fields.py`로 `fields/shape.npz`(`points`, `triangles`, `values`)로 바꾼다. 플랫폼이 이를 업로드해 설계변수 → 3D 형상 모델을 학습한다(규약 1-1).
  - `values`는 기본 0이다(형상만 학습). `--values dist_centroid|x|y|z`로 기하량을 넣을 수 있다.
  - STL 형상량 `stl_volume`, `stl_area`, `stl_cx..`, `stl_bbox_*`가 results.json에 추가된다. `stl_volume`이 `volume_mm3`와 1e9배 다르면 STL이 m 단위다. `stl_scale=1000`을 준다.

## 7. 이미지 저장 → images/
- `image=1`: `ShowNamedView2("*Isometric", 7)` → `ViewZoomtofit2()` → `Extension.SaveAs("iso.png")`를 시도한다. 실패하면 `.jpg`로 저장하고 Pillow로 PNG로 바꾼다. TODO(사내검증): 버전별 PNG 지원, 숨김 창에서 렌더링 여부
- 실패해도 케이스는 성공으로 둔다.

## 8. Live Link (열어 둔 SolidWorks와 함께 모델링)
- COM은 실행 중인 SolidWorks에 붙는다(`GetActiveObject("SldWorks.Application")`). 사용자가 보는 화면에서 에이전트가 치수를 바꾸고 재생성 결과를 바로 보여 줄 수 있다.
- `attach=1`이면 **활성 문서**(`sw.ActiveDoc`)에 작업한다. 문서를 열거나 닫지 않고, Save As도 하지 않는다. 저장은 사용자가 결정한다.
- 진행은 `live-modeling` 스킬 원칙을 따른다. 한 번에 한두 개 변수만 바꾸고, `inspect=1`로 이름을 먼저 확인하고, 매 단계 화면으로 확인받는다. dds-live MCP에는 아직 SolidWorks 어댑터가 없어 `run_script(tool="solidworks_com", args=["attach=1", ...])`로 실행한다. TODO
- 모델이 정리되면 설계변수를 전역변수로 옮기고, 같은 스크립트를 `attach` 없이 스터디 어댑터에 연결한다.
- 사용자가 스케치·피처 편집 중이면 API 호출이 실패하거나 편집이 취소된다. 실행 전에 편집을 끝내 달라고 요청한다.

## 9. 흔한 오류와 대처
| 증상 | 원인/대처 |
|---|---|
| `Invalid class string` | SolidWorks 미설치·COM 미등록. 설치 복구 또는 버전별 ProgID 사용 |
| OpenDoc6가 None, errors=2 | 파일 없음(swFileNotFoundError). 경로·확장자 확인. 다른 값은 swFileLoadError_e 참조 |
| `Parameter(...)`가 None | 치수 이름 불일치. `inspect=1`의 `name`을 그대로 쓴다(`@파트이름` 접미어 없이) |
| 값을 바꿨는데 형상이 그대로 | 방정식이나 Design Table이 덮어씀, 또는 다른 구성 활성 |
| 스크립트가 멈춤 | 모달 대화상자(라이선스, 재생성 오류, 누락 참조). `visible=1`로 확인 |
| 케이스끼리 결과가 섞임 | 병렬 실행. 스터디 병렬도 1 |
| SLDWORKS.exe가 남음 | ExitApp 실패. 사용자 동의 후 작업 관리자에서 정리 |
- **모르는 API는 지어내지 않는다.** **Tools → Macro → Record**로 GUI 작업을 VBA로 기록하고 메서드 이름을 가져온다. 공식 문서는 SOLIDWORKS API Help(`help.solidworks.com` → API)다.

## 10. SOLIDWORKS Simulation (TODO)
- Simulation API는 애드인 객체로 접근한다: `sw.GetAddInObject("SldWorks.Simulation")` → `.CosmosWorks` → `ActiveDoc.StudyManager` → 스터디 실행·결과 조회. TODO(사내검증): ProgID, 라이선스(Simulation Standard/Pro), 결과 단위. 이 스킬 범위 밖이며 필요하면 별도 스크립트로 만든다.
