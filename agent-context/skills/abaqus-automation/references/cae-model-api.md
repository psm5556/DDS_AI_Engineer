# Abaqus/CAE 모델 수정 API 레퍼런스 (abaqus cae noGUI)

공통 import (CAE 스크립트 맨 위):
```python
from abaqus import *
from abaqusConstants import *
from caeModules import *
import mesh          # mesh.ElemType
```
**가장 확실한 API 확인 방법**: GUI에서 같은 작업을 한 번 하고 작업 폴더의 `abaqus.rpy`(리플레이 파일)를 연다. Abaqus가 실행한 정확한 명령이 기록된다. 스크립트는 이 기록을 그대로 일반화해서 만든다. 이름 기반 참조(`findAt` 좌표 대신)를 쓰려면 기록 전에 `session.journalOptions.setValues(replayGeometry=COORDINATE, recoverGeometry=COORDINATE)`를 실행해 두면 좌표 기반으로 기록된다.

## 모델 열기/저장
```python
openMdb(pathName='bracket.cae')          # 전역 mdb 가 교체됨
m = mdb.models['Model-1']
mdb.saveAs(pathName='case_model.cae')    # 원본을 덮어쓰지 말 것
Mdb()                                    # 빈 데이터베이스로 새로 시작
```
`print(m.parts.keys(), m.materials.keys(), m.sections.keys(), m.loads.keys(), m.boundaryConditions.keys(), m.steps.keys())`로 이름을 먼저 확인한다.

## 형상
| 작업 | 코드 |
|---|---|
| 피처 목록 | `p.features.keys()` |
| 돌출 깊이 | `p.features['Solid extrude-1'].setValues(depth=20.0)` |
| 스케치 교체 | `p.features['Solid extrude-1'].setValues(sketch=s)` |
| 재생성 | `p.regenerate()` 후 `m.rootAssembly.regenerate()` |
| 피처 억제/복원 | `p.features['Fillet-1'].suppress()` / `.resume()` |

스케치 치수 수정은 SKILL.md 3-2의 패턴을 쓴다(`ConstrainedSketch(name='__edit__', objectToCopy=feat.sketch)` → `projectReferencesOntoSketch` → `s.dimensions[i].setValues(value=...)` → `feat.setValues(sketch=s)`).
- 치수 인덱스 `i`는 GUI 기록에서 확인한다. TODO(사내검증): 스케치 파라미터(`s.Parameter(name=..., path='dimensions', expression=...)`, `s.parameters[name].setValues(expression=...)`)를 쓰는 모델이면 이름으로 접근할 수 있다. 버전별로 확인한다.

### 새로 만들기 (abq_build_plate_model.py 참고)
```python
s = m.ConstrainedSketch(name='__profile__', sheetSize=400.0)
s.rectangle(point1=(0.0, 0.0), point2=(L, W))
s.CircleByCenterPerimeter(center=(cx, cy), point1=(cx + R, cy))
p = m.Part(name='Plate', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=T)          # 쉘이면 p.BaseShell(sketch=s)
del m.sketches['__profile__']
```
좌표 기반 선택: `inst.faces.getByBoundingBox(xMin=, yMin=, zMin=, xMax=, yMax=, zMax=)`, `inst.faces.findAt(((x, y, z),))`, `inst.edges`, `inst.vertices`도 같은 방식으로 쓴다.

## 재료
```python
mat = m.materials['Steel']
mat.elastic.setValues(table=((E, nu),))
mat.density.setValues(table=((rho,),))
mat.plastic.setValues(table=((sy0, 0.0), (sy1, ep1)))     # (항복응력, 소성변형률)
# 새 재료
mat = m.Material(name='Al'); mat.Elastic(table=((70000.0, 0.33),)); mat.Density(table=((2.7e-9,),))
```
현재 값 읽기: `mat.elastic.table`

## 단면 / 두께
```python
m.sections['Shell-1'].setValues(thickness=2.5)            # HomogeneousShellSection
m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)
p.SectionAssignment(region=p.Set(cells=p.cells, name='ALL'), sectionName='Sec')
```
쉘 두께가 `thicknessType=DISTRIBUTION`이거나 복합재(CompositeShellSection)이면 해당 객체의 설정 방식을 rpy로 확인한다.

## 하중
| 종류 | 생성 | 값 변경(생성 스텝) | 후속 스텝에서 변경 |
|---|---|---|---|
| 집중하중 | `m.ConcentratedForce(name, createStepName, region, cf1=, cf2=, cf3=)` | `m.loads[n].setValues(cf2=-1000.0)` | `m.loads[n].setValuesInStep(stepName='Step-2', cf2=-2000.0)` |
| 압력 | `m.Pressure(name, createStepName, region=surface, magnitude=)` | `setValues(magnitude=1.5)` | `setValuesInStep(stepName=, magnitude=)` |
| 중력 | `m.Gravity(name, createStepName, comp3=-9810.0)` | `setValues(comp3=...)` | |

진폭: `m.TabularAmplitude(name='Amp-1', data=((0.0, 0.0), (1.0, 1.0)))` 후 하중에 `amplitude='Amp-1'`.

## 경계조건
```python
m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FIX'])
m.DisplacementBC(name='Disp', createStepName='Step-1', region=a.sets['TOP'], u2=-1.0)
m.boundaryConditions['Disp'].setValues(u2=-2.0)
m.boundaryConditions['Disp'].setValuesInStep(stepName='Step-2', u2=-3.0)
```
자유도를 비워두려면 `UNSET`, 고정은 `SET`(회전 등).

## 메쉬
```python
p.deleteMesh()
p.seedPart(size=h, deviationFactor=0.1, minSizeFactor=0.1)
p.seedEdgeBySize(edges=p.edges.getByBoundingBox(...), size=h2, constraint=FINER)   # 국부 시드
p.setMeshControls(regions=p.cells, elemShape=TET, technique=FREE)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D10, elemLibrary=STANDARD),))
p.generateMesh()
print(len(p.elements), len(p.nodes))
```
- 인스턴스가 independent(`dependent=OFF`)이면: `a.seedPartInstance(regions=(inst,), size=h, deviationFactor=0.1, minSizeFactor=0.1)`, `a.generateMesh(regions=(inst,))`.
- 쉘: `elemShape=QUAD`, `technique=STRUCTURED|SWEEP|FREE`, 요소 `S4R`.
- 메쉬 후 `m.rootAssembly.regenerate()`.

## 스텝 / 출력 요청
```python
m.steps['Step-1'].setValues(timePeriod=1.0, initialInc=0.1, minInc=1e-8, maxInc=0.1, maxNumInc=1000, nlgeom=ON)
m.steps['Step-1'].setValues(timePeriod=0.01)     # ExplicitDynamicsStep
m.StaticStep(name='Load', previous='Initial', timePeriod=1.0, initialInc=1.0)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'PEEQ', 'U', 'RF', 'EVOL'))
m.HistoryOutputRequest(name='H-RF', createStepName='Load', variables=('RF1', 'RF2', 'RF3'), region=a.sets['FIX'])
```

## 잡
```python
job = mdb.Job(name='case_job', model='Model-1', numCpus=4, numDomains=4, memory=90, memoryUnits=PERCENTAGE)
mdb.jobs['case_job'].setValues(numCpus=8, numDomains=8)
job.writeInput(consistencyChecking=OFF)    # inp 만 쓰기 (템플릿 만들 때 유용)
job.submit(consistencyChecking=OFF)
job.waitForCompletion()
```
- 판정은 `.sta`/`.log` 파일로 한다(SKILL.md 5절).
- Explicit 배정밀도: `mdb.Job(..., explicitPrecision=DOUBLE_PLUS_PACK)`.
- GPU, 병렬 모드 같은 옵션은 사내 라이선스 정책에 따른다.

## 질량
```python
mp = m.rootAssembly.getMassProperties()       # 딕셔너리
mass = mp['mass']; com = mp['centerOfMass']; vol = mp['volume']
```
- 섹션과 재료에 밀도가 있어야 `mass`가 계산된다(없으면 None).
- 특정 영역만: `getMassProperties(regions=(inst,))`. TODO(사내검증): 버전별 `regions` 허용 타입(인스턴스, 셋)을 확인한다.
