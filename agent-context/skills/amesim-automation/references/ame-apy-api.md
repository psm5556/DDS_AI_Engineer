# Simcenter Amesim API (`ame_apy`) 레퍼런스

**근거.** 이 문서의 함수 이름은 다음 자료에서 확인했다.
- Siemens 지식베이스 예제(`AMEInitAPI`, `AMESetParameterValue('param@alias', '80')`)
- Volupe 튜토리얼(`AMEInitAPI`, `AMECreateCircuit`, `AMEAddComponent`, `AMECloseCircuit(True)`, `AMECloseAPI`)
- 공개 GitHub 코드(`AMEOpenAmeFile(path)`, `AMESaveCircuit()`, `AMEChangeSubmodel`)
- 공개 MCP 래퍼(`AMEGetParameterValue`, `AMESetRunParameter`, `AMERunSimulation`, `AMEGetVariableValues`, `AMEGenerateCode`)

정확한 시그니처는 설치본에서 `help(ame_apy.<함수>)`로 확인한다(`scripts/probe_api.py`).

## 임포트
```python
import ame_apy as api          # 권장: 이름공간 유지
# from ame_apy import *        # 공개 예제에서 흔한 형태
```
- AMEPython으로 실행해야 import된다. Tools → Python command interpreter에서는 `ame_apy`와 `amesim`이 자동으로 import된다.

## 수명주기
| 함수 | 신뢰도 | 설명 |
|---|---|---|
| `AMEInitAPI(False)` | 확인(이름). 인자 의미는 TODO(사내검증) | API 초기화와 **라이선스 체크아웃**. 다른 API 호출보다 먼저 호출한다 |
| `AMECloseAPI()` | 확인 | 라이선스 반납. `finally`에서 반드시 호출한다 |
| `AMEGetAPIVersion()` | 확인(이름) | API 버전 |

## 모델(서킷)
| 함수 | 신뢰도 | 설명 |
|---|---|---|
| `AMEOpenAmeFile(path)` | 확인 | .ame 열기. 열린 모델이 활성 서킷이 된다. 절대경로를 권장한다 |
| `AMESaveCircuit()` | 확인 | 저장 |
| `AMECloseCircuit(save_bool)` | 확인 | 닫기. `True`면 저장 후 닫는다 |
| `AMEGetActiveCircuit()` | 확인(이름) | 활성 서킷 |
| `AMECreateCircuit(name)` | 확인 | 새 서킷 |
| `AMEAddComponent(icon, alias, (x, y))`, `AMEConnectTwoPorts(...)`, `AMEChangeSubmodel(alias, submodel[, path])` | 확인(이름) | 모델 구성 변경(형상 자동화에는 거의 쓰지 않음) |
| `AMEGenerateCode()` | 확인(이름) | 컴파일. 구조 변경 후 필수이고, 실행 전에 한 번 호출하는 것이 안전하다 |

## 파라미터
```python
api.AMESetParameterValue("k@spring01", "15000")    # 값은 문자열(Siemens 예제)
value, unit = api.AMEGetParameterValue("k@spring01")
info = api.AMEGetParameterInfos("k@spring01")      # TODO(사내검증): 반환 구조
```
- 데이터패스는 `파라미터명@별칭` 형식이다. 슈퍼컴포넌트 내부 표기는 TODO(사내검증).
- 정수, 열거형 파라미터(예: 옵션 선택)도 문자열로 넣는다. 열거형은 표시 문자열이 아니라 인덱스일 수 있다. TODO(사내검증)
- `AMEAddGlobalParameter`라는 함수 이름이 존재한다. Global Parameter 값 변경 방법은 TODO(사내검증).

## 실행 조건, 실행
```python
api.AMESetRunParameter("stop_time_s", "10")     # TODO(사내검증): 이름/값 형식
api.AMESetRunParameter("interval_s", "0.001")
api.AMESetRunParameter("tolerance", "1e-7")
api.AMERunSimulation()                            # 메인 스레드에서만 호출
```
- 이름 후보: `start_time_s`, `stop_time_s`, `interval_s`, `integ_method`, `tolerance`, `simulation_mode`. 출처는 제3자 래퍼의 docstring이므로 반드시 help로 확인한다.
- 배치(Study Manager) 실행 API는 설치본 help에서 `Batch` 키워드로 찾는다. TODO(사내검증). 플랫폼은 케이스마다 프로세스를 따로 띄우므로 단일 실행 API만으로 충분하다.

## 결과
```python
series = api.AMEGetVariableValues("x@mass01")    # [[t, v], ...]  (TODO(사내검증))
infos  = api.AMEGetVariableInfos("x@mass01")     # 단위 등 (TODO(사내검증): 반환 구조)
```
- 변수 데이터패스 형식도 `변수명@별칭`이다(예: `x1@mass_friction2port`).
- 결과는 모델 폴더의 `<model>_.results`(바이너리)에도 저장된다. 그러나 API로 읽는 것을 권장한다.
