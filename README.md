# DDS AI Engineer

**말로 요청하면 에이전트가 CAD·CAE 툴을 다뤄 모델링 → 해석 → 데이터 축적 → 예측 모델·최적화 → 보고서 → 공유까지 해 주는 사내 웹앱.**
PIDO 툴처럼 스크립트를 직접 짜고 프로세스를 잇는 대신, 사용자는 대화로 지시한다.
에이전트(Gemini CLI · Claude Code · Codex CLI)는 작업 흐름을 그려 승인받은 뒤 사용자 PC의 CAD/CAE 툴로 실행한다.

```
┌ 사내 서버 (server/) ────────────────────────┐        ┌ 사용자 PC (PC Agent, agent/) ─────────────────────────┐
│ FastAPI + 웹앱(React) + SQLite               │  WS    │ dds-agent (트레이 아이콘, 자동 업데이트)               │
│ 계정(PIN)·프로젝트 공유·대화 이력            │◀──────▶│  ├ AI CLI: gemini --acp / claude-agent-acp / codex-acp │
│ Flow·결과·스터디·GP 학습·BO·Simulator·보고서 │ (Agent │  ├ MCP: dds-flow · dds-cae · dds-study · dds-live      │
│ Agent 배포(버전)·모니터링·설치 파일 제공     │  가 먼저│  ├ 케이스 실행(로컬/HPC) ─ NX·Abaqus·LS-DYNA·Ansys…    │
└──────────────────────────────────────────────┘  접속)  │  └ Live Link ─ 열려 있는 CAD/CAE GUI에 붙어 함께 모델링│
                                                         └────────────────────────────────────────────────────────┘
```

## 사내에서 이어서 개발하기
- **개발 지침(AI·사람 공통)**: [AGENTS.md](AGENTS.md). Claude Code·Gemini CLI·Codex는 저장소를 열면 자동으로 읽는다(`CLAUDE.md`·`GEMINI.md`가 불러옴).
- **현재 상태·다음 할 일**: [docs/handoff.md](docs/handoff.md)
- **사내 서버 준비(PC Agent 설치 파일까지)**: [docs/onsite-ai-runbook.md](docs/onsite-ai-runbook.md)
  - 사내 AI에게 "docs/onsite-ai-runbook.md대로 서버를 준비해 줘"라고 요청한다.
  - 직접 할 때: `python scripts\prepare_onsite.py --publish --server http://127.0.0.1:7014 --user <관리자> --pin <PIN>`
- **실제 CAD/CAE 툴 검증**: [docs/onsite-validation.md](docs/onsite-validation.md)

## 화면 (Claude·Codex 데스크톱 방식)
- **사이드바**: 새 작업 · CAD·CAE 툴 · 프로젝트 검색 · 프로젝트 목록(한 줄씩, 진행 중·승인 대기·실패만 표시). 아래에 PC Agent 연결 상태와 내 메뉴(설정·관리·테마·로그아웃).
- **첫 화면(새 프로젝트)**: ① 프로젝트 이름(선택) → ② 내 PC 작업 폴더(찾아보기…·경로 붙여넣기, 비우면 기본 위치) → ③ 요청. 보내면 프로젝트를 만들고 폴더를 정한 뒤 대화로 들어간다. 입력창에서 첨부(+)·AI를 고른다.
- **작업 공간**: 머리글 `프로젝트 ▾ / 대화 ▾`(대화 전환·새 대화) · 공유 · 캔버스 켜고 끄기(Ctrl+J).
  - 가운데 **대화**: 작업 흐름 제안 카드에서 승인, 실행 확인 카드에서 허용, 결과 목록.
  - 오른쪽 **캔버스**: 작업 흐름 · 결과(보고서 받기) · 데이터·모델(케이스·예측 모델·Simulator 열기) · 파일(내 PC 작업 폴더).
    에이전트가 만든 것으로 탭이 자동으로 바뀐다.
- **Simulator**: 왼쪽 설계변수·예측 결과, 오른쪽 3D 형상·필드와 민감도. 공유 링크로 사내 누구에게나 줄 수 있다.
- **CAD·CAE 툴**: 지원 29종을 분야별 앱 아이콘으로 한눈에(바로 쓸 수 있는 툴은 컬러, 없는 툴은 흐리게). 아이콘을 누르면 찾은 실행 파일·경로 설정 방법이 오른쪽에 열린다.
- **설정**: 내 PC Agent 상태·설치, AI CLI 설치, 로그인 PIN, 토큰. **관리**: PC Agent 현황, 사용자 승인, Agent 배포(설치 준비 점검표).
- **로컬 데이터 규칙**: 프로젝트·스터디·케이스별 폴더와 `cases.csv`(`docs/local-data.md`). 프로젝트마다 작업 폴더를 직접 정할 수 있다.
- **3D 형상·필드 학습**: 케이스의 `fields/*.npz`로 '설계변수 → 3D 형상 + 응력 분포'를 학습한다(PCA + GP).
- **HPC**: Slurm·PBS·LSF로 제출하고 상태를 감시한다(`agent.toml [hpc]`, 스터디별 `runner: "hpc"`). 사내 웹 CAE 뷰어는 서버 `web_viewer_url`로 연결한다.
- 디자인 토큰은 `frontend/src/design/tokens.css`이고, 최종 디자인 층은 `frontend/src/modern.css`다. 라이트/다크 모드를 지원한다.

## 폴더
| 폴더 | 내용 |
|---|---|
| `server/` | FastAPI 서버(`app/`)와 테스트(`tests/`) |
| `frontend/` | React + TypeScript + TanStack Query + React Flow + Plotly |
| `agent/` | PC Agent(`dds_agent/`: ACP 러너, 케이스 실행, 업데이트, 트레이), `launcher.py` |
| `mcp_servers/` | 에이전트가 쓰는 MCP 툴: `dds_flow`, `dds_cae`, `dds_study`, `dds_live` |
| `cae_adapters/` | 케이스 실행 어댑터(`mock`, `script`)와 툴 명령 테이블 |
| `agent-context/` | 에이전트 작업 규칙(`INSTRUCTIONS.md`), 스킬 32종, Live Link 플러그인(`live/`) |
| `installer/` | `DDS-Agent-Setup.exe` 소스(WinForms, C#) |
| `scripts/` | 개발 실행, 번들·런타임·설치 파일 빌드, E2E 테스트 |
| `docs/skill-contract.md` | 툴 스크립트 입출력 규약(`params.json` → `results.json`, `images/`) |

### 스킬 (`agent-context/skills/`)
- CAD/CAE 툴 29종: 웹앱 **CAD·CAE 툴** 화면에서 지원 목록과 내 PC 설치 상태를 본다(목록: `cae_adapters/catalog.py`).
  - CAD: NX, CATIA V5, SOLIDWORKS, Creo
  - 전처리·후처리: ANSA·META, HyperMesh·OptiStruct·HyperView
  - 구조: Simcenter 3D, Abaqus, Ansys, MSC Nastran / 충돌: LS-DYNA
  - 유동·열: STAR-CCM+, Fluent, OpenFOAM, Flotherm / 다중물리: COMSOL
  - 전처리: + Altair SimLab / 유동·열: + Cadence Celsius / 전자기: CST Studio Suite
  - 다물체: Adams, RecurDyn, Altair MotionSolve / 사출: Moldflow, Moldex3D / 1D: Amesim / 피로: nCode DesignLife
  - 광학: LightTools / 설계 최적화: Altair Inspire / AI 예측: Altair PhysicsAI
  - 툴 경로는 아무도 적지 않는다. PC Agent가 설치할 때 설치된 앱 목록·시작 메뉴·환경변수·모든 드라이브에서 찾고, 실행 중에도 설치·제거·업그레이드를 알아채 다시 찾는다. 못 찾은 툴만 웹 화면에서 실행 파일을 고른다.
- 프로세스: `workflow-planning`, `doe-surrogate-optimization`, `live-modeling`
- 툴 API 중 사내에서 확인이 필요한 부분은 각 스킬에 `TODO(사내검증)`로 표시돼 있다.

## 개발 (집 PC)
```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt
cd frontend; npm install; npm run build; cd ..
.venv\Scripts\python scripts\dev_live.py --agent demo     # 서버 + PC Agent, http://127.0.0.1:7014 (admin / PIN 123456)
scripts\dev_live.cmd claude   # 같은 실행을 별도 콘솔 창으로(더블클릭하면 demo). AI 코딩 에이전트가 띄울 때는 이 방식을 쓴다
#   --agent claude | gemini | codex : 실제 AI CLI 사용 (먼저 터미널에서 각 CLI 로그인)
#   프론트 개발 중에는: cd frontend; npm run dev  → http://localhost:5173
```
- `demo` 에이전트는 Gemini 없이 전체 흐름을 검증하는 시나리오 에이전트다.
  - 흐름: 작업 흐름 제안 → 승인 → 실행 확인 → Branin mock DOE → GP → BO → Simulator 배포
- 전체 과정 데모: 첫 화면에서 에이전트를 "데모(시나리오)"로 고르고 **처음부터 끝까지** 시작 버튼을 누른 뒤 보낸다.
  - 흐름: 브래킷 CAD(STL·expression) → 해석 모델(Abaqus .inp, 고정·하중 그림) → DOE 20점 → 질량·응력 예측 모델 + 형상·응력 필드 학습 → 3D Simulator → 결론 게시와 Word·HTML 보고서(`reports/`)
  - 실제 툴 대신 연습용 스크립트(`agent/dds_agent/demo_scripts/`)와 mock `bracket3d` 솔버를 쓴다.
- Live Link 연습: 대화에서 "연습용 CAD로 Live Link 해 봐"라고 하거나, `live_launch(tool="practice")`를 쓴다.

### 테스트
| 명령 | 확인 내용 |
|---|---|
| `.venv\Scripts\python -m pytest server/tests agent/tests` | API: 인증(PIN·잠금)·공유·Flow·DOE/학습/BO/배포·보고서·Simulator 공유·첨부·3D 형상·필드(리메쉬 포함) / HPC 실행기 |
| `.venv\Scripts\python scripts\e2e_demo.py` | 서버 + PC Agent + ACP + MCP 실제 프로세스 시나리오 |
| `.venv\Scripts\python scripts\e2e_install.py` | 포터블 설치 → 연결 → 새 버전 배포 → 자동 업데이트 |
| `cd frontend; npm run smoke` (`-- --demo`) | 실제 브라우저(Edge)로 주요 화면 점검·스크린샷, 화면 오류 있으면 실패. `--demo`는 전체 과정까지 |

## 사내 서버 설치
`data\`·`dist\`·`frontend\dist\`는 git에 들어 있지 않다. 사내 서버에서는 아래 절차로 만든다(인터넷 불필요, 사내 pip·npm 미러 사용).
자세한 절차와 문제 해결은 [docs/onsite-ai-runbook.md](docs/onsite-ai-runbook.md)에 있다.

1. Python 3.12(python.org 설치본)·Node 18+를 준비하고 `python -m venv .venv; .venv\Scripts\pip install -r requirements-server.txt`
2. (선택) `server.toml`을 작성한다: `port`, `data_dir`, `secret_key`
3. 실행: `.venv\Scripts\python -m uvicorn server.app.main:app --host 0.0.0.0 --port 7014` (서비스 등록: NSSM 또는 작업 스케줄러)
4. 첫 가입자가 관리자가 된다. 이후 가입은 관리자가 승인한다(관리 > 사용자).
5. PC Agent 설치 파일 준비: `.venv\Scripts\python scripts\prepare_onsite.py --publish --server http://127.0.0.1:7014 --user <관리자> --pin <PIN>`
   - 웹 빌드 → `DDS-Agent-Setup.exe` → 포터블 런타임(이 서버의 Python·Node + 사내 pip 미러) → 서버 코드로 Agent 첫 버전 등록 → 점검
   - 마지막 줄이 `READY`이고, 웹앱 **관리 > Agent 배포**의 "PC Agent 설치 준비" 점검표가 모두 초록색이면 끝이다.
   - 인터넷이 되는 PC가 있다면 `python scripts\fetch_runtimes.py`(공식 포터블 Python·Node)로 만든 `data\agent_runtime\`을 복사해도 된다.

**사용자 PC**
1. 웹앱 > 설정 > PC Agent에서 `DDS-Agent-Setup.exe`를 받고 토큰을 발급한다.
2. 설치 창에서 토큰을 붙여 넣고 사용할 AI CLI를 고른 뒤 [설치]
   - 포터블 Python·Node가 사용자 폴더에 설치된다. 관리자 권한은 필요 없다.
3. 처음 한 번 각 AI CLI에 로그인한다: `gemini` / `claude` → `/login` / `codex login`
4. CAD/CAE 툴은 설치 프로그램이 자동으로 찾는다(설치 창에 "바로 쓸 수 있는 툴 N종"). 이후 툴을 설치·제거하면 PC Agent가 알아서 반영한다.
   - 웹앱 **CAD·CAE 툴** 화면에서 확인한다. 못 찾은 툴은 눌러서 "찾아보기…"로 실행 파일을 고른다(설정 파일을 고치지 않는다).
5. 웹앱 오른쪽 위와 Windows 트레이에서 "PC Agent 연결됨"을 확인한다.

**사내 검증 (라이선스 필요)**
- 툴별 스킬의 `TODO(사내검증)` 항목: API 이름, 배치 옵션, 결과 파일 형식
- Live Link 플러그인
  - Abaqus: `agent-context/live/abaqus/abaqus_plugins/ddsLive`
  - HyperMesh: `agent-context/live/hypermesh/dds_live_hm.tcl`
  - NX: `agent-context/live/nx/dds_live_nx.py`
- HPC 실행기: `agent/dds_agent/job_runner.py`의 `HpcRunner`. 사내 스케줄러(LSF/PBS/Slurm)에 맞춰 구현한다.
- AI CLI의 사내 정책: 사용 가능한 계정·모델·프록시

## 운영
- **Agent 업데이트**: `agent/dds_agent/__init__.py`의 `__version__`을 올린 뒤 `build_agent_bundle.py --publish`
  - 연결된 PC는 작업이 없을 때 자동으로 업데이트하고 재시작한다.
  - 새 버전이 바로 죽으면 런처가 이전 버전으로 롤백한다.
  - 관리 > Agent 배포에서 이전 버전을 "최신으로 지정"하면 롤백된다.
- **모니터링**: 관리 > PC Agent 현황에서 사용자별 PC를 본다(버전·연결·실행 중 케이스·설치된 AI CLI). [업데이트] 버튼으로 원격 지시한다.
- **보안**
  - 로그인은 6자리 PIN이다. 5회 틀리면 10분 잠기고, 관리자가 초기화한다.
  - PC Agent는 API 토큰으로 서버에 먼저 접속한다. 사용자 PC 쪽 포트를 열 필요가 없다.
  - Live Link 플러그인은 127.0.0.1에만 열리고 토큰을 확인한다.
  - 에이전트의 툴 실행은 웹앱에서 승인(Flow·권한 카드)한다.
