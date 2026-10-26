// RunCase.java -- STAR-CCM+ 케이스 실행 템플릿 (DDS 플랫폼 규약 준수)
//
// 실행:   starccm+ -batch RunCase.java [-np 8] [-power -podkey KEY -licpath 1999@host] model.sim
// tool:   starccm   ({starccm} -batch {script} {args})
//
// 동작 순서:
//   1) 케이스 폴더의 params.json 읽기(외부 라이브러리 없는 평탄 JSON 파서)
//   2) 숫자 값 -> 같은 이름(또는 PARAM_NAME_MAP)의 Global Parameter 에 대입
//   3) (선택) max_steps 적용, 재메쉬, 해 초기화
//   4) 해석 실행
//   5) Report 값 -> results.json (모든 단계 성공 후 마지막에 기록)
//   6) Scene / Plot -> images/*.png (실패해도 케이스 실패로 보지 않음)
//   7) (선택) sim 저장
//
// 주의:
//   - 클래스 이름은 파일 이름과 같아야 한다 (파일명을 바꾸면 클래스명도 변경).
//   - Java 8 문법만 사용한다.
//   - 확인되지 않은 API는 실행 코드에 넣지 않는다(컴파일 실패 시 매크로 전체가 동작하지 않음).
package macro;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.*;
import star.base.report.*;
import star.vis.*;
import star.meshing.*;

public class RunCase extends StarMacro {

  // ===================== 사용자 설정 =====================
  /** params.json 키 -> Global Parameter 이름. 비어 있으면 키와 같은 이름을 사용. */
  static final String[][] PARAM_NAME_MAP = {
    // {"thickness", "Thickness"},
  };
  /** Global Parameter 로 보내지 않는 params.json 키 (제어용). */
  static final String[] CONTROL_KEYS = {"max_steps"};
  /** true: 숫자 파라미터에 대응하는 Global Parameter 가 없으면 실패 처리 (DOE 에서 권장). */
  static final boolean STRICT_PARAMS = true;

  static final boolean REMESH = true;          // 형상/메쉬 파라미터가 바뀌면 반드시 true
  static final boolean CLEAR_SOLUTION = true;  // 이전 해에서 이어 계산하지 않도록

  /** {"Report 이름", "results 키"}. 비어 있으면 모든 Report 를 snake_case 키로 추출. */
  static final String[][] REPORT_MAP = {
    // {"Pressure Drop", "pressure_drop"},
    // {"Outlet Temperature", "outlet_temp"},
  };

  static final boolean EXPORT_ALL_SCENES = true;
  static final String[] SCENES = {};           // EXPORT_ALL_SCENES=false 일 때 사용할 Scene 이름
  static final boolean EXPORT_PLOTS = true;    // 모든 Plot 을 PNG 로 (TODO(사내검증): encode 시그니처)
  static final int IMG_W = 1600;
  static final int IMG_H = 900;

  static final boolean SAVE_SIM = false;       // DOE 에서는 용량 때문에 보통 false
  // =======================================================

  public void execute() {
    Simulation sim = getActiveSimulation();
    File caseDir = findCaseDir(sim);
    log(sim, "case dir: " + caseDir.getAbsolutePath());

    try {
      // 1) params.json
      File paramsFile = new File(caseDir, "params.json");
      Map<String, String> params = new LinkedHashMap<String, String>();
      if (paramsFile.isFile()) {
        params = parseFlatJson(readText(paramsFile));
      } else {
        log(sim, "WARNING: params.json 없음 -> 기준 모델 그대로 실행");
      }
      log(sim, "params: " + params);

      // 2) Global Parameter
      applyGlobalParameters(sim, params);

      // 3) 정지조건 / 메쉬 / 초기화
      if (params.containsKey("max_steps")) {
        int n = (int) Math.round(Double.parseDouble(params.get("max_steps")));
        StepStoppingCriterion c = (StepStoppingCriterion)
            sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
        c.setMaximumNumberSteps(n);
        // TODO(사내검증): 신버전에서 위 메서드가 없으면 아래 형태로 교체
        // c.getMaximumNumberStepsObject().getQuantity().setValue((double) n);
        log(sim, "Maximum Steps = " + n);
      }
      if (REMESH) {
        log(sim, "generating mesh...");
        sim.get(MeshPipelineController.class).generateVolumeMesh();
      }
      if (CLEAR_SOLUTION) {
        sim.getSolution().clearSolution();
      }
      sim.getSolution().initializeSolution();

      // 4) 실행
      log(sim, "running solver...");
      sim.getSimulationIterator().run();
      log(sim, "solver finished");

      // 5) Report 추출 (아직 파일로 쓰지 않음)
      Map<String, Double> results = extractReports(sim);
      if (results.isEmpty()) {
        throw new RuntimeException("추출된 Report 결과가 없음 (REPORT_MAP 또는 sim 의 Report 확인)");
      }

      // 6) 이미지 (실패해도 계속)
      File imagesDir = new File(caseDir, "images");
      imagesDir.mkdirs();
      exportScenes(sim, imagesDir);
      if (EXPORT_PLOTS) {
        exportPlots(sim, imagesDir);
      }

      // 7) sim 저장 (실패해도 계속)
      if (SAVE_SIM) {
        try {
          sim.saveState(sim.getSessionPath());
        } catch (Exception e) {
          log(sim, "WARNING: sim 저장 실패: " + e);
        }
      }

      // 마지막에 results.json 기록 -> 존재하면 성공
      writeResults(new File(caseDir, "results.json"), results);
      log(sim, "results.json written: " + results);

    } catch (Exception e) {
      StringWriter sw = new StringWriter();
      e.printStackTrace(new PrintWriter(sw));
      log(sim, "ERROR: " + sw.toString());
      try {
        writeText(new File(caseDir, "error.txt"), sw.toString());
      } catch (Exception ignore) {
        // ignore
      }
      // results.json 을 쓰지 않고 종료 -> 플랫폼이 failed 처리
      // TODO(사내검증): 예외 시 starccm+ 프로세스 종료코드가 0 이 아닌지 확인
      throw new RuntimeException(e);
    }
  }

  // ------------------------------------------------------------------
  // 파라미터
  // ------------------------------------------------------------------
  private void applyGlobalParameters(Simulation sim, Map<String, String> params) {
    GlobalParameterManager gpm = sim.get(GlobalParameterManager.class);
    for (Map.Entry<String, String> e : params.entrySet()) {
      String key = e.getKey();
      String raw = e.getValue();
      if (isControlKey(key)) {
        continue;
      }
      Double value = toDouble(raw);
      if (value == null) {
        log(sim, "skip non-numeric param: " + key + " = " + raw);
        continue;
      }
      String gpName = mapName(key);
      ScalarGlobalParameter p = null;
      try {
        p = (ScalarGlobalParameter) gpm.getObject(gpName);
      } catch (Exception ex) {
        p = null;
      }
      if (p == null) {
        String msg = "Global Parameter 없음: '" + gpName + "' (params 키 '" + key + "')";
        if (STRICT_PARAMS) {
          throw new RuntimeException(msg);
        }
        log(sim, "WARNING: " + msg);
        continue;
      }
      p.getQuantity().setValue(value.doubleValue());
      log(sim, "set " + gpName + " = " + value);
    }
  }

  private static String mapName(String key) {
    for (String[] pair : PARAM_NAME_MAP) {
      if (pair[0].equals(key)) {
        return pair[1];
      }
    }
    return key;
  }

  private static boolean isControlKey(String key) {
    for (String k : CONTROL_KEYS) {
      if (k.equals(key)) {
        return true;
      }
    }
    return false;
  }

  // ------------------------------------------------------------------
  // 결과
  // ------------------------------------------------------------------
  private Map<String, Double> extractReports(Simulation sim) {
    Map<String, Double> out = new LinkedHashMap<String, Double>();
    ReportManager rm = sim.getReportManager();
    if (REPORT_MAP.length > 0) {
      for (String[] pair : REPORT_MAP) {
        Report r = rm.getReport(pair[0]);
        putChecked(out, pair[1], r.getReportMonitorValue());
      }
    } else {
      for (Report r : rm.getObjects()) {
        putChecked(out, toSnake(r.getPresentationName()), r.getReportMonitorValue());
      }
    }
    return out;
  }

  private static void putChecked(Map<String, Double> out, String key, double v) {
    if (Double.isNaN(v) || Double.isInfinite(v)) {
      throw new RuntimeException("Report 값이 NaN/Inf (발산 의심): " + key);
    }
    out.put(key, Double.valueOf(v));
  }

  // ------------------------------------------------------------------
  // 이미지
  // ------------------------------------------------------------------
  private void exportScenes(Simulation sim, File imagesDir) {
    try {
      if (EXPORT_ALL_SCENES) {
        for (Scene s : sim.getSceneManager().getScenes()) {
          printScene(sim, s, imagesDir);
        }
      } else {
        for (String name : SCENES) {
          printScene(sim, sim.getSceneManager().getScene(name), imagesDir);
        }
      }
    } catch (Exception e) {
      log(sim, "WARNING: scene export 실패: " + e);
    }
  }

  private void printScene(Simulation sim, Scene s, File imagesDir) {
    String path = new File(imagesDir, toSnake(s.getPresentationName()) + ".png").getAbsolutePath();
    try {
      s.printAndWait(path, 1, IMG_W, IMG_H);
      log(sim, "scene -> " + path);
    } catch (Exception e) {
      log(sim, "WARNING: scene '" + s.getPresentationName() + "' 실패: " + e);
    }
  }

  private void exportPlots(Simulation sim, File imagesDir) {
    try {
      for (StarPlot p : sim.getPlotManager().getPlots()) {
        String path = new File(imagesDir, "plot_" + toSnake(p.getPresentationName()) + ".png")
            .getAbsolutePath();
        try {
          // TODO(사내검증): encode(String path, String format, int w, int h) 시그니처
          p.encode(path, "png", IMG_W, IMG_H);
          log(sim, "plot -> " + path);
        } catch (Exception e) {
          log(sim, "WARNING: plot '" + p.getPresentationName() + "' 실패: " + e);
        }
      }
    } catch (Exception e) {
      log(sim, "WARNING: plot export 실패: " + e);
    }
  }

  // ------------------------------------------------------------------
  // 유틸: 경로, 로그, 파일, JSON
  // ------------------------------------------------------------------
  private File findCaseDir(Simulation sim) {
    File cwd = new File(System.getProperty("user.dir"));
    if (new File(cwd, "params.json").isFile()) {
      return cwd;
    }
    File sessionDir = new File(sim.getSessionDir());
    if (new File(sessionDir, "params.json").isFile()) {
      return sessionDir;
    }
    return cwd;
  }

  private static void log(Simulation sim, String msg) {
    sim.println("[RunCase] " + msg);
  }

  private static String readText(File f) throws java.io.IOException {
    return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
  }

  private static void writeText(File f, String text) throws java.io.IOException {
    Writer w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8);
    try {
      w.write(text);
    } finally {
      w.close();
    }
  }

  /** 평탄 JSON {"k": 1.5, "s": "text", "b": true} -> Map<String,String> (문자열은 따옴표 제거). */
  static Map<String, String> parseFlatJson(String text) {
    Map<String, String> map = new LinkedHashMap<String, String>();
    Pattern p = Pattern.compile(
        "\"((?:[^\"\\\\]|\\\\.)*)\"\\s*:\\s*"
        + "(\"(?:[^\"\\\\]|\\\\.)*\"|-?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?|true|false|null)");
    Matcher m = p.matcher(text);
    while (m.find()) {
      String key = unescape(m.group(1));
      String val = m.group(2);
      if (val.startsWith("\"")) {
        val = unescape(val.substring(1, val.length() - 1));
      }
      map.put(key, val);
    }
    return map;
  }

  private static String unescape(String s) {
    return s.replace("\\\"", "\"").replace("\\\\", "\\").replace("\\/", "/");
  }

  private static Double toDouble(String s) {
    if (s == null) {
      return null;
    }
    if (s.equals("true")) {
      return Double.valueOf(1.0);
    }
    if (s.equals("false")) {
      return Double.valueOf(0.0);
    }
    try {
      return Double.valueOf(Double.parseDouble(s.trim()));
    } catch (NumberFormatException e) {
      return null;
    }
  }

  static String toSnake(String name) {
    String s = name.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", "_");
    s = s.replaceAll("^_+", "").replaceAll("_+$", "");
    return s.isEmpty() ? "value" : s;
  }

  private static void writeResults(File f, Map<String, Double> results) throws java.io.IOException {
    StringBuilder sb = new StringBuilder("{\n");
    int i = 0;
    for (Map.Entry<String, Double> e : results.entrySet()) {
      sb.append("  \"").append(e.getKey()).append("\": ")
        .append(Double.toString(e.getValue().doubleValue()));
      sb.append(++i < results.size() ? ",\n" : "\n");
    }
    sb.append("}\n");
    File tmp = new File(f.getParentFile(), f.getName() + ".tmp");
    writeText(tmp, sb.toString());
    if (f.exists()) {
      f.delete();
    }
    if (!tmp.renameTo(f)) {
      writeText(f, sb.toString());
      tmp.delete();
    }
  }
}
