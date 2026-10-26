---
name: recurdyn-automation
description: FunctionBay RecurDyn 다물체 동역학 배치 자동화. 솔버 입력(.rmd) 템플릿의 플레이스홀더 치환(질량, 강성, 감쇠, 하중, 종료시간), 솔버 배치 실행과 로그/출력 파일 판정, Plot 곡선 내보내기(CSV/TXT) 결과 추출, matplotlib 그래프 저장, ProcessNet(Parametric Value 변경, 해석, 곡선 내보내기) 연동 방향을 다룬다. 사용자가 RecurDyn 모델 DOE/최적화, 기구·체인·벨트·트랙 해석 반복 실행, 리커다인 결과 최대값 추출, 시간 이력 그래프를 요청할 때 사용한다.
---

# RecurDyn 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고, `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- RecurDyn 모델의 스칼라 설계변수(질량, 스프링/부싱 강성, 감쇠, 하중 크기, 마찰계수, 종료시간)를 바꾸며 반복 해석할 때.
- 해석 결과 곡선(위치, 속도, 반력, 접촉력)에서 최대/최종값과 그래프를 뽑을 때.
- 버전: RecurDyn V9R4 이후, 2023/2024 계열을 가정한다. 파일: `.rdyn`(모델), `.rmd`(솔버 입력), `.rad`(애니메이션 결과), `.rpl`(플롯 결과).
- 내장 스크립팅은 **ProcessNet**(.NET, VSTA 기반 C#/VB.NET)이다. TODO(사내검증): 사내 버전에서 Python ProcessNet 지원 여부와 Python 버전.
- 이 스킬의 템플릿은 모두 **플랫폼 venv 파이썬(Python 3)**에서 돈다. RecurDyn 내부 API를 쓰지 않으므로 버전 의존이 적다.
- 라이선스: GUI(전처리, ProcessNet)와 솔버 라이선스가 따로일 수 있다. 툴킷(Chain, Belt, Track, FFlex 등) 모듈 라이선스도 필요하다. TODO(사내검증): 솔버 단독 실행에 필요한 라이선스 기능 이름.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| 템플릿 치환 + 솔버 + 판정 | `python` | `rd_case_run.py` (내부에서 `recurdyn_solver` 경로 호출) |
| 곡선 결과 추출 + 그래프 | `python` | `rd_extract.py`, `rd_plots.py` |
| 솔버 단독 | `recurdyn_solver` | `{recurdyn_solver} <args>` |
| GUI/ProcessNet 매크로 | `recurdyn` | `{recurdyn} <args>` |

```json
{"tool": "python", "script": "scripts/rd_case_run.py",
 "args": ["--template", "crank.rmd", "--solver-args", "{rmd}", "--expect", "*.rad"],
 "inputs": ["models/crank.rmd"],
 "post": {"tool": "python", "script": "scripts/rd_extract.py",
          "args": ["--csv", "*.csv", "--metric", "max_force=absmax:Joint1_FM", "--metric", "z_end=final:Body1_Pos_Z",
                   "--plot", "Joint1_FM"]},
 "outputs": ["max_force", "z_end"], "timeout_sec": 7200}
```
- 솔버 경로는 `--solver`를 생략하면 환경변수 `DDS_TOOL_RECURDYN_SOLVER`(agent.toml `[tools] recurdyn_solver`)를 쓴다.
- TODO(사내검증): **솔버 배치 명령행 형식**(실행파일 이름, .rmd 인자 앞에 옵션이 필요한지, 스레드 수 옵션)을 사내 설치본의 도움말로 확인하고 `--solver-args`에 반영한다. 템플릿 기본값 `{rmd}`는 확인되지 않은 가정이다.
- 곡선 CSV를 솔버만으로 얻을 수 없으면 `--post-exe`/`--post-args`로 내보내기 단계(ProcessNet 매크로)를 붙인다. 6절 참고.

## 3. 파라미터 변경
### 3-1. .rmd 플레이스홀더 (배치, 권장)
1. GUI에서 모델을 완성하고 솔버 입력(.rmd)을 내보낸다. TODO(사내검증): 메뉴 위치(해석 대화상자의 솔버 파일 내보내기)와 .rmd만으로 솔버가 도는지.
2. 텍스트 편집기로 .rmd를 열어 바꿀 값(예: 질량 `2.5`)을 `{{arm_mass}}`로 바꾼다. 값을 찾기 쉽도록 GUI에서 일부러 독특한 값(예: 2.34567)을 넣고 내보내 검색한다.
3. 스터디 `inputs`에 이 .rmd를 넣는다. `rd_case_run.py`가 params.json 값으로 치환한다(`%.10g`).
- params 키가 어느 템플릿에도 쓰이지 않거나, 값이 없는 플레이스홀더가 남으면 실패로 처리한다.
- **Parametric Value(PV)나 Parametric Point(PP)로 연결된 값은 .rmd에서 이미 숫자로 풀려 있다.** 하나의 PV가 여러 자리(마커 좌표, 형상)에 퍼지므로, 형상/하드포인트 변경은 이 방식으로 하지 말고 3-2를 쓴다.
### 3-2. ProcessNet (GUI 자동화, 형상 변경 포함)
.rdyn을 열어 PV 값을 바꾸고 해석과 곡선 내보내기까지 하는 ProcessNet 프로그램을 만든다. 형상과 연결 관계가 자동으로 갱신되므로 하드포인트 변경에 맞다. 개요와 검증 항목: `references/processnet-notes.md`
### 3-3. AutoDesign (RecurDyn 내장 DOE/최적화)
RecurDyn 안에서 끝나는 DOE에는 AutoDesign을 쓸 수 있다. 다만 플랫폼 스터디(케이스별 params.json, 서로게이트 학습)와 연결되지 않으므로 DDS 스터디에는 3-1/3-2를 쓴다.

## 4. 해석 조건 변경
| 대상 | 방법 |
|---|---|
| 종료시간, 출력 스텝 수 | .rmd의 해당 값을 `{{_end_time}}`, `{{_steps}}`로 바꾼다(제어 키는 미사용 검사에서 제외) |
| 질량, 관성 | 바디 질량 자리 플레이스홀더 |
| 스프링/부싱 강성, 감쇠 | 힘 요소의 계수 자리 플레이스홀더 |
| 하중, 구동 크기 | 식(expression)의 상수를 플레이스홀더로. 식 전체를 바꾸지 않는다 |
| 마찰, 접촉 강성 | 접촉 파라미터 자리 플레이스홀더 |
| 적분기 오차, 최대 스텝 | 해석 옵션 자리 플레이스홀더. TODO(사내검증): .rmd의 해석 옵션 표기 |
- 단위계를 먼저 확인한다(RecurDyn 기본은 N-kg-mm-s 계열이 많다. 모델 설정 확인).
- 같은 숫자가 여러 곳에 있으면 원하는 자리만 바꿨는지 GUI에서 독특한 값으로 다시 확인한다.

## 5. 실행과 완료 판정
`rd_case_run.py`는 다음 순서로 판정한다. 하나라도 걸리면 종료코드 1이다.
1. 솔버 표준출력(`recurdyn_solver_stdout.txt`)과 실행 중 새로 생긴 `*.msg`, `*.log`에서 `ERROR_PATTERNS`(error:, fatal, failed to converge 등)를 찾는다.
2. `--expect` 패턴(기본 `*.rad`)의 파일이 실행 뒤에 새로 생겼는지 확인한다.
3. `SUCCESS_PATTERNS`가 설정돼 있으면 그 문구가 로그에 있어야 한다.
4. `rd_extract.py`가 곡선의 마지막 시간이 `_end_time`에 도달했는지 확인한다(도중 종료 감지).
- 종료코드는 참고만 한다. TODO(사내검증): 실제 성공/실패 로그의 문구로 `ERROR_PATTERNS`, `SUCCESS_PATTERNS`를 보정한다.

## 6. 결과 추출 → results.json
`rd_extract.py`는 시간 열과 값 열로 된 표 파일(CSV/TXT)을 읽는다. 구분자, 머리글, 단위 줄을 자동으로 처리한다.
```
python rd_extract.py --csv "*.csv" --list
python rd_extract.py --csv "*.csv" --metric max_force=absmax:Joint1_FM --metric pos_05=at0.5:Body1_Pos_Z
```
- stat: `max, min, absmax, mean, rms, final, initial, range, at<t>`. 파일이 여러 개면 키는 `<파일명>.<열 이름>`이다.
- **곡선 CSV를 만드는 경로**(셋 중 하나, TODO(사내검증)):
  - (a) RecurDyn Plot에서 곡선을 Export(CSV/텍스트). 수동이므로 탐색용이다.
  - (b) ProcessNet 매크로로 .rpl의 곡선을 CSV로 쓴다. `--post-exe`로 연결한다.
  - (c) 솔버가 텍스트 결과를 직접 쓰는 출력 옵션이 있으면 그것을 쓴다.
- .rad/.rpl은 바이너리 형식이고 공개 형식 문서를 확인하지 못했다. 직접 파싱하지 않는다.

## 7. 이미지 저장 → images/
- `rd_extract.py --plot KEY` 또는 `rd_plots.py KEY ... [--overlay]`가 matplotlib으로 `images/<key>.png`를 만든다. 이미지 실패는 케이스 실패가 아니다.
- 3D 애니메이션 캡처는 GUI(ProcessNet)에서만 가능하다. TODO(사내검증): ProcessNet의 뷰 캡처 API.

## 8. 3D 필드 (fields/*.npz)
강체 해석에는 노드 필드가 없다. 유연체(RFlex/FFlex)의 응력, 변형 컨투어는 GUI에서 FE 형식이나 텍스트로 내보낼 수 있으면 `nodes(x,y,z) + value` 표를 만들어 npz로 바꾼다(`ncode-automation/scripts/ncode_fields.py`의 BDF+CSV 방식을 참고). TODO(사내검증): FFlex 결과 내보내기 형식.

## 9. 흔한 오류와 대처
- `params not used in any template`: params 키 철자, 또는 플레이스홀더를 넣지 않은 .rmd를 inputs에 넣었다.
- `expected output not produced: *.rad`: 솔버가 시작하지 못했다(경로, 라이선스, 인자). `recurdyn_solver_stdout.txt`를 본다.
- 결과 파일 이름이 .rmd 이름과 다르면 `--expect`를 실제 패턴으로 준다.
- 곡선이 `_end_time`보다 일찍 끝남: 적분 실패. 최대 스텝, 접촉 강성, 초기 간섭을 확인한다.
- 케이스 경로에 한글과 공백을 쓰지 않는다. 결과 파일 이름도 ASCII로 둔다.
- 로그 위치: `run.log`(플랫폼), `recurdyn_solver_stdout.txt`, 케이스 폴더의 `*.msg`/`*.log`.

## 파일
- `scripts/rd_case_run.py`: (python) 템플릿 치환 → 솔버 → 판정 → (선택) 곡선 내보내기 단계
- `scripts/rd_extract.py`: (python) 곡선 CSV/TXT → 지표 → results.json, `--plot`이면 그림
- `scripts/rd_plots.py`: (python) 곡선 그래프 → images/*.png
- `references/processnet-notes.md`: ProcessNet 연동 개요와 검증 항목
