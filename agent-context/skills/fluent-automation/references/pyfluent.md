# PyFluent (ansys-fluent-core) 요약

TUI 저널이 기본 경로다. PyFluent는 파이썬 로직(조건 분기, 반복 중 값 확인)이 필요할 때 쓴다.
설치: `<ansys_python> -m pip install ansys-fluent-core` — **설치한 Fluent 릴리스와 맞는 버전**을 고른다.
tool 키는 `fluent_python`(`{ansys_python} script.py args`)이다. 문서: https://fluent.docs.pyansys.com

## 세션 시작
```python
import ansys.fluent.core as pyfluent
solver = pyfluent.launch_fluent(mode="solver", precision="double", dimension=3,
                                processor_count=4, ui_mode="no_gui")
```
- 인자 이름이 버전마다 바뀌었다(`show_gui` → `ui_mode`, `version="3d"` → `dimension=3`, 최신은 `pyfluent.Solver.from_install(...)` 형식도 있음). TODO(사내검증): 설치된 패키지 버전의 `launch_fluent` 시그니처.
- Fluent 설치 위치는 환경변수 `AWP_ROOT<ver>`(예: `AWP_ROOT242`)로 찾는다. 여러 버전이 있으면 `product_version` 인자로 고정한다. TODO(사내검증)

## settings API
```python
solver.settings.file.read_case(file_name="duct.cas.h5")
inlet = solver.settings.setup.boundary_conditions.velocity_inlet["inlet"]
inlet.momentum.velocity_magnitude.value = 5.0     # TODO(사내검증): 구버전은 momentum.velocity.value
solver.settings.solution.initialization.hybrid_initialize()
solver.settings.solution.run_calculation.iterate(iter_count=500)
vals = solver.settings.solution.report_definitions.compute(report_defs=["outlet-temp"])
solver.settings.file.write(file_type="case-data", file_name="result.cas.h5")
solver.exit()
```
- 구버전(0.1x)은 `solver.setup...`, `solver.solution...`처럼 `settings` 없이 접근한다.
- 객체 경로를 모르면 대화형 세션에서 `dir(obj)`나 `obj.child_names`, 현재 값은 `obj()`로 확인한다.
- `compute()` 반환 형식(리스트 안의 dict, 값이 [value, unit] 쌍 등)은 버전마다 다르다. 출력해서 확인한 뒤 파싱한다. TODO(사내검증)

## TUI 호출 (settings에 없는 기능)
```python
solver.tui.define.boundary_conditions.set.velocity_inlet("inlet", "()", "vmag", "no", 5, "quit")
solver.execute_tui("/solve/iterate 100")     # TODO(사내검증): 메서드 존재 여부(버전별)
```

## 규약 연결
- 스크립트 첫 부분에서 `params.json`을 읽고, 마지막에 `results.json`을 쓴다. 예외가 나면 `solver.exit()` 후 `sys.exit(1)`.
- 리포트 값 추출은 PyFluent `compute()` 대신 Report File을 켜고 `fluent_extract.py`를 post로 붙여도 된다(버전 의존성이 적다).
