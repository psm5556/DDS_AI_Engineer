# Simcenter Nastran 결과 파일과 pyNastran 추출

## 파일
| 확장자 | 내용 | 용도 |
|---|---|---|
| `.dat` | 솔버 입력(Bulk Data) | 모델 확인, NX 밖에서 재해석할 때 |
| `.f06` | 텍스트 출력 | **완료와 실패 판정**(`FATAL`, `END OF JOB`), 경고, 질량(GPWG) |
| `.op2` | 바이너리 결과 | **결과 추출**(pyNastran) |
| `.log` | 솔버 실행 로그 | 메모리, 디스크, 라이선스 오류 |

## f06 판정
- `*** USER FATAL MESSAGE nnnn` 또는 `SYSTEM FATAL`이 있으면 실패다. 메시지 번호로 원인을 찾는다. 예: 9050은 특이 강성(구속 부족), 2025는 요소 기하 불량.
- 정상 종료 시 f06 끝에 `* * * END OF JOB * * *`가 있다.
- `USER WARNING`은 실패가 아니다. 다만 `EPSILON`이 크면 수치적으로 불안정하다는 뜻이다(구속 부족 의심).

## pyNastran (플랫폼 venv, 검증 버전 1.4.1)
```python
from pyNastran.op2.op2 import read_op2
model = read_op2("x.op2", build_dataframe=False, debug=False, mode="nx")
# 변위: dict(subcase key -> RealDisplacementArray), data shape (ntimes, nnodes, 6) = t1,t2,t3,r1,r2,r3
for k, d in model.displacements.items(): d.data, d.node_gridtype
# 응력: 1.4+ 는 model.op2_results.stress.ctetra_stress (1.3 은 model.ctetra_stress)
obj.get_headers()   # solid: [..., 'von_mises'] / plate: ['fiber_distance','oxx','oyy','txy','angle','omax','omin','von_mises']
obj.data            # (ntimes, n_rows, n_cols), obj.element_node 으로 요소/절점 매핑
model.spc_forces    # SPC 반력 (data[:, :, :3] 힘)
model.eigenvalues   # dict(title -> RealEigenvalues), .cycles [Hz], .eigenvalues
```
- 응력 출력 위치에 따라 행이 달라진다. 출력 위치는 Solid는 CENTER 또는 CORNER, Plate는 상하 fiber 2행이다. 템플릿은 **전체 최대값**만 쓴다.
- op2 응력은 **요소별 비평균값**이다. NX 후처리 컨투어의 기본값(절점 평균)보다 높게 나온다. 평가 기준을 사용자와 합의하고 results.json 키 이름에 반영한다. 예: `max_von_mises_elem`.
- 특정 위치 값이 필요하면 `node_gridtype[:,0]`(절점 ID)나 `element_node[:,0]`(요소 ID)로 행을 골라낸다. 관심 영역의 절점/요소 ID를 GUI의 Group으로 만들어 두고 ID 목록을 받으면 편하다.
- 단위: Nastran은 단위가 없다. 모델 단위계(NX 기본 mm-N-s → 응력 MPa)를 따른다. NX FEM의 단위계를 사용자에게 확인한다.
- 여러 subcase(하중 케이스)가 있으면 템플릿은 전체 최대값을 쓴다. 반력은 첫 subcase 기준이다. 케이스별 값이 필요하면 `obj.isubcase`로 나눠 `_sc1` 같은 접미사를 붙인다.
