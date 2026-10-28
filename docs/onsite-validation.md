# 사내(실제 CAD/CAE 툴) 첫 주 검증 체크리스트

집 PC에서는 Mock 솔버·연습용 CAD·dry_run으로만 검증했다. 실제 툴 API는 버전마다 다르므로, 사내 PC에서 아래 순서로 **툴마다 한 번씩** 확인하고 고친다.
스킬과 플러그인의 `TODO(사내검증)` 표시가 확인할 지점이다.

## 0. 공통 준비
1. 웹앱 **CAD·CAE 툴** 화면에서 툴이 "바로 사용"으로 나오는지 본다. PC Agent가 설치할 때와 실행 중에 자동으로 찾는다.
   - 못 찾았으면 "다시 찾기"를 누른다. 그래도 없으면 툴을 눌러 "찾아보기…"로 실행 파일을 고른다(사용자 PC에 파일 선택 창이 뜬다).
   - 못 찾은 설치 위치·회사 이름은 `cae_adapters/catalog.py`의 `guess`·`FIND`에 넣어 다음 버전부터 자동으로 찾게 한다.
2. 웹앱에서 프로젝트를 만들고 테스트 모델 파일을 대화에 **첨부**한다. 첨부한 파일이 `inputs/`에 오는지 확인한다.
3. 툴별로 아래 표의 요청을 대화창에 그대로 보낸다. 실패하면 대화에 나온 오류와 케이스 폴더의 `run.log`를 확인한다.
   - 에이전트에게 "스킬의 해당 부분을 이 오류에 맞게 고쳐 줘"라고 요청한다.
   - 고친 스크립트가 동작하면 `agent-context/skills/<툴>/scripts/`에 반영한다. 이후 다음 Agent 배포에 포함된다.

## 1. 툴별 확인 요청 (대화창에 그대로)
| 툴 | 1단계: 단발 실행 | 2단계: 스터디 반복 | 3단계: Live Link |
|---|---|---|---|
| NX | "첨부한 prt의 expression 목록을 보여 줘" | "두께 expression을 2~6으로 바꿔가며 질량을 DOE 6점으로 구해 줘" | NX에서 `dds_live_nx.py`를 리본에 등록 → "열린 파트의 두께를 4로 바꿔 줘" |
| Simcenter 3D | "첨부한 sim을 열어 해석하고 최대 변위를 알려 줘" | "하중을 3수준으로 바꿔 해석해 줘" | (NX와 같음) |
| Abaqus | "첨부한 cae 모델 구조(파트·스텝·하중·세트)를 알려 줘" | "하중을 바꿔가며 최대 Mises DOE 8점 → 예측 모델" | ddsLive 플러그인 설치 → "열린 모델에 재료 AL6061을 만들어 할당해 줘" |
| LS-DYNA | "첨부한 k 파일의 *PARAMETER를 보여 줘" | "충돌 속도 3수준으로 해석하고 내부에너지 비교" | — |
| Ansys | "Workbench 프로젝트 파라미터 목록을 보여 줘" | "두께 파라미터 DOE 6점" | Mechanical gRPC 연결(스킬 참고) |
| Altair | "fem 파일의 PSHELL 두께를 바꿔 OptiStruct로 해석해 줘" | "두께 DOE 8점" | HyperMesh에서 `source dds_live_hm.tcl` → "컴포넌트 목록을 보여 줘" |
| STAR-CCM+ | "sim의 Global Parameter와 Report 목록을 보여 줘" | "입구 속도 3수준" | 서버 공유 모드(스킬 참고) |
| Amesim | "모델 파라미터 목록을 보여 줘(probe_api)" | "스프링 강성 3수준으로 최대 변위" | — |

### 1-1. NX Live Link 빠른 툴 (설계 수정·화면 조작)
에이전트가 코드를 쓰지 않고 세션 안 도우미(`agent-context/live/nx/ddslive_nx_ops.py`)를 부르는 툴이다. 열린 NX에서 아래를 차례로 요청한다.
| 요청 | 쓰이는 툴 | 확인할 것 |
|---|---|---|
| "열린 파트에서 바꿀 수 있는 치수를 보여 줘" | `live_info` | expression·피처·바디 이름이 맞게 나오는지 |
| "두께를 4, 리브 높이를 12로 바꿔 줘" | `live_set_params` | 업데이트 1번, Edit > Undo 목록에 "DDS: …" 마크 1개 |
| "정면에서 모서리까지 보이게 하고 커버는 숨겨 줘" | `live_view` | 방향·표시 방식·숨기기 |
| "방금 것 되돌려 줘" | `live_undo` | 바꾸기 전 값으로 돌아오는지 |
| "두께를 0으로 바꿔 줘"(형상이 깨지는 값) | `live_set_params` | 자동으로 되돌리고 오류를 알려 주는지 |
| (Simcenter 3D sim을 연 상태) "두께를 4로 바꾸고 메쉬까지 업데이트해 줘" | `live_set_params(fem=true)` | Idealized·FEM 업데이트 후 원래 화면으로 돌아오는지 |
| "지금 화면을 결과에 올려 줘" | `live_capture` | 뷰포트만 찍히는지, NX 창이 앞으로 튀어나오지 않는지 |

- 결과의 `ms`(update·fem·capture·wait·round_trip)를 적어 둔다. 어디서 느린지 보는 기준이다.
- 도우미의 `TODO(사내검증)` 항목은 다음과 같다. 안 되면 해당 동작을 Journal로 기록해 맞춘다.
  - RenderingStyleType 이름
  - DisplayManager.BlankObjects·UnblankObjects
  - UndoToMark
  - 대화형 세션의 ImageExportBuilder
  - CAE 파트의 WorkView
  - UpdateFemodel
- 사내에서 바꾼 NX 실행 방식이 있으면 빠른 툴도 그 방식으로 돈다(모두 `live_exec(tool="nx")`를 거친다). 대기열 파일은 다 쓴 뒤 `job_*.py`로 이름을 바꾸므로, 대기열을 자동으로 읽어도 반쯤 쓴 코드를 실행하지 않는다.

## 2. 3D 형상·필드 학습
- 케이스 스크립트가 `fields/<이름>.npz`(points, values, triangles)를 남기게 한다(`docs/skill-contract.md` 1-1절).
  - Abaqus: odb에서 표면 노드 좌표와 `S` Mises를 노드 평균으로 추출한다.
  - Nastran: pyNastran으로 OP2를 읽는다.
- 요청 예: "케이스마다 응력 필드를 fields/stress.npz로 남기고, 형상·필드 모델을 학습해서 Simulator에서 3D로 보여 줘"
- 노드 수가 케이스마다 다르면(리메쉬) 서버가 가장 촘촘한 케이스의 메쉬로 자동 보간한다. 위상이 바뀌는 큰 형상 변화에는 맞지 않는다.
- 툴별 추출 스크립트: Abaqus `abq_odb_fields.py`, Nastran·OptiStruct `op2_fields.py`, LS-DYNA `dyna_fields.py`, Ansys는 스킬의 PyDPF 예시. 스터디 `adapter_config.post`(여러 단계 가능)에 붙인다.

## 3. 자체 CAE 뷰어 연결
`agent.toml`에 확장자별 명령을 넣으면 웹앱의 [뷰어로 열기]가 그 뷰어를 실행한다.
```toml
[viewers]
".odb" = ['C:\DDS\Viewer\ddsview.exe', '--open', '{file}']
".d3plot" = ['C:\DDS\Viewer\ddsview.exe', '--open', '{file}']
"*" = ['C:\DDS\Viewer\ddsview.exe', '{file}']   # 그 밖의 결과 파일
```
사내 뷰어가 웹 방식이면 아래 5절의 `web_viewer_url`을 쓴다.

## 4. HPC (구현됨 — 사내 설정·검증 필요)
`agent/dds_agent/job_runner.py`의 `HpcRunner`가 Slurm·PBS·LSF 프리셋으로 제출 → 상태 폴링 → 종료코드 판정을 한다(가짜 스케줄러로 테스트 통과).
케이스 폴더(`workspace_dir`)는 **클러스터와 공유되는 스토리지**에 두어야 한다.
```toml
[hpc]
scheduler = "slurm"                 # slurm | pbs | lsf
ssh = "me@login01"                  # 클러스터 명령을 로그인 노드에서 실행 (비우면 이 PC에서 바로)
path_map = { 'Z:/' = '/shared/' }   # Windows 공유 드라이브 → 클러스터 경로
exe_map = { 'abaqus.bat' = 'abaqus', 'lsdyna_smp.exe' = '/opt/lsdyna/ls-dyna_mpp' }   # 실행 파일 → 클러스터 명령
ncpu = 16
header = "#SBATCH -p cae\n#SBATCH -t 04:00:00"
poll_sec = 30
```
- 스터디마다 `adapter_config`에 `"runner": "hpc"`를 넣으면 그 스터디의 케이스가 HPC로 제출된다. 전체를 HPC로 보내려면 `runner = "hpc"`로 둔다.
- 확인 순서: `ssh me@login01 squeue`가 비밀번호 없이 되는지 확인(SSH 키) → 케이스 1건 제출 → 케이스 폴더의 `hpc_job.sh`, `hpc.log`, `.dds_exitcode` 확인
- 사내 스케줄러의 출력 형식이 다르면 `submit`·`status`·`id_regex`를 `[hpc]`에 직접 지정한다.

## 5. 사내 웹 CAE 뷰어
서버 `server.toml`에 URL 템플릿을 넣으면, 캔버스 '내 PC 파일'의 결과 파일 옆에 **웹 뷰어** 버튼이 생긴다.
```toml
web_viewer_url = "http://cae-viewer.corp/open?host={host}&file={path}"   # {path} 그 PC의 절대경로, {rel} 프로젝트 기준 경로, {host} PC 이름, {project_id}
```
