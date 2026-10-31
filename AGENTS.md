# DDS Conversa — 개발 지침 (AI 코딩 에이전트·사람 공통)

> 이 문서는 **이 저장소를 개발하는** AI 코딩 에이전트(Claude Code · Gemini CLI · Codex)와 사람을 위한 것이다.
> `CLAUDE.md`·`GEMINI.md`는 이 파일을 불러온다.
> 앱 이름은 **DDS Conversa**다(이전 이름 DDS AI Engineer). 저장소 폴더·코드 이름(`DDS_AI_Engineer`, `dds_agent` 등)은 그대로 둔다.
>
> **예외:** 지금 작업 폴더가 DDS 프로젝트 작업 폴더라면(`DDS_Workspace\P0001_…`처럼 그 안에 `project.json`이 있는 곳) 이 문서는 무시한다.
> 그 폴더에서 일하는 것은 웹앱의 CAD/CAE 에이전트이고, 그 폴더의 `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` 지침을 따른다.

이어받아 개발을 시작한다면 **[docs/handoff.md](docs/handoff.md)**(현재 상태·다음 할 일)를 먼저 읽는다.
기능·디자인 요구사항과 다음 일감은 **[docs/inhouse-ai-guide.md](docs/inhouse-ai-guide.md)**, 화면·기능 전체 명세는 **[docs/product-spec.md](docs/product-spec.md)**(디자인 원칙·화면별 명세·기능·API·MCP·수용 기준)에 있다.
사내 서버에 PC Agent 설치 파일이 아직 없다면 **[docs/onsite-ai-runbook.md](docs/onsite-ai-runbook.md)**부터 한다.

---

## 1. 무엇을 만드는가
사내 CAE 부서용 웹앱이다. 엔지니어가 말로 요청하면 AI 에이전트가 CAD/CAE 툴을 다뤄 다음을 이어서 한다.
- 모델링 → 해석 → 결과 추출
- DOE → 예측 모델(GP) → 최적화
- 3D Simulator 배포 → 보고서

```
[브라우저] ──HTTP/WS──> [사내 서버(Windows·Linux): FastAPI + React 빌드본 + SQLite]  (포트 7014)
                              │  학습·최적화·Simulator·보고서, 프로젝트·대화·결과 저장
                              │  WebSocket (/ws/agent, 사용자 토큰)
                              ▼
                       [사용자 PC: PC Agent (agent/dds_agent)]
                              ├─ AI CLI(Claude Code·Gemini·Codex)를 ACP로 구동 ── MCP 서버(mcp_servers/*)로 서버 API 호출
                              ├─ CAD/CAE 툴 실행(스킬 스크립트) · 케이스 실행(로컬/HPC) · 결과 업로드
                              └─ 작업 폴더(프로젝트마다, 사용자가 정할 수 있음)
```
- AI CLI와 CAD/CAE 툴은 **사용자 PC**에서 돈다. 서버에는 숫자 결과·이미지·3D 필드·보고서만 올라간다.
- 무거운 원본(odb·d3plot 등)은 PC에 남는다.
- 사용자마다 PC Agent는 1개만 연결된다(마지막 연결 우선). 서버 컴퓨터에 PC Agent를 띄우면 작업이 서버에서 돌게 되니 운영에서는 하지 않는다.

## 2. 개발 환경 (사내: 인터넷 없음, pip·npm 사내 미러)
```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt     # Python 3.12
cd frontend; npm ci; npm run build; cd ..                                    # Node 18+
.venv\Scripts\python scripts\dev_live.py --agent demo                        # 서버 + PC Agent, http://127.0.0.1:7014  (admin / PIN 123456)
scripts\dev_live.cmd claude   # 같은 실행을 별도 콘솔 창으로(더블클릭하면 demo). AI 코딩 에이전트가 띄울 때는 이 방식을 쓴다
```
- `--agent demo`는 AI 없이 정해진 시나리오로 전체 흐름을 재현한다(CAD→해석→DOE→학습→3D Simulator→보고서). UI·흐름 검증용이다.
  실제 AI는 `--agent claude|gemini|codex`이고, 각 CLI에 먼저 로그인해야 한다.
- 프론트만 고칠 때: `cd frontend; npm run dev` → http://localhost:5173 (API는 7014로 프록시)
- `dev_live.py`는 **개발용**(서버와 PC Agent를 한 컴퓨터에 같이 띄움)이다. 운영 서버는 `uvicorn server.app.main:app --host 0.0.0.0 --port 7014`만 실행한다. Linux 서버는 `deploy/linux/`(systemd)와 `docs/onsite-ai-runbook.md` L절을 따른다.

## 3. 디렉터리 지도
| 경로 | 내용 |
|---|---|
| `server/app/` | FastAPI. `main.py`(앱·정적 파일), `models.py`(SQLAlchemy), `schemas.py`, `config.py`(포트·data_dir) |
| `server/app/routers/` | `auth` 계정·PIN · `projects` 프로젝트·멤버 · `sessions` 대화·첨부·내 PC 폴더 · `flows` 작업 흐름·결과 · `studies` DOE·케이스·모델·Simulator·공유 · `agents` PC Agent 배포·설치·모니터링 |
| `server/app/agent/` | `hub.py` 대화 턴·세션 관리(ACP 이벤트 → 메시지) · `link.py` PC Agent WebSocket 연결과 요청/응답 |
| `server/app/study/` | `doe`(LHS/Sobol) · `surrogate`(GP) · `bayesopt`(EI·제약) · `fieldmodel`(3D 형상·필드 PCA+GP) · `service` |
| `server/app/report.py`·`report_md.py`·`assets/` | Word·HTML 보고서와 공통 틀 · 본문 markdown 해석 · 로고·파비콘 사본 |
| `agent/dds_agent/` | PC Agent. `main.py`(서버 메시지 처리 `handle()`) · `acp_runner.py`(AI CLI) · `executor.py`(케이스 실행) · `workspace.py`(로컬 폴더 규칙·직접 지정 폴더) · `job_runner.py`(로컬/HPC) · `demo_agent.py` · `installer.py`·`updater.py`·`tray.py` |
| `mcp_servers/` | AI CLI가 쓰는 MCP 툴: `dds_flow`(작업 흐름·결과 게시·보고서) · `dds_cae`(스크립트·툴 실행, `list_tools`) · `dds_study`(DOE·학습·BO·배포) · `dds_live`(실행 중인 CAD/CAE와 Live Link) |
| `cae_adapters/` | `catalog.py`(지원 툴 29종 단일 목록·기본 설치 경로·설치 흔적 단어) · `tool_commands.py`(실행 명령 키) · `script.py`(케이스 스크립트 실행) · `mock.py`(연습용 솔버) |
| `agent-context/` | AI CLI에게 주는 작업 규칙(`INSTRUCTIONS.md`)과 툴별 스킬(`skills/<툴>/SKILL.md`·`scripts/`). PC Agent가 프로젝트 폴더로 동기화하고(`agent/dds_agent/context.py`), DDS만 만든 항목은 탐색기에서 숨긴다. 사용자 규칙 파일은 표시 구간만 고친다(`docs/local-data.md`) |
| `frontend/src/` | React+TS. `pages/`(첫 화면·작업 공간·툴·Simulator·설정·관리) · `chat/`(대화·입력창) · `canvas/`(오른쪽 패널 탭) · `components/`(`HeroMesh` 로그인 3D, `AgentGlyph` 에이전트 기호·글자 로고, `Skeleton`) · `motion.ts`(움직임 도구) · `modern.css`(최종 디자인 층) |
| `installer/` | `DdsAgentSetup.cs` → `DDS-Agent-Setup.exe`(서버가 없거나 소스가 새로우면 자동 빌드). 색은 `tokens.css` 값을 쓴다 |
| `deploy/linux/` | Linux 서버 systemd 서비스 파일 |
| `scripts/` | `dev_live.py` · `prepare_onsite.py`(사내 서버 준비, Linux 서버용 설치 묶음 `--pack`·`--import-pack`) · `fetch_runtimes.py` · `build_agent_bundle.py` · `install-agent.ps1` · `e2e_*.py` · `hero/`(로그인 화면의 해석 결과 생성기. 실행에는 쓰이지 않고, 별도 환경에 gmsh가 필요하다. 결과는 `frontend/public/hero/`) |
| `docs/` | `handoff.md` 인수인계 · `onsite-ai-runbook.md` 서버 준비 · `onsite-validation.md` 실제 툴 검증 · `local-data.md` 로컬 폴더 규칙 · `skill-contract.md` 스킬 규약 · `cae-data-hdf5.md` 해석 데이터(case.h5) · `shape-learning-plan.md` 형상 학습 계획(train.h5·GPU 서버·PhysicsNeMo) · `product-spec.md` 디자인·기능 명세서 · `inhouse-ai-guide.md` 사내 AI 지침(기능·디자인 요구사항, 다음 일감) |
| `data/`·`dist/`·`frontend/dist/` | **git에 없음**(.gitignore). DB·설치 파일·런타임·빌드 결과 |

## 4. 변경 후 반드시 검증
| 바꾼 것 | 실행 | 통과 기준 |
|---|---|---|
| Python(서버·Agent·MCP·스킬 규약) | `.venv\Scripts\python -m pytest server/tests agent/tests -q` | 모두 passed |
| 프론트엔드 | `cd frontend; npm run build` | TypeScript 오류 없음 |
| 화면·흐름 | 서버를 띄운 뒤 `cd frontend; npm run smoke` (흐름까지: `npm run smoke -- --demo`) | 마지막 줄 `PASS`, `frontend\e2e\shots\` 스크린샷을 직접 열어 확인 |
| ACP·MCP 실제 프로세스 | `.venv\Scripts\python scripts\e2e_demo.py` | 시나리오 완료 |
| 세션 유지·대기 표시 | `.venv\Scripts\python scripts\e2e_sessions.py` (실제 서버·Agent + 가짜 CLI `agent/tests/fake_acp.py`, 창 없는 브라우저) | 마지막 줄 `E2E 통과`, 스크린샷 `frontend\e2e\shots\sessions\` |
| 설치·자동 업데이트 | `.venv\Scripts\python scripts\e2e_install.py` | 설치→연결→새 버전→업데이트 |
| 스킬 스크립트 | `python -m py_compile <파일>` · 툴 목록 일관성은 pytest의 `test_tool_catalog_consistent` | — |
| 실제 CAD/CAE 연동(서버에 툴 없음) | `scripts/pc_verify.py`로 사용자 PC에서 직접 시험(`deploy` → `suite --tool <툴>`·`chat "요청"`). [docs/pc-verification.md](docs/pc-verification.md) | 사람에게 눌러 보라고 하지 않고 결과를 받아 판단 |

화면을 바꿨다면 스크린샷을 눈으로 확인한다(겹침·잘림·빈 화면). 테스트가 실패하면 원인을 고치고, 실패를 숨기지 않는다.

## 5. 작업 규칙
**UI·UX** (사용자: 사내 CAE 엔지니어, AI 전문가 아님)
- 화면 문구는 한국어, 쉬운 말로 쓴다. "무엇을 하면 무엇이 된다"가 보이게 한다. 영어 약어는 처음 나올 때 풀어 쓴다.
- Claude·Codex 데스크톱 앱 방식을 따른다.
  - 단계(depth)는 최소로 한다.
  - 같은 기능의 버튼을 두 곳에 만들지 않는다.
  - 상태는 한 곳에서 보여 준다(예: PC Agent 상태는 사이드바 아래).
  - 아이콘만 있는 버튼은 뜻이 분명할 때만 쓰고, 아니면 글자를 붙인다.
- 색·글자 크기·둥글기·그림자·움직임 시간은 `frontend/src/design/tokens.css`의 토큰만 쓴다(`server/tests/test_design_tokens.py`가 hex 색·px 글자 크기·px 둥글기·움직임 시간 하드코딩을 막고, 두 테마의 글자 대비를 검사한다). 새 스타일은 `modern.css` 끝에 덧붙인다(마지막에 불러오는 층). 규칙은 `docs/product-spec.md` 2절.
- 무대(로그인·첫 화면·빈 화면·보고서 표지·Simulator 머리)는 큰 글자와 등장 움직임을, 작업대(대화·작업 흐름·표·설정)는 상태가 바뀔 때의 짧은 움직임만 쓴다.
  - 기본 CSS가 최종 모습이고 `@keyframes`는 시작만 정한다. 새로 생긴 것만 등장시킨다(`frontend/src/motion.ts`의 `useFreshKeys`).
  - 화면을 바꾸면 `npm run smoke -- --reduced`도 돌린다. 애니메이션 라이브러리는 넣지 않는다.
- 데스크톱(1440 폭) 기준이다. 모바일은 깨지지 않는 정도만 유지한다.
- 오류는 원인과 할 일을 함께 보여 준다. 예: "서버에 설치 파일이 없습니다 → 관리 > Agent 배포에서 준비".

**코드**
- 주변 코드와 같은 스타일을 따른다. 주석은 한국어로, 왜 그렇게 했는지를 짧게 쓴다.
- 새 의존성은 사내 미러에서 받을 수 있는 것만 쓴다. `requirements-*.txt`는 버전을 고정한다.
- 서버 API를 바꾸면 `schemas.py`와 테스트를 같이 바꾼다. PC Agent 메시지를 추가할 때는 양쪽을 짝으로 맞춘다.
  - PC Agent 쪽: `agent/dds_agent/main.py`의 `handle()`
  - 서버 쪽: `registry.runner(user.id).call("타입", {...})`
- PC Agent·MCP·스킬을 바꿨으면 `agent/dds_agent/__init__.py`의 `__version__`을 올린다.
  배포는 관리 > Agent 배포 > "서버 코드로 첫 버전 배포"(같은 버전은 한 번만)로 하며, 자동 업데이트가 켜진 PC(설정, 기본 켜짐)는 작업이 없고 5분 조용할 때 받아 두고, AI CLI가 떠 있으면 1시간 조용할 때 다시 시작한다(없으면 5분).
- 비밀값(토큰·PIN·secret key)과 `data/` 아래 파일은 커밋하지 않는다.
- 셸에서 백슬래시가 든 텍스트를 heredoc으로 파일에 쓰면 깨질 수 있다. 파일 편집 도구를 쓴다.

**커밋**
- 메시지는 한국어로 쓴다. 첫 줄은 무엇을 했는지 요약하고, 본문은 "- " 목록으로 쓴다.
- 검증(4절)을 통과한 뒤에 커밋한다. 푸시는 사용자가 요청할 때 한다.

## 6. 자주 하는 작업
| 하고 싶은 일 | 손댈 곳 |
|---|---|
| CAD/CAE 툴 추가 | `cae_adapters/catalog.py`(목록·기본 설치 경로 `guess`·설치 흔적 단어 `FIND`) → `tool_commands.py`(실행 키) → `agent-context/skills/<툴>-automation/SKILL.md`+`scripts/` → `agent-context/INSTRUCTIONS.md` 목록 → pytest |
| 실제 툴에서 스킬 고치기 | `docs/onsite-validation.md`의 요청을 `docs/pc-verification.md`의 도구로 사용자 PC에서 직접 시험한다. 고친 곳의 `TODO(사내검증)` 표시를 지운다 |
| Live Link | **사용자에게 리본 버튼·저널·스크립트·플러그인 실행을 부탁하지 않는다.** 연결은 `mcp_servers/dds_live.py`가 스스로 하고, 못 붙으면 에이전트가 배치로 진행한다(`test_live_autoconnect.py`가 문구를 막는다) |
| MCP 툴 추가 | `mcp_servers/dds_*.py`(FastMCP 데코레이터) → 서버 API(필요 시) → `agent-context/INSTRUCTIONS.md`에 사용법 |
| 대화 흐름(승인·권한·결과 카드) | `frontend/src/chat/ChatView.tsx` · 서버 `server/app/agent/hub.py` |
| 오른쪽 패널 탭 | `frontend/src/canvas/CanvasPanel.tsx` · `views.tsx`(작업 흐름: 흐름도 고정 + 'N단계' 결과 스크롤 · 데이터: 입력/출력·학습 데이터·케이스) · `ReportView.tsx`(보고서) · `SimulatorTab.tsx`(Simulator·모델 버전) · 단계 번호 `frontend/src/flow/order.ts` |
| Simulator | 화면 `frontend/src/pages/SimulatorPage.tsx`(`SimulatorView`를 탭과 넓은 화면이 함께 쓴다) · 목록 `SimulatorsPage.tsx` · 버전·3D 묶기 `server/app/study/service.py` `deploy_version`·`field_models_for` · 목록·공유 `server/app/routers/studies.py` · 규칙 `docs/product-spec.md` 3-7절 |
| DOE·학습·최적화 로직 | `server/app/study/*` + `server/tests` |
| 보고서 | AI가 새로 쓰는 보고서: 자료·저장 `server/app/routers/reports.py`, 틀(표지·목차·맺음말·머리글)과 Word·HTML `server/app/report.py`, 본문 해석 `server/app/report_md.py`, 로고·파비콘 사본 `server/app/assets/`(`frontend/public`과 같아야 한다), 웹 탭 `frontend/src/canvas/ReportView.tsx`, 쓰는 규칙(AI는 제목과 본문만, `##`부터) `agent-context/INSTRUCTIONS.md` 1-0절·`mcp_servers/dds_flow.py` `write_report`, 검사 `server/tests/test_report_template.py` |
| 해석 데이터 저장(HDF5) | `cae_adapters/h5store.py`(case.h5 쓰기·읽기·묶기·검증) → PC Agent `executor._archive` → 서버 `/api/cases/{id}/archive` · 규약 `docs/cae-data-hdf5.md` · 툴별 전체 추출 `dyna_to_h5.py`·`op2_to_h5.py`·`abq_odb_to_npz.py` |
| 학습 데이터(train.h5·데이터셋) | 입력 덱 추출 `cae_adapters/deck.py`(다른 툴을 더하는 곳) · 샘플 `train_h5.py`(표준 이름표 `STANDARD`) · 데이터셋 `server/app/study/dataset.py` · 규약 `docs/cae-data-hdf5.md` 8절 · 계획 `docs/shape-learning-plan.md` |
| Output 추가 추출 | `server/app/study/service.py` `add_outputs` → PC Agent `case.extract`(`executor.py`) → 어댑터 `extract()` · 규약 `docs/skill-contract.md` 1-2절 |
| PC Agent 설치 과정 | `scripts/install-agent.ps1`(실제 설치) · `installer/DdsAgentSetup.cs`(설치 창) · `server/app/routers/agents.py`(배포·설치 파일) |
| Live Link 속도 개선 | `.claude/skills/live-link-speedup/SKILL.md`부터 읽는다(개발용 스킬, Agent 배포에는 들어가지 않음) |

## 7. 사내 환경에서 주의할 점
- 실제 CAD/CAE 툴 연동은 집 PC(라이선스 없음)에서 검증하지 못했다.
  - 스킬에 `TODO(사내검증)` 표시가 약 560곳 있다.
  - 첫 실행은 반드시 작은 모델로 한다.
- 사내에서는 Linux 서버에서 개발·배포하고 툴은 사용자 PC에만 있다. 개발 AI는 `docs/pc-verification.md`의 원격 검증으로 그 PC에서 직접 시험하고 고친다.
- 외부 인터넷에서 무언가를 받으려 하지 않는다(설치·런타임은 `prepare_onsite.py`가 사내 자원만으로 만든다).
- 글꼴 `@fontsource-variable/geist-mono`가 사내 npm 미러에 없으면 `npm ci`가 실패한다. 그때는 `frontend/package.json`과 `main.tsx`에서 빼면 된다. 고정폭은 Consolas로 대체된다.
- 사용자 PC의 작업 폴더는 사람마다·PC마다 다르다. 서버에는 저장하지 않고 PC의 `index.json`에 저장한다(`docs/local-data.md`).
