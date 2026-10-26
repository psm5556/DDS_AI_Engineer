---
name: nx-cad-automation
description: Siemens NX(CAD) 배치 자동화 — NX Open Python 저널(run_journal)로 파트/어셈블리 열기, expression(설계변수) 조회·변경, 모델 업데이트와 실패 감지, 질량·부피·면적·무게중심 측정, STEP/Parasolid/STL 내보내기, Save As, 뷰 이미지 저장. 사용자가 NX 모델의 치수/파라미터를 바꾸거나, NX 형상으로 DOE·최적화 케이스를 만들거나, NX에서 질량특성·형상 파일을 뽑아달라고 할 때 사용. 해석(Simcenter 3D .sim/.fem)까지 필요하면 simcenter3d-automation 스킬을 함께 사용.
---

# NX CAD 자동화 (NX Open Python 저널)

## 1. 언제 쓰나 / 전제
- 파라메트릭 NX 모델(.prt)의 expression을 바꿔 형상을 재생성하고, 질량특성이나 형상 파일(STEP 등)을 얻을 때 쓴다.
- PIDO 흐름에서 **형상 단계**를 맡는다. 해석까지 이어지면 `simcenter3d-automation`을 쓴다. Simcenter 저널이 마스터 .prt의 expression도 함께 바꾼다.
- 전제
  - NX 1847 이상(NX Series)을 권장한다. 이 버전들에는 NX 내장 **Python 3**가 들어 있어 템플릿을 그대로 쓸 수 있다. NX 12 이하도 Python 3이지만 버전별 차이는 Journal 기록으로 확인한다.
  - 템플릿은 f-string을 쓰지 않는다(규약). NX Open 외에 별도 pip 패키지가 필요하지 않다.
  - run_journal 실행에도 NX 라이선스가 필요하다. 일부 API는 별도 모듈 라이선스가 필요할 수 있다.

## 2. 실행 방법
- tool 키: **`nx`**
- 명령 템플릿: `{nx_run_journal} {script} -args {args}`. run_journal.exe는 보통 `%UGII_BASE_DIR%\NXBIN\run_journal.exe`에 있다.
- 인자는 **`key=value`** 형식으로 넘긴다. 저널은 `sys.argv` 중 `=`가 있는 토큰만 읽는다.
- 스크립트는 **케이스 폴더(cwd)** 에서 실행된다. 입력 .prt는 스터디 어댑터의 `inputs`로 케이스 폴더에 복사된다.

| 템플릿 | 용도 |
|---|---|
| `scripts/nx_inspect_part.py` | **먼저 실행**한다. expression 목록, 바디 수, 어셈블리 트리를 `model_info.json`에 기록한다. |
| `scripts/nx_param_update.py` | 케이스 실행을 맡는다. params를 expression에 반영하고 업데이트한 뒤, 질량특성, 선택적 STEP/STL·Save As·이미지 저장을 거쳐 results.json을 쓴다. |

단발 실행(run_script)은 이렇게 한다.
```
run_script(tool="nx", script=<nx_inspect_part.py 내용>, filename="nx_inspect_part.py", args=["part=bracket.prt"])
```
스터디 어댑터(DOE와 최적화) 설정 예시다.
```json
{"tool": "nx", "script": "scripts/nx_param_update.py",
 "args": ["part=bracket.prt", "export_step=1"],
 "inputs": ["models/bracket.prt"],
 "outputs": ["mass_kg", "volume_mm3", "cog_z_mm"]}
```

## 3. 파라미터(형상) 변경
1. `nx_inspect_part.py`로 expression 목록을 뽑는다. `auto_named=false`(사용자가 이름 붙인 것)이면서 RHS가 숫자 상수인 항목이 설계변수 후보다.
2. 사용자와 설계변수를 확정하고, **params.json 키를 expression 이름과 똑같이** 맞춘다. 대소문자도 구분한다. 값은 그 expression의 단위로 해석된다(mm 등).
3. `nx_param_update.py`가 `Expressions.FindObject` → `EditWithUnits(expr, expr.Units, "값")` → `UpdateManager.DoUpdate(mark)` 순서로 처리한다.
   - 파트에 없는 키가 있으면 기본(`strict=1`)은 실패다. DOE 값이 조용히 무시되는 사고를 막기 위해서다.
   - 수식으로 구동되는 expression을 덮어쓰면 경고를 출력한다. 수식 관계가 끊기기 때문이다.
4. **업데이트 실패 판정**: `DoUpdate` 반환값(오류 수)이 0보다 크거나 예외가 나면 실패로 처리한다. 솔리드 바디가 0개여도 실패다. 실패 시 results.json을 쓰지 않는다. 이는 DOE에서 형상이 성립하지 않는 점을 failed 케이스로 남기는 정상 동작이다.

## 4. 해석조건 변경
CAD 스킬 범위 밖이다. 재료, 하중, 메쉬, 솔버는 `simcenter3d-automation`을 쓴다. CAD에서는 **재료 밀도**만 질량 결과에 영향을 준다. 질량이 목적이면 파트에 재료를 할당했는지 사용자에게 확인한다(아래 6절).

## 5. 실행과 완료/실패 판정
- 성공: `results.json`이 생성된다.
- 실패: 예외가 나면 traceback을 표준출력(run.log)에 남기고 results.json을 쓰지 않는다. 템플릿은 시작할 때 **이전 results.json을 삭제**해서 오래된 결과를 성공으로 오인하지 않게 한다.
- `sys.exit(1)`이 run_journal의 종료코드로 전달되는지는 TODO(사내검증)다. 전달되지 않아도 results.json이 없으므로 failed로 판정된다.

## 6. 결과 추출 → results.json
`nx_param_update.py` 기본 출력:
```json
{"mass_kg": 0.412, "volume_mm3": 52600.0, "area_mm2": 18800.0,
 "cog_x_mm": 0.0, "cog_y_mm": 12.3, "cog_z_mm": 40.1,
 "n_solid_bodies": 1, "n_params_applied": 2}
```
- 단위는 키 이름에 붙인다. 측정 단위를 코드에서 명시하므로 inch 파트여도 mm/kg로 나온다.
- **밀도 미지정 바디는 NX 기본 밀도**로 계산된다. 질량값이 맞는지 사용자 모델로 한 번 비교한다.
- 거리, 각도, bounding box 같은 추가 측정은 `references/nxopen-cad-api.md` 3절을 참고한다. 측정 대상 면이나 모서리는 GUI에서 **이름(Name)을 붙여 두고** 코드에서 이름으로 찾게 한다.

## 7. 이미지 저장
- `image=1`이면 아이소메트릭 뷰로 맞춘 뒤 `images/iso.png` 저장을 시도한다.
- **run_journal 배치에는 그래픽 창이 없어 이미지가 실패할 수 있다.** 템플릿은 실패해도 케이스를 성공으로 둔다. 대안은 reference 5절에 있다: UF Disp.CreateImage, 대화형 NX에서 저널 재생, STL을 내보내 외부 렌더링. TODO(사내검증)

## 8. 자주 하는 실수와 디버깅
- **원본 파일 수정 사고**: 어셈블리나 참조 파트가 Load Options(As Saved) 때문에 원본 폴더에서 열린다. 템플릿은 `LoadMethod.FromDirectory`를 설정한다. 그래도 `model_info.json`의 `loaded_parts` 경로를 확인한다.
- 원본 이름으로 `Save`하지 않는다. 템플릿은 `<이름>_case.prt`로 `SaveAs`한다.
- `FindObject`는 객체가 없으면 None을 반환하지 않고 **예외**를 던진다. 반드시 try/except로 감싼다.
- `OpenBaseDisplay`는 Python에서 `(part, load_status)` **튜플**을 반환한다.
- 파트가 이미 열린 세션에서 같은 파일을 다시 열면 오류가 난다. 저널 끝에서 `CloseAll`을 호출한다(템플릿 finally).
- 로그: 플랫폼 `run.log`에 `[DDS]` 접두어 메시지와 traceback이 남는다. NX 시스템 로그(syslog)는 NX 세션 로그 위치에 있다(Help → Log File).
- **모르는 API는 지어내지 않는다.** NX에서 **Developer → Journal → Record**로 같은 작업을 GUI에서 한 번 하고, 기록된 .py에서 메서드와 enum 이름을 그대로 가져온다(reference 7절). 사용자에게 기록 파일을 요청하는 것도 좋은 방법이다.

## 9. 사내 검증 필요 항목 (TODO(사내검증))
- `UpdateManager.ErrorList` / `GetErrorInfo` 속성 이름
- `LoadOptions.ComponentLoadMethod = LoadMethod.FromDirectory` enum 이름, 어셈블리 컴포넌트 로드 경로
- `StepCreator.ExportAs` 옵션(Ap214/Ap242)과 `SettingsFile` 기본 경로
- `CreateStlCreator` 속성 이름과 `ExportSelectionBlock.Add` 시그니처
- Parasolid 내보내기(`UFSession.Ps.ExportData`) 시그니처
- 배치(run_journal)에서 이미지 저장 가능 여부: `UI.CreateImageExportBuilder`, `UF Disp.CreateImage` enum 경로
- 어셈블리 질량(occurrence 수집)과 컴포넌트 파트 expression 변경 시 SetWork 필요 여부
- run_journal 종료코드 전달 여부, `sys.argv[0]` 내용

상세 API: [references/nxopen-cad-api.md](references/nxopen-cad-api.md)
