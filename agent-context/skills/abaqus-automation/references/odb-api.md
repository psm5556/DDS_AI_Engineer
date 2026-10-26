# Abaqus ODB 추출 / 이미지 API 레퍼런스

## 열기
```python
from odbAccess import openOdb            # abaqus python, abaqus cae 둘 다 사용 가능
odb = openOdb(path='case_job.odb', readOnly=True)
...
odb.close()
```
- 구버전 ODB: `from odbAccess import isUpgradeRequiredForOdb, upgradeOdb`
  `if isUpgradeRequiredForOdb(upgradeRequiredOdbPath=p): upgradeOdb(existingOdbPath=p, upgradedOdbPath='up.odb')`
  명령줄은 `abaqus upgrade -job up -odb old`.
- `.lck` 파일이 있으면 다른 프로세스가 쓰는 중이거나 비정상 종료된 것이다.

## 구조
```
odb.rootAssembly
  .instances['PART-1-1']   .nodes, .elements, .nodeSets, .elementSets
  .nodeSets['FIX'] / .elementSets['ALL']   (어셈블리 셋, 이름은 대문자)
odb.steps['Step-1']
  .frames[i]  .frameValue(시간), .fieldOutputs['S'|'U'|'RF'|'PEEQ'|'E'|'EVOL'...]
  .historyRegions['Node PART-1-1.12' | 'Assembly ASSEMBLY' | 'Element PART-1-1.5 Int Point 1']
       .historyOutputs['U2'|'RF2'|'ALLIE'|'ALLKE'...].data  -> ((t, v), ...)
```
목록은 `abq_odb_extract.py --list`로 출력한다.

## 필드 출력
```python
frame = odb.steps[list(odb.steps.keys())[-1]].frames[-1]
S = frame.fieldOutputs['S']
S_set = S.getSubset(region=odb.rootAssembly.elementSets['ALL'])
S_nodal = S.getSubset(position=ELEMENT_NODAL)     # from abaqusConstants import ELEMENT_NODAL
for v in S_set.values:
    v.mises, v.maxPrincipal, v.minPrincipal, v.tresca, v.press   # 텐서 불변량
    v.elementLabel, v.integrationPoint, v.instance.name
U = frame.fieldOutputs['U']
for v in U.values:
    v.nodeLabel, v.data, v.magnitude
```
- 최대 Mises: `max(v.mises for v in S.values)`(적분점 기준).
- 위치 찾기: 최대값의 `v.elementLabel`, `v.instance.name`을 함께 기록하면 디버깅이 쉽다.
- 반력 합: `sum(v.data[1] for v in frame.fieldOutputs['RF'].getSubset(region=nset).values)`
- 특정 절점 값: `inst.getNodeFromLabel(12)`로 노드를 찾고, `U.getSubset(region=node)`로 값을 뽑는다.
- 성능: 대형 모델은 `for v in field.values` 루프가 느리다. `field.bulkDataBlocks`(numpy 배열 `.data`, `.nodeLabels`, `.elementLabels`)를 쓰면 빠르다. TODO(사내검증): `bulkDataBlocks[i].mises` 배열 지원 여부는 버전별로 확인한다. 없으면 `.data`(6성분)로 직접 Mises를 계산한다.
- 좌표계 변환: `field.getTransformedField(datumCsys=...)`.

## 히스토리 출력
```python
h = odb.steps['Step-1'].historyRegions['Assembly ASSEMBLY'].historyOutputs['ALLIE'].data
t_last, v_last = h[-1]
peak = max(abs(v) for t, v in h)
```
- 절점 히스토리 영역 이름은 `Node <INSTANCE>.<label>` 형식이다. 셋으로 요청했어도 절점별 영역으로 저장된다.

## 이미지 (abaqus cae noGUI 전용)
```python
odb = session.openOdb(name='case_job.odb')
vp = session.viewports['Viewport: 1']
vp.setValues(displayedObject=odb)
vp.odbDisplay.setFrame(step=0, frame=len(odb.steps['Step-1'].frames) - 1)  # 스텝/프레임 인덱스
vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))   # DEFORMED, CONTOURS_ON_UNDEF, UNDEFORMED
vp.odbDisplay.setPrimaryVariable(variableLabel='S', outputPosition=INTEGRATION_POINT, refinement=(INVARIANT, 'Mises'))
vp.odbDisplay.setPrimaryVariable(variableLabel='U', outputPosition=NODAL, refinement=(INVARIANT, 'Magnitude'))
vp.odbDisplay.setPrimaryVariable(variableLabel='PEEQ', outputPosition=INTEGRATION_POINT)
vp.odbDisplay.commonOptions.setValues(deformationScaling=UNIFORM, uniformScaleFactor=10.0)
vp.odbDisplay.contourOptions.setValues(maxAutoCompute=OFF, maxValue=300.0, minAutoCompute=OFF, minValue=0.0)
vp.view.setValues(session.views['Iso']); vp.view.fitView()
vp.view.setViewpoint(viewVector=(1.0, -1.0, 1.0), cameraUpVector=(0.0, 0.0, 1.0))
vp.viewportAnnotationOptions.setValues(triad=ON, legend=ON, title=OFF, state=ON, compass=OFF)
session.printOptions.setValues(vpDecorations=OFF, reduceColors=False)
session.pngOptions.setValues(imageSize=(1600, 1000))
session.printToFile(fileName='images/mises_iso', format=PNG, canvasObjects=(vp,))
```
- 특정 셋만 보이기: `leaf = dgo.LeafFromElementSets(elementSets=('PART-1-1.ALL',))`, `vp.odbDisplay.displayGroup.replace(leaf=leaf)`. TODO(사내검증): 셋 이름 형식(인스턴스 접두어)은 rpy 기록으로 확인한다.
- 모델(해석 전) 그림: `vp.setValues(displayedObject=mdb.models['Model-1'].rootAssembly)`, `vp.assemblyDisplay.setValues(mesh=ON)`. TODO(사내검증): 메쉬 표시 옵션 이름은 rpy 기록으로 확인한다.
- 빈 이미지가 나오면 원격 세션, 그래픽 드라이버 문제일 수 있다. TODO(사내검증): 배치 서버에서 noGUI 렌더링 확인.
