---
name: live-modeling
description: 사용자가 열어 둔 CAD/CAE GUI(NX, Abaqus/CAE, HyperMesh, STAR-CCM+, Ansys Mechanical)에 붙어서 사람과 같은 화면에서 형상 생성·해석 모델링(재료·하중·경계조건·메쉬)을 함께 하는 방법(Live Link, dds-live MCP). "같이 모델링하자", "열어 둔 모델에서", "화면 보면서", "해석 모델 만들어 줘"처럼 대화형 모델 준비를 요청할 때 사용.
---

# Live Link: 열려 있는 CAD/CAE 화면에서 함께 모델링

자동화(배치·DOE) 전에 **해석 모델을 만드는 단계**는 사람의 판단이 많이 필요하다. Live Link는 사용자가 보고 있는 GUI 세션에서 에이전트가 작은 단계씩 작업하고, 매 단계 화면을 보여 주며 확인받는 방식이다.

## 0. 절대 규칙: 사용자에게 툴 조작을 부탁하지 않는다
- 리본 버튼 클릭, 저널·매크로·스크립트 실행, 플러그인 시작, 메뉴 선택을 **절대** 요청하지 않는다. 연결과 실행은 도구가 스스로 한다.
- 요청받은 작업을 바로 한다. "준비되면 알려 주세요"처럼 사용자를 기다리지 않는다.
- 도구가 붙지 못했다고 돌려주면 그 원인을 읽고 스스로 해결한다. 다시 시도하거나 `live_launch(model_path=...)`로 새 세션을 열거나 `dds-cae` 배치로 같은 작업을 한다.
- 끝내 못 하면 무엇을 시도했고 왜 안 됐는지 알린다. 이때도 조작을 부탁하지 않는다.

## 1. 진행 원칙
1. `dds-live.live_status`로 상태를 확인한다.
   - 꺼져 있으면 `live_launch`로 스스로 띄운다. NX는 첫 `live_*` 호출 때 열린 세션에 스스로 붙으므로 그냥 호출한다.
2. `live_info`로 열린 모델 구조(파트·재료·스텝·세트)를 먼저 읽는다. 이름을 추측하지 않는다.
3. **하나의 논리 단위씩** 실행한다(예: 재료·단면·할당 → 스텝·하중·경계조건 → 메쉬).
   - 치수·설계변수 바꾸기, 화면 바꾸기, 되돌리기는 빠른 툴(`live_set_params`·`live_view`·`live_undo`)을 먼저 쓴다. 코드를 쓰지 않아 빠르고 틀릴 일이 적다(지금은 NX, `live_set_params`는 연습용 CAD도).
   - 사용자는 툴 화면을 직접 보고 있다. 캡처는 큰 단계가 끝났을 때·사용자가 요청할 때·보고서용으로만 한다. 빠른 툴은 `capture=true`로 같은 호출에서 게시한다.
   - 사용자의 확인이나 수정 요청을 반영한다.
4. 실패하면 오류를 읽고 고쳐서 다시 실행한다.
   - 툴의 Undo로 되돌릴 수 있게 한 단계는 되도록 하나의 명령 묶음으로 만든다.
   - NX는 단계마다 Undo 마크가 남는다.
5. 모델이 완성되면 **파라미터화**를 제안한다.
   - 바꿀 값(두께·하중·메쉬 크기 등)을 변수로 정리한다.
   - 같은 작업을 배치 스크립트(툴별 *-automation 스킬)로 저장한다.
   - 그 스크립트를 `dds-study`의 script 어댑터로 DOE와 최적화에 연결한다.
6. 해석 제출·대량 반복처럼 오래 걸리는 일은 Live Link로 하지 않는다. GUI가 멈추기 때문이다. `dds-cae.run_script`나 스터디로 배치 실행한다.

## 2. 툴별 방법

### 연습용 CAD (practice) — 라이선스 없이 흐름 연습
- 시작: `live_launch(tool="practice")`
- 코드(Python): `model["width"] = 150; model["note"] = "리브 추가"` → 화면이 바로 바뀜. `result = model`로 값을 돌려받는다.

### Abaqus/CAE (abaqus) — Python, 커널 객체 `mdb`, `session`
- 시작: `live_launch(tool="abaqus", model_path="...cae")`
  - 플러그인을 사용자 폴더에 스스로 등록하므로 이후 여는 CAE에서는 저절로 켜진다.
  - 플러그인 없이 이미 열린 세션에는 붙을 수 없다. 저장된 cae를 새 세션으로 열거나 배치로 한다.
- 예시
  ```python
  m = mdb.models["Model-1"]
  m.Material(name="AL6061"); m.materials["AL6061"].Elastic(table=((68900.0, 0.33),))
  m.HomogeneousSolidSection(name="Sec-AL", material="AL6061")
  p = m.parts["Bracket"]; p.SectionAssignment(region=p.sets["ALL"], sectionName="Sec-AL")
  result = list(m.materials.keys())
  ```
- 뷰포트를 바꿔 보여 줄 때: `session.viewports["Viewport: 1"].setValues(displayedObject=p)`
- 상세 API는 `abaqus-automation` 스킬(references/cae-model-api.md)을 본다.

### HyperMesh (hypermesh) — Tcl
- 시작: `live_launch(tool="hypermesh", model_path="...hm")`
  - hmcustom.tcl에 스스로 등록하므로 이후 여는 HyperMesh에서는 저절로 켜진다.
  - 플러그인 없이 이미 열린 세션에는 붙을 수 없다. 저장된 모델을 새 세션으로 열거나 배치로 한다.
- 예시: `*createmark comps 1 "all"; set n [hm_marklength comps 1]; set n` (마지막 값이 result)
- 상세 명령은 `altair-automation` 스킬(references/hypermesh-tcl.md)을 본다.

### NX / Simcenter 3D (nx) — Python NXOpen, 대기열 방식
- NX Open은 GUI 스레드에서만 안전하다. 그래서 에이전트가 `live_exec(tool="nx", code=...)`로 코드를 대기열에 넣는다.
- 첫 호출 때 도구가 열린 NX에 세션 안 실행기(`dds_live_nx.py`)를 스스로 띄운다. NX가 없으면 NX부터 띄운다. 이후에는 실행기가 대기열을 바로 처리한다.
- 열린 세션에서 실행되고 Undo 마크가 남는다. 호출이 시간 안에 끝나지 않으면 `live_result(job)`로 다시 확인한다.
- 코드에서 쓸 수 있는 것: `session`, `work_part`, `display_part`, `NXOpen`
  - 상세 API는 `nx-cad-automation`, `simcenter3d-automation` 스킬을 본다.

#### NX를 빠르게 쓰는 법
빠른 툴은 세션 안 도우미(`live/nx/ddslive_nx_ops.py`)를 부른다. 도우미는 사내에서 검증된 배치 스크립트 함수(`nx_param_update`·`sc_update_solve`)를 그대로 쓴다.
1. 시작할 때 `live_info(tool="nx")`를 한 번 읽는다. expression·피처·바디 이름은 여기서만 가져온다(추측 금지).
   - 사용자가 이름 붙인 expression만 나온다. p12 같은 자동 이름까지 필요하면 `all_expressions=true`.
2. 치수·설계변수: `live_set_params(tool="nx", values={"thickness": 4, "rib_h": 12})`
   - 여러 개는 **한 호출에** 모은다. 업데이트를 한 번만 하고 Undo 마크도 하나만 남는다.
   - 업데이트가 실패하면 자동으로 바꾸기 전으로 되돌리고 오류를 돌려준다.
   - Simcenter 3D에서 메쉬까지 바꿔야 할 때만 `fem=true`(Idealized·FEM 업데이트, 재메쉬라 느리다). 질량이 필요하면 `measure=true`.
3. 화면: `live_view(tool="nx", orient="front", style="edges", hide=["EXTRUDE(3)"])`
   - orient: front·back·top·bottom·left·right·iso·trimetric / style: shaded·edges·wire·hidden / show·hide·only: 바디 또는 피처 이름
4. 되돌리기: `live_undo(tool="nx", steps=1)` — 빠른 툴로 바꾼 것을 최근 것부터 되돌린다.
5. `live_exec`는 새 피처 만들기처럼 빠른 툴로 안 되는 일에만 쓴다. 하나의 명령 묶음으로 만든다.
6. 결과의 `ms`에 걸린 시간이 있다(update: 형상 업데이트, fem: 재메쉬, capture: 화면 저장, wait: 실행까지 대기, round_trip: 전체).
   - 느리다는 말이 나오면 이 값으로 어디서 걸리는지 사용자에게 알려 준다.

### STAR-CCM+ — 클라이언트-서버 공유 (TODO(사내검증))
- 에이전트가 `dds-cae`로 `starccm+ -server -port 47827 model.sim` 서버를 띄운다. GUI는 같은 서버에 접속해 화면을 보여 준다.
- 에이전트는 매크로를 `dds-cae.run_script(tool="starccm", args=["-host","localhost","-port","47827"])`로 같은 서버에 실행한다. 같은 sim이므로 사용자 화면에 반영된다.

### Ansys Mechanical — PyMechanical GUI 연결 (TODO(사내검증))
- Mechanical을 gRPC로 띄운다(`ansys-mechanical -g --port 10000`, 버전별 옵션 확인).
- `dds-cae.run_script(tool="ansys_python")`로 `ansys.mechanical.core.connect_to_mechanical(port=10000)` 후 `run_python_script(...)`를 실행한다. 사용자가 보는 Mechanical 트리가 바로 바뀐다.

## 3. 사용자에게 보여 주는 방식
- 큰 단계가 끝날 때 `live_capture(tool, title="2. 재료·단면 할당")` → 오른쪽 캔버스 '작업 흐름' 탭의 그 단계 아래에 화면이 쌓인다(flow_id·node_id를 넣는다). 이미지는 긴 변 1600px로 줄여 올린다.
- 큰 흐름은 `dds-flow.propose_flow`로 먼저 보여 준다(type: cad/pre). 각 단계를 `update_node`로 진행 표시한다.
