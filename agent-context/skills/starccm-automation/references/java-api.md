# STAR-CCM+ Java API 조각 모음

각 조각에는 신뢰도를 붙였다.
- **[확인]**: 녹화 매크로와 공개 예제에서 널리 쓰이는 형태다.
- **TODO(사내검증)**: 버전에 따라 이름이나 시그니처가 다르거나 확인이 덜 된 형태다. 템플릿의 실행 코드에 넣기 전에 **매크로 녹화**(`Tools → Macro → Start Recording`)로 확인한다.

매크로 안의 존재하지 않는 API는 **매크로 전체 컴파일 실패**로 이어진다. 확인되지 않은 코드를 실행 코드에 넣지 않는다.

## 0. 매크로 뼈대 [확인]
```java
package macro;

import java.io.*;
import java.util.*;
import star.common.*;
import star.base.neo.*;
import star.base.report.*;
import star.vis.*;
import star.meshing.*;

public class MyMacro extends StarMacro {
  public void execute() {
    Simulation sim = getActiveSimulation();
    sim.println("hello");
  }
}
```
- 경계조건과 유동 관련 클래스는 `star.flow.*`, 에너지는 `star.energy.*`, 물성은 `star.material.*`, 3D-CAD는 `star.cadmodeler.*`, 파트는 `star.meshing.*`/`star.base.neo.*` 패키지에 있다. 필요한 import는 녹화 매크로에서 복사한다.
- `resolvePath("x.png")`(StarMacro 메서드)는 매크로 기준 상대경로를 절대경로로 바꾼다. 템플릿은 케이스 폴더 기준 `File`의 절대경로를 직접 만든다.

## 1. Global Parameter [확인]
```java
GlobalParameterManager gpm = sim.get(GlobalParameterManager.class);
ScalarGlobalParameter p = (ScalarGlobalParameter) gpm.getObject("inlet_velocity");
p.getQuantity().setValue(12.0);          // 값(파라미터 단위 기준)
p.getQuantity().setDefinition("${a}*2"); // 표현식
// 단위까지 지정하려면:
// Units u = (Units) sim.getUnitsManager().getObject("m/s");
// p.getQuantity().setValueAndUnits(12.0, u);   // TODO(사내검증): 메서드 존재 여부(버전별)
```
- 벡터 파라미터는 `VectorGlobalParameter`를 쓴다. TODO(사내검증): 녹화로 값 지정 형식을 확인한다.
- 다른 곳(BC, CAD 치수, 물성)에서는 GUI 값 입력란에 `${inlet_velocity}`처럼 적어 참조한다. **이것이 PIDO 자동화의 권장 구조다.**

## 2. 3D-CAD 설계 파라미터, CAD 파트
### 2-1. 3D-CAD 설계 파라미터 직접 변경 — TODO(사내검증)
권장 방법은 CAD 치수에 `${param}`을 참조시키고 Global Parameter만 바꾸는 것이다. 직접 바꿔야 한다면 녹화 결과가 대략 아래 형태다.
```java
// import star.cadmodeler.*;
// CadModel cad = (CadModel) sim.get(SolidModelManager.class).getObject("3D-CAD Model 1");
// ScalarQuantityDesignParameter dp =
//     (ScalarQuantityDesignParameter) cad.getDesignParameterManager().getObject("Length");
// dp.getQuantity().setValue(0.12);
// cad.update();   // 또는 녹화에 나오는 업데이트 호출
// // 3D-CAD 모델이 연결된 파트를 갱신하는 호출도 녹화로 확인한다 (예: 파트 컨텍스트 메뉴 'Update')
```
### 2-2. 외부 CAD 파트 재임포트 — TODO(사내검증)
- `PartImportManager`의 `importCadPart(...)` 시그니처는 버전과 옵션(테셀레이션 밀도, 단위)에 따라 인자가 많고 달라진다. 반드시 녹화한다.
- 파트를 교체하면 Region, Boundary 연결(Part → Region assignment)이 끊길 수 있다. 같은 이름과 같은 면 이름으로 교체하는 방식(Replace/Update from file)을 녹화해서 쓴다.
- 외부 파라메트릭 CAD(NX 등)는 플랫폼의 다른 스킬로 먼저 형상을 내보내고(STEP/x_t), STAR-CCM+에서는 재임포트만 한다.

## 3. 메쉬 [확인]
```java
MeshPipelineController mpc = sim.get(MeshPipelineController.class);
mpc.clearGeneratedMeshes();   // 선택
mpc.generateSurfaceMesh();    // 표면만
mpc.generateVolumeMesh();     // 파이프라인 전체(표면 포함)
```
- 기본 크기 같은 메쉬 설정을 바꿀 때도 Global Parameter를 참조시키는 방식(`${base_size}`)이 가장 간단하다.
- 직접 바꾸는 예시는 다음과 같다. TODO(사내검증):
  ```java
  // AutoMeshOperation op = (AutoMeshOperation) sim.get(MeshOperationManager.class).getObject("Automated Mesh");
  // op.getDefaultValues().get(BaseSize.class).setValue(0.005);   // 신버전은 .getQuantity().setValue / setValueAndUnits
  ```

## 4. 경계조건 (import star.flow.*, star.energy.*)
```java
Region region = sim.getRegionManager().getRegion("Fluid");                 // [확인]
Boundary inlet = region.getBoundaryManager().getBoundary("Inlet");         // [확인]

// 입구 속도 크기 — TODO(사내검증): 프로파일 클래스/메서드 형태는 녹화로 확인
// VelocityMagnitudeProfile vmp = inlet.getValues().get(VelocityMagnitudeProfile.class);
// vmp.getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(10.0);

// 정온도 — TODO(사내검증)
// StaticTemperatureProfile stp = inlet.getValues().get(StaticTemperatureProfile.class);
// stp.getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(300.0);

// 경계 타입 변경 — TODO(사내검증)
// inlet.setBoundaryType(VelocityBoundary.class);
```
- 질량유량 입구는 `MassFlowRateProfile`, 압력 출구는 `StaticPressureProfile`, 벽 열유속은 `HeatFluxProfile` 같은 클래스가 대응된다(이름은 녹화로 확인).
- **권장**: 값 입력란에 `${inlet_velocity}`를 넣어 두고 Global Parameter만 바꾼다.

## 5. 물리 연속체, 물성 — TODO(사내검증)
```java
PhysicsContinuum pc = (PhysicsContinuum) sim.getContinuumManager().getContinuum("Physics 1");  // [확인]
// 단상 기체 예(녹화 형태 예시):
// SingleComponentGasModel gm = pc.getModelManager().getModel(SingleComponentGasModel.class);
// Gas gas = (Gas) gm.getMaterial();
// ConstantMaterialPropertyMethod m = (ConstantMaterialPropertyMethod)
//     gas.getMaterialProperties().getMaterialProperty(ConstantDensityProperty.class).getMethod();
// m.getQuantity().setValue(1.18);
// 액체는 SingleComponentLiquidModel / Liquid, 고체는 SolidModel / Solid
// 점도: DynamicViscosityProperty, 비열: SpecificHeatProperty, 열전도도: ThermalConductivityProperty
```
- 물성도 GUI에서 값 입력란에 `${rho}`를 넣어 두면 Global Parameter로 바꿀 수 있다.

## 6. 솔버, 정지조건
```java
// 최대 스텝 [확인: 구버전 녹화 형태, 신버전에서는 deprecated일 수 있음]
StepStoppingCriterion c = (StepStoppingCriterion)
    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
c.setMaximumNumberSteps(1000);
// TODO(사내검증) 신버전 녹화 형태: c.getMaximumNumberStepsObject().getQuantity().setValue(1000.0);

// 정지조건 활성/비활성 — TODO(사내검증)
// c.setIsUsed(true);

// 비정상: 시간 간격, 최대 물리시간 — TODO(사내검증)
// ImplicitUnsteadySolver us = sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
// us.getTimeStep().setValue(1.0e-3);
// PhysicalTimeStoppingCriterion pt = (PhysicalTimeStoppingCriterion)
//     sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Physical Time");
// pt.getMaximumTime().setValue(2.0);
```
- 정지조건 이름(`"Maximum Steps"`, `"Maximum Physical Time"` 등)은 sim마다 다를 수 있다. `ListObjects.java`로 확인한다.
- 잔차나 모니터 기반의 수렴 정지조건(`MonitorIterationStoppingCriterion`)은 GUI에서 미리 만들어 두는 것을 권장한다.

## 7. 실행 [확인]
```java
sim.getSolution().clearSolution();
sim.getSolution().initializeSolution();
sim.getSimulationIterator().run();       // 정지조건까지
sim.getSimulationIterator().run(200);    // 200 스텝 더 (TODO(사내검증): run(int) 오버로드)
sim.getSimulationIterator().step(200);   // 정지조건 무시하고 200 스텝
```

## 8. Report → 결과 [확인]
```java
Report r = sim.getReportManager().getReport("Pressure Drop");
double v = r.getReportMonitorValue();
for (Report rr : sim.getReportManager().getObjects()) { ... }
```
- 자주 쓰는 Report 종류: Surface Average, Mass Flow, Mass Flow Averaged, Force/Moment, Max/Min, Expression, Volume Average.
- 반복 마지막 N 스텝 평균처럼 진동하는 값을 정리하려면 Statistics Report나 Monitor 평균을 쓴다(GUI에서 준비).
- 단위: Report의 Units 설정을 따른다. 결과 키에 단위가 드러나지 않으므로, 단위는 스터디 설명에 명시한다.

## 9. Scene, Plot 이미지
```java
// [확인]
Scene s = sim.getSceneManager().getScene("Scalar Scene 1");
s.printAndWait("C:/case/images/scalar.png", 1, 1600, 900);   // (경로, 배율, 가로, 세로)
for (Scene sc : sim.getSceneManager().getScenes()) { ... }

// TODO(사내검증) 시그니처 확인
StarPlot plot = sim.getPlotManager().getPlot("Residuals");
plot.encode("C:/case/images/residuals.png", "png", 1200, 800);
for (StarPlot pl : sim.getPlotManager().getPlots()) { ... }

// Monitor Plot 데이터 CSV — TODO(사내검증)
// ((MonitorPlot) plot).export("C:/case/history.csv", ",");
```
- 뷰 방향 변경(카메라)은 녹화 시 `CurrentView.setInput(...)` 형태로 나온다. 인자가 많으므로, 사전에 `View`(카메라)를 저장해 두고 `scene.getCurrentView().setView(view)` 식으로 적용하는 것을 권장한다. TODO(사내검증)
- 스칼라 컨투어의 표시 필드나 범위 변경도 녹화로 확인한다(`ScalarDisplayer`, `getScalarDisplayQuantity()`).

## 10. 저장 [확인]
```java
sim.saveState(sim.getSessionPath());                       // 덮어쓰기
sim.saveState(new File(caseDir, "result.sim").getAbsolutePath());
```
- DOE에서 sim은 케이스마다 수 GB가 될 수 있다. 저장이 필요 없으면 `SAVE_SIM=false`로 둔다.
