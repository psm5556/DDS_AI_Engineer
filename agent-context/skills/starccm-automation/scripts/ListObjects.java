// ListObjects.java -- STAR-CCM+ sim 구조 조사 매크로 (자동화 스크립트 작성 전 1회 실행)
//
// 실행:  starccm+ -batch ListObjects.java model.sim      (tool: starccm, run_script 로 실행)
// 출력:  inventory.txt  -- Global Parameter / Report / Scene / Plot / Region-Boundary /
//                          Continuum / 정지조건 이름 목록 (GUI 트리 표시 이름)
//        results.json   -- 각 항목 개수 (규약상 성공 표시용)
//
// 확인된 API만 사용한다. 파라미터 현재 값, 3D-CAD 설계 파라미터 목록은 주석 참고(TODO(사내검증)).
package macro;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;

import star.common.*;
import star.base.neo.*;
import star.base.report.*;
import star.vis.*;

public class ListObjects extends StarMacro {

  public void execute() {
    Simulation sim = getActiveSimulation();
    File caseDir = new File(System.getProperty("user.dir"));
    StringBuilder sb = new StringBuilder();
    int nParam = 0, nReport = 0, nScene = 0, nPlot = 0, nBoundary = 0, nCont = 0, nStop = 0;

    sb.append("# sim: ").append(sim.getSessionPath()).append("\n\n");

    sb.append("## Global Parameters\n");
    // 요소 타입 이름(GlobalParameterBase 등)에 의존하지 않도록 Object 로 순회
    for (Object o : sim.get(GlobalParameterManager.class).getObjects()) {
      NamedObject p = (NamedObject) o;
      sb.append("- ").append(p.getPresentationName())
        .append("  [").append(o.getClass().getSimpleName()).append("]\n");
      // TODO(사내검증): 현재 값 출력 -- ((ScalarGlobalParameter) p).getQuantity().getDefinition()
      nParam++;
    }

    sb.append("\n## Reports\n");
    for (Report r : sim.getReportManager().getObjects()) {
      sb.append("- ").append(r.getPresentationName())
        .append("  [").append(r.getClass().getSimpleName()).append("]\n");
      nReport++;
    }

    sb.append("\n## Scenes\n");
    for (Scene s : sim.getSceneManager().getScenes()) {
      sb.append("- ").append(s.getPresentationName()).append("\n");
      nScene++;
    }

    sb.append("\n## Plots\n");
    for (StarPlot p : sim.getPlotManager().getPlots()) {
      sb.append("- ").append(p.getPresentationName())
        .append("  [").append(p.getClass().getSimpleName()).append("]\n");
      nPlot++;
    }

    sb.append("\n## Regions / Boundaries\n");
    for (Region reg : sim.getRegionManager().getRegions()) {
      sb.append("- ").append(reg.getPresentationName()).append("\n");
      for (Boundary b : reg.getBoundaryManager().getBoundaries()) {
        sb.append("    - ").append(b.getPresentationName()).append("\n");
        // TODO(사내검증): 경계 타입 -- b.getBoundaryType().getPresentationName()
        nBoundary++;
      }
    }

    sb.append("\n## Continua\n");
    for (Continuum c : sim.getContinuumManager().getObjects()) {
      sb.append("- ").append(c.getPresentationName())
        .append("  [").append(c.getClass().getSimpleName()).append("]\n");
      nCont++;
    }

    sb.append("\n## Stopping Criteria\n");
    for (SolverStoppingCriterion c : sim.getSolverStoppingCriterionManager().getObjects()) {
      sb.append("- ").append(c.getPresentationName())
        .append("  [").append(c.getClass().getSimpleName()).append("]\n");
      nStop++;
    }

    // 3D-CAD 설계 파라미터 -- TODO(사내검증) 후 주석 해제 (import star.cadmodeler.*; 필요)
    // for (SolidModelPart ...) / sim.get(SolidModelManager.class).getObjects() -> CadModel
    //   -> getDesignParameterManager().getObjects()

    try {
      write(new File(caseDir, "inventory.txt"), sb.toString());
      String json = "{\n"
          + "  \"n_global_parameters\": " + nParam + ",\n"
          + "  \"n_reports\": " + nReport + ",\n"
          + "  \"n_scenes\": " + nScene + ",\n"
          + "  \"n_plots\": " + nPlot + ",\n"
          + "  \"n_boundaries\": " + nBoundary + ",\n"
          + "  \"n_continua\": " + nCont + ",\n"
          + "  \"n_stopping_criteria\": " + nStop + "\n}\n";
      write(new File(caseDir, "results.json"), json);
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
    sim.println(sb.toString());
  }

  private static void write(File f, String text) throws java.io.IOException {
    Writer w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8);
    try {
      w.write(text);
    } finally {
      w.close();
    }
  }
}
