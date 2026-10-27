---
name: ansa-automation
description: BETA CAE ANSA·META 배치 자동화. ANSA Python API(`ansa.base`, `constants`, `batchmesh`, `morph`)로 모델 열기(.ansa, Nastran/Abaqus/LS-DYNA 덱 import), 속성 두께·재료 카드 변경, 모핑 형상 파라미터, Batch Mesh 재메쉬, 솔버 덱 export(OutputNastran/OutputAbaqus/OutputLSDyna)를 하고, META 세션 파일(.ses)로 컨투어 이미지와 곡선을 뽑는다. 사용자가 ANSA 전처리, 모핑 DOE, 메쉬 재생성, 솔버 덱 생성, META 후처리 이미지·리포트 자동화를 요청할 때 사용한다.
---

# ANSA · META 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽는다. ANSA는 **전처리 단계**라서 `results.json`을 쓰지 않는다. 결과는 다음 단계(솔버 실행 + 추출 스크립트)가 쓴다. 어느 단계든 실패하면 `results.json`이 생기지 않아 케이스가 failed가 된다.

## 1. 언제 쓰나 / 전제
| 상황 | 경로 |
|---|---|
| 형상(모핑), 메쉬, ANSA에서만 정의된 정보가 바뀐다 | **ANSA 배치 → 덱 export → 솔버 스킬**(이 스킬 + `msc-nastran-automation` 등) |
| 덱이 이미 있고 카드 값(두께, E, 하중)만 바뀐다 | ANSA 없이 덱을 직접 편집한다: `msc-nastran-automation/scripts/bdf_card_edit.py` (Nastran), `abaqus-automation` inp 템플릿, `lsdyna-automation` |
| 결과 그림을 META 품질로 남기고 싶다 | META 세션 배치(7절). 라이선스 없이 그리려면 Nastran 스킬의 matplotlib 이미지 |
- **ANSA 내장 Python**에서 돈다. 버전별 파이썬(ANSA v20 이후 Python 3 계열)을 확인한다. numpy 같은 외부 패키지는 쓰지 않는다. 콘솔 출력은 영문 ASCII로 한다.
- 템플릿은 공식 API 이름(`base.Open`, `base.InputNastran`, `base.GetEntity`, `base.GetEntityCardValues`, `base.SetEntityCardValues`, `base.OutputNastran`, `base.SaveAs`, `base.SetCurrentDeck`, `constants.NASTRAN`)만 직접 호출한다. 모핑, Batch Mesh, CAD 변환, 종료 호출은 TODO(사내검증)이다.
- 모르는 API는 지어내지 않는다. ANSA **Help > Scripting(API reference)** 과 설치 폴더의 예제 스크립트에서 이름과 인자를 확인하고 쓴다.
- 라이선스: ANSA/META 토큰(BETA LM). 병렬 케이스 수는 남은 토큰 안에서 정한다.

## 2. 실행 방법 (tool 키)
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `ansa` | `{ansa} -nogui -exec "load_script: '{script}'" -exec "main()"` | ANSA 배치. 스크립트에 `main()`이 있어야 한다. 인자는 넘길 수 없으므로 cwd의 `params.json`, `ansa_config.json`을 읽는다 |
| `meta` | `{meta} -b -s {script}` | META 세션(.ses) 배치 |
| `msc_nastran` / `python` | 솔버 실행, 결과 추출 | `msc-nastran-automation` 참고 |
경로 키는 `ansa`(예: `C:/Program Files/BETA_CAE_Systems/ansa_v24.1.0/ansa64.bat`), `meta`(`.../meta_post_v24.1.0/meta_post64.bat`)다. PC Agent가 설치 위치를 자동으로 찾는다.

**ANSA(덱 생성) → MSC Nastran(실행·추출)** 스터디 예시:
```json
{"tool": "ansa", "script": "scripts/ansa_batch_deck.py",
 "inputs": ["models/bracket.ansa", "models/ansa_config.json",
            "scripts/bdf_card_edit.py", "scripts/nastran_op2_extract.py"],
 "post": [{"tool": "python", "script": "scripts/nastran_run.py",
           "args": ["--deck", "case.bdf", "--no-edit", "--job", "case", "--fields", "stress"]}],
 "outputs": ["max_von_mises", "max_disp", "mass"], "timeout_sec": 7200}
```
- ANSA가 `case.bdf`를 쓰고, `nastran_run.py --no-edit`이 실행·판정·추출한다(`PARAM,POST,-1` 자동 삽입).
- META 이미지까지 원하면 `post`에 `{"tool": "meta", "script": "scripts/meta_contour.ses"}`를 마지막에 붙인다.
- Abaqus/LS-DYNA 덱이면 `ansa_config.json`의 `deck`/`output`을 바꾸고 post를 각 솔버 스킬 스크립트로 바꾼다.

## 3. 파라미터 변경
### 3-1. 속성·재료 카드 값 (검증된 API)
```python
deck = constants.NASTRAN
base.SetCurrentDeck(deck)
ent = base.GetEntity(deck, "PSHELL", 1)
base.SetEntityCardValues(deck, ent, {"T": 2.5})
print(base.GetEntityCardValues(deck, ent, ("T",)))   # 반드시 다시 읽어 확인
```
- `ansa_config.json`의 `cards`가 params 키 → (`type`, `id`, `field`)을 정한다. `mode: "scale"`이면 기존 값에 곱한다.
- `field`는 ANSA 카드 편집기에 보이는 **필드 라벨**이다. Nastran 덱은 대부분 Nastran 이름(`T`, `E`, `NU`, `RHO`)과 같지만 LS-DYNA(`*SECTION_SHELL` T1 등), Abaqus 섹션은 라벨이 다르다. TODO(사내검증): GUI 카드에서 라벨을 확인한다.
- 템플릿은 쓰고 나서 다시 읽은 값이 다르면 실패로 끝낸다(라벨 오타 방지).
- 이름으로 찾아야 하면 `base.CollectEntities(deck, None, "PSHELL")`로 모은 뒤 `base.GetEntityCardValues(deck, ent, ("Name",))`로 거른다. TODO(사내검증): 이름 필드 라벨.

### 3-2. 모핑 (형상 파라미터)
- 권장 절차: GUI에서 모핑 박스/파라미터(또는 Optimization Task의 Design Variable)를 만들어 `.ansa`에 저장한다. 배치에서는 **값 설정 → 적용 → 메쉬 품질 확인**만 한다.
- 템플릿의 `apply_morph()`는 일부러 비워 두었다. `morph` 설정에 있는 키가 params에 오면 `NotImplementedError`로 실패한다(모르는 API로 엉뚱한 형상이 나오는 것보다 낫다). TODO(사내검증): `ansa.morph` 모듈에서 파라미터 값 설정·적용 함수를 확인해 구현한다.
- 형상이 바뀌면 노드 좌표가 바뀌므로 덱을 통째로 새로 export해야 한다. 카드 텍스트 편집으로는 할 수 없다.

### 3-3. 재메쉬 (Batch Mesh)
- `.ansa`에 Batch Mesh 세션(시나리오, 파라미터, 품질 기준)을 저장해 두고, `ansa_config.json`의 `batchmesh: true`로 켠다.
- 템플릿은 `batchmesh.RunAllMeshingScenarios`가 있을 때만 호출하고 없으면 실패한다. TODO(사내검증): 함수 이름, 반환값, 품질 불량(off elements) 개수 조회 방법.

### 3-4. CAD 입력
- CAD(.stp/.igs/.CATPart) 변환·정리·중간면 추출은 단계가 많고 버전 의존적이다. GUI에서 한 번 `.ansa`를 만들고 그 DB를 템플릿으로 쓰는 것을 기본으로 한다. TODO(사내검증): 배치 CAD 변환 호출.

## 4. 해석조건 변경
- ANSA 안에서 바꿀 수 있지만, **카드 값 변경은 export한 덱을 텍스트로 고치는 쪽이 더 빠르고 재현성이 좋다.** 하중 크기, SOL, EIGRL, 출력 요청은 `nastran_run.py`의 카드 규칙으로 처리한다(`--no-edit`를 빼고 `card_rules.json` 제공).
- 구속·하중 세트 자체를 새로 정의하거나 Set/접촉이 형상을 따라가야 할 때만 ANSA에서 바꾼다. 해당 엔티티 타입 이름과 필드 라벨은 GUI 카드에서 확인한다. TODO(사내검증)
- 단위계는 ANSA DB의 단위를 따른다(mm-t-s-N이면 RHO 7.85E-9).

## 5. 실행과 완료 판정
- ANSA 종료코드는 신뢰하지 않는다. TODO(사내검증): `session.Quit()`과 `sys.exit(code)`가 플랫폼까지 전달되는지.
- 판정은 파일로 한다. 템플릿은 시작할 때 출력 덱, `ansa_status.json`, `results.json`을 지우고, 성공하면 덱과 `ansa_status.json`(`"ok": true`, 바뀐 값)을 쓴다. 실패하면 덱을 지우므로 다음 단계(`nastran_run.py`)가 "deck not found"로 실패한다.
- `-nogui` 실행 후 ANSA가 종료하지 않고 남으면 `finish()`의 종료 호출을 사내 버전에 맞게 고친다. 어댑터 `timeout_sec`를 반드시 준다.
- 솔버 완료 판정(f06 FATAL / END OF JOB)은 솔버 스킬이 한다.

## 6. 결과 추출 → results.json
- ANSA 단계는 결과를 쓰지 않는다. Nastran이면 `msc-nastran-automation/scripts/nastran_op2_extract.py`가 OP2에서 `max_von_mises`, `max_disp`, `mass`, `freq_n`, `buckling_factor_n`을 쓴다.
- ANSA에서 질량이나 메쉬 품질 수치가 꼭 필요하면 `ansa_status.json`에 남기고, 추출 스크립트가 그 값을 `results.json`에 합치게 한다. TODO(사내검증): 질량 계산 API.
- META에서 곡선(시간이력, FRF)을 뽑을 때는 세션에서 CSV로 내보내고 python post 단계가 CSV를 읽어 숫자로 바꾼다. TODO(사내검증): 곡선 export 명령.

## 7. 이미지 저장 → images/ (META)
`scripts/meta_contour.ses`는 **골격**이다. 각 줄(`read geom`, `read dis`, `read onlyfun`, `view default isometric`, `options fringebar on`, `write png`, `exit`)의 정확한 문법은 TODO(사내검증)다.
- 가장 확실한 방법: META GUI에서 OP2를 열고 von Mises 컨투어, 뷰, PNG 저장까지 한 번 한 뒤 **세션 기록 기능으로 저장한 .ses**를 템플릿으로 쓴다. 경로는 상대 경로(`case.op2`, `images/vm.png`)로 바꾼다. 세션은 cwd(케이스 폴더) 기준으로 실행된다.
- 세션 파일은 params를 읽을 수 없다. 파일명은 고정(`case.op2`)으로 두고 케이스 폴더가 달라지게 한다.
- DOE 비교 그림은 범례 최소/최대를 고정한다(META fringe bar 범위 고정 명령, TODO(사내검증)).
- 배치(`-b`)에서 그래픽 없이 PNG가 저장되는지 확인한다. 안 되면 Nastran 스킬의 matplotlib 이미지(`--images vm,disp`)를 쓴다. 이미지 실패로 케이스를 실패시키지 않으려면 META 단계를 마지막 post에 두되, 실패가 잦으면 빼 둔다(어댑터는 어느 단계든 종료코드가 0이 아니면 케이스를 실패 처리한다).

## 8. 3D 필드 (fields/*.npz)
- 해석 결과에서 만든다. Nastran: `nastran_run.py --fields stress,disp` 또는 `nastran_op2_extract.py --fields stress`. Abaqus: `abaqus-automation/scripts/abq_odb_fields.py`, LS-DYNA: `lsdyna-automation/scripts/dyna_fields.py`.
- 모핑으로 형상이 바뀌어도 노드 수·순서가 유지되면(모핑은 보통 유지) 서버 보간 없이 가장 정확하다. 재메쉬하면 서버가 기준 메쉬로 보간한다. 위상이 바뀌는 큰 변화는 정확도가 떨어진다.

## 9. 흔한 오류와 대처
| 증상 | 원인 / 대처 |
|---|---|
| ANSA가 끝나지 않음 | 종료 호출이 맞지 않거나 대화창 대기. `finish()` 확인, `timeout_sec` 지정 |
| `PSHELL id 1 not found` | ID가 다르거나 현재 덱이 다름. `deck` 설정과 `base.SetCurrentDeck` 확인 |
| `set ..., read back ...` | 필드 라벨 오류. GUI 카드 라벨을 그대로 쓴다 |
| 덱 export 후 솔버 오류 | export 옵션(include 분리, 형식) 차이. GUI export와 같은 옵션인지 확인(TODO(사내검증)) |
| 모핑 후 메쉬 품질 불량 | 극단 케이스 2~3개를 먼저 단발 실행하고 GUI로 열어 본다. `save_ansa`로 DB를 남긴다 |
| META PNG가 비어 있음 | 배치 그래픽 문제. GUI 모드 세션 실행 또는 matplotlib 대체 |
| 경로 오류 | 케이스 경로에 한글·공백을 피한다. `load_script` 경로는 작은따옴표 안에 있다 |
- 로그: 플랫폼 `run.log`의 `[DDS]` 줄, `ansa_status.json`, ANSA 트레이스백.

**사내 검증 필요 항목 (TODO(사내검증))**
1. `-nogui` 실행 시 cwd 유지, `session.Quit()`/종료코드 전달
2. `InputNastran`/`OutputNastran`/`OutputAbaqus`/`OutputLSDyna`의 버전별 키워드 옵션
3. 카드 필드 라벨(LS-DYNA, Abaqus 섹션 두께, 이름 필드)
4. 모핑 파라미터 설정·적용 API(`ansa.morph`), Batch Mesh 실행 API(`ansa.batchmesh`), 품질 조회
5. CAD 배치 변환, 질량 계산 API
6. META 세션 명령 문법(read/view/fringe 범위/write png/exit, 곡선 export), `-b` 배치 PNG

## 파일
- `scripts/ansa_batch_deck.py`: (ansa) params → 모델 열기 → 모핑(TODO) → 재메쉬(옵션) → 카드 값 변경·검증 → 덱 export → `ansa_status.json`
- `scripts/ansa_config.example.json`: `ansa_config.json` 예시(모델, 덱 종류, 카드 규칙, 출력 이름)
- `scripts/meta_contour.ses`: (meta) OP2 → von Mises 컨투어 PNG 세션 골격
- [references/ansa-meta-api.md](references/ansa-meta-api.md): API 요약, 확인 상태
