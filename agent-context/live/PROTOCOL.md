# DDS Live Link 프로토콜

에이전트가 사용자가 열어 둔 CAD/CAE **GUI 세션**에 붙어서 사람과 같은 화면을 보며 모델링한다.
툴 안에서 도는 작은 플러그인이 `127.0.0.1:<포트>`에서 요청을 받는다. 요청받은 코드는 **GUI(메인) 스레드에서** 실행한다. 그래야 사용자 화면이 바로 바뀌고, 툴 API의 스레드 제약도 지킬 수 있다.

## 메시지 (줄 단위 JSON, UTF-8)
요청 → `{"id": 1, "op": "ping" | "exec" | "screenshot" | "info", ...}`

| op | 추가 필드 | 응답 |
|---|---|---|
| `ping` | — | `{"id", "ok": true, "tool": "abaqus", "version": "2024", "pid": 1234}` |
| `info` | — | `{"id", "ok": true, "model": "...", "summary": {...}}` 현재 모델 요약(파트·스텝·세트 등) |
| `exec` | `code`: 툴 언어 코드(Python/Tcl/Java…) | `{"id", "ok", "stdout": "...", "result": "...", "error": "..."}` |
| `screenshot` | `path`: 저장할 PNG 경로 | `{"id", "ok", "path": "..."}` 뷰포트 이미지 |

- `exec` 코드가 `result` 변수에 값을 넣으면 문자열로 바꿔 돌려준다(Python 계열).
- 오류는 `ok: false`, `error`에 메시지와 traceback 일부를 담는다.
- 한 번에 하나씩 처리한다. 긴 작업(해석 제출 등)은 GUI를 멈추게 하므로 Live Link로 하지 말고 배치(`dds-cae.run_script` / 스터디)로 한다.

## 기본 포트
| 툴 | 포트 | 플러그인 | 실행 방식 |
|---|---|---|---|
| practice (연습용) | 47800 | `practice_tool.py` | `python practice_tool.py` |
| Abaqus/CAE | 47801 | `abaqus/dds_live_abaqus.py` | `abaqus cae startup=dds_live_abaqus.py` |
| HyperMesh | 47802 | `hypermesh/dds_live_hm.tcl` | HyperMesh 실행 후 `source dds_live_hm.tcl` (또는 hmcustom.tcl에 추가) |
| NX / Simcenter 3D | 47803 | `nx/dds_live_nx.py` | NX에서 Journal 재생(리스너) — TODO(사내검증) |
| STAR-CCM+ | 47827 | (플러그인 없음) | `starccm+ -server -port 47827 model.sim` 후 GUI가 같은 서버에 접속, 에이전트는 `-batch macro.java -host localhost -port 47827`로 매크로 실행 — TODO(사내검증) |
| Ansys Mechanical | 10000 | (PyMechanical) | GUI를 gRPC로 띄우고(`ansys-mechanical -g --port 10000`) PyMechanical `connect_to_mechanical(port=10000)` → `run_python_script` — TODO(사내검증) |

## 보안
- 플러그인은 **127.0.0.1에만** 바인드한다(외부 접속 불가).
- 첫 연결 시 `token`을 확인한다. 플러그인을 시작할 때 `DDS_LIVE_TOKEN` 환경변수(없으면 `%USERPROFILE%\.dds-agent\live.token` 파일)를 읽는다. 요청 JSON에 `"token"`이 맞아야 처리한다. 같은 PC의 다른 프로그램이 코드를 실행하지 못하게 하기 위해서다.
