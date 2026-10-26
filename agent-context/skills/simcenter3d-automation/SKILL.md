---
name: simcenter3d-automation
description: Simcenter 3D(NX CAE Pre/Post) + Simcenter Nastran 배치 자동화 — .sim 열기(sim→fem→idealized→prt 체인), CAD expression 변경 후 Idealized/FEM 업데이트·재메쉬, 재료·물리속성·하중·구속·솔버 설정 변경, 해석 실행과 f06 완료/실패 판정, op2(pyNastran) 결과 추출(최대 변위·von Mises·반력·고유진동수), 컨투어 이미지. 사용자가 Simcenter 3D/NX Nastran 해석 자동화, NX 형상 파라미터 기반 구조해석 DOE·최적화, .sim/.op2/.f06 결과 추출을 요청할 때 사용. 형상만 다루면 nx-cad-automation 사용.
---

# Simcenter 3D 자동화 (NX Open CAE + Simcenter Nastran)

## 1. 언제 쓰나 / 전제
- NX 파라메트릭 형상을 바꿔 가며 구조해석을 반복할 때 쓴다(PIDO: CAD 파라미터 → FEM 업데이트 → 해석 → 결과 추출).
- 이미 해석된 `.op2`/`.f06`에서 결과만 뽑을 때도 쓴다(`sc_run_case.py --extract-only`).
- 전제
  - NX/Simcenter 3D 1847 이상과 Simcenter Nastran(NX Nastran)이 필요하다. NX 내장 **Python 3**로 저널을 실행한다.
  - 결과 추출은 **플랫폼 venv 파이썬 + `pyNastran`**(1.4.x로 API 확인)과 numpy를 쓴다. NX 내장 파이썬에는 pyNastran이 없다.
  - 케이스 폴더에 **체인 파일 4개를 모두** 복사한다: `.prt`, `_fem1_i.prt`, `_fem1.fem`, `_sim1.sim`. 파일 이름은 바꾸지 않는다.

## 2. 실행 방법

| 템플릿 | 실행 위치(tool) | 용도 |
|---|---|---|
| `scripts/sc_inspect_sim.py` | `simcenter` | **먼저 실행**한다. 로드된 파트 경로, 파트별 expression, 솔루션·하중·구속·물리속성·재료 이름을 `model_info.json`에 기록한다. |
| `scripts/sc_update_solve.py` | `simcenter` | 케이스 저널이다. params 반영 → Idealized/FEM 업데이트 → (하중 변경) → Solve → f06 판정 → `solve_status.json` |
| `scripts/sc_run_case.py` | `python` | **스터디용 드라이버**다. run_journal로 위 저널을 실행하고 op2/f06에서 추출한 결과로 `results.json`을 만든다. `--extract-only` 모드도 있다. |

- `simcenter` 명령 템플릿은 `{nx_run_journal} {script} -args {args}`이고, 인자는 `key=value` 형식이다.
- **스터디(DOE/최적화)에는 `python` + `sc_run_case.py`를 권장**한다. 해석과 추출을 한 케이스 안에서 끝내야 results.json이 나오기 때문이다.
```json
{"tool": "python", "script": "scripts/sc_run_case.py",
 "args": ["--run-journal", "C:/Program Files/Siemens/NX2306/NXBIN/run_journal.exe",
          "--journal", "C:/.../scripts/sc_update_solve.py",
          "--sim", "bracket_sim1.sim", "solution=Solution 1"],
 "inputs": ["models/bracket.prt", "models/bracket_fem1_i.prt", "models/bracket_fem1.fem", "models/bracket_sim1.sim"],
 "outputs": ["max_von_mises", "max_disp_mm", "freq_1"]}
```
  - run_journal 경로는 `--run-journal`, 환경변수 `NX_RUN_JOURNAL`, `%UGII_BASE_DIR%\NXBIN\run_journal.exe` 순으로 찾는다.
  - `--journal`을 생략하면 `sc_run_case.py`와 같은 폴더의 `sc_update_solve.py`를 쓴다.
  - 알 수 없는 `key=value` 인자는 그대로 저널에 전달된다. 예: `solution=...`, `save=1`, `strict=0`.
- tool `simcenter` 하나로 끝내려면 저널에 `extract_python=<pyNastran이 있는 python.exe> extract_script=<sc_run_case.py 경로>`를 넘긴다. 저널이 해석 뒤에 추출을 호출한다.

## 3. 파라미터(형상) 변경 — CAD→FEM 연관 워크플로
1. `sc_inspect_sim.py`로 각 파트(prt/fem/sim)의 expression을 확인한다. 사용자와 설계변수를 정한 뒤 **params.json 키를 expression 이름과 똑같이** 맞춘다.
2. 저널의 라우팅 순서는 `BC_PARAM_MAP` → 마스터 CAD(.prt) → SIM → FEM이다. 어디에도 없는 키가 있으면 기본(`strict=1`) 동작은 실패다.
3. 처리 순서:
   1) .prt expression을 바꾸고 `UpdateManager.DoUpdate`를 실행한다. 오류가 있으면 실패다.
   2) Idealized(_i.prt)를 표시하고 `DoUpdate`를 실행한다(WAVE 링크 갱신).
   3) .fem을 표시하고 `BaseFEModel.UpdateFemodel()`을 실행한다. 이 단계에서 **재메쉬**가 되고 경계조건이 재연결된다.
   4) .sim을 표시하고 하중·구속을 반영한 뒤 해석한다.
- 하중과 구속은 **형상(면/모서리)에 걸어야** 형상이 바뀌어도 유지된다. 절점에 직접 건 조건은 재메쉬 후 깨진다.
- 메쉬 크기, 쉘 두께, 하중 크기는 **GUI에서 필드에 expression을 넣어 두는 방식**을 가장 먼저 권한다. 이렇게 하면 API 문자열 추측 없이 expression만 바꿔 제어할 수 있다. 필드 지원 여부는 TODO(사내검증)다.

## 4. 해석조건 변경 (재료, 물리속성, 하중, 구속, 메쉬, 솔버)
- **하중·구속 크기**: 저널의 `BC_PARAM_MAP`에 `"force_n": ("Force(1)", "Magnitude", "Newton")`을 넣는다. 그러면 `CreateBcBuilderForBc` → `PropertyTable` 경로로 크기를 바꾼다. 속성 이름은 TODO(사내검증)다.
- **재료, 쉘 두께, 메쉬 크기, 솔버 옵션, 출력 요청**: 이 API들은 문자열 속성 이름에 의존한다. `references/simcenter-cae-api.md` 4~7절에 후보 코드가 있지만, **반드시 Journal 기록으로 확인한 뒤** 템플릿에 추가한다.
- 출력 요청(변위, 응력 von Mises, SPC 반력 → OP2)은 GUI에서 한 번 설정해 두고 저장한 모델을 입력으로 쓴다.

## 5. 해석 실행과 완료/실패 판정
- `SimSolveManager.GetSimSolveManager(session).SolveChainOfSolutions([sol], SolveOption.Solve, SetupCheckOption.CompleteCheckAndOutputErrors, SolveMode.Foreground)`로 실행한다. Foreground이므로 해석이 끝날 때까지 기다린다.
- 판정 기준: 반환값 `failed == 0`, `solved >= 1`, f06에 `FATAL` 없음, f06에 `END OF JOB` 있음, op2 존재. 하나라도 어긋나면 실패다.
- 결과 파일은 .sim과 같은 폴더에 `<sim이름>-<솔루션명 소문자_밑줄>.f06/.op2`로 생긴다. 못 찾으면 해석 이후 생긴 최신 f06을 쓴다.
- 실패 원인은 f06의 `USER FATAL MESSAGE` 번호와 `.log`에서 찾는다(`references/nastran-results.md`).

## 6. 결과 추출 → results.json
`sc_run_case.py`(pyNastran)의 기본 키는 다음과 같다. 해당 결과가 op2에 있을 때만 나온다.

| 키 | 의미 |
|---|---|
| `max_disp_mm` | 최대 변위 크기(모델 단위계) |
| `max_von_mises`, `max_von_mises_ctetra` 등 | 요소 von Mises 최대값. **비평균** 요소값이므로 NX 컨투어(절점 평균)보다 클 수 있다. |
| `reaction_fx/fy/fz` | SPC 반력 합계(첫 subcase) |
| `freq_1..freq_n` | 고유진동수 [Hz] (`--n-modes`) |

- 특정 위치나 그룹의 값, subcase별 값이 필요하면 `references/nastran-results.md`의 방법으로 `extract_op2()`를 수정한다.
- 질량: CAD 질량은 `nx-cad-automation`에서 얻는다. FE 질량은 `PARAM,GRDPNT,0`을 넣고 f06의 GPWG에서 읽는다(TODO(사내검증)).
- NX 내부 후처리 API(ResultManager/ResultAccess)는 이름 확인이 덜 됐다. NX 평균 절점값이 꼭 필요할 때만 Journal 기록으로 확인해서 쓴다(reference 9-2).

## 7. 이미지 저장 (컨투어)
- 후처리 Post view와 이미지 내보내기 API는 TODO(사내검증)다(reference 10절). **run_journal 배치에는 그래픽 창이 없어 캡처가 실패할 수 있다.**
- 이미지가 꼭 필요하면 두 가지 방법이 있다. (1) 대화형 Simcenter에서 후처리 저널을 재생한다. (2) 플랫폼 파이썬에서 pyNastran으로 bdf/op2를 읽고 pyvista로 렌더링해 `images/*.png`로 저장한다.
- 이미지 실패로 케이스를 실패 처리하지 않는다.

## 8. 자주 하는 실수와 디버깅
- **원본 폴더 파일이 열리는 문제**: Load Options가 As Saved이면 케이스 폴더 사본이 아닌 원본 .fem/.prt가 열려 수정된다. 템플릿은 `FromDirectory`를 설정하고, 로드된 파일 경로가 케이스 폴더 밖이면 즉시 실패시킨다.
- 체인 파일 중 하나라도 빠지거나 이름이 바뀌면 FEM이 마스터를 못 찾는다(업데이트가 안 됨).
- `FindObject`는 객체가 없으면 예외를 던진다. 하중과 솔루션은 이름 반복문으로 찾는다. 이름은 `model_info.json`에서 확인한다.
- 형상 변화가 커서 면이 바뀌면 메쉬 실패나 경계조건 연결 끊김이 생긴다. DOE의 **극단 케이스 2~3개를 먼저 단발 실행**하고 GUI로 열어 확인한다.
- 응력 출력이 Max Shear로 설정되어 있으면 von Mises가 없다(로그에 경고가 남는다).
- 로그는 플랫폼 `run.log`의 `[DDS]` 메시지, `solve_status.json`, `.f06`, `.log`(Nastran)에서 본다.
- **모르는 API는 지어내지 않는다.** NX에서 **Developer → Journal → Record**로 GUI 작업을 기록하고, 기록된 메서드, 속성 문자열, enum을 그대로 쓴다. 사용자에게 기록 파일을 요청해도 된다.

## 9. 사내 검증 필요 항목 (TODO(사내검증))
- `LoadOptions.ComponentLoadMethod = LoadMethod.FromDirectory`로 체인 파일이 케이스 폴더에서 로드되는지
- Idealized 파트를 별도로 업데이트해야 하는지, `UpdateFemodel()` 반환값으로 메쉬 실패를 감지할 수 있는지
- 어셈블리 FEM(.afm) 구조에서 `SimPart.FemPart` 사용 가능 여부
- `CreateBcBuilderForBc` → `"Magnitude"` 속성 이름, 편집 커밋이 `CommitAddBc`인지
- 하중, 메쉬 크기, 두께 필드에 expression을 쓸 수 있는지
- 메쉬 빌더(`CreateMesh3dTetBuilder`, `"quad mesh overall edge size"`), 재료 로드(`LoadFromNxlibrary`), 물리속성 `SetMaterialPropertyValue`, 쉘 두께 속성 이름
- 솔버 옵션과 출력 요청 편집 API
- 결과 파일 위치와 이름 규칙(Customer Defaults)
- ResultManager/ResultAccess 메서드 이름, Post view와 이미지 API, 배치에서 이미지 캡처 가능 여부
- run_journal 종료코드 전달 여부, `sys.argv` 형식
- FE 질량(`PARAM,GRDPNT`) 출력과 파싱

상세 API: [references/simcenter-cae-api.md](references/simcenter-cae-api.md) · 결과 파일/pyNastran: [references/nastran-results.md](references/nastran-results.md)
