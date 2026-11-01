# 설계 결정 (사용자 요청 3가지). 구현자는 이 결정을 따른다. 조사 근거는 같은 폴더의 *.md

## 사용자 요구 원문
1. 사내 보안 때문에 Gemini·Claude·Codex CLI 실행이 늦다. 사용자가 기다려도 멈춘 것으로 생각하지 않게 한다.
2. 세션이 죽거나 사용자가 끊은 게 아니면 세션을 닫지 말고 계속 이어서 쓴다(다시 호출하는 지연이 없게).
3. 사용자 PC에 세션의 커맨드 창은 보이면 안 된다. 모두 백그라운드.
4. 이미 해석한 여러 케이스 결과가 있으면 바로 예측 모델을 만들어 Simulator로 만들 수 있어야 한다.
5. 입력·출력을 표(tabular)로 갖고 있으면 바로 예측 모델·Simulator를 만들 수 있어야 한다.
6. 다양한 진입 시점으로 요청해도 언제든 대응되는지 시나리오로 검증하고 구현한다.

## 공통 규칙
- AGENTS.md를 따른다. 화면 문구 한국어·쉬운 말, 주석 한국어로 왜를 짧게, 색·크기는 tokens.css 토큰, 새 CSS는 modern.css 끝.
- 새 의존성을 넣지 않는다(xlsx는 표준 라이브러리로 읽는다). PC Agent·MCP·스킬을 바꾸면 agent/dds_agent/__init__.py 버전을 올린다.
- 사용자가 이 PC를 쓰고 있다: 포커스를 뺏지 말고, 키 입력을 흉내 내지 말고, 보이는 창을 띄우는 시험을 최소화한다. 실제 로그인·실제 CAD 실행 금지.
- 개발 서버(포트 7014)와 포트 8000 프로세스를 끄거나 재시작하지 않는다. 시험 서버는 다른 포트와 임시 DDS_DATA_DIR로. data/ 아래를 건드리지 않는다.
- 테스트에서 서버 코드를 import하기 전에 server.tests.test_api를 먼저 import한다(임시 데이터 폴더). 안 그러면 실제 data/app.db를 쓴다.
- git commit·push는 하지 않는다(메인이 한다). 다른 스트림의 변경을 되돌리지 않는다.

## 스트림 B: 창 숨김
- 새 공용 모듈 cae_adapters/hidden.py (agent·mcp_servers·cae_adapters가 모두 불러 쓸 수 있고 Linux에서 import가 깨지지 않게):
  - hidden_kwargs(): Windows에서 {"creationflags": CREATE_NO_WINDOW}, 그 외 {}.
  - console_python(): sys.executable이 pythonw면 같은 폴더 python.exe(없으면 그대로). 이유: pythonw가 끼면 숨은 콘솔 상속이 끊겨 그 아래 솔버가 창을 연다(실험 E·F).
  - kill_tree(proc): Windows는 taskkill /T /F(숨김), 그 외 terminate. .cmd 셈은 terminate로 node가 남기 때문.
  - KillOnExitJob: Job Object(KILL_ON_JOB_CLOSE, BREAKAWAY_OK). Agent가 비정상 종료해도 숨은 CLI가 고아로 쌓이지 않게 CLI에만 건다. 실패해도 조용히 넘어간다.
  - spawn_gui_detached(cmd, env, cwd): Live Link가 사용자 GUI(NX·Abaqus·HyperMesh)를 띄울 때. CLI 트리와 끊어(cmd /c start "" /B ..., CREATE_BREAKAWAY_FROM_JOB|CREATE_NO_WINDOW) Agent가 업데이트로 재시작해도 사용자의 미저장 모델이 죽지 않게 한다. 콘솔은 안 보이고 GUI만 보인다.
- 적용: acp_runner.py, headless_runner.py, installer.py, job_runner.py(로컬·HPC, stdin=DEVNULL도), devtest.py, updater.py, diagnose·catalog(이미 있음), main.py MCP 명령과 demo {python}과 tool_commands {python}을 console_python으로, dds_live.py Popen들(abaqus·hypermesh·ugraf).
- launcher는 자동 업데이트로 교체되지 않으므로 pythonw 그대로여도 되게 위 지점에서 막는다. launcher도 고치되 의존하지 않는다.
- 의도적으로 보이는 것은 유지: 설치 창의 로그인 창, 폴더 선택 창, Live Link GUI 본체, 뷰어.
- INSTRUCTIONS.md: "GUI는 dds_live로만 띄운다. 셸 도구로 start·Start-Process를 쓰지 않는다"(콘솔 창 방지).
- 테스트: (1) AST 린트: agent/·mcp_servers/·cae_adapters/의 subprocess.Popen/run/check_output 호출에 hidden_kwargs가 없으면 실패(허용 목록 명시). (2) 실제 Windows 시험: pythonw 부모 → AcpRunner가 가짜 .cmd CLI 실행 → 그 아래 MCP(console_python) → 솔버 체인에서 보이는 콘솔 창 0개(EnumWindows로 해당 프로세스 트리의 visible 창 검사). (3) kill_tree가 .cmd 손자 node/python까지 끝내는지. (4) spawn_gui_detached 프로세스가 kill_tree 뒤에도 살아 있는지.

## 스트림 A: 세션 유지와 대기 표시
핵심 원칙: 느린 CLI를 절대 실패·종료시키지 않는다. 세션은 죽었거나 사용자가 끊었을 때만 버린다. 기다리는 동안 무엇을 왜 기다리는지 항상 보인다.

서버(hub.py·link.py·main.py·sessions.py):
- POST /sessions/{id}/prompt는 바로 돌아온다. 사용자 메시지 저장·busy 등록·turn_started(started_at, stage)를 CLI 시작 전에 한다. CLI 준비는 백그라운드. 진행 중이면 409로 중복 차단.
- /status는 {busy, stage, stage_detail, started_at, elapsed_sec, typical_start_sec, link: "ok"|"lost", pending_permissions}.
- stage: agent_starting(CLI 프로그램 시작) · session_new · session_load · thinking · tool · reconnecting. turn_stage 이벤트로 방송.
- 시작 구간 타임아웃 제거(서버 쪽 None, Agent 쪽은 설정값 cli_start_timeout_sec 기본 1800). conn.request 시간 초과는 AgentError로 바꿔 원인과 할 일을 보인다. 빈 오류 버그 수정(main.py `str(e) or repr(e)`, link.resolve는 "error" in msg로 판단).
- 시작 중 중지가 동작한다: 취소 표시 후 세션 준비가 끝나면 프롬프트를 보내지 않는다. CLI는 죽이지 않는다(따뜻하게 유지).
- 재연결: 끊겨도 턴과 세션을 버리지 않는다. forget_user 대신 link=lost 표시. hello에 live_sessions(살아 있는 sid)·active_turns(turn_id, chat_id, sid, started_at)를 보내고 서버가 대조한다. 서버 재시작 뒤에도 hello로 턴을 복원한다. active_turns에 없는 턴은 끝난 것으로 정리하고 한 번만 안내한다.
- session.prompt는 응답 대기형이 아니라 turn_id를 준 알림으로 바꾸고, 끝은 Agent가 agent.turn_end로 알린다. Agent는 끊긴 동안 이벤트·turn_end를 outbox(크기 제한)에 모았다가 재연결 때 순서대로 보낸다. 미응답 권한 요청은 재연결 때 다시 보내고 서버는 중복 제거.
- rid는 서버 전체에서 유일(재연결 뒤 충돌 방지). ws 루프는 메시지 하나의 예외로 끊기지 않는다(로그만). 옛 연결의 finally는 현재 연결이 아니면 아무것도 지우지 않고 방송도 안 한다. registry.add는 옛 소켓을 닫는다. mark_disconnected도 같은 조건.
- ensure_session: live_sessions에 있으면 session/load 없이 그대로 쓴다(Gemini는 load가 세션을 dispose한다). 없으면 load. load가 "지원 안 함/없음"일 때만 new + recap(이전 대화 요약 + DDS 컨텍스트). first_turn 판단은 ensure_session 뒤. 새 세션으로 대체됐으면 대화에 한 번 안내.
- Agent의 runner_exit 알림을 받으면 그 runner의 sid를 잊고 진행 중 턴을 오류로 끝낸다. 다음 프롬프트는 CLI 재시작 + load → 실패 시 new + recap. 죽은 세션으로 대화가 막히지 않는다.
- 사용자가 에이전트를 바꾸거나 작업 폴더를 바꾸면 그때만 새 세션(기존 동작 유지).

PC Agent(main.py·acp_runner.py·runner.py·context.py·updater 쪽):
- 시작 타임아웃 설정값으로. 시간 초과가 나도 프로세스를 죽이지 않고, initialize가 늦게 오면 agent_info를 채운다(늦은 응답을 버리지 않게 initialize는 별도 처리). 시작 단계를 session.stage로 서버에 알린다(context_sync, cli_start, initialize, session_new, session_load + 경과).
- 프로세스 감시 스레드: 죽으면 pending 실패·세션 목록 비움·agent.runner_exit 알림. 다음 호출 때 재시작.
- 미리 띄우기: Agent 시작·재연결 때 마지막에 쓴 에이전트 CLI를 백그라운드로 띄워 initialize까지 한다(상태 파일 ~/.dds-agent/state.json, 없으면 안 띄움. 설치·로그인 안 된 것은 건너뜀). 마지막 대화 세션도 미리 session/load 해 둔다.
- 시작 시간 측정: 에이전트별 최근 시작 시간을 state.json에 저장하고 hello·session.stage에 typical_start_sec로 보낸다.
- 자동 업데이트: CLI 세션이 살아 있으면 다시 시작하지 않고 더 오래 기다린다(IDLE_RESTART_SEC 기본 3600, CLI 없으면 기존 300). 다시 시작한 뒤 미리 띄우기로 복구. 받아 두기(prepare)는 기존처럼 유휴 5분 뒤.
- 예외 방어: handle 전체 try/except, session.cancel·permission.reply는 create_task + to_thread로 수신 루프를 막지 않음, BrokenPipe/ValueError로 링크를 끊지 않음. cancel은 그 세션의 권한만 취소. 큰 agent.event(도구 입출력)는 잘라서 보낸다(WS 16MB 한도).
- 강제 재시작: 사용자가 중지했는데 응답이 없으면 화면에 "AI 도구 다시 시작" 버튼. session.force_restart가 runner를 kill_tree하고 그 runner의 턴을 끝낸다. 이것만이 사용자가 끊는 경로다.
- 컨텍스트 동기화는 내용이 그대로면 건너뛴다(스탬프 파일). 세션마다 465개 복사가 보안 검사에 걸린다.
- MCP 시작·도구 시간 제한: CLI 환경 변수(Claude: MCP_TIMEOUT, MCP_TOOL_TIMEOUT)와 Codex 설정을 조사해 긴 도구가 실패로 보이지 않게 한다. Codex 전역 설정 파일은 고치지 않는다. codex-acp가 받는 인자를 번들에서 확인한다(%APPDATA%\npm\node_modules). 안 되면 긴 MCP 도구를 짧은 호출 여러 번(폴링)으로 나눈다.
- 인증 오류 문구를 에이전트 이름에 맞추고 "로그인 필요"를 구분한다.

화면(ChatView·Composer·live.ts·WorkspacePage):
- 대기 말풍선: 단계 문구 + 경과 시간(1초마다) + 안내.
  - agent_starting: "Claude Code를 시작하는 중 · 1분 20초" + "사내 보안 검사로 몇 분 걸릴 수 있습니다. 멈춘 것이 아닙니다. 다른 일을 하다 와도 이어서 진행합니다." typical_start_sec 있으면 "지난번에는 3분 12초 걸렸습니다".
  - session_new/session_load: "새 대화를 여는 중" / "이전 대화를 이어 붙이는 중".
  - thinking: "생각하는 중 · 12초". 60초 넘게 출력이 없으면 "답을 기다리는 중 · 2분 10초. 오래 걸리는 작업일 수 있습니다."
  - reconnecting: "PC Agent 연결을 다시 잡는 중. 작업은 PC에서 계속됩니다."
- 새 대화 첫 메시지에서 재마운트돼도 busy·메시지·경과 시간이 유지된다(status에서 복원). 대기 중 입력창은 막히고 중지 버튼이 보인다. 새로고침 뒤 복원. WS 재연결 때 status·메시지 다시 읽음.
- Agent가 끊겼을 때 대화 전체를 설치 안내로 바꾸지 않는다. 기록이 있으면 위에 안내 띠만. 기록 없고 정말 설치가 안 된 때만 안내.
- 중지 후 응답이 없으면(30초) "AI 도구 다시 시작" 버튼.

시험 도구: agent/tests/fake_acp.py(실제 ACP stdio 가짜 CLI: 시작 지연, loadSession 지원 여부, 프로세스 기동·session/new·session/load 횟수 기록, 도중 죽기 옵션). 시험: 느린 시작(지연>기존 60초 제한을 monkeypatch로 축소 재현), 후속 턴 재기동 0회, 재연결 때 load 0회, 서버 재시작 복원, CLI 사망 후 load·recap, 끊긴 동안 이벤트 보존, rid 충돌, 옛 연결 finally, 중복 전송 409, 시작 중 중지. scripts/e2e_sessions.py: 실제 uvicorn·Agent·가짜 CLI. Playwright로 대기 말풍선 스크린샷.

## 스트림 C: 어느 시점에서든 진입 (기존 결과·표 → Simulator)
핵심: 케이스 등록 경로를 만든다. AI 없는 화면 경로와 MCP 도구가 같은 서버 API를 쓴다. AI 없이 표 하나로 Simulator까지 가능해야 한다(CLI 시작 지연 없음, Agent 끊겨도 됨).

공통 파서 cae_adapters/tabular.py(표준 라이브러리만):
- CSV(UTF-8/UTF-8 BOM/CP949 자동, 구분자 , ; 탭 자동, 천 단위·소수점 쉼표 주의), XLSX(zipfile+xml: 시트 선택, sharedStrings, inlineStr, 숫자, 불리언, 수식 캐시값).
- 머리글 행 자동 탐지(제목 줄 건너뜀), "thickness [mm]"·"두께(mm)"에서 단위 분리, 한글 이름 허용.
- 열 분석: 숫자/상수/범주형/결측 수/min/max, 입력·출력 추측(이름 키워드). 두 파일 결합(key 열).
서버:
- 새 어댑터 "data"(cae_adapters 등록). 실행하면 "불러온 데이터 스터디라 해석을 돌릴 수 없습니다 → 해석 방법을 연결하세요" 오류. PUT /api/studies/{id}/adapter로 나중에 연결 가능(대기 DOE도 이어 감).
- POST /api/projects/{pid}/studies/import : {name, source, inputs:[{name,unit,lower?,upper?}], outputs:[..], objectives?, rows:[{...}], fixed?} → 스터디 + status=complete 케이스(source="import", 행 해시로 멱등: 같은 표를 다시 넣어도 중복 없음).
- POST /api/studies/{id}/cases/import : 기존 스터디에 행 추가(같은 검증, 범위 밖 값이면 범위 넓힘 + 보고). 재학습 시 새 모델 버전, 공유 링크가 따라감.
- POST /api/projects/{pid}/studies/import/preview (multipart CSV/XLSX) : 열 분석 결과. POST .../import/file : 매핑과 함께 바로 등록.
- 검증 보고: 받은 행·등록 행·제외 행과 이유(결측, 숫자 아님, 중복), 상수 열은 고정값으로 기록(변수 아님), 범주형은 제외 + 경고(뜻을 설명), 3행 미만·입력 0개·출력 0개는 이유와 할 일을 담은 400. NaN·Inf 절대 저장 안 함. 500 금지.
- 학습: NaN·누락 행은 이유와 함께 제외(침묵 금지). 행이 많으면(기본 >500) 공간 채움 표본으로 줄이고 알림(GP 시간 측정: 400행 93초). train MCP 호출 제한시간을 늘리고 재시도 중복 방지. 품질: R²와 표본 수로 quality(good/fair/poor)와 쉬운 말 경고를 응답·모델에 저장, Simulator 화면·탭에 표시. 배포는 막지 않되 poor면 경고 표시(공유 때도). 기존 R² 계산 방식을 먼저 읽고(홀드아웃/LOO) 맞춘다.
- 입력 범위는 데이터 min~max. 이상치 경고.
- optimize가 재학습 때 출력을 잃는 버그 수정(dds_study.py:164). /run {}이 대기 케이스 전부를 돌리는 문제 수정.
- data 스터디의 optimize: 모델로만 탐색해 후보를 돌려주고 "해석으로 확인되지 않은 예측"이라고 표시. suggest도 같은 뜻.
- 3D 필드: MCP로 케이스별 필드 등록(기존 라우트 재사용). 스칼라 출력이 없으면 필드 최대값을 출력으로 자동 생성해 Simulator가 성립하게 한다.
- 외부 joblib 모델은 pickle 실행 위험이 있어 받지 않는다. 입력·출력 표를 달라고 안내한다.
- DOE 계획 내보내기: CSV 받기(API + 화면 버튼 + MCP).
MCP(dds_study.py):
- import_table(path, inputs?, outputs?, objectives?, name?, sheet?, outputs_path?, key?, study_id?): 파일을 PC에서 직접 읽어 올린다(행을 AI 인자로 넘기지 않는다). inspect_table(path): 열 분석만.
- scan_results(folder, max_depth?): 케이스 폴더 찾기(results.json·params.json·case.json, csv 요약, 확장자별 개수, 폴더 이름의 변수 패턴 t=3.0_w=30, 추출 스크립트 추천). 원본을 고치지 않는다. UNC·절대 경로 허용.
- import_cases(cases=[{params,results,run_dir}], ...) 또는 폴더에서 직접. run_dir은 원본 경로로 기록만, 원본에 쓰지 않는다. G25 수정: 외부 run_dir에서 Output 추가 시 record_case가 터지지 않고, 결과 파일은 작업 폴더 쪽에 쓴다.
- list_studies, list_models, predict_batch, export_doe, set_study_adapter 추가. train_surrogate 결과에 품질 포함.
지침(전부 함께):
- INSTRUCTIONS.md에 "어디서 시작하든" 표: 무엇을 가졌는가 → 첫 도구. 표 있음 → inspect_table/import_table/train/deploy. 해석 폴더 → scan_results → (필요하면 추출 스크립트) → import. 모델 있음 → 이어서. CAD만 → 기존 흐름. 결과 1건 → 후처리·보고서. 조회 → 바로 답.
- 승인 규칙 축소: Flow 제안·승인은 **해석·긴 계산을 새로 돌리거나 사용자 모델을 바꾸는 일**에만. 조회·후처리·기존 데이터로 학습·Simulator 배포·보고서는 승인 없이 바로 한다. "모델 파일이 없으면 질문" 조건을 "데이터도 모델도 없을 때"로. inspect 스크립트 실행은 승인 전에 허용(모순 해소). missing 툴이어도 데이터 작업은 진행. R²<0.9 처방은 솔버 유무로 분기(솔버 없으면 데이터 추가 요청).
- 같은 뜻으로 hub.py 첫 턴 문구, dds_flow.py instructions·설명, dds_study.py instructions, doe-surrogate-optimization·workflow-planning 스킬 수정. 새 스킬 agent-context/skills/existing-data-to-simulator/SKILL.md(툴별 추출 스크립트 연결: abq_odb_extract, dyna_lasso_extract, nastran_op2_extract 등). 지침 일관성 린트 테스트.
화면:
- 데이터 탭 + 첫 화면에서 "표로 바로 Simulator": 파일 선택 → 미리보기(열마다 입력/출력/제외, 단위, 경고) → 만들기 → 학습·배포 → Simulator 탭 열기. Agent가 끊겨 있어도 된다. 첫 화면 시작 예시에 표·해석 폴더 시나리오 추가. 대화 빈 화면 문구·작업 흐름 빈 탭 문구를 승인 규칙에 맞게.
- Simulator 화면·탭에 데이터 출처(불러온 표 N행), 정확도(R²)와 경고 표시.
데모 에이전트:
- 첨부·경로·표 요청을 구분해 실제로 import_table → train → deploy를 돈다(엉뚱하게 Branin을 돌려 거짓 통과하지 않게). 해석 폴더 요청도 scan_results → import. 단순 질문은 Flow 없이 답. 후속 턴 첫 문단 잘림 수정.
이전 흐름 재사용·멤버 인수(사용자 질문으로 추가. 지금은 전용 기능이 없다. list_flows MCP 없음, get_flow는 id가 필요, 스터디 복제 없음, 스크립트는 만든 사람 PC에만 있어 멤버 PC에서는 script.py가 "스크립트가 없습니다"):
- MCP: list_flows(프로젝트의 흐름 목록과 연결된 스터디), clone_study(study_id, name, 바꿀 값: 변수·범위·출력·목적·어댑터 설정을 복사하고 케이스는 복사 안 함). INSTRUCTIONS에 "신규 모델로 지난번처럼" 규칙: 흐름·스터디·스크립트를 찾아 → 새 모델을 inspect해 옛 모델과 달라진 점(부품·면 이름, 변수 범위, 단위)을 먼저 알리고 → 새 스터디로 진행. 옛 스터디·모델·Simulator는 건드리지 않는다. 해석을 새로 돌리므로 Flow 승인은 받는다.
- 화면: 작업 흐름 탭에 "이 흐름을 새 모델로 다시" → 대화 입력창을 채우고 파일 첨부를 안내(prefillAgent 재사용). 같은 기능의 버튼을 두 곳에 만들지 않는다.
- 레시피 서버 저장: 스터디의 스크립트·adapter_config를 서버에 작은 묶음으로 올려(크기 제한, 비밀값 금지) 다른 멤버 PC가 내려받아 이어 쓸 수 있게 한다. 다른 사람이 쓴 스크립트를 내 PC에서 실행하는 것이므로 실행 전 Flow 승인에 "○○님이 만든 스크립트"로 보여 준다. 스크립트가 PC에 없으면 내려받기로 복구한다. 입력 모델은 첨부로 올린 것만 받을 수 있고 없으면 무엇이 필요한지 안내한다.
- 시나리오 E15 "신규 모델로 지난 흐름 다시"(옛 결과 불변 확인), E16 "다른 멤버가 내 PC 없이 이어받기"(내 PC 꺼짐 상태에서: 결과 조회·학습·Simulator 가능, 재해석은 멤버 PC에서 레시피로, 내 원본이 필요한 Output 추가는 이유와 할 일을 안내).
- 문서: AGENTS.md와 local-data.md가 "서버에는 숫자·이미지·필드·보고서만"이라고 줄여 쓰지만 실제로는 대화 첨부 원본과 500MB 이하 case.h5도 서버에 올라간다. 사실대로 고치고, 멤버가 볼 수 있는 범위를 product-spec에 표로 적는다.
표 입력 최종 결정(사용자 2026-10-31, 아래 내용보다 우선): "results.json, case.h5 이런 형식은 보안 프로그램과 상관없습니다. 표 데이터는 파일 선택 방식이 아니라 프롬프트창에 붙여넣던지, 표를 붙여넣을 수 있는 입력 창을 띄워서 붙여넣으면 알아서 인식하고 output이 뭔지만 사용자에게 물어보면 될것 같습니다."
→ 표 데이터의 파일 선택 UI 없음. 입구는 프롬프트창 붙여넣기와 붙여넣기 입력 창 둘뿐. 열 매핑 화면 없음. 자동 인식 후 질문은 "어느 열이 출력인가" 하나(추측값 미리 선택), 나머지 숫자 열은 입력. 버튼 하나로 생성·학습·배포·Simulator 열기. 결과 폴더 가져오기(results.json·case.h5)는 그대로.
표 입력은 복사·붙여넣기가 기본(사용자 요구 2026-10-31: "우리 회사는 csv, 엑셀 등은 모두 보안 프로그램이 붙어서 암호화 되어 있습니다. 표로 바로 Simulator 만드는 경우는 테이블 형태를 복사/붙여넣기로 진행하도록 해줘야 합니다."):
- 화면의 첫 입력은 붙여넣기 칸이다. 엑셀에서 복사한 탭 구분 글(text/plain)을 받아 서버의 같은 미리보기·등록 API로 보낸다. 파일 선택은 보조로 낮춘다.
- 암호화 파일 판별: CSV·XLSX 파서·inspect_table·import_table·scan_results가 읽지 못하면(XLSX인데 zip이 아님, 글자가 아닌 바이트, 알 수 없는 머리) 500이나 깨진 표 대신 "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요"로 답한다. AI도 파일을 못 읽으면 붙여넣기를 안내하고 추측하지 않는다.
- 대화 입력창에 표를 붙여 넣으면 AI를 거치지 않고 표로 바로 Simulator 화면을 열 수 있게 하고, AI로 보낼 때도 행을 AI 인자로 나르지 않게 첨부처럼 서버에 저장한다. PC의 암호화 파일을 전제로 한 시나리오는 붙여넣기로 바꾼다.
- 엑셀 복사의 형식을 처리: 천 단위 쉼표, 퍼센트, 빈 행·열, 따옴표 안 줄바꿈, 앞뒤 공백, 병합 셀로 생긴 빈칸, 수식 오류(#N/A, #DIV/0!), 표시 형식 때문에 줄어든 자릿수 경고, 머리글 없는 표는 머리글을 입력받기. 크기 한도와 초과 시 안내.
시나리오 검증:
- docs/entry-points.md: 시나리오 표(E0~) + 통과 기준 + 자동 시험 위치.
- E0 처음부터(대조군), E1 CSV→Simulator(해석 0건, Agent 없이), E1b XLSX·CP949·제목 줄·단위 머리글·한글 변수, E2 해석 폴더 N개→Simulator(재해석 0건), E2b 입력·출력 파일 분리, E3 표+행 추가→v2·공유 링크 따라감, E3b 같은 표 두 번(중복 없음), E4 표 모델→최적화(후보만, 확인 안 됨 표시), E5 나쁜 표(NaN·상수·문자·3행 미만·빈 파일·이상치) 500 없음, E6 Simulator→Output 추가·되돌리기, E7 결과만→보고서(Flow 없이), E8 단순 조회(Flow 0), E9 툴 missing+표 데이터, E10 Flow 검토 중 다른 요청, E11 세션 유지(스트림 A와 연결), E12 DOE 계획만→나중에 해석 연결, E13 필드 결과→3D Simulator, E14 다른 프로젝트 스터디 조회.
- server/tests/test_entry_points.py(API), agent/tests/test_mcp_entry_points.py(TestClient로 dds_study 직접 호출), scripts/e2e_scenarios.py(실제 프로세스+데모 대화), pc_verify chat 개선(--attach, --session, --expect, 측정값 저장, 합격 판정, POST 제한 수정, 현재 대화 Flow만 승인).
