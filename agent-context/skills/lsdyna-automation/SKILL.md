---
name: lsdyna-automation
description: LS-DYNA 키워드 자동화와 결과 추출. *PARAMETER/*PARAMETER_EXPRESSION 기반 파라미터화, 고정폭(10자) 필드를 지키는 안전한 텍스트 치환, *INCLUDE 구조, 재료/쉘 두께/속도/하중/종료시간/질량 스케일링 변경, 솔버 실행(i= ncpu= memory=)과 Normal termination 판정, glstat/matsum/rcforc/nodout ASCII 파싱, lasso-python으로 binout/d3plot 읽기, LS-PrePost cfile 배치 이미지를 다룬다. 사용자가 LS-DYNA 충돌/낙하/성형 해석의 DOE, 최적화, 결과 추출, 이미지 캡처 스크립트를 요청할 때 사용한다.
---

# LS-DYNA 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고, `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 키워드(.k/.key/.dyn) 모델의 설계변수(두께, 재료, 충돌 속도, 종료시간 등)를 바꾸며 반복 해석할 때.
- 해석이 끝난 폴더에서 에너지, 접촉력, 변위, 응력, 변형률을 숫자로 뽑거나 그림을 저장할 때.
- LS-DYNA에는 내장 파이썬이 없다. 전처리, 실행, 후처리 스크립트는 **플랫폼 venv 파이썬(tool 키 `python`, Python 3)**으로 작성한다.
- binout/d3plot 읽기에는 **lasso-python을 플랫폼 venv에 설치해야 한다**(`pip install lasso-python`). ASCII 파서는 표준 라이브러리만 쓴다.
- 라이선스: `LSTC_LICENSE`, `LSTC_LICENSE_SERVER` 같은 환경변수가 실행 환경에 설정돼 있어야 한다(사내 설정 확인).

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| 솔버 단독 | `lsdyna` | `lsdyna i=main.k ncpu=4 memory=200m` |
| 이미지 | `lsprepost` | `lsprepost -nographics c=dds_images.cfile` |
| 파라미터 반영 + 실행 + 추출 | `python` | `dyna_case_run.py` |

스터디 어댑터는 본 단계 1개와 `post` 1개만 실행한다. 파라미터를 반영한 뒤 솔버를 돌려야 하므로 **본 단계는 `python` + `dyna_case_run.py`**(내부에서 솔버를 서브프로세스로 실행)로 둔다.
```json
{"tool": "python", "script": "scripts/dyna_case_run.py",
 "args": ["--lsdyna", "C:/LSDYNA/program/lsdyna_smp_d_R13.1_winx64.exe", "--ncpu", "-4", "--memory", "400m"],
 "inputs": ["models/crash/main.k", "models/crash/mesh.k", "models/crash/params.k"],
 "post": {"tool": "python", "script": "scripts/dyna_lasso_extract.py", "args": ["--nodes", "1001"]},
 "outputs": ["max_disp", "max_contact_force_1", "final_internal_energy"],
 "timeout_sec": 14400}
```
- `args`에는 `{lsdyna}` 같은 치환이 되지 않는다. 실행파일 전체 경로를 적거나 환경변수 `LSDYNA_EXE`, `LSPREPOST_EXE`를 쓴다.
- MPP: `mpiexec -np 8 lsdyna_mpp.exe i=main.k memory=...` 형식이다. `dyna_case_run.run_solver()`의 `cmd`를 고쳐서 쓴다. TODO(사내검증): 사내 MPI 종류(Intel MPI/MS-MPI)와 명령.
- `ncpu=-4`처럼 음수를 주면 SMP 일관성 옵션이 켜진다(CPU 수와 무관하게 같은 결과, DOE 비교에 권장).
- `memory=`는 워드 단위다(`200m` = 2억 워드). 부족하면 d3hsp에 메모리 오류가 남는다.

## 3. 파라미터 변경
### 3-1. *PARAMETER (권장)
설계변수를 `*PARAMETER` 한 곳에 모으고 카드에서는 `&name`으로 참조한다.
```
*PARAMETER
$#   prmr1      val1     prmr2      val2
R      thk       1.5R      vel   -13.889
*SECTION_SHELL
$#   secid    elform      shrf       nip     propt
         1        16     0.833         5         1
$#      t1        t2        t3        t4
      &thk      &thk      &thk      &thk
*PARAMETER_EXPRESSION
R     thk2   &thk*1.2
```
- `dyna_case_run.py`는 케이스 폴더의 모든 키워드 파일에서 `*PARAMETER` 카드를 찾아 params.json 값으로 바꾼다. 고정폭과 콤마 형식을 모두 처리하고, 값은 10자에 맞게 서식화한다. **params 키가 어디에도 반영되지 않으면 실패로 처리한다**(오타 방지).
- params 키가 길면 스크립트 상단 `NAME_MAP = {"thickness": "thk"}`으로 매핑한다.
- `I` 타입 파라미터에는 반올림한 정수가 들어간다.

### 3-2. 플레이스홀더 템플릿
`{{name}}`을 값으로 치환하고 **10자 폭으로 오른쪽 정렬**한다. 플레이스홀더는 반드시 **필드 시작 열(1, 11, 21, …열)**에서 시작해야 한다. 아니면 그 줄을 콤마 자유형식으로 쓴다(`1,16,0.833,5`, `{{thk}},{{thk}},{{thk}},{{thk}}`).

### 3-3. 기존 카드 직접 수정 (`dyna_keyword_tools.py`)
*PARAMETER가 없는 기존 모델은 필드 위치만 교체하는 함수를 쓴다.
```python
from dyna_keyword_tools import read_lines, write_lines, set_shell_thickness, set_mat_field, set_termination
lines = read_lines("main.k")
set_shell_thickness(lines, secid=3, t=1.8)      # T1~T4
set_mat_field(lines, mid=1, field=2, value=2.1e5)  # *MAT 카드1의 E
set_termination(lines, 0.05)
write_lines("main.k", lines)
```
- 절대 `str.replace("1.5", "1.8")`처럼 값 문자열을 치환하지 않는다. 다른 카드의 같은 숫자까지 바뀐다.
- 한 줄 길이를 바꾸는 치환을 하지 않는다(고정폭 필드가 밀린다).

### 3-4. *INCLUDE 구조
메쉬, 재료, 파라미터를 파일로 나누고 `main.k`에서 `*INCLUDE`한다. **스터디 `inputs`에 인클루드 파일을 모두 넣어야** 케이스 폴더로 복사된다. 트리 확인: `python dyna_keyword_tools.py includes main.k`.
- 상세: `references/keyword-editing.md`

## 4. 해석조건 변경 (카드와 필드)
| 대상 | 카드(필드) |
|---|---|
| 쉘 두께 | `*SECTION_SHELL` 카드2 T1~T4 |
| 재료 | `*MAT_ELASTIC`(RO, E, PR), `*MAT_PIECEWISE_LINEAR_PLASTICITY`(SIGY, ETAN, LCSS 곡선) |
| 강제 운동 | `*BOUNDARY_PRESCRIBED_MOTION_NODE/SET/RIGID`의 SF(배율)나 LCID 곡선, VAD(0 속도, 1 가속도, 2 변위) |
| 초기 속도 | `*INITIAL_VELOCITY_GENERATION`(VX, VY, VZ), `*INITIAL_VELOCITY` 카드2 |
| 하중 크기 | `*DEFINE_CURVE` SFO(세로축 배율) |
| 종료시간 | `*CONTROL_TERMINATION` ENDTIM |
| 시간 간격/질량 스케일링 | `*CONTROL_TIMESTEP` TSSFAC, DT2MS(음수면 그 시간 간격을 유지하도록 질량 추가) |

- 단위계(mm-ms-kg-kN, mm-s-tonne-N 등)를 모델에서 먼저 확인하고 값을 넣는다. 속도 50 km/h = 13.889 mm/ms = 13889 mm/s.
- 질량 스케일링을 쓰면 glstat의 added mass 비율을 results에 함께 남긴다.

## 5. 해석 실행과 완료/실패 판정
- `d3hsp`, `messag`(SMP), `mes0000…`(MPP) 끝부분에서 공백을 모두 지운 뒤 `Normaltermination`이 있으면 성공, `Errortermination`이 있으면 실패로 본다. 원본 문구는 `N o r m a l    t e r m i n a t i o n`이다.
- 솔버 종료코드는 신뢰하지 않는다.
- 실패하면 `*** Error` 줄을 run.log에 출력한다(템플릿에 반영됨).
- 정상 종료여도 결과가 이상할 수 있다. energy ratio(≈1), hourglass/internal 비율(<10%), added mass를 확인한다.

## 6. 결과 추출 → results.json
| 원천 | 도구 | 비고 |
|---|---|---|
| glstat, matsum, rcforc, nodout(ASCII) | `dyna_ascii_parsers.py` | 표준 라이브러리. `*DATABASE_xxx` BINARY=1 또는 3 필요 |
| binout | `dyna_lasso_extract.py` (`lasso.dyna.Binout`) | MPP 기본 출력. 변수 이름은 `--list`로 확인 |
| d3plot | `dyna_lasso_extract.py` (`lasso.dyna.D3plot`) | 최대 변위, 쉘/솔리드 Mises, 유효소성변형률 |
- `dyna_case_run.py`는 기본으로 glstat, rcforc를 읽고, 없으면 binout을 읽는다. 더 필요한 값은 `extract_results()`에 추가하거나 `post`에서 위 스크립트로 합친다(기존 results.json에 merge).
- 상세: `references/results-extraction.md`

## 7. 이미지 저장 (LS-PrePost cfile)
```
open d3plot "C:/case/d3plot"
ac
anim last
fringe 9
pfringe
print png "C:/case/images/vonmises_last.png" LANDSCAPE nocompress gamma 1.000 opaque enlisted "OGL1x1"
quit
```
- `dyna_case_run.py --lsprepost <exe>`이면 위 cfile을 만들어 `lsprepost -nographics c=...`로 실행한다. 이미지 실패는 케이스 실패로 보지 않는다.
- **명령 문법은 버전마다 다르다.** GUI에서 같은 조작을 한 뒤 종료하면 남는 `lspost.cfile`(명령 기록)로 반드시 확인하고 템플릿을 고친다. 상세: `references/lsprepost-cfile.md`

## 8. 자주 하는 실수와 디버깅
- **필드 밀림**: 값이 10자를 넘거나 탭 문자가 들어가면 생긴다. 탭을 쓰지 않는다. 고친 카드를 `$#` 눈금 주석 줄과 나란히 출력해 열을 확인한다.
- 정수 필드에 실수(`5.`)를 넣는 실수.
- `&name` 참조가 `*PARAMETER` 정의보다 앞에 있다.
- 인클루드 파일을 스터디 inputs에 넣지 않아 케이스 폴더에 없다(d3hsp에 file not found 오류).
- 이전 실행의 d3plot, binout이 남아 있어 결과가 섞인다. 케이스 폴더는 플랫폼이 새로 만들지만, 수동 재실행할 때는 지운다.
- 로그 위치: `run.log`(플랫폼), `lsdyna_stdout.txt`(솔버 표준출력), `d3hsp`, `messag`/`mes*`.
- 경로에 한글과 공백을 쓰지 않는다.

## 9. 사내 검증 필요 항목 (TODO(사내검증))
- LS-PrePost cfile 명령: `anim last`, `fringe 9`(von Mises 번호), `pfringe`, `print png` 옵션, 뷰 명령, `-nographics` 렌더링. (`open d3plot`, `fringe 7`, `quit`은 공개 예제로 확인됨)
- glstat/matsum/rcforc/nodout ASCII 줄 형식, R12 이후 master/slave → main/secondary 표기.
- lasso Binout 변수 이름(nodout, rcforc), D3plot 파트 필터와 `state_array_filter` 옵션.
- `*DATABASE_*` BINARY 기본값(SMP/MPP).
- d3hsp의 질량 요약 문구.
- *PARAMETER 이름 규칙(길이, 밑줄), 자유형식 *PARAMETER 카드.
- MPP 실행 명령, 라이선스 환경변수.

## 파일
- `scripts/dyna_case_run.py`: (python) 파라미터 반영 → 솔버 실행 → 판정 → glstat/rcforc(또는 binout) 추출 → (선택) 이미지 → results.json
- `scripts/dyna_keyword_tools.py`: 고정폭 필드 편집 라이브러리/CLI(두께, 재료, 종료시간, 강제운동 SF, 인클루드 트리)
- `scripts/dyna_ascii_parsers.py`: glstat/matsum/rcforc/nodout 파서, 후처리 CLI
- `scripts/dyna_lasso_extract.py`: binout/d3plot 추출(lasso-python 필요)
- `scripts/dyna_images.cfile`: LS-PrePost 이미지 cfile 템플릿(TODO(사내검증))
- `references/keyword-editing.md`, `references/results-extraction.md`, `references/lsprepost-cfile.md`

## 3D 필드 저장 (형상·필드 학습 → 3D Simulator)
케이스 폴더에 `fields/<이름>.npz`(points, values, triangles — 외곽 표면 노드)를 남기면, 플랫폼이 서버로 올린다. 그다음 `dds-study.train_field_model(study_id, field="<이름>")`으로 '설계변수 → 3D 형상 + 분포'를 학습하고 Simulator에서 3D로 본다.
리메쉬로 노드 수가 케이스마다 달라도 서버가 기준 메쉬에 자동으로 보간한다. 단, 형상이 크게 달라지면(위상 변화) 정확도가 떨어진다.

```
{"tool": "python", "script": "scripts/dyna_fields.py", "args": ["d3plot", "--field", "vm", "--state", "-1", "--name", "stress", "--deformed", "1"]}
```
- 충돌 해석은 `--deformed 1`로 마지막 시점의 변형 형상까지 학습하면, Simulator에서 '찌그러진 모양 + 응력'을 바로 볼 수 있다.
- `--field eps`(유효 소성 변형률), `disp`(변위 크기)도 된다. d3plot에 해당 결과가 저장되도록 `*DATABASE_EXTENT_BINARY`를 확인한다.
