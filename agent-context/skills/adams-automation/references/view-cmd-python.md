# Adams View 명령 언어(.cmd)와 Python 인터페이스 메모

모든 명령은 GUI에서 같은 조작을 한 뒤 `aview.log`(명령 기록)로 철자와 인자를 확인하고 쓴다. 아래는 템플릿이 쓰는 명령과 자주 쓰는 명령이다.

## 배치 실행
```
mdi.bat aview ru-standard b dds_view_export.cmd      (tool 키 adams_view)
```
- 배치가 끝나면 View가 종료돼야 한다. 템플릿은 마지막에 `quit`를 넣는다. TODO(사내검증): 배치에서 `quit` 확인창 없이 종료되는지.
- 로그: 케이스 폴더의 `aview.log`.

## 템플릿이 쓰는 명령 (adams_case_run.py `view_export`)
```
file binary read file_name = "susp.bin"
file command read file_name = "susp.cmd"                       ! .cmd 모델일 때
variable modify variable_name = .susp.DV_k real_value = 25.0
file adams_data_set write model_name = .susp file_name = "case.adm"
quit
```
- TODO(사내검증): `file adams_data_set write`의 인자 이름, `.bin`에 여러 모델이 있을 때 `model_name`.
- 설계변수는 View의 `variable`이다. 단위가 있는 변수는 `real_value`에 모델 단위계 값으로 넣는다.

## View 안에서 시뮬레이션까지 할 때 (대안)
```
simulation single_run transient type = auto_select end_time = 1.0 number_of_steps = 500 model_name = .susp initial_static = no
```
- 결과는 `.susp.Last_Run` 분석으로 남는다. 결과를 파일로 내보내는 명령은 템플릿에서 쓰지 않는다. TODO(사내검증): 분석 결과 내보내기(`file ... write`) 명령. 권장은 .adm을 내보내 솔버로 푸는 템플릿 경로다.

## Python 인터페이스 (Adams 2017.2+, View 내장 파이썬)
```python
import Adams
Adams.read_command_file("setup.cmd")                       # .cmd 실행
Adams.execute_cmd("variable modify variable_name=.susp.DV_k real_value=25.0")
val = Adams.evaluate_exp(".susp.DV_k")                       # View 식 평가
```
- 실행: `mdi python script.py`(tool 키 `adams_python`) 또는 View 안에서 파이썬 파일 읽기 명령. TODO(사내검증): View 안에서 .py를 읽는 명령 이름(`file python read` 여부), 독립 `mdi python`에서 `import Adams`가 되는지, View 세션 안에서만 되는지. 내장 파이썬 버전(2.7/3.x)도 확인한다.
- `Adams.Models`, `model.Parts` 같은 객체 API는 버전마다 다르다. 위 세 함수로 명령 언어를 감싸 쓰는 것이 가장 이식성이 높다. TODO(사내검증).
