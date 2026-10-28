# Simcenter STAR-CCM+

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: dds-live에 STAR-CCM+ 어댑터가 없다(PROTOCOL.md·live-modeling 스킬에 TODO로만 있다).
  - 사용자가 `starccm+ -server -port 47827 model.sim`으로 서버를 띄우고 GUI 클라이언트로 접속한다.
  - 에이전트는 단계마다 `starccm+ -batch macro.java -host localhost -port 47827`로 매크로를 실행한다.
- 병목
  - **단계마다 배치 클라이언트(JVM)를 새로 띄우고 서버에 붙는다.** 한 번에 수 초~수십 초가 든다.
  - AI가 Java 매크로를 매번 새로 쓴다. 매크로는 실행할 때마다 컴파일된다.

## 2. 빠른 툴 대응
### 2-1. 범용 매크로 하나 (먼저 할 것)
- AI가 Java를 쓰지 않게 한다. 요청 파일(JSON)을 읽어 처리하는 범용 매크로 `DdsLive.java`를 미리 만든다.
- 매번 같은 매크로를 실행하고, 요청 파일만 바꾼다.

| 도우미 | STAR-CCM+ Java API | 상태 |
|---|---|---|
| `summary` | Global Parameter 목록(이름·값·단위), Scene 목록, 지오메트리 파트 이름 | 확인 필요 |
| `set_params` | `ScalarGlobalParameter`의 값 설정(단위 포함) | 확인 필요 |
| ↳ 메쉬 | `MeshPipelineController`로 메쉬 생성(요청할 때만) | 확인 필요 |
| `undo` | 기억한 이전 값 다시 쓰기 | — |
| `view` | Scene의 카메라(방향·맞추기) | 확인 필요 |
| `capture` | `scene.printAndWait(path, 1, w, h)` | 확인 필요 |

### 2-2. 연결을 계속 들고 있는 클라이언트 (사내에서 시험)
- 배치 클라이언트 하나를 띄워 두고, 그 안에서 리스너 매크로가 대기열 폴더를 0.1초마다 확인하며 요청을 처리하게 한다. NX 대기열과 같은 방식이다.
- 그러면 단계마다 드는 JVM 시작·연결 비용이 사라진다.
- **사내에서 먼저 확인할 것**
  - 리스너가 도는 동안 사용자 GUI 클라이언트가 멈추지 않는지
  - 바뀐 값이 GUI에 바로 보이는지
- 안 되면 2-1만 쓴다. 그때도 값 여러 개를 한 번의 실행에 모은다.

## 3. values 이름 규칙
- Global Parameter 이름을 그대로 쓴다(예: `inlet_velocity`).
- 단위는 `summary`가 준 파라미터 단위를 따른다.
- 형상 치수가 3D-CAD 모델 파라미터라면 CAD 모델 업데이트가 필요하다. 업데이트 방법을 매크로 기록(5절)으로 확인한다.

## 4. 연결·실행 개선
- 매크로는 고정 파일 하나만 쓴다. 컴파일 결과가 재사용되는지 확인한다.
- 요청 파일과 결과 파일은 임시 이름으로 쓰고 이름을 바꾼다.
- `ms`에 `connect`(클라이언트 시작), `macro`, `mesh`, `capture`를 나눠 넣는다.
- 메쉬와 해석은 오래 걸린다. 메쉬는 요청할 때만 하고, 해석은 배치(`dds-cae`)로 한다.

## 5. API 확인 (기록 기능)
- GUI의 Tools > Macro > Start Recording으로 같은 동작을 한 번 하고, 기록된 Java에서 클래스·메서드 이름을 가져온다.

## 6. 주의
- 서버와 GUI가 같은 sim을 보고 있어야 한다. 다른 sim을 열면 바뀐 값이 보이지 않는다.
- 파라미터 값을 바꿔도 메쉬·해석 결과는 자동으로 바뀌지 않는다. 이 점을 사용자에게 알린다.
- 라이선스: 배치 클라이언트가 추가 라이선스를 쓰는지 확인한다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "Global Parameter 목록을 보여 줘" | `live_info` | 이름·값·단위 |
| "inlet_velocity를 3으로" | `live_set_params` | GUI에 반영, `ms.connect` |
| "되돌려 줘" | `live_undo` | 이전 값 |
| "Scene 1을 정면에서 맞춰 보여 주고 화면 올려 줘" | `live_view` + `capture=true` | 이미지 |

## 8. 지침에 넣을 예
- `live_set_params(tool="starccm", values={"inlet_velocity": 3.0})`
- `live_view(tool="starccm", orient="front", capture=true)`
