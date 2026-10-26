# comsol batch 와 Java 메서드

## comsol batch 주요 옵션
| 옵션 | 의미 |
|---|---|
| `-inputfile m.mph` | 입력 모델 (Model File for Java `.class` 도 가능) |
| `-outputfile out.mph` | 해석 후 저장할 파일. 생략하면 입력 파일을 덮어쓴다 |
| `-study std1` | 실행할 스터디 태그 |
| `-job b1` | 실행할 Batch/Job 노드 태그 |
| `-pname a,b` / `-plist ...` / `-punit ...` | 파라미터 이름·값·단위. 다중 파라미터 형식은 TODO(사내검증) |
| `-methodcall name` | 모델의 Application Builder 메서드 실행 |
| `-batchlog batch.log` | 로그 파일 |
| `-np 4` | 코어 수 |
| `-nosave` | 결과 저장 안 함 (TODO(사내검증): 버전별 지원) |

- Windows는 `comsolbatch.exe`(bin\win64)를 직접 부를 수도 있다. 이 경우 `batch` 하위명령 없이 옵션만 쓴다. `comsol_batch_run.py`는 실행 파일 이름이 `comsolbatch`로 시작하면 하위명령을 빼고 호출한다.
- 실행 예:
  ```
  comsol batch -inputfile heatsink.mph -outputfile out.mph -study std1 -pname fin_height -plist 25 -punit mm -batchlog batch.log
  ```

## 결과를 파일로 남기는 메서드 (Application Builder, Java 문법)
batch에서 결과값을 꺼내려면 모델에 메서드를 만들어 `-methodcall`로 부른다. 예시(메서드 이름 `exportResults`):
```java
// Derived Values -> 표 -> CSV
model.result().numerical("gev1").setResult();          // 결과를 표(tbl1)로 계산
model.result().export("tbl1").set("filename", "results_table.csv");
model.result().export("tbl1").run();
model.result().export("img1").set("pngfilename", "images/temperature.png");   // TODO(사내검증): 속성명
model.result().export("img1").run();
```
- 상대 경로의 기준 폴더가 batch 실행 폴더(cwd)인지 확인한다. TODO(사내검증)
- `setResult()`는 Derived Value를 연결된 표에 새로 쓴다. `appendResult()`는 행을 추가한다.
- 메서드 안의 API는 GUI 조작을 녹화(Record Method)해서 얻는 것이 가장 확실하다. Application Builder는 Windows 버전에서만 쓸 수 있다.

## 표 파일 형식 (Table export)
```
% Model:              heatsink.mph
% Version:            COMSOL 6.2
% Date:               ...
% T_max (degC),Q_out (W)
85.123,12.40
```
- `%` 줄은 머리말이고 마지막 `%` 줄이 열 이름이다. `comsol_batch_run.py`는 마지막 숫자 행을 결과로 쓴다.
- 열 이름 → 결과 키 매핑은 `--map "T_max (degC)=max_temp"`로 준다. 매핑이 없으면 단위 표기를 지우고 snake 키를 만든다(`t_max`).
