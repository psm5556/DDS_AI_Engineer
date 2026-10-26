---
name: starccm-automation
description: Simcenter STAR-CCM+ 배치 자동화(Java 매크로). Global Parameter와 3D-CAD 설계변수 변경, 메쉬 재생성, 경계조건·물성·정지조건 변경, 해석 실행, Report 값을 results.json으로 추출, Scene/Plot 이미지 저장, sim 저장을 다룬다. 사용자가 STAR-CCM+(.sim) 모델 자동화, CFD 파라메트릭/DOE/최적화, STAR-CCM+ 결과 추출이나 이미지 캡처를 요청할 때 사용한다.
---

# STAR-CCM+ 자동화 (Java 매크로)

플랫폼 공통 규약은 `docs/skill-contract.md`를 따른다. 입력은 케이스 폴더의 `params.json`, 출력은 `results.json`과 `images/*.png`이다.

## 1. 언제 쓰나 / 전제
- `.sim` 파일 하나를 기준 모델로 두고 형상·조건을 바꿔 가며 반복 해석할 때(PIDO, DOE, 최적화) 쓴다.
- STAR-CCM+ 자동화는 **Java 매크로**(`StarMacro`를 상속한 `.java` 파일)로 한다. 매크로는 STAR-CCM+가 실행 시 직접 컴파일하므로 별도의 javac나 외부 라이브러리가 필요 없다.
  - 클래스 이름은 **파일 이름과 같아야 한다**. 예를 들어 `RunCase.java`의 클래스는 `public class RunCase extends StarMacro`이다.
  - 외부 jar(Gson 등)는 쓰지 않는다. JSON은 `scripts/RunCase.java`에 들어 있는 정규식 기반 평탄 JSON 파서로 읽는다.
  - 버전마다 번들 JDK 버전이 다르다(대략 Java 8에서 Java 11/17 사이). `var`, 텍스트 블록, record를 쓰지 말고 **Java 8 문법**으로 작성한다.
- 매크로는 컴파일 단위가 하나다. **존재하지 않는 클래스나 메서드가 하나라도 있으면 매크로 전체가 컴파일 오류로 실행되지 않는다.** 그래서 확인되지 않은 API는 템플릿에서 주석으로만 남긴다.
- API를 가장 확실하게 확인하는 방법은 **매크로 녹화**다. GUI에서 `Tools → Macro → Start Recording`으로 녹화를 시작하고, 원하는 조작을 한 번 한 뒤 `Stop Recording`을 누른다. 생성된 `.java`에서 정확한 클래스와 메서드 이름을 복사한다. 이 문서의 `TODO(사내검증)` 항목은 모두 이 방법으로 확인한다.
- 라이선스, 명령행 옵션, 병렬 실행은 [references/batch-cli.md](references/batch-cli.md)에 정리했다.

## 2. 실행 방법
- tool 키 `starccm`. 명령 템플릿은 `{starccm} -batch {script} {args}`이다.
- `args`에는 STAR-CCM+ 옵션과 sim 파일을 넣는다. 예: `["-np", "8", "model.sim"]`, `["-power", "-podkey", "<KEY>", "-licpath", "1999@flex.cd-adapco.com", "-np", "16", "model.sim"]`
- 스터디 어댑터 예시는 다음과 같다.
  ```json
  {"tool": "starccm", "script": "scripts/RunCase.java", "args": ["-np", "8", "model.sim"],
   "inputs": ["models/model.sim"], "outputs": ["pressure_drop", "outlet_temp"]}
  ```
- 매크로 없이 sim에 저장된 설정 그대로 돌리려면 `starccm+ -batch run model.sim`을 쓴다. 메쉬 후 해석은 `-batch mesh,run`이다. 이 방식은 파라미터를 바꿀 수 없어 결과 추출용 매크로를 따로 실행해야 하므로, 자동화에는 매크로 방식을 권장한다.
- 매크로는 명령행 인자를 받지 못한다. 그래서 입력은 항상 **케이스 폴더의 `params.json`** 에서 읽는다. 템플릿은 `System.getProperty("user.dir")`에 `params.json`이 있으면 그 폴더를 쓰고, 없으면 `sim.getSessionDir()`(sim 파일이 있는 폴더)을 쓴다.

### 템플릿
| 파일 | 용도 |
|---|---|
| `scripts/RunCase.java` | 표준 케이스 실행. params.json을 Global Parameter에 반영하고, 재메쉬, 해석, Report 값을 results.json으로 쓰고, Scene과 Plot을 PNG로 저장한 뒤 sim을 저장한다 |
| `scripts/ListObjects.java` | 처음 보는 sim의 구조 조사. Global Parameter, Report, Scene, Plot, Region/Boundary, Continuum, 정지조건 이름을 `inventory.txt`에 쓴다. 자동화 스크립트를 쓰기 전에 `run_script`로 먼저 실행한다 |

## 3. 파라미터(형상) 변경
**권장 설계 방식.** 사내 모델을 준비할 때 설계변수를 모두 **Global Parameter**(`Tools → Parameters`)로 만들어 둔다. 3D-CAD 치수, 경계조건 값, 물성 같은 곳에는 `${thickness}` 같은 표현식으로 그 파라미터를 참조시킨다. 이렇게 하면 매크로는 Global Parameter 값만 바꾸면 되므로 가장 안정적이다.

```java
ScalarGlobalParameter p = (ScalarGlobalParameter)
    sim.get(GlobalParameterManager.class).getObject("thickness");
p.getQuantity().setValue(2.5);              // 파라미터에 지정된 단위 기준
// p.getQuantity().setDefinition("${a}*2"); // 표현식으로 지정할 때
```
- `params.json`의 키와 Global Parameter 이름이 같으면 `RunCase.java`가 자동으로 대입한다. 이름이 다르면 `PARAM_NAME_MAP`에 매핑을 적는다.
- 단위는 Global Parameter를 만들 때 지정한 단위(Dimensions/Units)를 따른다. mm로 만든 파라미터에는 mm 값을 넣는다.
- 3D-CAD 설계 파라미터를 직접 바꾸는 방법과 외부 CAD 파트를 재임포트하는 방법은 [references/java-api.md §2](references/java-api.md)에 있다(TODO(사내검증)). 형상이 바뀌면 반드시 **재메쉬**해야 한다.

## 4. 해석조건 변경
상세 코드는 [references/java-api.md](references/java-api.md)에 있다.
- **메쉬 재생성**: `sim.get(MeshPipelineController.class).generateVolumeMesh();`는 표면 메쉬와 볼륨 메쉬를 파이프라인 전체로 다시 만든다. 표면 메쉬만 만들려면 `generateSurfaceMesh()`, 기존 메쉬를 지우려면 `clearGeneratedMeshes()`를 쓴다.
- **경계조건(입구 속도, 온도)**: Boundary의 `getValues().get(VelocityMagnitudeProfile.class)` 등으로 프로파일을 얻은 뒤 `getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(...)`를 호출한다. 다만 Global Parameter를 참조시키는 방식을 우선 권장한다.
- **물성(밀도, 점도 등)**: `ConstantMaterialPropertyMethod`로 값을 바꾼다. 모델 종류마다 경로가 달라 TODO(사내검증)이며, 녹화로 확인한다.
- **정지조건, 반복 수**:
  ```java
  StepStoppingCriterion c = (StepStoppingCriterion)
      sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
  c.setMaximumNumberSteps(1000);
  // TODO(사내검증): 신버전 녹화 형식은 c.getMaximumNumberStepsObject().getQuantity().setValue(1000.0);
  ```
  `params.json`에 `max_steps`가 있으면 템플릿이 이 값을 적용한다.
- **비정상(Unsteady) 해석**: 시간 간격과 최대 물리시간을 바꾸는 코드는 references에 있다(TODO(사내검증)).

## 5. 해석 실행과 완료/실패 판정
```java
sim.getSolution().clearSolution();      // 이전 해 삭제(재초기화)
sim.getSolution().initializeSolution();
sim.getSimulationIterator().run();      // 정지조건까지 실행
// sim.getSimulationIterator().step(100); // 정확히 N 스텝
```
- 예외가 나면 템플릿은 `error.txt`를 쓰고 예외를 다시 던진다. 이때 `results.json`은 생기지 않으므로 플랫폼이 케이스를 failed로 처리한다.
- 매크로 예외가 발생해도 STAR-CCM+ 프로세스의 종료코드가 항상 0이 아니라고 보장할 수는 없다(TODO(사내검증)). 그래서 **성공 판정은 `results.json` 존재 여부로 한다**. 템플릿은 모든 단계가 끝난 뒤 마지막에만 `results.json`을 쓴다.
- 발산하면 로그에 `Floating point exception`이나 residual `NaN`이 찍힌다. Report 값이 NaN이나 Infinity이면 템플릿이 실패로 처리한다.

## 6. 결과 추출 → results.json
- **Report**가 결과 추출의 기본 단위다. 질량유량, 압력강하, 면적평균 온도, 힘·모멘트 같은 값은 모델 준비 단계에서 Report로 만들어 둔다.
  ```java
  Report r = sim.getReportManager().getReport("Pressure Drop");
  double v = r.getReportMonitorValue();
  ```
- `REPORT_MAP`에 `{"Report 이름", "results_키"}`를 적으면 그 Report만 추출한다. 비워 두면 **모든 Report**를 추출하고, 이름을 소문자_스네이크 키로 바꾼다(예: `Pressure Drop` → `pressure_drop`).
- 출력 키는 스터디 어댑터의 `outputs`와 정확히 일치해야 한다.
- 수렴 이력(반복별 값)이 필요하면 Monitor Plot을 CSV로 내보낸다(references §6, TODO(사내검증)).

## 7. 이미지 저장
```java
Scene s = sim.getSceneManager().getScene("Scalar Scene 1");
s.printAndWait(new File(imagesDir, "scalar_scene_1.png").getAbsolutePath(), 1, 1600, 900);
```
- 인자는 (경로, 배율, 가로, 세로)이다. 뷰 방향은 sim에 저장된 카메라(View)를 쓰므로, 모델 준비 단계에서 Scene의 뷰를 고정해 두는 것이 가장 안정적이다.
- Plot은 `plot.encode(path, "png", w, h)`로 저장한다(TODO(사내검증): 버전별 시그니처 확인).
- 이미지 저장에 실패해도 케이스는 실패로 처리하지 않는다. 템플릿은 로그만 남긴다.

## 8. 자주 하는 실수와 디버깅
- **컴파일 오류**: 로그에 `error: cannot find symbol`이 보이면 클래스 이름, 메서드 이름, import를 확인한다. 녹화한 매크로의 import 블록을 그대로 복사하는 것이 가장 빠르다.
- **클래스 이름 불일치**: 파일 이름을 바꿨으면 클래스 이름도 바꾼다.
- **객체 이름**: `getObject("이름")`은 GUI 트리에 보이는 **표시 이름(Presentation Name)** 을 쓴다. 대소문자와 공백까지 정확해야 하며, 이름이 없으면 예외가 난다. 모르면 `ListObjects.java`로 먼저 조사한다.
- **형상을 바꿨는데 결과가 같음**: 재메쉬를 안 했거나, CAD 파트가 업데이트되지 않았거나, 이전 해에서 이어서 계산했을 수 있다. `REMESH`와 `CLEAR_SOLUTION`을 켠다.
- **이미지가 검은색이거나 비어 있음**: 배치 모드의 오프스크린 렌더링 문제일 수 있다. 버전에 맞는 그래픽 옵션을 확인한다(TODO(사내검증)).
- **라이선스 대기, 실패**: 로그에서 `license` 문자열을 찾는다. Power-on-demand이면 `-power -podkey -licpath`를 확인한다.
- 로그는 플랫폼이 `run.log`에 기록한다. 매크로 안에서 `sim.println("...")`으로 남긴 메시지도 여기에 나온다.

## 9. 사내 검증 필요 항목 (TODO(사내검증))
1. `StepStoppingCriterion.setMaximumNumberSteps(int)`가 현재 버전에서 동작하는지, 아니면 `getMaximumNumberStepsObject()` 방식만 되는지 확인한다.
2. `StarPlot.encode(String, String, int, int)` 시그니처, 그리고 Monitor Plot CSV 내보내기 메서드.
3. 3D-CAD 설계 파라미터를 직접 변경하는 API(`CadModel`, `ScalarQuantityDesignParameter`, CAD 업데이트 호출)와 CAD 파트 재임포트 API.
4. 물성 변경 경로(모델 종류별 `getMaterial()` 접근)와 비정상 해석의 시간 간격 API.
5. 매크로 예외 발생 시 프로세스 종료코드, 그리고 배치 모드 Scene 렌더링 옵션.
6. 사내 라이선스 서버 주소와 POD 키 관리 방식.
