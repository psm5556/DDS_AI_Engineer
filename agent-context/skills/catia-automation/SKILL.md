---
name: catia-automation
description: Dassault CATIA V5 자동화 — COM(win32com, CATIA.Application)이나 배치 매크로(CNEXT -batch -macro)로 CATPart 열기, 파라미터(Parameters) 변경, Part.Update 실패 감지, 질량·부피·면적·무게중심 측정, STEP/STL 내보내기, Save As, 뷰 캡처를 한다. 사용자가 CATIA 모델 치수·파라미터 변경, CATIA 형상으로 DOE·최적화 케이스 생성, CATPart 질량특성·STEP/STL 추출, 열어 둔 CATIA 화면에서 작업을 요청할 때 사용.
---

# CATIA V5 자동화 (COM / 배치 매크로)

## 1. 언제 쓰나 / 전제
- 파라메트릭 CATPart의 파라미터를 바꿔 형상을 재생성하고, 질량특성이나 형상 파일(STEP/STL)을 얻을 때 쓴다. PIDO 흐름의 **형상 단계**다.
- 전제
  - CATIA V5 R2x(V5-6R20xx)가 설치되고 라이선스가 잡혀 있어야 한다. COM 서버(`CATIA.Application`)는 설치 시 등록된다. 등록이 안 됐으면 관리자 권한으로 `CNEXT.exe /regserver`를 실행한다. TODO(사내검증)
  - COM은 **로그인한 대화형 데스크톱 세션**에서만 안정적으로 동작한다. 서비스·작업 스케줄러(로그오프 상태)에서는 실패한다.
  - 플랫폼 venv 파이썬에 `pywin32`가 있어야 한다. 이미지 PNG 변환에 Pillow를 쓴다(matplotlib 의존성으로 설치돼 있음).
  - 숨김 실행: 스크립트가 새로 띄운 CATIA는 `Visible=False`로 둔다. 단, 뷰 캡처는 창이 보여야 신뢰할 수 있어 `image=1`이면 창을 띄운다.
  - **좀비 프로세스**: 스크립트가 띄운 CATIA만 `Quit()`한다. Quit이 실패하면 시작 시점에 새로 생긴 `CNEXT.exe` PID만 `taskkill`한다. 사용자가 쓰던 CATIA는 절대 종료하지 않는다. 남은 프로세스는 작업 관리자에서 확인하고 사용자 동의 후 정리한다.
- 3DEXPERIENCE(CATIA V6)는 다루지 않는다. TODO: 3DEXPERIENCE는 `CATIA.Application` COM 대신 플랫폼 세션·PLM 객체 기반이라 별도 스킬이 필요하다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `catia_com` | `{python} {script} {args}` | **기본**. pywin32로 CATIA COM 제어. 실행 중 세션 attach 가능 |
| `catia_batch` | `{catia} -batch -macro {script}` | CATScript 매크로를 GUI 없이 실행. 인자 전달 불가 |

경로 키 `catia`(기본 `CNEXT`)는 `agent.toml [tools]`에 `...\B28\win_b64\code\bin\CNEXT.exe`처럼 지정한다.

| 템플릿 | 용도 |
|---|---|
| `scripts/catia_param_update.py` | 케이스 실행. `inspect=1`이면 파라미터 목록만 `model_info.json`에 쓴다(**먼저 실행**) |
| `scripts/catia_batch_case.CATScript` | 배치 매크로 대안. params.json → 파라미터 → Update → 측정 → STEP → results.json |
| `scripts/stl_to_fields.py` | STL → `fields/*.npz` 변환과 STL 기반 형상량(순수 파이썬+numpy) |

인자는 `key=value`다: `part=`, `inspect`, `attach`, `strict`, `visible`, `export_step`, `export_stl`, `fields`, `save_as`, `image`. 모델 파일은 `part=` → `case.json`의 `inputs` → 케이스 폴더의 `*.CATPart` 1개 순으로 찾는다.

단발 실행:
```
run_script(tool="catia_com", script=<catia_param_update.py 내용>, filename="catia_param_update.py", args=["part=bracket.CATPart", "inspect=1"])
```
스터디 어댑터:
```json
{"tool": "catia_com", "script": "scripts/catia_param_update.py",
 "args": ["part=bracket.CATPart", "export_stl=1", "fields=1"],
 "inputs": ["models/bracket.CATPart"],
 "outputs": ["mass", "volume_mm3", "cog_z"]}
```
배치 매크로(인자가 없으므로 케이스 폴더에 CATPart가 하나만 있어야 한다):
```json
{"tool": "catia_batch", "script": "scripts/catia_batch_case.CATScript",
 "inputs": ["models/bracket.CATPart"], "outputs": ["mass", "volume_mm3"]}
```
- 케이스를 **병렬로 돌리지 않는다**. COM `Dispatch`는 실행 중인 CATIA 하나에 붙으므로 케이스끼리 문서가 섞인다. 스터디 병렬도는 1로 둔다.
- `catia_batch`는 사내 환경에 따라 `-env <환경명> -direnv <폴더>`가 필요할 수 있다. 이 경우 `agent.toml [tool_commands]`에서 명령을 덮어쓴다. TODO(사내검증)

## 3. 파라미터 변경
1. `inspect=1`로 `model_info.json`을 만든다. `name`, `value`, `value_str`, `readonly`를 보고 설계변수를 사용자와 확정한다.
2. **params.json 키는 파라미터 이름과 똑같이** 쓴다. `Parameters.Item(name)`은 트리 표시 이름과 전체 경로(`PartBody\Pad.1\FirstLimit\Length`)를 모두 받는다. 사용자 파라미터(Knowledge의 `f(x)`로 만든 것)를 쓰는 것이 가장 안전하다.
3. 값의 단위: `Dimension.Value` 규약대로 **길이 mm, 각도 deg**다. 단위를 문자열로 주려면 `p.ValuateFromString("12.5mm")`를 쓴다.
4. 처리 순서: `Parameters.Item(name)` → `p.Value = v` → `Part.Update()`.
   - 없는 키는 기본(`strict=1`) 실패다. DOE 값이 조용히 무시되는 사고를 막는다.
   - Relation(수식·룰·디자인 테이블)으로 구동되는 파라미터는 Update 때 덮어써진다. 템플릿은 `OptionalRelation`으로 경고한다. TODO(사내검증)
   - 디자인 테이블이 걸린 모델은 테이블 행 선택이 우선한다. 사내 모델 관리 방식을 먼저 확인한다.

## 4. 모델 업데이트와 실패 감지
- `Part.Update()`가 업데이트 오류를 **COM 예외**로 던진다. 템플릿은 이를 실패로 처리하고 results.json을 쓰지 않는다. 형상이 성립하지 않는 DOE 점은 failed 케이스로 남는 것이 정상이다.
- 보조 판정: `Part.IsUpToDate(part)`가 False이거나 `MainBody.Shapes.Count == 0`이면 실패다. TODO(사내검증): `IsUpToDate` 인자 형식
- `DisplayFileAlerts = False`로 업데이트 오류 대화상자에 멈추지 않게 한다. 그래도 멈추면 창을 띄우고(`visible=1`) 어떤 대화상자인지 확인한다.
- 템플릿은 시작할 때 이전 results.json을 지운다.

## 5. 측정(질량특성) → results.json
기본 출력:
```json
{"mass": 0.412, "volume_mm3": 52600.0, "area_mm2": 18800.0,
 "cog_x": 0.0, "cog_y": 12.3, "cog_z": 40.1, "ixx": 1.2e-4, "iyy": 1.1e-4, "izz": 2.0e-4,
 "n_params_applied": 2}
```
- `doc.Product.Analyze`의 `Mass`, `Volume`, `WetArea`를 쓴다. PartDocument에도 `.Product`가 있다.
- 무게중심(`GetGravityCenter`)과 관성(`GetInertia`)은 **ByRef 배열 인자**라 pywin32로 직접 부르기 어렵다. 템플릿은 `CATIA.SystemService.Evaluate`로 VBScript 함수를 실행해 배열을 받는다(pycatia와 같은 방식). TODO(사내검증): 언어 enum 0 = VBScript
- TODO(사내검증) **단위**: Mass kg, Volume m^3, WetArea m^2, GravityCenter mm, Inertia kg·m^2로 가정하고 mm 단위로 바꿔 쓴다. 10x10x10 mm 블록으로 한 번 대조한다. `fields=1`이면 STL에서 구한 `stl_volume`(mm^3)과도 비교된다.
- 재료가 없으면 기본 밀도(1000 kg/m^3)로 계산된다. 질량이 목적이면 재료 적용 여부를 사용자에게 확인한다.
- 대안(부피·면적): `spa = doc.GetWorkbench("SPAWorkbench")` → `ref = part.CreateReferenceFromObject(part.MainBody)` → `meas = spa.GetMeasurable(ref)` → `meas.Volume`, `meas.Area`. 거리·각도 측정도 Measurable로 한다(references 참고).

## 6. 형상 내보내기(STEP/STL)
- `doc.ExportData(path, "stp")`, `doc.ExportData(path, "stl")`. 확장자 문자열로 포맷을 고른다. 결과 파일이 생기지 않으면 실패로 처리한다.
- STEP AP214/AP242, STL 공차는 **Tools → Options → General → Compatibility**의 설정을 따른다. 배치에서 바꾸려면 `SettingControllers`를 써야 하며 버전마다 다르다. TODO(사내검증)
- **3D 형상 학습**: `fields=1`이면 STL을 `stl_to_fields.py`로 `fields/shape.npz`(`points`, `triangles`, `values`)로 바꾼다. 플랫폼이 이를 업로드해 설계변수 → 3D 형상 모델을 학습한다(규약 1-1).
  - `values`는 기본 0이다. 형상만 학습할 때 쓴다. `--values dist_centroid|x|y|z`로 기하량을 넣을 수 있다.
  - STL은 케이스마다 삼각형 수가 달라진다. 서버가 보간하지만, 위상이 바뀌는 큰 변화에는 맞지 않는다.
  - STL 형상량 `stl_volume`, `stl_area`, `stl_cx..`, `stl_bbox_*`가 results.json에 추가된다(교차검증용).

## 7. 이미지 저장 → images/
- `image=1`: `ActiveWindow.ActiveViewer`에서 시선 방향을 아이소메트릭으로 두고 `Reframe()` 후 `CaptureToFile(4, bmp)`(4 = BMP)로 받는다. Pillow로 `images/iso.png`로 바꾼다. `CaptureToFile`은 PNG를 지원하지 않는다.
- 숨김 창이나 배치 모드에서는 캡처가 비거나 실패할 수 있다. 실패해도 케이스는 성공으로 둔다. TODO(사내검증): `Viewpoint3D.PutSightDirection` 동작

## 8. Live Link (열어 둔 CATIA와 함께 모델링)
- COM은 실행 중인 CATIA에 붙는다(`GetActiveObject("CATIA.Application")`). 그래서 사용자가 보고 있는 화면에서 에이전트가 파라미터를 바꾸고 결과를 보여 줄 수 있다.
- `attach=1`로 실행하면 파일을 열지 않고 **활성 문서**에 작업한다. 문서를 닫거나 CATIA를 종료하지 않는다. Save As도 하지 않는다.
- 진행은 `live-modeling` 스킬 원칙을 따른다. 작은 단계로 실행하고, 매 단계 `image=1` 캡처나 사용자 화면으로 확인받는다. dds-live MCP에는 아직 CATIA 어댑터가 없다. 지금은 `run_script(tool="catia_com", args=["attach=1", ...])`로 단계별로 실행한다. TODO
- 모델이 완성되면 설계변수를 사용자 파라미터로 정리하고, 같은 스크립트를 `attach` 없이 스터디 어댑터에 연결한다.
- 사용자가 편집 중일 때(스케치 편집 모드 등) COM 호출은 막히거나 실패한다. 실행 전에 편집 모드를 끝내 달라고 요청한다.

## 9. 흔한 오류와 대처
| 증상 | 원인/대처 |
|---|---|
| `Invalid class string` / `-2147221005` | COM 미등록. `CNEXT.exe /regserver`(관리자). TODO(사내검증) |
| `Parameters.Item` 예외 | 이름 불일치. `inspect=1` 결과의 `name`을 그대로 쓴다 |
| Update 예외인데 GUI에선 정상 | 다른 문서가 활성. 템플릿은 `doc.Part`를 직접 쓴다. 링크된 외부 참조(Publication) 파일이 케이스 폴더에 있는지 확인한다 |
| 스크립트가 멈춤 | 대화상자 대기. `visible=1`로 확인, `DisplayFileAlerts=False` 유지 |
| 케이스끼리 결과가 섞임 | 병렬 실행. 스터디 병렬도 1 |
| CNEXT.exe가 계속 남음 | Quit 실패. 템플릿의 PID 정리 로그 확인, 필요 시 사용자 동의 후 `taskkill /IM CNEXT.exe /F` |
| COG/관성만 실패 | `SystemService.Evaluate` 제약(보안 설정). 경고만 남고 나머지 결과는 저장된다 |
- **모르는 API는 지어내지 않는다.** CATIA **Tools → Macro → Start Recording**(언어 CATScript/VBA)으로 같은 작업을 GUI에서 한 번 하고, 기록된 매크로의 메서드 이름을 가져온다. 공식 문서: `V5Automation.chm`(설치 폴더 `win_b64\code\bin`).

상세 API: [references/catia-com-api.md](references/catia-com-api.md)
