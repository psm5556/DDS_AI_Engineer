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

## 화면 (Agentic 레이아웃)
- **첫 화면**: "무엇을 만들고 싶으세요?" 큰 입력창. 입력하면 프로젝트가 생기고 바로 대화가 시작된다.
- **작업 공간**: 가운데 **대화**와 오른쪽 **라이브 캔버스**(작업 흐름 · 결과 · 데이터·모델 · Simulator).
  - 대화 속 카드에서 Flow 승인, 실행 권한 허용, 결과 확인을 바로 한다.
  - 캔버스는 에이전트가 만든 것을 실시간으로 따라간다.
- **Simulator**: 배포된 예측 모델을 슬라이더로 바로 예측한다(± 불확실도, 민감도 곡선). 공유 링크로 사내 누구에게나 줄 수 있다.
- **보고서**: 결과를 모아 HTML/Word로 내려받는다. 에이전트가 결론 요약을 쓸 수도 있다.
- **입력창**:
  - → 키로 상황별 추천 프롬프트를 받는다. ↑↓로 다른 추천을 고른다.
  - 파일은 첨부(📎·끌어 놓기·붙여넣기)한다. 첨부는 내 PC 프로젝트 `inputs/`에 자동으로 받아진다.
- **프로젝트·대화 관리**: ⋯ 메뉴나 우클릭으로 이름 바꾸기, 즐겨찾기, 멤버·공유, 내 PC 폴더 열기, 삭제를 한다. 대화도 이름을 바꾸고 삭제할 수 있다.
- **로컬 데이터 규칙**: 프로젝트·스터디·케이스별 폴더와 `cases.csv`를 둔다(`docs/local-data.md`).
  - 캔버스의 **내 PC 파일** 탭에서 탐색기나 CAE 뷰어로 연다.
- **3D 형상·필드 학습**: 케이스의 `fields/*.npz`로 '설계변수 → 3D 형상 + 응력 분포'를 학습한다(PCA + GP). 결과는 Simulator에서 3D로 본다.
- **HPC**: Slurm·PBS·LSF로 제출하고 상태를 감시한다(`agent.toml [hpc]`, 스터디별 `runner: "hpc"`). 사내 웹 CAE 뷰어는 서버 `web_viewer_url`로 연결한다.
- **내 PC 진단**: 설정 화면에서 CAD/CAE 툴 경로·버전, 뷰어, 작업 폴더를 확인한다. 사내 첫 주 검증은 `docs/onsite-validation.md`를 따른다.
- 디자인은 DDS ReDO 디자인 시스템(`frontend/src/design/tokens.css`)을 따른다. 라이트/다크 모드를 지원한다.

## 폴더
| 폴더 | 내용 |
|---|---|
| `server/` | FastAPI 서버(`app/`)와 테스트(`tests/`) |
| `frontend/` | React + TypeScript + TanStack Query + React Flow + Plotly |
| `agent/` | PC Agent(`dds_agent/`: ACP 러너, 케이스 실행, 업데이트, 트레이), `launcher.py` |
| `mcp_servers/` | 에이전트가 쓰는 MCP 툴: `dds_flow`, `dds_cae`, `dds_study`, `dds_live` |
| `cae_adapters/` | 케이스 실행 어댑터(`mock`, `script`)와 툴 명령 테이블 |
| `agent-context/` | 에이전트 작업 규칙(`INSTRUCTIONS.md`), 스킬 12종, Live Link 플러그인(`live/`) |
| `installer/` | `DDS-Agent-Setup.exe` 소스(WinForms, C#) |
| `scripts/` | 개발 실행, 번들·런타임·설치 파일 빌드, E2E 테스트 |
| `docs/skill-contract.md` | 툴 스크립트 입출력 규약(`params.json` → `results.json`, `images/`) |

### 스킬 (`agent-context/skills/`)
- CAD/CAE 툴: `nx-cad-automation`, `simcenter3d-automation`, `abaqus-automation`, `lsdyna-automation`, `ansys-automation`, `altair-automation`, `starccm-automation`, `amesim-automation`
- 프로세스: `workflow-planning`, `doe-surrogate-optimization`, `live-modeling`
- 툴 API 중 사내에서 확인이 필요한 부분은 각 스킬에 `TODO(사내검증)`로 표시돼 있다.

## 개발 (집 PC)
```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt
cd frontend; npm install; npm run build; cd ..
.venv\Scripts\python scripts\dev_live.py --agent demo     # 서버 + PC Agent, http://127.0.0.1:8765 (admin / PIN 123456)
#   --agent claude | gemini | codex : 실제 AI CLI 사용 (먼저 터미널에서 각 CLI 로그인)
#   프론트 개발 중에는: cd frontend; npm run dev  → http://localhost:5173
```
- `demo` 에이전트는 Gemini 없이 전체 흐름을 검증하는 시나리오 에이전트다.
  - 흐름: Flow 제안 → 승인 → 권한 → Branin mock DOE → GP → BO → Simulator 배포
- Live Link 연습: 대화에서 "연습용 CAD로 Live Link 해 봐"라고 하거나, `live_launch(tool="practice")`를 쓴다.

### 테스트
| 명령 | 확인 내용 |
|---|---|
| `.venv\Scripts\python -m pytest server/tests` | API: 인증(PIN·잠금)·공유·Flow·DOE/학습/BO/배포·보고서·Simulator 공유 |
| `.venv\Scripts\python scripts\e2e_demo.py` | 서버 + PC Agent + ACP + MCP 실제 프로세스 시나리오 |
| `.venv\Scripts\python scripts\e2e_install.py` | 포터블 설치 → 연결 → 새 버전 배포 → 자동 업데이트 |

## 사내 이전 체크리스트
**집(인터넷 되는 곳)에서 준비**
1. `python scripts\fetch_runtimes.py` 실행 → `data\agent_runtime\`에 다음이 생긴다.
   - 포터블 Python 3.12, Node 22
   - Agent 의존성 wheel 묶음
2. `powershell -File scripts\build_setup_exe.ps1` 실행 → `DDS-Agent-Setup.exe`가 빌드되고 `data\agent_runtime\`에 복사된다.
3. `cd frontend; npm run build` 실행 → `frontend\dist\`
4. 저장소와 `data\agent_runtime\` 폴더를 사내로 옮긴다.

**사내 서버**
1. Python 3.12를 준비하고 사내 미러로 `pip install -r requirements-server.txt`
2. (선택) `server.toml`을 작성한다: `port`, `data_dir`, `secret_key`
3. `data\agent_runtime\` 폴더를 서버 `data_dir` 아래에 둔다.
4. 실행: `uvicorn server.app.main:app --host 0.0.0.0 --port 8765`
   - 서비스 등록: NSSM 또는 작업 스케줄러
5. 첫 가입자가 관리자가 된다. 이후 가입은 관리자가 승인한다(관리 > 사용자).
6. Agent 첫 배포: `python scripts\build_agent_bundle.py --publish --server http://<서버>:8765 --user admin --pin <PIN> --notes "첫 배포"`

**사용자 PC**
1. 웹앱 > 설정 > PC Agent에서 `DDS-Agent-Setup.exe`를 받고 토큰을 발급한다.
2. 설치 창에서 토큰을 붙여 넣고 사용할 AI CLI를 고른 뒤 [설치]
   - 포터블 Python·Node가 사용자 폴더에 설치된다. 관리자 권한은 필요 없다.
3. 처음 한 번 각 AI CLI에 로그인한다: `gemini` / `claude` → `/login` / `codex login`
4. CAD/CAE 툴 경로를 `%USERPROFILE%\.dds-agent\agent.toml`에 적는다.
   ```toml
   [tools]
   nx_run_journal = 'C:\Program Files\Siemens\NX2312\NXBIN\run_journal.exe'
   abaqus = 'C:\SIMULIA\Commands\abaqus.bat'
   lsdyna = 'C:\LSDYNA\program\ls-dyna_smp_d_R13.exe'
   ansys_python = 'C:\Users\me\ansys-venv\Scripts\python.exe'
   [tool_env.amesim]
   AME = 'C:\Simcenter\Amesim\2310'
   PATH = 'C:\Simcenter\Amesim\2310;C:\Simcenter\Amesim\2310\win64;{PATH}'
   ```
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
