---
name: altair-automation
description: Altair HyperWorks 자동화. HyperMesh 배치(hmbatch -tcl)로 속성 두께·재료·하중 변경과 솔버 덱(.fem) export, .fem 텍스트 직접 수정(PSHELL, MAT1, FORCE), OptiStruct 실행과 완료 판정, .out/.op2(pyNastran) 결과 추출, HyperView Tcl 컨투어 PNG 저장을 다룬다. 사용자가 HyperMesh, OptiStruct, HyperView, Radioss 모델 자동화, DOE/최적화 케이스 실행, 결과 추출을 요청할 때 사용.
---

# Altair HyperWorks 자동화 스킬

이 스킬은 `docs/skill-contract.md`를 따른다. 스크립트는 케이스 폴더(cwd)에서 실행되고 `params.json`을 읽는다. 결과는 `results.json`(숫자, snake_case 키)과 `images/*.png`로 남긴다. 결과 추출에 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 끝낸다.

## 1. 언제 쓰나: 경로 선택

| 상황 | 경로 | tool 키 | 템플릿 |
|---|---|---|---|
| 해석 덱(.fem)이 이미 있고 두께, 재료 상수, 하중 크기만 바뀐다(DOE 대부분) | **.fem 텍스트 직접 수정 + OptiStruct 실행 + 추출** | `python` | `scripts/os_case_driver.py` |
| 메쉬나 형상(모핑)이 바뀌거나, HyperMesh에서만 정의된 정보가 바뀐다 | HyperMesh 배치 → .fem export → 위 드라이버 | `altair_hm`, 연결 시 `python` | `scripts/hm_update_export.tcl` + `os_case_driver.py --hm-script` |
| 해석만 1회 돌린다 | OptiStruct 직접 실행 | `optistruct` | `args: ["model.fem", "-nt", "4"]` |
| 컨투어 이미지가 필요하다 | HyperView Tcl(hwi/post) | `altair_hv` | `scripts/hv_contour_png.tcl` |

선택 원칙은 다음과 같다.
- **.fem 직접 수정을 먼저 고려한다.** HyperMesh 라이선스와 시작 시간이 필요 없고, 결과를 재현하기 쉽다. 덱을 사람이 diff로 검토할 수도 있다.
- 형상이 바뀌는 연구(HyperMorph shape 변수, 리메쉬)에는 HyperMesh 배치가 필요하다. 이 경우에도 HM 단계는 덱 생성만 맡고, 실행과 추출은 드라이버가 한다.
- 결과 숫자는 `.out`(질량 등)과 `.op2`(pyNastran)에서 뽑는다. `.h3d`는 이미지(HyperView)용으로 쓴다.

전제 조건:
- HyperWorks 버전(2021/2022/2023+)에 따라 실행파일(`hmbatch`, `hw`, `runhwx`)과 UI 구조가 다르다. HyperWorks 2023 이후의 새 UI에서는 classic HyperView Tcl이 바뀌었을 수 있다. `TODO(사내검증)`
- 라이선스는 Altair Units(HWU)를 쓴다. 병렬 케이스 수는 남은 유닛 수 안에서 정한다.

## 2. 실행 방법

| tool 키 | 명령 템플릿 | 비고 |
|---|---|---|
| `altair_hm` | `{altair_hmbatch} -tcl {script}` | HyperMesh 배치. Tcl에는 인자를 넘길 수 없으므로 cwd의 `params.json`을 읽는다 |
| `optistruct` | `{altair_optistruct} {args}` | 예: `model.fem -nt 4`. Windows에서는 `.../hwsolvers/scripts/optistruct.bat` |
| `altair_hv` | `{altair_hvp} {script}` | HyperView 배치. 실제로 쓸 실행파일(`hw -b -tcl`, `hvp`, `runhwx`)은 `TODO(사내검증)` |
| `python` | 플랫폼 venv 파이썬 | `os_case_driver.py`(템플릿 → 실행 → 추출) |

스터디 어댑터 예시:
```json
{"tool": "python", "script": "scripts/os_case_driver.py",
 "args": ["--base-fem", "plate.fem", "--nt", "4"],
 "inputs": ["models/plate.fem"], "outputs": ["mass", "max_mises", "max_disp"]}
```
OptiStruct 경로는 `--optistruct` 인자나 환경변수 `DDS_ALTAIR_OPTISTRUCT`로 넘긴다. HM 경로는 `--hmbatch`나 `DDS_ALTAIR_HMBATCH`로 넘긴다.

## 3. 파라미터(형상/속성) 변경

### 3.1 .fem 텍스트 직접 수정(권장)
`os_case_driver.py`에는 두 가지 모드가 있다.
- `--mode cards`(기본): `CARD_RULES`로 카드와 필드를 지정해 값을 바꾼다. 예: `"thickness": {"card": "PSHELL", "match": {2: 1}, "field": 4, "mode": "set"}`는 PID 1인 PSHELL의 T를 바꾼다. 하중은 `"mode": "scale"`로 기존 값에 배율을 곱한다.
- `--mode placeholder`: 덱의 대상 카드를 **자유필드(콤마) 형식**으로 바꾸고 `{{thickness}}`를 넣는다. 예: `PSHELL,1,1,{{thickness}},1,,1`

**Nastran 고정필드 주의**(자세한 내용: [references/fem-format.md](references/fem-format.md)):
- small field는 8칸(1~8열은 카드명, 이후 8칸씩 9개 필드, 73~80열은 continuation)이다. large field(`PSHELL*`)는 16칸이고 continuation 줄은 `*`로 시작한다.
- 숫자는 필드 폭 안에 들어가야 한다(8칸: `210000.`, `7.85E-9`). 실수에는 소수점이 필요하다(`2`가 아니라 `2.`). 드라이버의 `nastran_real()`이 이를 처리한다.
- 탭 문자가 들어간 줄은 열 위치를 알 수 없어 수정하지 않고 에러로 끝낸다.
- `INCLUDE`로 분리된 덱은 메인 파일만 수정된다. 대상 카드가 include 파일에 있으면 그 파일을 `--base-fem`으로 지정하거나 덱을 합친다.

### 3.2 HyperMesh 배치(Tcl)
```tcl
*readfile "plate.hm"                                  ;# TODO(사내검증) argument form
*setvalue props name=shell_prop STATUS=1 95=2.5       ;# PSHELL T (OptiStruct template attr 95)
*setvalue mats name=steel STATUS=1 1=210000           ;# MAT1 E   TODO(사내검증)
set tpl [file join [hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR] feoutput optistruct optistruct]
hm_answernext yes
*feoutputwithdata $tpl "model.fem" 0 0 1 1 0         ;# TODO(사내검증) argument values
```
- **가장 확실한 방법은 GUI에서 같은 작업을 한 번 하고 `command.tcl`(작업 폴더나 Documents)에 기록된 줄을 복사하는 것이다.** 속성 attribute ID와 export 인자는 버전과 템플릿마다 다르다.
- 모핑: HyperMorph shape를 GUI에서 만들어 저장한 뒤, 적용 명령을 command.tcl에서 복사해 배율을 파라미터로 바꾼다. `TODO(사내검증)`
- HyperMesh Python API(2022 이후, `import hm`, `hm.entities`)도 대안이다. 실행 방식(배치 실행파일, 인자)과 객체 속성 이름이 버전마다 달라 `TODO(사내검증)`이다. 사내 표준이 Tcl이면 Tcl을 유지한다.
- 자세한 내용: [references/hypermesh-tcl.md](references/hypermesh-tcl.md)

## 4. 해석조건 변경

| 항목 | .fem 카드 | 비고 |
|---|---|---|
| 쉘 두께 | `PSHELL` field 4(T) | 바디별 PID로 구분 |
| 재료 | `MAT1` field 3(E), 5(NU), 6(RHO) | 단위계 일관성(mm-t-s-N이면 RHO는 t/mm³) |
| 집중하중 | `FORCE` field 5(F, 배율), 6~8(방향) | `mode: scale` 권장 |
| 압력 | `PLOAD4` field 4(P1) / `PLOAD2` field 3 | |
| 경계조건 | `SPC`/`SPC1` | 대부분 고정. 바꿀 때는 HM에서 |
| 서브케이스, 출력 요청 | Case control(`SUBCASE`, `DISPLACEMENT=ALL`, `STRESS=ALL`, `SPCFORCE=ALL`) | `BEGIN BULK` 위 |
| 출력 형식 | I/O 옵션 `OUTPUT,OP2` / `OUTPUT,H3D` 또는 `PARAM,POST,-1` | 문법은 [references/optistruct.md](references/optistruct.md), `TODO(사내검증)` |

## 5. 해석 실행과 완료/실패 판정

```
optistruct.bat case.fem -nt 4          # Linux: optistruct case.fem -nt 4
```
- 판정 기준은 `case.out`에 `ANALYSIS COMPLETED`(최적화는 수렴 메시지)가 있고 `*** ERROR`가 없는 것이다. 종료코드는 보조 지표로만 쓴다. 문구는 버전별로 확인한다. `TODO(사내검증)`
- 추가 확인 파일은 `case.stat`(진행 상태)와 `case.out` 끝부분의 에러 번호다. 문법 오류는 `*** ERROR #` 뒤에 카드 이름과 줄 번호가 나온다.
- `-check` 옵션은 해석 없이 덱 문법만 검사한다. 템플릿을 처음 만들 때 유용하다.

## 6. 결과 추출 → results.json

| 결과 | 소스 | 방법 |
|---|---|---|
| 질량 | `.out` | `Mass = ...` 정규식(드라이버의 `mass_from_out`). 상세 질량과 무게중심은 `OUTPUT,MASSPROP`. `TODO(사내검증)` 문구 |
| 최대 변위 | `.op2` | pyNastran `op2.displacements[sc].data[-1,:,:3]`의 노름 최대값 |
| 최대 von Mises | `.op2` | 응력 테이블의 `headers`에서 `von_mises` 열 최대값(요소 응력) |
| 반력 | `.op2` | `op2.spc_forces[sc].data[-1,:,:3]` 합 |
| 컴플라이언스, 응답값(최적화) | `.out` | 반복 요약 표. 정규식은 사내 .out 샘플로 작성 `TODO(사내검증)` |

- pyNastran이 OptiStruct OP2의 일부 테이블을 읽지 못할 수 있다. 읽기 실패 시 로그를 보고 `.pch`(PUNCH) 텍스트 파싱이나 HyperView 쿼리 내보내기로 대체한다. `TODO(사내검증)`
- 자세한 내용: [references/optistruct.md](references/optistruct.md)

## 7. 이미지 저장

`hv_contour_png.tcl`(HyperView hwi/post Tcl API)는 `.h3d`를 열어 von Mises와 변위 컨투어를 `images/*.png`로 저장한다.
- 데이터 타입과 성분 이름(`"Element Stresses (2D & 3D)"`, `"vonMises"`)은 결과 파일마다 다르다. HyperView Contour 패널에 보이는 이름을 그대로 쓴다.
- 배치 모드 캡처는 그래픽 드라이버에 따라 빈 이미지나 작은 이미지가 나올 수 있다. 그러면 GUI 모드로 실행한 뒤 종료하는 방식을 쓴다. `TODO(사내검증)`
- 이미지를 만들지 못해도 숫자 결과는 이미 드라이버가 썼으므로 케이스는 성공으로 유지된다.
- 자세한 내용: [references/hyperview-post.md](references/hyperview-post.md)

## 8. 자주 하는 실수와 디버깅

- **고정필드가 밀림**: 값이 8칸을 넘었거나 탭이 들어간 경우다. OptiStruct 에러는 카드 이름과 함께 출력된다. 드라이버는 수정한 줄을 직접 포맷한다.
- **수정이 반영되지 않음**: `CARD_RULES`의 match ID가 틀렸거나 카드가 include 파일에 있는 경우다. 드라이버는 매칭되는 카드가 0개면 에러로 끝난다.
- **단위**: OptiStruct에는 단위 개념이 없다. 덱의 단위계(mm-t-s-N 등)를 사용자에게 확인하고, 그 단위계로 results.json 값을 해석한다.
- **HM 배치가 멈춤**: 덮어쓰기 확인창이 뜨는 경우다. 파일을 쓰는 명령 앞에 `hm_answernext yes`를 넣는다. 오류 위치는 `command.tcl`과 `hmbatch` 출력에서 확인한다.
- **HM 버전 차이**: `*readfile`과 `*feoutputwithdata`의 인자 수는 버전마다 다르다. command.tcl과 비교한다.
- **OP2가 없음**: 덱에 OP2 출력 요청이 없는 경우다. 기본 출력은 H3D다.
- **Radioss**(간단히): 실행은 `radioss model_0000.rad -nt 4`(starter와 engine 순차 실행)다. 정상 종료는 엔진 `.out` 끝의 `NORMAL TERMINATION`으로 확인한다. 결과는 `T01`(시간이력)과 `A###`(애니메이션)에 있고, 추출은 HyperGraph/HyperView 또는 T01 변환 도구로 한다. 실행 스크립트 이름, 옵션, 종료 문구는 `TODO(사내검증)`이다.

## 9. 사내 검증 필요 항목(TODO(사내검증))

1. `*readfile`, `*feoutputwithdata`, `*writefile`의 인자 형식(버전별 command.tcl과 비교)
2. 속성과 재료 attribute ID(PSHELL T=95, MAT1 E=1)와 하중 크기 변경 명령, 모핑 적용 명령
3. HyperMesh Python API(`hm` 모듈)의 배치 실행 방법
4. OptiStruct 완료 문구와 `.out`의 질량, 응답 출력 형식. OP2 출력 카드 문법(`OUTPUT,OP2` 대 `PARAM,POST`)
5. pyNastran의 OptiStruct OP2 호환성(응력 테이블, 서브케이스 키)
6. HyperView 배치 실행파일과 `CaptureScreen`/`CaptureScreenToSize` 인자, 2023 이후 새 UI에서 hwi API를 쓸 수 있는지
7. Radioss 실행 스크립트와 종료 판정 문구

참고 문서:
- [references/hypermesh-tcl.md](references/hypermesh-tcl.md)
- [references/fem-format.md](references/fem-format.md)
- [references/optistruct.md](references/optistruct.md)
- [references/hyperview-post.md](references/hyperview-post.md)

## 3D 필드 저장 (형상·필드 학습 → 3D Simulator)
케이스 폴더에 `fields/<이름>.npz`(points, values, triangles — 외곽 표면 노드)를 남기면, 플랫폼이 서버로 올린다. 그다음 `dds-study.train_field_model(study_id, field="<이름>")`으로 '설계변수 → 3D 형상 + 분포'를 학습하고 Simulator에서 3D로 본다.
리메쉬로 노드 수가 케이스마다 달라도 서버가 기준 메쉬에 자동으로 보간한다. 단, 형상이 크게 달라지면(위상 변화) 정확도가 떨어진다.

- OptiStruct OP2 출력(`OUTPUT,OP2`)이 있으면 `simcenter3d-automation/scripts/op2_fields.py`를 그대로 쓴다:
  `{"tool": "python", "script": "scripts/op2_fields.py", "args": ["model.op2", "--bdf", "model.fem", "--field", "vm"]}`
  (스크립트를 프로젝트 `scripts/`로 복사해서 쓴다)
