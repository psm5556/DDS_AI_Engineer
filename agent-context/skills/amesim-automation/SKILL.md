---
name: amesim-automation
description: Simcenter Amesim 1D 시스템 시뮬레이션 배치 자동화(Amesim 내장 Python, AMEPython). .ame 모델 열기, 파라미터 변경(param@alias 데이터패스), 실행 조건(종료시간, 출력간격, 허용오차) 변경, 실행, 변수 시계열에서 최종값·최대·최소·RMS·정착시간을 계산해 results.json으로 쓰고, matplotlib 그래프를 images/에 저장한다. 사용자가 Amesim 모델 자동화, 파라메트릭/DOE/최적화, Amesim 결과 추출을 요청할 때 사용한다.
---

# Simcenter Amesim 자동화 (Python)

플랫폼 공통 규약은 `docs/skill-contract.md`를 따른다. 입력은 케이스 폴더의 `params.json`, 출력은 `results.json`과 `images/*.png`이다.

## 1. 언제 쓰나 / 전제
- 유압·공압·열관리·전동 같은 1D 시스템 모델(.ame)의 파라미터를 바꿔 가며 반복 실행할 때 쓴다(DOE, 최적화, 민감도 분석).
- Python 인터페이스는 두 계열이다. **새 스크립트는 (A)로 작성한다.**

| 계열 | 모듈 | 성격 | 라이선스 |
|---|---|---|---|
| (A) Simcenter Amesim **API (Circuit API)** | `ame_apy` (`AMEInitAPI`, `AMEOpenAmeFile`, `AMESetParameterValue`, `AMERunSimulation`, `AMEGetVariableValues` ...) | 모델을 열고, 수정하고, 컴파일하고, 실행하고, 결과까지 한 프로세스에서 처리 | Amesim GUI와 Libraries 라이선스. `AMEInitAPI()`에서 체크아웃, `AMECloseAPI()`에서 반납 |
| (B) **Simulation Scripting** | `amesim` (`ameputp`, `amegetsimopt`, `amerunsingle`, `ameloadvarst`, `ameputbatch`, `amerunbatch` ...) | 파라미터와 실행 중심. 모델을 먼저 체크, 컴파일, 언팩(`AMECirChecker`, `AMELoad`)해야 함 | Runtime 라이선스 |

- 함수별 세부 사항과 신뢰도는 [references/ame-apy-api.md](references/ame-apy-api.md)(A)와 [references/legacy-scripting-and-cli.md](references/legacy-scripting-and-cli.md)(B, 명령행, 결과 파일, 환경)에 정리했다.
- 흔히 언급되는 `amesetp`, `amegetp`, `ameloadt`, `amerun`이라는 이름은 **공개 자료에서 확인되지 않았다**. (B) 계열에서 확인된 이름은 `ameputp`, `amerunsingle`, `ameloadvarst` 등이다. 지어낸 이름은 쓰지 않는다.
- **내장 Python 버전**: `AMEPython.exe`는 버전마다 다르다(예: 2021.1은 Python 3.6). 구버전은 2.7일 수 있으므로 템플릿은 **py2/py3 겸용**으로 작성하며, f-string을 쓰지 않는다.
- API 이름과 시그니처를 가장 확실하게 확인하는 방법은 다음 두 가지다.
  1. `scripts/probe_api.py`를 실행해 설치본의 `ame_apy`와 `amesim` 함수 목록, help 텍스트를 `api_probe.txt`로 받는다.
  2. Amesim GUI의 **Generate model script**(모델을 Python 코드로 변환) 기능과 Tools → Python command interpreter의 `help(ame_apy.AMExxx)`를 쓴다.

## 2. 실행 방법
- tool 키 `amesim`. 명령 템플릿은 `{amesim_python} {script} {args}`이다.
- `amesim_python`은 `%AME%\win64\AMEPython.exe`이다(예: `C:\Program Files\Simcenter\2021.1\Amesim\win64\AMEPython.exe`).
- 필요한 환경변수는 다음과 같다. 상세는 references에 있다.
  - `AME`: Amesim 설치 폴더
  - `PATH`: `%AME%`, `%AME%\win64`를 앞에 추가
  - 라이선스: `LMS_LICENSE`, `SC1D_LICENSING_TYPE`. TODO(사내검증): 사내 값을 확인한다.
- AMEPython은 시작할 때 `Starting Python ...` 배너를 표준출력에 찍는다. 로그에 있어도 정상이다.
- 스터디 어댑터 예시는 다음과 같다.
  ```json
  {"tool": "amesim", "script": "scripts/run_case.py", "args": ["valve_circuit.ame"],
   "inputs": ["models/valve_circuit.ame"], "outputs": ["p_out_max", "x_spool_settling_time"]}
  ```
- **라이선스 주의**: `AMEInitAPI()`가 라이선스를 잡는다. 반드시 `finally`에서 `AMECloseAPI()`를 호출한다. 라이선스가 1개뿐이면 GUI를 열어 둔 상태에서 배치 API가 실패할 수 있다(Siemens KB). DOE 동시 실행 수는 라이선스 수 이하로 제한한다.
- **스레드 주의**: `AMERunSimulation`은 메인 스레드가 아닌 곳에서 호출하면 멈춘다는 보고가 있다. 스크립트는 단일 스레드로 작성한다.

### 템플릿
| 파일 | tool | 용도 |
|---|---|---|
| `scripts/run_case.py` | `amesim` | 표준 케이스 실행. params.json을 데이터패스에 반영하고, 실행 조건을 설정하고, 실행하고, 변수 통계를 results.json에 쓰고, 시계열 CSV와 PNG를 저장한다 |
| `scripts/probe_api.py` | `amesim` | 설치된 API 함수 목록과 help를 `api_probe.txt`로 받는다(라이선스 체크아웃 없음) |
| `scripts/plot_timeseries.py` | `python` | AMEPython에 matplotlib이 없을 때 `ts_*.csv`를 플랫폼 venv에서 PNG로 그린다 |

## 3. 파라미터 변경
- 파라미터는 **데이터패스** `파라미터명@컴포넌트별칭`으로 지정한다(예: `k@elect01`, `x1@mass_friction2port`). 별칭은 Sketch 모드에서 컴포넌트를 우클릭해 지정하거나 확인한다. 파라미터명은 Parameter 모드의 파라미터 우클릭 → 데이터패스 복사로 확인한다. TODO(사내검증): 메뉴 이름은 버전별로 다르다.
- `run_case.py`의 `PARAM_MAP`에 `params.json 키 → 데이터패스`를 적는다. 매핑되지 않은 숫자 키가 있으면 DOE 결과가 조용히 틀어지므로 실패로 처리한다.
  ```python
  PARAM_MAP = {"spring_k": "k@spring01", "orifice_d": "diam@or01"}
  api.AMESetParameterValue("k@spring01", "15000")   # 값은 문자열로 전달(Siemens 예제 형식)
  value, unit = api.AMEGetParameterValue("k@spring01")
  ```
- **단위**: 값은 해당 파라미터의 현재 표시 단위 기준이다. 읽어 온 `unit`을 로그에 남겨 확인한다.
- **Global Parameter**: 모델에서 여러 컴포넌트가 하나의 Global Parameter를 참조하게 해 두면 매핑이 단순해진다. TODO(사내검증): Global Parameter 값 변경을 `AMESetParameterValue("<gp 이름>", ...)`로 할 수 있는지 확인한다.

## 4. 실행 조건 변경
- 종료시간, 출력간격, 허용오차, 적분법은 `AMESetRunParameter(name, value)`로 바꾼다.
  - 이름 후보: `start_time_s`, `stop_time_s`, `interval_s`, `tolerance`, `integ_method`, `simulation_mode`. 이 이름들은 TODO(사내검증) 대상이며, `probe_api.py`의 help 출력으로 확인한다.
- `params.json`에 `final_time`, `print_interval`, `tolerance` 키가 있으면 템플릿이 실행 조건으로 적용한다(`RUN_PARAM_KEYS`).
- 출력간격이 너무 크면 최대값과 정착시간이 부정확해진다. 동특성을 보려면 시스템 최소 시상수의 1/10 이하로 잡는다.

## 5. 실행과 완료/실패 판정
```python
api.AMEInitAPI(False)
api.AMEOpenAmeFile(os.path.abspath("model.ame"))
# ... 파라미터 설정 ...
api.AMEGenerateCode()      # 컴파일(구조 변경 시 필수, 처음 열 때 안전하게 호출)
api.AMERunSimulation()
...
api.AMECloseCircuit(False) # True면 저장 후 닫기
api.AMECloseAPI()
```
- 실행 실패(컴파일 오류, 적분 실패)는 예외로 나타나거나 결과가 비어 있는 형태로 나타난다. 템플릿은 두 경우 모두 실패로 처리한다. 즉 `results.json`을 쓰지 않고 종료코드 1로 끝낸다.
- 결과에 NaN이 있거나 마지막 시각이 종료시간보다 많이 짧으면(적분 중단) 실패로 처리한다(`MIN_END_RATIO`).

## 6. 결과 추출 → results.json
- `AMEGetVariableValues("var@alias")`는 `[[t, v], ...]` 시계열을 돌려준다. TODO(사내검증): 반환 형식을 확인한다. 템플릿은 `(times, values)` 형식도 처리한다.
- `OUTPUT_VARS = {"p_out": "p2@pipe01"}`처럼 적으면 `p_out_final`, `p_out_max`, `p_out_min`, `p_out_rms` 등이 생성된다. 통계 종류는 `STATS`로 고른다.

| 통계 | 정의 |
|---|---|
| `final` | 마지막 값 |
| `max`, `min` | 최대, 최소 |
| `mean`, `rms` | 시간가중(사다리꼴) 평균, RMS |
| `settling_time` | 최종값 ±`SETTLING_BAND`(기본 2%) 밴드를 마지막으로 벗어난 직후 시각 |
| `overshoot_pct` | (최대 − 최종)/│최종 − 초기│ × 100 |

- 키는 소문자_스네이크이며 스터디 어댑터의 `outputs`와 일치시킨다.
- 시계열은 `ts_<키>.csv`(time,value)로도 저장한다. 다른 도구에서 재분석하거나 그래프를 다시 그릴 때 쓴다.

## 7. 이미지 저장
- `run_case.py`는 AMEPython에서 `matplotlib`(Agg 백엔드) import를 시도한다. 성공하면 `images/<키>.png`를 저장한다.
- 실패하면(번들에 matplotlib 없음) 같은 케이스 폴더에서 `scripts/plot_timeseries.py`를 tool `python`(플랫폼 venv)으로 실행한다. 이 스크립트는 `ts_*.csv`를 읽어 PNG를 만든다.
- 이미지 실패는 케이스 실패로 보지 않는다.

## 8. 자주 하는 실수와 디버깅
- **`ImportError: ame_apy`**: 시스템 Python으로 실행했거나 `AME`, `PATH`가 설정되지 않은 경우다. 반드시 `AMEPython.exe`로 실행한다.
- **라이선스 오류, 대기**: GUI가 라이선스를 잡고 있거나, `AMECloseAPI()`가 호출되지 않은 채 프로세스가 남아 있는 경우다. 작업 관리자에서 남은 `AMEPython`과 `AMESim` 프로세스를 확인한다.
- **파라미터가 안 바뀐 것 같음**: 데이터패스 오타(별칭, 대소문자), 단위 착오, 또는 Global Parameter가 값을 덮어쓰는 경우다. `AMEGetParameterValue`로 읽어 로그를 확인한다.
- **모델 파일 충돌**: `.ame`을 열면 같은 폴더에 작업 파일이 생긴다. 케이스마다 별도 폴더(플랫폼 기본 동작)에서 실행하고, 원본 모델을 직접 열지 않는다.
- **컴파일 실패**: C 컴파일러 설정(Amesim이 쓰는 컴파일러, `AMEGetCompilers` 참고) 문제다. GUI에서 같은 모델이 컴파일되는지 먼저 확인한다.

## 9. 사내 검증 필요 항목 (TODO(사내검증))
1. `AMESetRunParameter`의 실행 조건 이름과 값 형식(문자열 또는 숫자).
2. `AMEGetVariableValues`의 반환 형식, 그리고 변수 데이터패스 표기(`var@alias`, 서브모델 인스턴스, 슈퍼컴포넌트 내부).
3. `AMEInitAPI(False)` 인자의 의미(GUI 표시 여부로 추정)와 `AMEGenerateCode()` 호출이 꼭 필요한지.
4. Global Parameter 변경 방법.
5. AMEPython 번들에 matplotlib과 numpy가 있는지.
6. (B) Simulation Scripting 함수(`ameputp`, `amegetsimopt`, `amerunsingle`, `ameloadvarst`)의 정확한 시그니처.
7. 사내 라이선스 환경변수 값과 동시 실행 가능 수.
