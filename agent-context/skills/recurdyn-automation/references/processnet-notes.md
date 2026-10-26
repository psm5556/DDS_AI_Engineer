# RecurDyn ProcessNet 연동 메모

ProcessNet은 RecurDyn의 사용자 프로그래밍 API다. .NET(C#, VB.NET) 기반이고 RecurDyn 안의 VSTA 편집기에서 개발한다. 이 문서는 DDS 스터디와 연결하는 **구조**만 적는다. 클래스와 멤버 이름은 사내 설치본의 ProcessNet Help(API Reference)와 예제 폴더에서 확인한 것만 쓴다.

## 연동 구조 (권장)
```
DDS 케이스 폴더 (cwd)
  params.json  ──►  ProcessNet 프로그램
                     1) params.json 읽기 (System.IO + JSON 파서)
                     2) .rdyn 열기
                     3) Parametric Value(PV) 값 설정  ← 형상(PP), 마커가 자동 갱신
                     4) 해석 실행(Dynamic/Kinematic, 종료시간, 스텝)
                     5) 필요한 곡선을 CSV로 저장 (시간 열 + 값 열)
                     6) 모델 닫기, RecurDyn 종료
  *.csv        ──►  rd_extract.py (post) → results.json, images/
```
- 1)~5)를 한 매크로로 묶고, 플랫폼에서는 `recurdyn` tool 키(`{recurdyn} {args}`)로 매크로를 실행하도록 하는 것이 목표다.
- TODO(사내검증): RecurDyn을 명령행에서 띄우며 ProcessNet 매크로(또는 DLL)를 자동 실행하는 인자. 확인 전에는 SKILL.md 3-1(.rmd 플레이스홀더) 경로를 쓴다.
- TODO(사내검증): 외부 프로그램(out-of-process)에서 실행 중인 RecurDyn에 붙는 방식(COM/.NET 원격)의 지원 여부. 지원되면 플랫폼 venv 파이썬에서 pywin32나 pythonnet으로 부를 수 있다.
- TODO(사내검증): 사내 버전에 Python ProcessNet이 있으면 Python 버전(3.x)과 모듈 이름.

## 의사코드 (실제 API 이름 아님)
```
app      = <ProcessNet 진입점의 Application 객체>
doc      = app.<모델 열기>("model.rdyn")
model    = doc.<최상위 Subsystem>
pv       = model.<Parametric Value 컬렉션>["PV_arm_length"]
pv.<값>  = params["arm_length"]
doc.<해석 설정>(종료시간=params["_end_time"], 스텝=params["_steps"])
doc.<동해석 실행>()
<곡선 데이터 가져오기>("Body1", "Pos_Z") → CSV 저장
doc.<닫기>(저장 안 함)
```
- PV 이름은 params.json 키와 같게 지으면 매핑이 필요 없다.
- 해석 실패를 매크로가 잡으면 CSV를 쓰지 않는다. 그러면 `rd_extract.py`가 실패하고 케이스가 failed가 된다.

## 사내 검증 체크리스트 (TODO(사내검증))
- ProcessNet 언어/런타임(.NET Framework 버전), 개발 환경(VSTA, Visual Studio 외부 빌드).
- 모델 열기, PV 설정, 해석 실행, 곡선(Plot) 데이터 접근 API 이름.
- 명령행 자동 실행 인자, 배치(무화면) 모드 여부.
- 솔버 단독 실행 파일 이름과 인자, 결과 파일 이름 규칙.
- Plot Export 텍스트 형식(머리글, 단위 줄, 구분자) — `rd_extract.py`의 자동 인식으로 읽히는지 `--list`로 확인.
