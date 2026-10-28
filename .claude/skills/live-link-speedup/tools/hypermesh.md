# HyperMesh (Altair)

## 1. 지금 방식과 병목 (집 저장소 기준)
- 연결: 툴 안 플러그인 `agent-context/live/hypermesh/dds_live_hm.tcl`
  - 127.0.0.1:47802 소켓을 쓴다.
  - Tcl 이벤트 루프(`fileevent`)에서 바로 실행한다. 전달 방식은 이미 빠르다.
- 병목
  - AI가 Tcl `*`명령을 매번 새로 쓴다.
    - mark·attribute ID·인자 수가 버전과 템플릿마다 달라 틀리기 쉽다.
  - `info`가 개수만 준다(`hm_count`). 컴포넌트·프로퍼티 이름과 값을 찾는 호출이 더 든다.
  - exec가 끝날 때마다 `*redraw`를 한다. 큰 모델에서는 비용이 든다.
  - 캡처 명령(`hm_screencapture`)이 확인되지 않아 창 캡처로 대신하는 일이 많다.

## 2. 빠른 툴 대응 (Tcl 도우미 `dds_live_hm_ops.tcl`을 플러그인과 함께 source)
| 도우미(proc) | HyperMesh 명령 | 상태 |
|---|---|---|
| `::ddsops::summary` | 컴포넌트·프로퍼티(두께)·재료(E·밀도) 이름·ID·값 목록, 요소·노드 수 | 이름·ID 조회는 `hm_getvalue`/`hm_entitylist` 확인 필요 |
| `::ddsops::set_params` 쉘 두께 | `*setvalue props name=<p> STATUS=1 95=<t>` (OptiStruct PSHELL T) | 확인 필요(템플릿마다 ID 다름) |
| `::ddsops::set_params` 재료 | `*setvalue mats name=<m> STATUS=1 1=<E>` (MAT1 E) | 확인 필요 |
| `::ddsops::undo` | 도우미가 기억한 **이전 값을 다시 쓴다**(가장 확실). HyperMesh Undo 명령은 버전별 확인 | — |
| `::ddsops::view` 방향·맞추기 | 툴바 보기 버튼과 맞추기를 command.tcl로 기록해 쓴다 | 확인 필요 |
| `::ddsops::view` 숨기기 | `*createmark comps 1 <이름…>` 후 표시/숨김 명령(command.tcl로 확인) | 확인 필요 |
| `::ddsops::capture` | 화면 저장 명령(버전별) → 안 되면 MCP 창 캡처 | 확인 필요 |

- Tcl에는 JSON 라이브러리가 없다. 도우미 결과는 플러그인의 `::ddslive::esc`로 직접 JSON 문자열을 만든다.
  - 숫자는 따옴표 없이, 이름은 따옴표로 감싼다.
- HyperMesh 2022 이상을 쓰고 사내가 Python API(`import hm`)를 표준으로 정했다면, 도우미를 Python으로 써도 된다. 빠른 툴 약속은 같다.

## 3. values 이름 규칙
| 이름 예 | 뜻 |
|---|---|
| `prop:shell_2mm/thickness` | 프로퍼티 두께 |
| `mat:steel/E`, `mat:steel/density` | 재료 값 |
| `comp:<이름>` | `live_view`의 show·hide·only에 쓰는 컴포넌트 이름 |

## 4. 연결·실행 개선
- exec 뒤 `*redraw`는 화면이 바뀌는 작업에만 한다. 도우미가 필요할 때 직접 부른다.
- 큰 모델에서 여러 값을 바꿀 때는 화면 갱신을 잠시 끈다(사용 가능한 명령은 command.tcl로 확인). 끝나면 한 번만 다시 그린다.
- 도우미는 플러그인을 시작할 때 한 번 source한다. 이후 exec 코드는 `::ddsops::set_params {...}` 한 줄이다.
- `ms`는 Tcl `clock milliseconds`로 잰다.
- 메쉬 재생성(자동 메쉬·모핑)은 느리므로 요청할 때만 한다.

## 5. API 확인 (기록 기능)
- GUI 작업은 모두 `command.tcl`(작업 폴더나 Documents)에 Tcl로 기록된다. 같은 동작을 한 번 하고 그 줄을 복사한다.
- attribute ID(95, 1 등)는 솔버 템플릿마다 다르다. 반드시 command.tcl에서 확인한다.
- 자세한 내용은 `agent-context/skills/altair-automation/references/hypermesh-tcl.md`에 있다.

## 6. 주의
- 덮어쓰기 같은 확인창이 뜨면 이벤트 루프가 멈춘다. 파일을 쓰는 명령 앞에는 `hm_answernext yes`를 넣는다.
- Tcl 8.6 미만이면 `binary decode base64`가 없다. 플러그인이 tcllib으로 대신한다.
- `*createmark`의 mark 번호(1·2)는 다른 작업과 겹치지 않게 도우미 안에서만 쓴다. 쓰고 나면 `*clearmark`로 비운다.

## 7. 확인 요청 (대화창에 그대로)
| 요청 | 툴 | 확인할 것 |
|---|---|---|
| "컴포넌트와 프로퍼티 두께 목록을 보여 줘" | `live_info` | 이름·값 |
| "shell_2mm 두께를 2.5로, steel의 E를 205000으로" | `live_set_params` | 카드 값 |
| "방금 것 되돌려 줘" | `live_undo` | 이전 값 |
| "정면에서 보고 BRACKET 컴포넌트만 보여 줘" | `live_view` | 방향·표시 |
| "지금 화면 올려 줘" | `live_capture` | 화면 저장 명령이 되는지, 안 되면 창 캡처 |

## 8. 지침에 넣을 예
- `live_set_params(tool="hypermesh", values={"prop:shell_2mm/thickness": 2.5})`
- `live_view(tool="hypermesh", orient="front", only=["comp:BRACKET"])`
