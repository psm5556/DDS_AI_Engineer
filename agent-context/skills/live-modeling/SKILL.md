---
name: live-modeling
description: 사용자가 열어 둔 CAD/CAE GUI(NX, Abaqus/CAE, HyperMesh, STAR-CCM+, Ansys Mechanical)에 붙어서 사람과 같은 화면에서 형상 생성·해석 모델링(재료·하중·경계조건·메쉬)을 함께 하는 방법(Live Link, dds-live MCP). "같이 모델링하자", "열어 둔 모델에서", "화면 보면서", "해석 모델 만들어 줘"처럼 대화형 모델 준비를 요청할 때 사용.
---

# Live Link: 열려 있는 CAD/CAE 화면에서 함께 모델링

자동화(배치·DOE) 전에 **해석 모델을 만드는 단계**는 사람의 판단이 많이 필요하다. Live Link는 사용자가 보고 있는 GUI 세션에서 에이전트가 작은 단계씩 작업하고, 매 단계 화면을 보여 주며 확인받는 방식이다.

## 1. 진행 원칙
1. `dds-live.live_status`로 붙을 수 있는 툴을 확인한다.
   - 연결이 안 돼 있으면 아래 툴별 '시작 방법'을 사용자에게 안내한다. 연습용이나 Abaqus는 `live_launch`로 직접 띄울 수 있다.
2. `live_info`로 열린 모델 구조(파트·재료·스텝·세트)를 먼저 읽는다. 이름을 추측하지 않는다.
3. **작은 단계씩** `live_exec`를 실행한다(예: 재료 하나 → 단면 → 할당 → 스텝 → 하중 → 경계조건 → 메쉬).
   - 단계마다 `live_capture`로 화면을 게시하고 짧게 설명한다.
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
  - 또는 사용자가 `%USERPROFILE%\abaqus_plugins\ddsLive`에 플러그인을 복사하고 CAE를 재시작하면 Plug-ins > DDS Live Link 시작
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
- 시작: 사용자가 HyperMesh에서 `source <설치폴더>/agent-context/live/hypermesh/dds_live_hm.tcl`
  - 상시 사용하려면 hmcustom.tcl에 추가한다.
- 예시: `*createmark comps 1 "all"; set n [hm_marklength comps 1]; set n` (마지막 값이 result)
- 상세 명령은 `altair-automation` 스킬(references/hypermesh-tcl.md)을 본다.

### NX / Simcenter 3D (nx) — Python NXOpen, 대기열 방식
- NX Open은 GUI 스레드에서만 안전하다. 그래서 에이전트가 `live_exec(tool="nx", code=...)`로 저널을 대기열에 넣는다.
- 그다음 사용자에게 **"NX에서 'DDS 실행' 버튼을 눌러 주세요"**라고 안내한다.
  - 버튼은 `dds_live_nx.py`를 리본에 등록한 것이다. 처음 한 번 등록을 도와준다.
- 버튼을 누르면 열린 세션에서 실행되고 Undo 마크가 남는다. 결과는 `live_result(job)`로 확인한다.
- 코드에서 쓸 수 있는 것: `session`, `work_part`, `display_part`, `NXOpen`
  - 상세 API는 `nx-cad-automation`, `simcenter3d-automation` 스킬을 본다.

### STAR-CCM+ — 클라이언트-서버 공유 (TODO(사내검증))
- 사용자가 `starccm+ -server -port 47827 model.sim`으로 서버를 띄우고 GUI 클라이언트로 접속한다.
- 에이전트는 매크로를 `dds-cae.run_script(tool="starccm", args=["-host","localhost","-port","47827"])`로 같은 서버에 실행한다. 같은 sim이므로 사용자 화면에 반영된다.

### Ansys Mechanical — PyMechanical GUI 연결 (TODO(사내검증))
- Mechanical을 gRPC로 띄운다(`ansys-mechanical -g --port 10000`, 버전별 옵션 확인).
- `dds-cae.run_script(tool="ansys_python")`로 `ansys.mechanical.core.connect_to_mechanical(port=10000)` 후 `run_python_script(...)`를 실행한다. 사용자가 보는 Mechanical 트리가 바로 바뀐다.

## 3. 사용자에게 보여 주는 방식
- 단계마다 `live_capture(tool, title="2. 재료·단면 할당")` → 오른쪽 캔버스 '결과'에 화면이 쌓인다.
- 큰 흐름은 `dds-flow.propose_flow`로 먼저 보여 준다(type: cad/pre). 각 단계를 `update_node`로 진행 표시한다.
