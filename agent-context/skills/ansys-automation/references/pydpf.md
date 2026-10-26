# PyDPF-Core(ansys-dpf-core) 레퍼런스: 공통 결과 추출기

DPF는 `.rst`(MAPDL/Mechanical/Workbench)를 솔버나 GUI 없이 읽는다. DPF 서버는 Ansys 설치(2021 R1 이후)에 포함되어 있고, 독립 실행형 DPF Server도 있다.

## 기본
```python
from ansys.dpf import core as dpf
model = dpf.Model(r"C:\work\case_0001\file.rst")
print(model)                                         # available results, sets, mesh info
last = model.metadata.time_freq_support.n_sets
mesh = model.metadata.meshed_region                  # mesh.nodes.n_nodes, mesh.elements.n_elements
```

## 결과 연산자
```python
disp_op = model.results.displacement()
disp_op.inputs.time_scoping([last])                 # specific set(s)
disp_fc = disp_op.outputs.fields_container()
norm_fc = dpf.operators.math.norm_fc(disp_fc).outputs.fields_container()

s_op = model.results.stress()
s_op.inputs.time_scoping([last])
s_op.inputs.requested_location.connect(dpf.locations.nodal)   # nodal averaged
vm_fc = dpf.operators.invariant.von_mises_eqv_fc(s_op.outputs.fields_container()).outputs.fields_container()

mm = dpf.operators.min_max.min_max_fc(vm_fc)
max_vm = max(mm.outputs.field_max().data)
```
- 특정 영역만 보려면 named selection 스코핑을 쓴다: `s_op.inputs.mesh_scoping.connect(model.metadata.named_selection("NS_NAME"))`. MAPDL에서 component 이름은 대문자다. `TODO(사내검증)`
- 요소 스코핑: `dpf.Scoping(ids=[...], location=dpf.locations.elemental)`
- 반력: `dpf.operators.result.reaction_force()`. 해석에서 반력 출력(`OUTRES,RSOL`)이 켜져 있어야 한다. `TODO(사내검증)`. 대안은 MAPDL `FSUM`이나 Mechanical Force Reaction 프로브다.

## 이미지
```python
field = vm_fc[0]
field.plot(off_screen=True, notebook=False, screenshot="images/mises.png")
# camera: field.plot(..., cpos=[(x,y,z),(fx,fy,fz),(ux,uy,uz)])
```

## 주의
- Mechanical 결과 객체의 기본값은 노드 평균이며, 바디 경계에서는 평균하지 않는다. DPF 노드 평균은 모든 요소를 평균할 수 있어 값이 조금 다를 수 있다. 같은 스터디에서는 한 가지 방법만 쓴다.
- 쉘 요소는 top/bottom/mid 레이어 선택에 따라 값이 다르다. `TODO(사내검증)`: `shell_layer` 입력
- 단위는 `field.unit`으로 확인해 로그에 남긴다.
