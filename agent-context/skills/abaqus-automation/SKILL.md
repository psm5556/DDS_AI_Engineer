---
name: abaqus-automation
description: Abaqus/CAE 배치 자동화. .cae 모델 파라미터(스케치 치수, 피처 깊이) 변경이나 스크립트로 모델 재구성, .inp 템플릿(*PARAMETER, 플레이스홀더) 치환, 재료/두께/하중/경계조건/메쉬/스텝/CPU 변경, 잡 제출과 완료 판정(.sta/.msg/.log), ODB 결과 추출(최대 Mises, 변위, 반력 합, 히스토리, 질량), 뷰포트 컨투어 PNG 저장을 다룬다. 사용자가 Abaqus 모델 자동화, DOE/최적화 케이스 스크립트, ODB 후처리, 결과 이미지 캡처를 요청할 때 사용한다.
---

# Abaqus 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 파라메트릭 형상 → 해석 → 결과 추출 → DOE/최적화 루프를 Abaqus로 돌릴 때.
- 기존 ODB에서 값과 그림만 뽑을 때.
- **내장 파이썬 버전**: Abaqus 2023 이하는 Python 2.7, 2024 이상은 Python 3.10 계열이다. Abaqus 안에서 도는 스크립트(`abaqus cae noGUI=`, `abaqus python`)는 반드시 **py2/py3 겸용**으로 쓴다.
  - f-string, `print(x, end=...)`(future import 없이), `pathlib`, 타입힌트를 쓰지 않는다.
  - 파일 첫 줄에 `# -*- coding: utf-8 -*-`를 넣는다(한글 주석 때문). 출력 메시지는 영문 ASCII로 쓴다(콘솔 cp949 문제 회피).
  - `dict.keys()`는 `list(...)`로 감싸서 인덱싱한다(`list(odb.steps.keys())[-1]`).
- 라이선스: `abaqus cae noGUI`는 CAE 토큰과 해석 토큰을 모두 쓴다. `abaqus python`은 ODB 읽기에 토큰이 필요 없다(사내 라이선스 정책은 확인할 것).

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| CAE 모델 수정, 잡 제출, 이미지 | `abaqus_cae` | `abaqus cae noGUI=script.py -- args` |
| ODB 추출만, .inp 처리 | `abaqus_python` | `abaqus python script.py args` |
| 솔버 단독 | `abaqus_solver` | `abaqus job=case input=case.inp cpus=4 interactive ask_delete=OFF` |
| 템플릿 렌더 + 솔버 호출(서브프로세스) | `python` | 플랫폼 venv 파이썬 |

스터디 어댑터는 `script`(본 단계)와 `post`(후처리 단계)를 순서대로 실행한다. 권장 구성은 다음 두 가지다.

**A. .cae 모델 기반 (형상 변경 포함)**
```json
{"tool": "abaqus_cae", "script": "scripts/abq_cae_param_run.py", "args": [],
 "inputs": ["models/bracket.cae"], "outputs": ["max_mises", "max_u", "mass"]}
```
`abq_cae_param_run.py` 하나로 수정, 제출, 대기, 판정, 추출, 이미지 저장까지 끝낸다.

**B. .inp 템플릿 기반 (CAE 토큰 없이 빠름, 형상은 메쉬 좌표가 고정)**
```json
{"tool": "python", "script": "scripts/abq_inp_run.py", "args": ["--abaqus", "abaqus", "--cpus", "4"],
 "inputs": ["models/plate_template.inp"],
 "post": {"tool": "abaqus_python", "script": "scripts/abq_odb_extract.py", "args": ["--odb", "case.odb"]},
 "outputs": ["max_mises", "rf_sum_2"]}
```
- 어댑터의 `post`는 한 단계뿐이다. inp 방식에서 추출과 이미지가 모두 필요하면 `post`를 `abaqus_cae`로 하고, `abq_odb_extract.py`의 추출 함수와 `abq_odb_images.py`의 이미지 코드를 합친 스크립트를 쓴다(`abaqus cae`에서도 `odbAccess`를 import할 수 있다). 이 경우 인자는 `sys.argv`의 `--` 뒤에서 받는다.
- `args`의 값은 템플릿 치환되지 않는다(`{abaqus}`를 쓸 수 없다). 실행파일 경로는 그대로 적는다.
- `abaqus cae noGUI` 스크립트의 인자는 `sys.argv`에서 `--` 다음 항목을 쓴다. 템플릿의 `script_args()` 함수를 참고한다.

## 3. 파라미터(형상) 변경
세 가지 방법이 있다. 견고한 순서는 3-3 > 3-1 > 3-2다.

### 3-1. 기존 .cae의 피처 값 변경
```python
p = mdb.models['Model-1'].parts['Part-1']
p.features['Solid extrude-1'].setValues(depth=25.0)   # 돌출 깊이
p.regenerate()
mdb.models['Model-1'].rootAssembly.regenerate()
```
### 3-2. 스케치 치수 변경
GUI가 기록하는 패턴이다. 치수 인덱스(`d[0]`)는 모델마다 다르므로 **GUI에서 한 번 치수를 수정하고 `abaqus.rpy` 또는 Macro Manager(`abaqusMacros.py`)에 기록된 인덱스를 그대로 쓴다.**
```python
m = mdb.models['Model-1']; p = m.parts['Part-1']
f = p.features['Solid extrude-1']
m.ConstrainedSketch(name='__edit__', objectToCopy=f.sketch)
s = m.sketches['__edit__']
p.projectReferencesOntoSketch(sketch=s, upToFeature=f, filter=COPLANAR_EDGES)
s.dimensions[0].setValues(value=30.0)     # 인덱스는 rpy에서 확인
f.setValues(sketch=s)
del m.sketches['__edit__']
p.regenerate()
```
- 형상이 바뀌면 face/edge 번호가 바뀌어 **지오메트리 기반 Set, Surface, 하중, BC가 끊길 수 있다.** 재생성 후 `len(a.sets['FIX'].nodes)`나 `.faces` 개수를 확인하고, 0이면 실패로 처리한다.
- 스케치 파라미터(`s.Parameter(...)`, `s.parameters['W'].setValues(expression='30')`)도 있다. TODO(사내검증): 버전별 동작을 확인하고, 안 되면 치수 인덱스 방식을 쓴다.

### 3-3. 스크립트로 모델 재구성 (권장)
형상, 재료, Set, 하중, 메쉬를 모두 스크립트로 새로 만든다. Set은 `getByBoundingBox`로 좌표 기반 선택을 하므로 형상이 바뀌어도 안전하다. `scripts/abq_build_plate_model.py`에 전체 예제가 있다.

### 3-4. .inp 템플릿
- `*PARAMETER` 블록: `name = value` 줄을 바꾸고 본문에서 `<name>`으로 참조한다. Abaqus inp는 콤마 구분 자유형식이라 폭 제약이 없다.
- 플레이스홀더: `{{thickness}}` 문자열을 값으로 치환한다. 남은 플레이스홀더가 있으면 오류로 처리한다.
- `*PARAMETER`를 쓴 inp는 CAE로 import할 수 없다. 메쉬(절점 좌표)가 바뀌는 형상 변경은 inp 방식으로 할 수 없다(두께, 재료, 하중, 스텝만 가능).
- 자세한 내용: `references/inp-templating.md`

## 4. 해석조건 변경
상세 API는 `references/cae-model-api.md`에 있다. 요약하면 다음과 같다.
| 대상 | 코드 |
|---|---|
| 탄성 | `m.materials['Steel'].elastic.setValues(table=((E, nu),))` |
| 밀도 | `m.materials['Steel'].density.setValues(table=((rho,),))` |
| 쉘 두께 | `m.sections['Shell-1'].setValues(thickness=t)` |
| 집중하중 | `m.loads['Load-1'].setValues(cf2=-F)` (생성 스텝) / `setValuesInStep(stepName=..., cf2=...)` (후속 스텝) |
| 압력 | `m.loads['Pressure-1'].setValues(magnitude=p)` |
| 변위 BC | `m.boundaryConditions['BC-2'].setValues(u2=-1.0)` |
| 메쉬 | `p.deleteMesh(); p.seedPart(size=h, deviationFactor=0.1, minSizeFactor=0.1); p.generateMesh()` |
| 스텝 | `m.steps['Step-1'].setValues(timePeriod=1.0, initialInc=0.1, maxInc=0.1, minInc=1e-8, maxNumInc=1000)` |
| CPU | `mdb.jobs['Job-1'].setValues(numCpus=4, numDomains=4)` |

- 단위계는 모델을 따른다(mm-N-MPa-tonne이면 밀도 7.85e-9).
- 인스턴스가 independent이면 메쉬를 `a.seedPartInstance(...)`와 `a.generateMesh(regions=...)`로 처리한다.

## 5. 해석 실행과 완료/실패 판정
```python
job = mdb.jobs['case_job']
job.submit(consistencyChecking=OFF)
job.waitForCompletion()
```
- 판정은 파일로 한다(가장 확실하다).
  - `<job>.sta` 마지막 부분: `THE ANALYSIS HAS COMPLETED SUCCESSFULLY` 이면 성공, `THE ANALYSIS HAS NOT BEEN COMPLETED` 이면 실패.
  - `<job>.log`: `COMPLETED` 이면 성공, `exited with error` 이면 실패.
  - `<job>.msg`, `<job>.dat`의 `***ERROR` 줄을 run.log에 출력해 원인을 남긴다.
- 솔버 단독 실행: `abaqus job=case input=case.inp cpus=4 interactive ask_delete=OFF`. `interactive`가 없으면 백그라운드로 가서 즉시 반환된다. 종료코드는 신뢰하지 말고 위 파일로 판정한다.
- 템플릿의 `check_job_status(job_name)` 함수를 그대로 쓴다.

## 6. 결과 추출 → results.json
상세 API는 `references/odb-api.md`에 있다.
```python
from odbAccess import openOdb
odb = openOdb(path='case_job.odb', readOnly=True)
step = odb.steps[list(odb.steps.keys())[-1]]
frame = step.frames[-1]
max_mises = max(v.mises for v in frame.fieldOutputs['S'].values)
max_u = max(v.magnitude for v in frame.fieldOutputs['U'].values)
nset = odb.rootAssembly.nodeSets['FIX']           # 어셈블리 Set 이름은 ODB에서 대문자
rf = frame.fieldOutputs['RF'].getSubset(region=nset)
rf_sum_2 = sum(v.data[1] for v in rf.values)
```
- `S` 기본값은 적분점 값이다. GUI 컨투어(절점 평균)와 다를 수 있다. 결과에 어떤 기준인지 키 이름이나 주석으로 남긴다.
- 질량: CAE에서 `mdb.models[...].rootAssembly.getMassProperties()['mass']`(해석 전, 밀도가 정의돼 있어야 함). ODB만 있으면 `EVOL` 출력 × 밀도로 계산한다(`abq_odb_extract.py` 참고).
- 히스토리: `step.historyRegions['Node PART-1-1.12'].historyOutputs['U2'].data` → `((t, v), ...)`. 영역 이름은 `historyRegions.keys()`로 먼저 출력해서 확인한다.
- 구버전 ODB를 신버전에서 열면 업그레이드가 필요할 수 있다(`odbAccess.isUpgradeRequiredForOdb`, `upgradeOdb`).

## 7. 이미지 저장
`abaqus_cae`에서만 가능하다(`abaqus python`에는 session이 없다).
```python
odb = session.openOdb(name='case_job.odb')
vp = session.viewports['Viewport: 1']
vp.setValues(displayedObject=odb)
vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
vp.odbDisplay.setPrimaryVariable(variableLabel='S', outputPosition=INTEGRATION_POINT, refinement=(INVARIANT, 'Mises'))
vp.view.setValues(session.views['Iso']); vp.view.fitView()
session.printOptions.setValues(vpDecorations=OFF, reduceColors=False)
session.pngOptions.setValues(imageSize=(1600, 1000))
session.printToFile(fileName='images/mises', format=PNG, canvasObjects=(vp,))   # .png 자동 추가
```
- 변형 배율: `vp.odbDisplay.commonOptions.setValues(deformationScaling=UNIFORM, uniformScaleFactor=1.0)`
- 뷰 방향: `session.views['Front'|'Back'|'Top'|'Bottom'|'Left'|'Right'|'Iso']` 또는 `vp.view.setViewpoint(viewVector=(1,1,1), cameraUpVector=(0,0,1))`
- DOE 비교 이미지는 범례 범위를 고정한다: `vp.odbDisplay.contourOptions.setValues(maxAutoCompute=OFF, maxValue=300.0, minAutoCompute=OFF, minValue=0.0)`
- 템플릿은 `scripts/abq_odb_images.py`에 있다.

## 8. 자주 하는 실수와 디버깅
- **로그 위치**: 케이스 폴더의 `run.log`(플랫폼), `abaqus.rpy`(CAE 명령 기록), `<job>.log/.msg/.dat/.sta`.
- `.lck` 파일이 남아 있으면 ODB가 열리지 않는다. 이전 실행이 강제 종료된 경우다.
- 잡 이름에 공백, 한글, 숫자로 시작하는 이름을 쓰지 않는다. 케이스 폴더 경로에도 한글과 공백을 피한다(솔버 오류 원인).
- `KeyError: 'Solid extrude-1'`: 피처/Set 이름을 `p.features.keys()`, `a.sets.keys()`로 출력해 확인한다.
- ODB의 Set 이름은 대문자다. 인스턴스 이름도 대문자다(`PART-1-1`).
- 형상 변경 후 메쉬 실패: `p.verifyMeshQuality`를 쓰기보다 `seedPart` 크기를 키우거나 `minSizeFactor`를 조정한다. 메쉬가 실패하면 결과를 쓰지 말고 종료한다.
- noGUI에서 예외가 나도 CAE 종료코드가 0일 수 있다. 그래서 **results.json을 쓰지 않는 것**을 실패 신호로 삼는다(플랫폼이 failed로 처리한다).
- `waitForCompletion()` 직후 ODB가 아직 잠겨 있으면 짧게 재시도한다(템플릿에 반영됨).

## 9. 사내 검증 필요 항목 (TODO(사내검증))
- `abaqus cae noGUI=... -- args`에서 `sys.argv`에 `--`가 남는지(템플릿은 `--` 이후를 인자로 본다. 없으면 params.json의 `_model` 키를 쓴다).
- 스케치 치수 인덱스와 `ConstrainedSketch.parameters` 사용 가능 여부.
- noGUI 모드의 `printToFile` 렌더링(일부 원격/가상 머신에서 빈 이미지가 나오면 그래픽 드라이버 또는 `ABAQUS_GRAPHICS`/OpenGL 설정을 확인한다).
- `FieldOutput.bulkDataBlocks`의 `mises` 배열(대형 모델 가속용, 템플릿은 기본적으로 values 루프를 쓴다).
- `.dat`의 총질량 출력 문자열(inp 방식 질량 대안).

## 파일
- `scripts/abq_cae_param_run.py`: (abaqus_cae) .cae 열기 → 파라미터, 조건 변경 → 질량 → 제출/대기/판정 → ODB 추출 → 이미지 → results.json
- `scripts/abq_build_plate_model.py`: (abaqus_cae) 구멍 뚫린 판 모델을 스크립트로 새로 만들고 해석, 추출까지 수행
- `scripts/abq_inp_run.py`: (python) inp 템플릿 렌더 → 솔버 실행 → 판정 (post에서 추출)
- `scripts/abq_odb_extract.py`: (abaqus_python) ODB → results.json
- `scripts/abq_odb_images.py`: (abaqus_cae) ODB → images/*.png
- `references/cae-model-api.md`, `references/odb-api.md`, `references/inp-templating.md`

## 3D 필드 저장 (형상·필드 학습 → 3D Simulator)
케이스 폴더에 `fields/<이름>.npz`(points, values, triangles — 외곽 표면 노드)를 남기면, 플랫폼이 서버로 올린다. 그다음 `dds-study.train_field_model(study_id, field="<이름>")`으로 '설계변수 → 3D 형상 + 분포'를 학습하고 Simulator에서 3D로 본다.
리메쉬로 노드 수가 케이스마다 달라도 서버가 기준 메쉬에 자동으로 보간한다. 단, 형상이 크게 달라지면(위상 변화) 정확도가 떨어진다.

```
# 해석 후 추출 단계(스터디 adapter_config의 post)로 붙인다
{"tool": "abaqus_python", "script": "scripts/abq_odb_fields.py", "args": ["job.odb", "--field", "S", "--invariant", "mises", "--name", "stress", "--deformed", "1"]}
```
- `--field U --invariant magnitude --name disp` 처럼 변위 크기도 저장할 수 있다. 여러 필드가 필요하면 post를 여러 번 실행하거나 스크립트를 두 번 호출한다.
- 스크립트: `scripts/abq_odb_fields.py`(Abaqus 내장 python, py2/3 겸용, 외곽 면 자동 추출, ELEMENT_NODAL → 노드 평균)

## 결과 전체 저장 — case.h5 (HDF5)
케이스가 끝나면 PC Agent가 케이스 폴더를 `case.h5`(HDF5 압축)로 묶어 서버에 올린다(docs/cae-data-hdf5.md). results.json·fields/*.npz·history/*.csv·로그·입력 덱은 자동으로 담긴다. **해석 결과 전체(모든 스텝·프레임·필드)를 빠짐없이 남기려면** 아래 추출 단계를 스터디 `adapter_config.post` 끝에 붙인다. 원본 결과 파일이 지워져도 나중에 Output을 추가하거나 3D 학습을 다시 할 수 있다.
```json
{"tool": "abaqus_python", "script": "scripts/abq_odb_to_npz.py", "args": ["job.odb"]}
```
- `scripts/abq_odb_to_npz.py`(Abaqus 내장 python, py2/3 겸용): 모든 스텝·프레임·필드 출력(bulkDataBlocks)과 이력 출력, 인스턴스별 메쉬를 `h5in/*.npz`·`history/*.csv`로 남긴다. Abaqus 파이썬에는 h5py가 없어서이고, PC Agent가 case.h5로 묶는다.
- 큰 ODB는 `--every 5`·`--fields S,U,PEEQ`로 줄일 수 있다(사용자에게 알린다). TODO(사내검증): 프레임마다 행 수, 큰 ODB 시간
