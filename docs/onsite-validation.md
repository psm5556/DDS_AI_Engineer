# 사내(실제 CAD/CAE 툴) 첫 주 검증 체크리스트

집 PC에서는 Mock 솔버·연습용 CAD·dry_run으로만 검증했다. 실제 툴 API는 버전마다 다르므로, 사내 PC에서 아래 순서로 **툴마다 한 번씩** 확인하고 고친다.
스킬과 플러그인의 `TODO(사내검증)` 표시가 확인할 지점이다.

## 0. 공통 준비
1. 웹앱 > 설정 > **내 PC 진단**에서 툴이 "찾음"으로 나오는지 본다. 없으면 `agent.toml`의 `[tools]`에 경로를 넣는다.
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

## 2. 3D 형상·필드 학습
- 케이스 스크립트가 `fields/<이름>.npz`(points, values, triangles)를 남기게 한다(`docs/skill-contract.md` 1-1절).
  - Abaqus: odb에서 표면 노드 좌표와 `S` Mises를 노드 평균으로 추출한다.
  - Nastran: pyNastran으로 OP2를 읽는다.
- 요청 예: "케이스마다 응력 필드를 fields/stress.npz로 남기고, 형상·필드 모델을 학습해서 Simulator에서 3D로 보여 줘"
- 노드 수가 케이스마다 다르면(리메쉬) 기준 메쉬에 보간해야 한다. 이 경우 에이전트에게 "기준 메쉬에 보간해서 저장해 줘"라고 요청한다.

## 3. 자체 CAE 뷰어 연결
`agent.toml`에 확장자별 명령을 넣으면 웹앱의 [뷰어로 열기]가 그 뷰어를 실행한다.
```toml
[viewers]
".odb" = ['C:\DDS\Viewer\ddsview.exe', '--open', '{file}']
".d3plot" = ['C:\DDS\Viewer\ddsview.exe', '--open', '{file}']
"*" = ['C:\DDS\Viewer\ddsview.exe', '{file}']   # 그 밖의 결과 파일
```
뷰어가 웹으로 열려야 하면(사내 웹 뷰어) 뷰어 쪽 URL 규칙을 알려 주면 된다. 그러면 캔버스에 '웹 뷰어' 탭을 붙일 수 있다.

## 4. HPC
- 사내 스케줄러(LSF/PBS/Slurm)와 제출 방식(ssh, 공유 스토리지)이 정해지면 `agent/dds_agent/job_runner.py`의 `HpcRunner`를 구현한다.
  - 구성: submit → status → fetch
- 스터디의 `adapter_config`에 `"runner": "hpc"`를 넣어 쓰게 한다.
