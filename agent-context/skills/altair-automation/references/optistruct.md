# OptiStruct 실행과 결과 레퍼런스

## 실행
```
optistruct.bat case.fem -nt 4                 # Windows (<install>/hwsolvers/scripts/optistruct.bat)
optistruct case.fem -nt 4                     # Linux
optistruct case.fem -check                    # syntax check only
optistruct case.fem -nt 4 -core in            # in-core solver
```
- `-nt`는 스레드 수다. MPI 병렬은 `-np`와 `-ddm` 계열을 쓰며 `TODO(사내검증)`이다.
- 출력 파일은 `case.out`(요약/에러), `case.stat`(진행), `case.h3d`(기본 결과), `case.op2`/`case.pch`(요청 시), `case.mvw`다.

## 완료/실패 판정
- 성공: `.out`에 `ANALYSIS COMPLETED`가 있고(최적화는 수렴 메시지) `*** ERROR`가 없다. 정확한 문구는 버전별로 확인한다. `TODO(사내검증)`
- 에러 블록은 `*** ERROR # nnn ***` 뒤에 설명과 카드 이름이 나온다. 드라이버는 실패 시 `.out` 경로를 로그에 남긴다.
- 경고(`*** WARNING`)는 실패로 보지 않지만 로그에 남긴다.

## 결과 출력 요청
Case control(SUBCASE 안이나 위):
```
DISPLACEMENT = ALL
STRESS = ALL          $ von Mises is the default stress output for shells/solids
SPCFORCE = ALL
```
I/O 옵션 섹션(파일 맨 위):
```
OUTPUT,H3D,ALL        $ default HyperView results
OUTPUT,OP2,ALL        $ Nastran OUTPUT2 -> case.op2   TODO(사내검증) exact field layout
OUTPUT,PUNCH,ALL      $ text .pch                     TODO(사내검증)
OUTPUT,MASSPROP,ALL   $ mass / CoG summary in .out    TODO(사내검증)
```
- 구형 `FORMAT` 카드보다 `OUTPUT` 카드가 우선한다. Bulk에 `PARAM,POST,-1`이 있으면 OP2가 출력된다는 기록도 있다. `TODO(사내검증)`

## .out 파싱
- 질량: 반복 요약의 `Mass = 1.234E+00`(드라이버 `mass_from_out`). 여러 번 출력되면 마지막 값을 쓴다.
- 최적화 응답, 목적함수, 제약은 반복별 표에 있다. 사내 `.out` 샘플로 정규식을 만들고 템플릿에 추가한다. `TODO(사내검증)`

## OP2 → pyNastran
```python
from pyNastran.op2.op2 import read_op2
op2 = read_op2("case.op2", build_dataframe=False, debug=False)
disp = op2.displacements[1].data          # (ntimes, nnodes, 6): tx ty tz rx ry rz
nids = op2.displacements[1].node_gridtype[:, 0]
spc  = op2.spc_forces[1].data
# stresses: pyNastran >= 1.3 -> op2.op2_results.stress.cquad4_stress / ctetra_stress / chexa_stress ...
obj = op2.op2_results.stress.cquad4_stress[1]
vm_col = obj.headers.index("von_mises")
vm_max = obj.data[-1, :, vm_col].max()
```
- 서브케이스 키는 보통 정수 ID다. 일부 버전에서는 튜플일 수 있으므로 `sorted(dict)[0]`로 첫 키를 쓴다.
- 쉘 응력은 상하 fiber 두 행이 있다. max는 두 면을 모두 포함한다.
- OptiStruct 고유 테이블을 읽지 못하면 pyNastran 경고가 출력된다. 대안은 `.pch` 파싱이나 HyperView 쿼리 export다. `TODO(사내검증)`

## Radioss(간단히)
- 덱은 starter `model_0000.rad`와 engine `model_0001.rad`로 나뉜다. 실행은 `radioss model_0000.rad -nt 4`이고 `TODO(사내검증)`이다.
- 정상 종료는 엔진 출력(`model_0001.out`)의 `NORMAL TERMINATION`으로 판정한다. `TODO(사내검증)`
- 결과: `modelT01`(시간이력), `modelA001...`(애니메이션). HyperGraph, HyperView, 또는 변환 도구(`th_to_csv`, `anim_to_vtk`. 도구 이름 `TODO(사내검증)`)로 추출한다.
