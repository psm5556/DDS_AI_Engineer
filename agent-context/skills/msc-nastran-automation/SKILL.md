---
name: msc-nastran-automation
description: MSC Nastran 단독 해석 덱(.bdf/.dat/.nas) 자동화. 고정필드(8칸)·라지필드(16칸, `*`)·자유필드(콤마) 카드를 열 밀림 없이 수정(PSHELL 두께, MAT1, FORCE/PLOAD4, PBARL 치수, EIGRL, Case Control)하고, `nastran job.bdf scr=yes old=no batch=no`로 실행한 뒤 f06(FATAL, END OF JOB)으로 완료를 판정하며, OP2(pyNastran)에서 최대 von Mises·변위·반력·질량·고유진동수·좌굴계수를 results.json으로 뽑고 컨투어 PNG와 fields/*.npz를 만든다. 사용자가 MSC Nastran, Patran에서 뽑은 BDF, SOL 101/103/105/200 덱의 DOE/최적화 케이스 실행이나 f06/OP2 결과 추출을 요청할 때 사용한다.
---

# MSC Nastran 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 끝낸다.

## 1. 언제 쓰나 / 전제
- 해석 덱(BDF)이 이미 있고 두께, 재료 상수, 하중 크기, 단면 치수, 모드 수 같은 **카드 값만** 바뀌는 DOE/최적화. 메쉬(GRID 좌표)가 바뀌는 형상 변경은 전처리기(ANSA: `ansa-automation`, HyperMesh: `altair-automation`)에서 덱을 다시 만들고 이 스킬로 실행·추출한다.
- Simcenter(NX) Nastran을 NX 안에서 돌리는 경우는 `simcenter3d-automation`을 쓴다. 이 스킬은 **MSC Nastran 단독 덱**용이다. 결과 형식(f06/OP2)은 같으므로 추출 로직은 서로 통한다.
- MSC Patran(PCL 세션 `.ses`)과 MSC Apex(Python API)도 덱 생성에 쓸 수 있지만 이 스킬은 다루지 않는다. Patran은 GUI에서 만든 덱을 템플릿으로 쓰고, 필요하면 `patran -b -sfp script.ses`류 배치 실행을 사내 확인 후 별도 tool 키로 추가한다. TODO(사내검증)
- 플랫폼 venv에 pyNastran 1.4.1, numpy, matplotlib가 있다. Nastran 라이선스(MSC One 토큰 등)는 사내 정책을 따른다. 병렬 케이스 수는 남은 토큰 수 안에서 정한다.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| 덱 수정 → 실행 → 판정 → 추출을 한 번에 (권장) | `python` | `python nastran_run.py --deck plate.bdf ...` (내부에서 nastran 호출) |
| 솔버 단독 1회 실행 | `msc_nastran` | `{msc_nastran} case.bdf scr=yes old=no batch=no` |
| 판정 + 추출 + 이미지 + 필드만 | `python` | `python nastran_op2_extract.py --job case` |
| 카드 수정만 | `python` | `python bdf_card_edit.py --deck plate.bdf --out case.bdf --verify` |

실행파일은 PC Agent가 자동으로 찾은 `msc_nastran` 경로(예: `C:/MSC.Software/MSC_Nastran/2024.1/bin/nastran.exe`)이고, 플랫폼이 `DDS_MSC_NASTRAN` 환경변수로 넘겨 준다. `nastran_run.py`는 `--nastran` → `DDS_MSC_NASTRAN` → PATH의 `nastran` 순서로 찾는다.

**A. 드라이버 하나로 (권장)**
```json
{"tool": "python", "script": "scripts/nastran_run.py",
 "args": ["--deck", "plate.bdf", "--job", "case", "--extra", "mem=4gb smp=4", "--images", "vm,disp", "--fields", "stress"],
 "inputs": ["models/plate.bdf", "models/card_rules.json", "scripts/bdf_card_edit.py", "scripts/nastran_op2_extract.py"],
 "outputs": ["max_von_mises", "max_disp", "mass"]}
```
- 어댑터는 `script` 파일 하나만 케이스 폴더에 복사한다. **보조 모듈 두 개를 `inputs`에 꼭 넣는다.** 빠지면 종료코드 7로 끝난다.
- `INCLUDE` 파일도 `inputs`에 넣는다. 편집기는 케이스 폴더 안의 include 사본만 고친다.

**B. 솔버 tool 키 + 추출 post**
```json
{"tool": "python", "script": "scripts/bdf_card_edit.py", "args": ["--deck", "plate.bdf", "--out", "case.bdf"],
 "inputs": ["models/plate.bdf", "models/card_rules.json"],
 "post": [{"tool": "msc_nastran", "args": ["case.bdf", "scr=yes", "old=no", "batch=no"]},
          {"tool": "python", "script": "scripts/nastran_op2_extract.py", "args": ["--job", "case"]}]}
```
- `args`에는 `{msc_nastran}` 같은 치환이 되지 않는다. 템플릿 덱에 `PARAM,POST,-1`이 있어야 OP2가 나온다(B 방식은 자동 삽입하지 않는다).
- Nastran 종료코드는 신뢰하지 않는다. 판정은 항상 f06으로 한다(5절).

## 3. 파라미터 변경 — 카드 템플릿
`bdf_card_edit.py`는 `card_rules.json`(없으면 내장 기본값)의 규칙대로 **해당 필드 글자만** 바꾼다. 줄마다 형식을 따로 판단하므로 형식이 섞인 덱도 안전하다.
```json
{"thickness":  {"card": "PSHELL", "id": 1, "field": 4},
 "e_mod":      {"card": "MAT1", "id": 1, "field": 3},
 "load_scale": {"card": "FORCE", "id": 10, "field": 5, "mode": "scale", "count": 2},
 "z1":         {"card": "PSHELL", "id": 1, "field": 10},
 "bar_h":      {"card": "PBARL", "id": 7, "field": 11},
 "n_modes":    {"kind": "regex", "section": "bulk", "pattern": "^(EIGRL,5,[^,]*,[^,]*,)\\s*\\d+", "replace": "\\g<1>{ivalue}"}}
```
- `field`는 QRG 1행 필드 번호다(1=카드명, 2=ID). 연속행은 이어서 센다: 2행 첫 데이터 필드가 10이다(pyNastran `card.field(field-1)`과 같다). 그래서 PSHELL Z1=10, PBARL DIM1=10, DIM2=11이다.
- `id`는 `match: {"2": id}`의 줄임이다. 다른 필드로 고르려면 `"match": {"3": 100}`처럼 쓴다. 숫자는 값으로 비교한다(`1`, `1.`, `1.0` 같음).
- `mode`: `set`(기본), `scale`(기존 값×배율, 하중에 권장), `add`. `type: "int"`이면 정수로 쓴다(MID, 모드 수).
- 맞는 카드가 0개면 오류로 끝난다. `count`를 주면 개수가 정확히 맞아야 한다.

**필드 형식별 주의** (상세: [references/bdf-format.md](references/bdf-format.md))
| 형식 | 폭 | 편집기 동작 |
|---|---|---|
| small field | 8칸, 73~80열은 연속 표시 | 해당 8칸만 오른쪽 정렬로 다시 쓰고 나머지 열과 연속 표시는 그대로 둔다 |
| large field (`PSHELL*`, 연속행 `*`) | 16칸, 한 줄에 데이터 4개 | 해당 16칸만 다시 쓴다 |
| free field (`PSHELL,1,1,2.5`) | 기본 8자로 제한 | 콤마 사이 토큰만 바꾸고 `$` 주석은 보존한다 |
- 실수는 반드시 소수점을 넣는다(`2.`). 8칸에 맞추려고 유효숫자를 줄이며, 상대오차가 1e-5를 넘으면 경고를 남긴다. 정밀도가 중요하면 템플릿 카드를 large field로 바꿔 둔다.
- MSC 자유필드에서 8자를 넘는 값을 허용하는지는 버전별로 다르다. 그래서 기본 `--free-width 8`이다. TODO(사내검증): 16자 허용 시 `--free-width 16`.
- 탭 문자가 든 줄은 열 위치를 알 수 없어 오류로 끝낸다. 템플릿에서 탭을 공백으로 바꿔 둔다.
- 플레이스홀더 방식(`--placeholders`): 대상 카드를 자유필드로 바꾸고 `PSHELL,1,1,{{thickness}},1`처럼 쓴다. 고정필드 줄에 `{{...}}`가 있으면 오류로 끝낸다(열 밀림 방지). Case Control의 `METHOD = {{m}}`도 된다.

## 4. 해석조건 변경
| 대상 | 위치 | 규칙 예 |
|---|---|---|
| 쉘 두께 | `PSHELL` f4(T) | `{"card":"PSHELL","id":1,"field":4}` |
| 재료 | `MAT1` f3(E) f5(NU) f6(RHO) | 단위계 일관성(mm-t-s-N이면 RHO는 t/mm³, 7.85E-9) |
| 집중하중 | `FORCE` f5(F), f6~8(방향) | `mode: scale` |
| 압력 | `PLOAD4` f4(P1) / `PLOAD2` f3 | 같은 SID 여러 장이면 `count` 생략, 전부 바뀐다 |
| 하중 조합 | `LOAD` f3(S), f4(S1) | 배율 하나로 전체 하중을 바꿀 때 편하다 |
| 구속 | `SPC1` / `SPC` | 보통 고정. 바꾸려면 Case Control `SPC = n`을 regex로 바꾼다 |
| 해석 종류 | 실행 제어 `SOL 101/103/105/200` | regex 규칙(`section: control`) |
| 고유치 | `EIGRL` f5(ND) / Case `METHOD` | SOL 103 모드 수, SOL 105는 양의 근 수 |
| 출력 | Case `DISPLACEMENT=ALL`, `STRESS(VONMISES)=ALL`, `SPCFORCES=ALL` | `BEGIN BULK` 위 |
| OP2/질량 | `PARAM,POST,-1`, `PARAM,GRDPNT,0` | 드라이버가 POST는 자동 삽입, GRDPNT는 템플릿에 넣는다 |
- SOL 105는 subcase 1(정적) + subcase 2(`METHOD`, `STATSUB=1`) 구성이 기본이다. SOL 200은 DESVAR/DRESP1/DCONSTR 카드 값도 같은 규칙으로 바꿀 수 있다.
- 병렬/메모리는 명령 키워드로 준다: `--extra "mem=4gb smp=4"`. 키워드 이름(`smp`/`parallel`)은 버전별로 확인한다. TODO(사내검증)

## 5. 실행과 완료 판정
```
nastran case.bdf scr=yes old=no batch=no
```
- `scr=yes`: 스크래치 DB 삭제, `old=no`: 이전 출력 버전 보관 안 함, `batch=no`: 포그라운드로 끝까지 기다린다.
- 드라이버는 실행 전에 `case.f06/.f04/.log/.op2`를 지워 이전 결과를 오인하지 않게 한다.
- 판정(`check_f06`): f06이 있어야 하고, `USER FATAL MESSAGE`/`SYSTEM FATAL MESSAGE`가 없어야 하며, `* * * END OF JOB * * *`가 있어야 한다. **FATAL이 나도 END OF JOB은 찍힐 수 있으므로** 두 조건을 모두 본다.
- f06이 없으면 `.log` 끝 20줄을 출력한다(라이선스, 메모리, 디스크 오류가 여기 남는다). `.f04`에는 모듈별 실행 시간과 디스크 사용량이 있다.
- SOL 200 수렴 문구(`HARD CONVERGENCE`/`SOFT CONVERGENCE`)는 템플릿이 아직 확인하지 않는다. TODO(사내검증)

## 6. 결과 추출 → results.json
`nastran_op2_extract.py`(pyNastran `read_op2(mode="msc")`). OP2에 있는 것만 키가 생긴다.
| 키 | 의미 |
|---|---|
| `max_disp` | 정적 subcase 전체의 최대 변위 크기 (모델 단위) |
| `max_von_mises`, `max_von_mises_cquad4` 등 | 요소 von Mises 최대값. **비평균 요소값**이라 Patran 절점평균 컨투어보다 크다 |
| `reaction_fx/fy/fz` | SPC 반력 합(`--subcase` 또는 첫 정적 subcase) |
| `mass` | OP2 grid point weight(`PARAM,GRDPNT,0`), 없으면 f06 GPWG `MASS AXIS SYSTEM (S)` |
| `freq_1..n` | SOL 103 고유진동수 [Hz] (`--n-modes`) |
| `buckling_factor_1..n`, `buckling_factor_min` | SOL 105 고유값(하중배수), 최소 양수값 |
- 정적 결과는 `analysis_code == 1`인 테이블만 쓴다. SOL 105의 좌굴 모드형상이 변위 최대값에 섞이지 않는다.
- SOL은 덱 실행 제어(`SOL 105`, `SOL SEBUCKL`)에서 읽는다.
- pyNastran 1.4.1 응력 객체는 `.headers`가 아니라 `get_headers()`를 쓴다. 템플릿은 둘 다 처리한다.
- 응력 출력이 `STRESS(MAXS)`이면 `von_mises` 열이 없어 경고만 남긴다. `STRESS(VONMISES)=ALL`로 요청한다.
- 특정 영역 값은 `obj.element_node[:,0]`(요소 ID), `obj.node_gridtype[:,0]`(절점 ID)로 행을 골라 `extract()`에 추가한다.
- Nastran에는 단위가 없다. 덱의 단위계를 사용자에게 확인하고 키 설명에 남긴다.

## 7. 이미지 저장 → images/
- 라이선스 없이 matplotlib로 그린다. 형상은 `<job>.bdf`(없으면 OP2 형상)에서, 값은 OP2에서 읽는다. 쉘 요소 전체와 솔리드 외곽면을 삼각형으로 그린다.
- `images/vm.png`: 요소 von Mises를 절점 평균한 컨투어. `images/disp.png`: 변위 크기.
- `--deformed 1 --scale 10`으로 변형 형상을, `--vmin 0 --vmax 300`으로 DOE 비교용 고정 범례를 쓴다.
- SOL 103에서는 정적 결과가 없어 그리지 않는다. 모드형상 그림이나 Patran 품질 그림이 필요하면 META(`ansa-automation` 7절)를 쓴다.
- 이미지 오류는 케이스를 실패시키지 않는다(`[DDS] ... error (ignored)` 로그만 남는다).

## 8. 3D 필드 (fields/*.npz)
- 추출 단계에 `--fields stress,disp`를 주면 `fields/stress.npz`, `fields/disp.npz`(points, values, triangles, 외곽 표면 노드)를 쓴다. `--deformed 1`이면 points가 변형 좌표다.
- 형식은 `simcenter3d-automation/scripts/op2_fields.py`와 같다. 변위 필드는 그 스크립트(`--field disp`)를 post로 써도 된다. 응력(`--field vm`)은 pyNastran 1.4.1에서 `.headers` 속성 문제로 실패할 수 있으므로 이 스킬의 `--fields stress`를 쓴다.
- 학습: `dds-study.train_field_model(study_id, field="stress")`. 메쉬가 케이스마다 같으면(카드 값만 변경) 가장 정확하다.

## 9. 흔한 오류와 대처
| 증상 | 원인 / 대처 |
|---|---|
| `USER FATAL MESSAGE 9050` | 특이 강성(구속 부족, 연결 안 된 파트). SPC, RBE, 접촉을 확인한다 |
| `USER FATAL MESSAGE 2025`류 | 요소 형상 불량. 메쉬 품질을 전처리기에서 확인한다 |
| 카드 값이 이상하게 읽힘 | 8칸 초과나 탭으로 필드가 밀렸다. `bdf_card_edit.py --verify`로 다시 읽어 확인한다 |
| `no PSHELL card matched` | ID가 틀렸거나 카드가 include 파일에 있는데 그 파일을 `inputs`에 안 넣었다 |
| f06 없음 | 실행파일 경로, 라이선스, 디스크 공간. `.log`를 본다. 경로에 한글/공백을 피한다 |
| `no case.op2` | `PARAM,POST,-1` 누락(B 방식). MSC 최신 버전 기본 출력(.h5/.xdb)은 이 템플릿이 읽지 않는다 |
| `max_von_mises` 키 없음 | `STRESS` 출력 요청 없음 또는 MAXS 설정 |
| 이전 결과가 남음 | 강제 종료 후 `.f06` 잠김. 드라이버가 삭제 실패를 경고로 남긴다 |

**사내 검증 필요 항목 (TODO(사내검증))**
- MSC 버전별 자유필드 값 길이 제한(8 vs 16), 병렬 키워드(`smp=`/`parallel=`), `mem=` 표기
- Windows에서 `nastran.exe`가 `batch=no`로 끝까지 대기하는지, 종료코드 의미
- f06 GPWG 블록 배치(질량 파싱), SOL 200 수렴 문구, 다중 `METHOD` subcase의 고유치 테이블 순서
- pyNastran으로 MSC 최신 OP2(SOL 105 `BLAMA`/`LAMA`)를 읽을 때 테이블 이름과 클래스

## 파일
- `scripts/bdf_card_edit.py`: (python) small/large/free 필드 카드 편집기, regex·플레이스홀더, INCLUDE 추적, `--verify`
- `scripts/nastran_run.py`: (python) 편집 → `PARAM,POST,-1` 보장 → nastran 실행 → 판정 → 추출
- `scripts/nastran_op2_extract.py`: (python) f06 판정, OP2 → results.json, images/*.png, fields/*.npz
- [references/bdf-format.md](references/bdf-format.md): 필드 형식, 주요 카드 필드 번호, SOL별 최소 덱
