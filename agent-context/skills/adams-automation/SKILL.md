---
name: adams-automation
description: MSC Adams(Adams View/Car, Adams Solver) 다물체 동역학 배치 자동화. 데이터셋(.adm) 텍스트 편집(질량, 마커 좌표, 스프링 강성, 하중 함수)이나 Adams View 설계변수 수정, ACF(종료시간, 스텝, 적분기) 작성, 솔버 배치 실행과 .msg 판정, XRF 결과(.res) 시계열 추출, matplotlib 그래프 저장을 다룬다. 사용자가 Adams 모델 DOE/최적화, 링키지·서스펜션·기구 해석 반복 실행, 리퀘스트(request) 결과 최대값 추출, 시간 이력 그래프를 요청할 때 사용한다.
---

# Adams 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고, `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 기구, 링키지, 서스펜션 모델의 설계변수(질량, 하드포인트 좌표, 스프링/부싱 강성, 구동 함수)를 바꾸며 반복 해석할 때.
- 해석이 끝난 `.res`에서 리퀘스트, 측정값의 최대/최종값과 시간 이력 그래프를 뽑을 때.
- 가장 견고한 경로는 **데이터셋(.adm) + ACF → Adams Solver** 이다. .adm/.acf는 문서화된 텍스트 형식이고 버전 간 변화가 작다. 템플릿 스크립트는 모두 **플랫폼 venv 파이썬(Python 3)**에서 돈다.
- Adams View 모델(.bin/.cmd)의 설계변수(variable)를 쓰려면 View 배치로 값을 바꾸고 .adm을 내보낸 뒤 같은 경로로 푼다(`--view-model`).
- 내장 파이썬: Adams View Python 인터페이스(`import Adams`)는 Adams 2017.2 이상에 있다. TODO(사내검증): 사내 버전의 내장 파이썬 버전(2.7/3.x). 내장 파이썬용 코드는 py2/py3 겸용으로 쓴다.
- 라이선스: View 배치는 Adams View 토큰, 솔버는 Adams Solver 토큰을 쓴다. 라이선스 서버는 `MSC_LICENSE_FILE` 환경변수로 지정한다(사내 설정 확인).

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| ADM 편집 + ACF + 솔버 + 판정 | `python` | `adams_case_run.py` (내부에서 `mdi ru-standard i case.acf exit` 호출) |
| 결과 추출 + 그래프 | `python` | `adams_extract.py`, `adams_plots.py` |
| View 명령 파일 배치 | `adams_view` | `mdi aview ru-standard b script.cmd` |
| 솔버 단독 | `adams_solver` | `mdi ru-standard <args>` |
| View Python 스크립트 | `adams_python` | `mdi python script.py <args>` |

스터디 어댑터는 본 단계와 `post` 1개를 실행한다. 본 단계가 파라미터 반영과 솔버 실행, 판정을 하고, `post`가 results.json과 그림을 만든다.

**A. ADM 템플릿 기반 (권장)**
```json
{"tool": "python", "script": "scripts/adams_case_run.py",
 "args": ["--adm", "linkage.adm", "--adams", "C:/Program Files/MSC.Software/Adams/2023_1/common/mdi.bat"],
 "inputs": ["models/linkage.adm"],
 "post": {"tool": "python", "script": "scripts/adams_extract.py",
          "args": ["--metric", "max_arm_angle=absmax:REQ_ARM.THETA", "--metric", "peak_force=max:REQ_F.FMAG",
                   "--plot", "REQ_ARM.THETA", "--plot", "REQ_F.FMAG"]},
 "outputs": ["max_arm_angle", "peak_force"], "timeout_sec": 3600}
```
**B. Adams View 모델(설계변수) 기반**
```json
{"tool": "python", "script": "scripts/adams_case_run.py",
 "args": ["--view-model", "susp.bin", "--view-model-name", ".susp"],
 "inputs": ["models/susp.bin"],
 "post": {"tool": "python", "script": "scripts/adams_extract.py", "args": ["--metric", "max_toe=absmax:REQ_TOE.F2"]},
 "outputs": ["max_toe"]}
```
- `args`의 값은 템플릿 치환되지 않는다. `--adams`를 생략하면 환경변수 `DDS_TOOL_ADAMS`(agent.toml `[tools] adams` 경로)를 쓴다.
- 솔버 인자는 `--solver-args "ru-standard i {acf} exit"`가 기본이다. TODO(사내검증): 버전별 명령행 형식(`-c` 커맨드라인 모드 필요 여부, `i`/`b` 선택). `b`는 백그라운드로 돌아 즉시 반환될 수 있으니 판정용으로 쓰지 않는다.
- Adams Car 이벤트: `files_only`로 만든 .adm/.acf를 Car 솔버로 푼다(`mdi acar ru-solver case.acf`). 현재 이 명령의 tool 키는 없다. `--solver-args "acar ru-solver {acf}"`로 우회한다. TODO(사내검증).

## 3. 파라미터 변경
### 3-1. 플레이스홀더 `{{name}}`
.adm(또는 ACF 템플릿)의 숫자 자리에 `{{spring_k}}`를 넣는다. 값은 `%.10g` 형식으로 들어간다. params 키가 어디에도 쓰이지 않거나 값이 없는 플레이스홀더가 남으면 실패로 처리한다.
### 3-2. statement 인자 직접 교체 (`STATEMENT_MAP`)
기존 .adm을 손대지 않고 쓸 때, `adams_case_run.py` 상단에 매핑을 적는다.
```python
STATEMENT_MAP = {
    "arm_mass": ("PART", 2, "MASS"),          # PART/2, MASS=...
    "spring_k": ("SPRINGDAMPER", 1, "K"),
    "pivot_y":  ("MARKER", 10, "QP", 1),      # QP=x,y,z 의 y 성분 (한 줄 안에 있어야 함)
    "gravity":  ("ACCGRAV", None, "JGRAV"),   # id 없는 statement
}
```
- 대상 인자가 없으면 실패한다(자동 추가하지 않는다. `FUNCTION=`은 statement의 마지막 인자여야 하는 등 순서 규칙이 있기 때문이다).
- 하드포인트를 옮길 때는 그 점을 쓰는 **모든 마커**(양쪽 부품, 조인트 I/J)를 함께 바꾼다. View에서는 design point/파라메트릭 표현이 자동으로 따라가므로 형상 변경이 많으면 B 방식이 낫다.
- 이어지는 줄은 `,`로 시작하고 주석은 `!` 뒤에 둔다. 줄 길이가 80자를 넘으면 경고를 낸다. TODO(사내검증): C++ 솔버의 줄 길이 제한.
### 3-3. View 설계변수
`--view-model`이면 `params`의 각 키를 `variable modify variable_name=<model>.<key> real_value=<값>`으로 바꾸고 `file adams_data_set write`로 .adm을 내보낸다. 이름이 다르면 `VIEW_DV_MAP`에 적는다. 명령 상세: `references/view-cmd-python.md`

## 4. 해석조건 변경
| 대상 | 방법 |
|---|---|
| 종료시간, 출력 스텝 | params `_end_time`, `_steps` → `SIMULATE/DYNAMIC, END=, STEPS=` |
| 해석 종류 | `_sim_type`: dynamic, kinematic, static. `_static_first: 1`이면 정적 평형 후 동해석 |
| 적분기, 오차 | `_integrator`(GSTIFF, WSTIFF, HHT …), `_integrator_error` → `INTEGRATOR/GSTIFF, ERROR=` |
| 중력 | `ACCGRAV/ IGRAV=, JGRAV=, KGRAV=` |
| 질량, 관성 | `PART/id, MASS=, IP=ixx,iyy,izz` |
| 스프링, 부싱 | `SPRINGDAMPER/id, K=, C=, LENGTH=`, `BUSHING/id, K=kx,ky,kz, C=` |
| 하중, 구동 | `SFORCE/id, FUNCTION=`, `MOTION/id, FUNCTION=` (예: `STEP(TIME,0,0,0.1,{{f_max}})`) |
- 단위계는 `UNITS/` statement를 먼저 확인한다(mm-kg-s-N이면 중력 9806.65).
- 더 복잡한 ACF(구간별 적분기, `DEACTIVATE/` 등)는 `--acf template.acf`로 직접 주고 `{{}}`로 값을 넣는다. 첫 줄은 데이터셋 이름, 둘째 줄은 출력 파일 접두어(`case`)여야 한다.
- 형식 상세: `references/adm-acf-format.md`

## 5. 실행과 완료 판정
- `adams_case_run.py`는 이전 출력(.res/.msg/.req/.gra/.out)을 지우고 솔버를 실행한다. 종료코드는 참고만 한다.
- 실패 판정: `case.msg`에서 오류 블록(`---- START: ERROR ----` 등 `ERROR_PATTERNS`)이 보이거나 `case.res`가 없거나 비어 있으면 실패. 오류 줄을 run.log에 남긴다.
- 도중 종료 판정: `adams_extract.py`가 `.res`의 마지막 시간이 `_end_time`에 도달했는지 확인한다. 미달이면 실패다(적분 실패로 중간에 멈춘 경우).
- TODO(사내검증): 실제 성공/실패 케이스의 .msg 문구로 `ERROR_PATTERNS`를 보정한다.

## 6. 결과 추출 → results.json
`.adm`에 `RESULTS/XRF`가 없으면 스크립트가 `END` 앞에 넣는다. XRF(.res)는 XML이라 표준 라이브러리로 읽는다.
```
python adams_extract.py --list                                  # 시계열 이름 확인 (ENTITY.COMPONENT)
python adams_extract.py --metric peak_force=max:REQ_F.FMAG --metric angle_end=final:REQ_ARM.THETA --metric x_05=at0.5:REQ_ARM.X
```
- stat: `max, min, absmax, mean, rms, final, initial, range, at<t>`. `--metric`이 없으면 모든 시계열의 `absmax_<이름>`을 쓴다. `end_time_reached`도 함께 쓴다.
- 값을 결과로 남기려면 모델에 `REQUEST/`(DISPLACEMENT/FORCE 또는 `F2=` 등 사용자 함수)를 정의한다. View의 measure도 내보낸 .adm에서는 REQUEST로 바뀐다. TODO(사내검증): measure가 어떤 Entity/Component 이름으로 .res에 들어가는지 `--list`로 확인.
- XRF 구조(StepMap의 Component id 순서 = Step 숫자 열 순서)는 가정이다. TODO(사내검증): 실제 .res 하나로 `--list` 결과와 GUI PostProcessor 값을 대조한다.

## 7. 이미지 저장 → images/
- `adams_extract.py --plot KEY`(또는 `adams_plots.py KEY ... [--overlay]`)가 matplotlib으로 `images/<key>.png`를 만든다. 이미지 실패는 케이스 실패로 보지 않는다.
- 3D 애니메이션 캡처는 Adams PostProcessor 배치가 필요하다. TODO(사내검증): PostProcessor 배치 명령(`ppt`)과 이미지 내보내기 명령은 확인하지 않았다. GUI에서 기록한 명령 파일로 확인한 뒤에 쓴다.

## 8. 3D 필드 (fields/*.npz)
강체 다물체 해석에는 노드 필드가 없어서 기본으로 만들지 않는다. 대안은 다음과 같다.
- 유연체(MNF, FLEX_BODY)는 Adams Durability로 응력/변형을 내보내거나 모달 좌표를 nCode, FE 후처리로 넘긴다(`ncode-automation` 스킬). TODO(사내검증).
- 마커 궤적(점 시계열)은 REQUEST로 뽑아 결과 스칼라로 쓴다.

## 9. 흔한 오류와 대처
- `placeholder {{x}} has no value`: params.json 키와 플레이스홀더 철자가 다르다.
- `argument K= not found`: 인자가 이어지는 줄에 있으면 찾는다. 벡터 인자(QP)가 줄을 넘어가면 못 찾으니 한 줄로 고친다.
- 정적 평형 실패: `_static_first` 해제, 초기 위치 확인, `INTEGRATOR` 오차 완화.
- 적분 실패로 중간 종료: `.msg`의 마지막 시간과 오류를 확인한다. `_steps`를 늘리거나 `HMAX`를 준다.
- `.res`가 XML이 아니라는 오류: `RESULTS/XRF`가 빠졌거나 다른 형식으로 저장됐다.
- 로그 위치: `run.log`(플랫폼), `adams_solver_stdout.txt`, `case.msg`, View 배치는 `adams_view_stdout.txt`와 `aview.log`.
- 케이스 경로에 한글과 공백을 쓰지 않는다.

## 파일
- `scripts/adams_case_run.py`: (python) params → (View 설계변수 → .adm 내보내기) → .adm 편집 → ACF → 솔버 → 판정
- `scripts/adams_extract.py`: (python) .res(XRF) → 지표 → results.json, `--plot`이면 그림
- `scripts/adams_plots.py`: (python) 시계열 그래프 → images/*.png
- `references/adm-acf-format.md`, `references/view-cmd-python.md`
