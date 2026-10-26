# MPh API 요약 (COMSOL Python)

MPh는 COMSOL Java API를 JPype로 감싼 오픈소스 패키지다(`pip install mph`). 문서: https://mph.readthedocs.io

## 클라이언트와 모델
```python
import mph
client = mph.start(cores=4)            # COMSOL 세션 시작(프로세스당 1회). version='6.2' 로 버전 지정
model = client.load("model.mph")      # Model 객체
client.models()                        # 로드된 모델 목록
client.remove(model); client.clear()   # 메모리 정리(케이스 반복 시 사용)
```
- 한 파이썬 프로세스에서 `mph.start()`는 한 번만 호출한다. 플랫폼은 케이스마다 새 프로세스를 띄우므로 템플릿은 문제가 없다.
- 세션 방식은 `mph.option('session', 'client-server' | 'stand-alone')`로 정한다. 기본은 플랫폼별(Windows는 stand-alone). TODO(사내검증): 사내 환경 기본값 확인.

## 조회
```python
model.parameters()          # {'w': '2[mm]', ...}   evaluate=True 면 숫자값
model.studies(); model.solutions(); model.datasets(); model.plots(); model.exports()
model.physics(); model.materials(); model.meshes(); model.geometries()
mph.tree(model)             # 모델 트리 전체 출력(노드 이름 확인용)
```

## 변경과 실행
```python
model.parameter("w", "2.5[mm]")       # 설정
model.parameter("w")                  # 현재 식 조회
model.build()                         # 지오메트리 빌드(인자: 지오메트리 이름)
model.mesh()                          # 메쉬 생성
model.solve("Study 1")                # 이름 생략 시 모든 스터디
model.save("out.mph")                 # 경로 생략 시 덮어쓰기
```
노드 API (MPh 1.1+):
```python
node = model/'physics'/'Heat Transfer in Solids'/'Temperature 1'
node.properties()                     # 속성 이름과 값 확인
node.property('T0', 'T_in')           # 속성 설정
node.toggle('off')                    # 피처 비활성화
```
- 속성 이름은 Java 이름(`T0`, `q0_input` 등)이다. GUI 표시 이름과 다르므로 `properties()`로 먼저 확인한다.

## 평가
```python
model.evaluate("comp1.maxop1(T)", "degC")                    # 전역식 -> 스칼라/배열
model.evaluate(["x", "y", "T"], ["mm", "mm", "K"], dataset="Surface 1")
model.evaluate("T", "K", dataset="Study 1//Solution 1", inner="last")  # 시간 해 마지막
```
- `inner`/`outer`로 시간 스텝·파라메트릭 해를 고른다(`'first'`, `'last'`, 인덱스 목록). TODO(사내검증): 설치된 mph 버전의 인자 이름.
- 반환 배열 모양은 데이터셋 종류에 따라 다르다. 템플릿은 마지막 해를 고르는 방식으로 처리한다.

## 내보내기
```python
model.export("Image 1", "images/T.png")   # 모델에 정의된 export 노드 실행, 파일명 지정
model.export()                            # 모든 export 노드를 저장된 파일명으로 실행
```

## Java API 직접
```python
j = model.java
j.param().set("w", "2.5[mm]")
j.study("std1").run()
j.result().numerical("gev1").getReal()   # Derived Values
j.result().export("tbl1").run()
```
태그(`std1`, `gev1`, `img1`)는 GUI 트리의 노드 Properties 또는 `Model File for Java` 저장본에서 확인한다.
