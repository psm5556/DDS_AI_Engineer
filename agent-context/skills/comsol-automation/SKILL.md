---
name: comsol-automation
description: COMSOL Multiphysics 배치 자동화. MPh 파이썬 패키지(mph.start, model.parameter, model.solve, model.evaluate, model.export)나 `comsol batch -inputfile -pname -plist -methodcall` 명령으로 .mph 모델의 파라미터·물성·경계조건·메쉬를 바꾸고 해석한 뒤, 전역 평가값을 results.json으로, Image export를 PNG로, 노드 필드를 fields/*.npz로 저장한다. 사용자가 COMSOL(.mph) 다중물리 해석의 파라메트릭/DOE/최적화, 결과 추출, 이미지 캡처를 요청할 때 사용한다.
---

# COMSOL 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- `.mph` 모델 하나를 기준으로 Global Parameter를 바꿔 가며 반복 해석할 때(DOE, 최적화) 쓴다.
- 모델 준비 원칙: 설계변수와 조건값은 모두 **Global Definitions → Parameters**에 두고, 형상 치수·경계조건·물성은 그 파라미터를 식으로 참조하게 만든다. 결과는 **Derived Values**(Global Evaluation, Surface Average 등) 또는 **Component Couplings**(maxop1, aveop1)로 한 줄 식이 되게 준비한다.
- 자동화 경로는 두 가지다.
  - **MPh (권장)**: 플랫폼 파이썬에서 `import mph`. COMSOL을 클라이언트-서버 또는 stand-alone으로 띄워 Java API를 감싼다. 설치: `<platform python> -m pip install mph`(JPype 포함). COMSOL 5.x/6.x 설치를 자동 탐색한다.
  - **comsol batch**: 파이썬 없이 솔버만 돈다. 결과는 모델 안의 Table/Data/Image export 노드나 `-methodcall` 메서드가 파일로 써야 한다.
- 라이선스: MPh와 batch 모두 COMSOL 라이선스(해당 모듈 포함)를 쓴다. Java API 직접 호출(LiveLink for MATLAB/Java)은 이 스킬 범위 밖이다.
- MPh의 Java API 원본은 `model.java`로 접근한다. 확실한 Java 호출은 COMSOL GUI에서 `File → Save as → Model File for Java`(.java)로 저장해 그대로 옮긴다. 이 방법이 이 문서의 모든 `TODO(사내검증)` 확인 수단이다.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| MPh 스크립트 (파라미터, 해석, 평가, 이미지, 필드) | `comsol_python` | `{python} script.py args` (플랫폼 파이썬, mph 설치 필요) |
| 솔버 단독 배치 | `comsol_batch` | `{comsol} batch -inputfile m.mph -outputfile out.mph ...` |
| 케이스별 인자를 만들어 batch 호출 | `python` | `comsol_batch_run.py`가 `DDS_COMSOL` 경로로 batch 실행 |

`comsol_batch`의 `args`는 스터디 전체에서 고정이라 케이스별 파라미터를 넣을 수 없다. 그래서 DOE에는 `comsol_python` 또는 `python`(드라이버) 방식을 쓴다. `comsol_batch`는 단발 실행(`run_script`)에 쓴다.

**A. MPh (권장)**
```json
{"tool": "comsol_python", "script": "scripts/comsol_mph_run.py",
 "args": ["--study", "Study 1", "--punit", "fin_height=mm",
          "--eval", "max_temp=comp1.maxop1(T)|degC", "--eval", "heat_flux=comp1.intop_out(ht.ntflux)|W",
          "--export", "Image 1=images/temperature.png",
          "--field", "temperature=T|degC", "--field-dataset", "Surface 1"],
 "inputs": ["models/heatsink.mph"], "outputs": ["max_temp", "heat_flux"], "timeout_sec": 7200}
```
**B. comsol batch + 표 파일**
```json
{"tool": "python", "script": "scripts/comsol_batch_run.py",
 "args": ["--study", "std1", "--methodcall", "exportResults", "--table", "results_table.csv",
          "--map", "T_max (degC)=max_temp", "--np", "4"],
 "inputs": ["models/heatsink.mph"], "outputs": ["max_temp"]}
```
- 템플릿 파일
  - `scripts/comsol_mph_run.py`: (comsol_python) 로드 → 파라미터 → solve → evaluate → export → fields → results.json
  - `scripts/comsol_batch_run.py`: (python) params.json → `comsol batch -pname/-plist` → batch.log 검사 → 표 파일 파싱 → results.json
- 상세: [references/mph-api.md](references/mph-api.md), [references/batch-and-java.md](references/batch-and-java.md)

## 3. 파라미터(형상) 변경
```python
import mph
client = mph.start(cores=4)
model = client.load("heatsink.mph")
print(model.parameters())                 # {'fin_height': '20[mm]', ...} 이름 확인
model.parameter("fin_height", "25[mm]")   # 문자열 식. 단위는 [ ]로 붙인다
model.parameter("q_chip", 12.5)           # 숫자도 된다(모델 기본 단위로 해석)
```
- 템플릿은 `--punit fin_height=mm`이면 `25.0[mm]`로 쓴다. 단위를 생략하면 무차원 숫자가 되고 사용처에서 SI 단위로 해석되므로(25 → 25 m, 단위 불일치 경고), 길이·온도 파라미터에는 단위를 붙인다.
- 템플릿은 모델에 없는 이름이면 실패한다(오타 방지). 새 파라미터를 만들려면 `--allow-new`를 쓴다.
- 형상은 study 실행 시 필요하면 다시 빌드된다. 명시적으로 하려면 `model.build()`, `model.mesh()`를 solve 전에 호출한다.
- batch: `-pname fin_height -plist 25 -punit mm`. 여러 파라미터 형식은 TODO(사내검증)이다.

## 4. 해석조건 변경
파라미터 참조가 1순위다. 직접 바꿔야 하면 MPh 노드 API 또는 `model.java`를 쓴다.
| 대상 | 방법 |
|---|---|
| 경계조건 값 | 모델에서 `T0 = T_in` 처럼 파라미터 참조. 또는 `(model/'physics'/'Heat Transfer in Solids'/'Heat Flux 1').property('q0_input', 'q_chip/A')` TODO(사내검증): 속성 이름은 Java 파일에서 확인 |
| 재료 물성 | 재료 노드 값을 파라미터 식(`k_al`)으로 바꿔 두고 `model.parameter('k_al', '200[W/(m*K)]')` |
| 메쉬 크기 | Size 노드의 `hauto`(1=Extremely fine ~ 9=Extremely coarse) 또는 `hmax`를 파라미터 참조. 직접: `model.java.component('comp1').mesh('mesh1').feature('size').set('hauto', 3)` 후 `model.mesh()` |
| 솔버 설정 | `model.java.sol('sol1').feature('s1')...` TODO(사내검증): 태그는 Java 파일에서 확인 |
| 스터디 선택 | `model.solve('Study 2')`, batch는 `-study std2` |
| 시간 범위 | 스터디 Step의 `tlist`를 `range(0,1,t_end)`처럼 파라미터 참조 |

- 노드 경로는 `model/'physics'`, `model/'materials'`, `model/'meshes'`처럼 GUI 트리 이름으로 찾는다. 모르면 `mph.tree(model)`로 트리를 출력한다.

## 5. 실행과 완료 판정
- MPh: `model.solve()`가 예외 없이 끝나면 해가 있다. 수렴 실패는 Java 예외(`com.comsol.util.exceptions...`)로 올라오므로 템플릿은 예외를 잡아 exit 1로 끝낸다.
- 평가값이 NaN/Inf면 실패로 본다(템플릿 반영).
- batch: 종료코드 + `batch.log`의 `Error` 줄 + 표 파일 생성 여부로 판정한다. 표 파일은 실행 전에 지워서 이전 값으로 오판하지 않게 한다.
- 비수렴인데 마지막 반복 해가 저장되는 경우가 있다. 수렴이 중요하면 Solver Log나 수렴 지표(잔차) 대신 물리 검증값(에너지 수지 오차 등)을 함께 출력해 판정한다.

## 6. 결과 추출 → results.json
```python
Tmax = model.evaluate("comp1.maxop1(T)", "degC")          # 컴포넌트 커플링 전역식
dp   = model.evaluate("comp1.aveop_in(p)-comp1.aveop_out(p)", "Pa")
T_ds = model.evaluate("T", "K", dataset="Surface 1")      # 데이터셋 노드값 배열
```
- 반환값은 numpy 배열이다. 파라메트릭/시간 해는 해 축이 앞에 붙으므로 템플릿은 **마지막 해**를 쓴다(`last_scalar`).
- Derived Values 노드를 쓰려면 `model.java.result().numerical('gev1').getReal()` (Java API, 2차원 배열). 결과 키는 `--eval key=expr|unit`의 key로 정한다.
- 키는 스터디 `outputs`와 정확히 같아야 한다.

## 7. 이미지 저장 → images/
- 모델에 **Results → Export → Image**(예: `Image 1`)를 플롯 그룹·뷰·해상도를 정해 만들어 둔다. 스크립트는 파일 이름만 바꿔 실행한다.
  ```python
  model.export("Image 1", "images/temperature.png")
  ```
- DOE 비교용이면 플롯 그룹의 컬러 범위를 수동 고정(Range → Manual color range)해 둔다.
- Java: `model.result().export('img1').set('pngfilename', path)` 후 `.run()`. TODO(사내검증): 버전별 파일명 속성(`pngfilename`/`filename`).
- 이미지 실패는 케이스 실패로 처리하지 않는다(템플릿은 WARN만 출력).

## 8. 3D 필드 (fields/*.npz)
- 표면 데이터셋(Datasets → Surface, 예: 히트싱크 외곽 경계)에 `x`,`y`,`z`와 값 식을 평가해 `points`, `values`를 저장한다(`--field temperature=T|degC --field-dataset "Surface 1"`).
- 메쉬 삼각형 연결은 MPh로 직접 얻기 어려워 `triangles`는 생략한다. 서버는 points만으로도 학습한다. TODO(사내검증): 데이터셋 평가가 메쉬 노드 기준인지(중복 노드 포함 여부)와 점 개수가 케이스마다 같은지 확인한다.
- 볼륨 전체를 평가하면 용량이 크다. 표면만 저장한다.

## 9. 흔한 오류와 대처
| 증상 | 원인 / 대처 |
|---|---|
| `mph.start()`에서 COMSOL을 못 찾음 | 비표준 설치 경로. `mph.start(version='6.2')` 또는 COMSOL 설치 확인. TODO(사내검증) |
| `JVM` 관련 오류 | JPype와 COMSOL 번들 JRE 충돌. mph 최신 버전으로 업데이트 |
| 파라미터를 바꿔도 결과 동일 | 형상/물성이 파라미터를 참조하지 않음. 모델에서 식을 확인 |
| `Undefined variable` | 식의 컴포넌트 접두어(`comp1.`) 누락이나 커플링 연산자 이름 오타 |
| batch 실행 후 표 파일 없음 | export 노드는 batch에서 자동 실행되지 않는다. `-methodcall`로 export 메서드 실행 |
| 라이선스 대기 | 동시 실행 케이스 수를 줄인다. 로그에서 `license` 확인 |
| Windows 콘솔 한글 깨짐 | 출력 메시지를 ASCII로 쓴다(템플릿 반영). 경로에 한글·공백을 피한다 |

### 사내 검증 필요 항목 (TODO(사내검증))
1. 사내 COMSOL 버전에서 `comsol batch`(Windows의 `comsolbatch.exe`)의 다중 파라미터 `-pname/-plist/-punit` 형식.
2. `-methodcall` 실행 시점(해석 전/후)과 Application Builder 메서드 작성 권한(Application Builder는 Windows 전용).
3. Image export 노드의 파일 이름 속성명, 배치 렌더링 가능 여부.
4. MPh가 사내 COMSOL 버전과 플랫폼 파이썬(3.12)을 지원하는지.
