# DDS Conversa — 디자인·기능 명세서

> 대상: 이 저장소를 이어서 개발하는 사내 AI 코딩 에이전트와 담당 엔지니어
> 기준: 2026-10-30, PC Agent 0.2.9, 커밋 `57ae8da` 이후
> 개발 규칙은 [AGENTS.md](../AGENTS.md)를 따른다(검증 → 한국어 커밋 → 버전 올림). 이 문서와 코드가 다르면 **코드가 맞다**. 고친 뒤 이 문서도 함께 고친다.

## 0. 이 문서를 쓰는 법
| 하려는 일 | 먼저 볼 절 |
|---|---|
| 화면을 고치거나 새로 만든다 | 2절(디자인 원칙·토큰·레이아웃) → 3절(화면별 명세) |
| 기능을 고치거나 늘린다 | 4절(기능 명세) → 5~7절(데이터·API·MCP) |
| AI 에이전트 동작을 바꾼다 | 8절 → `agent-context/INSTRUCTIONS.md` |
| 다 했는지 확인한다 | 10절(수용 기준·검증) |
| 남은 일 | 11절 |

관련 문서
- 사내 AI 지침(요구사항·다음 일감): [inhouse-ai-guide.md](inhouse-ai-guide.md)
- 인수인계: [handoff.md](handoff.md)
- 사내 서버 준비: [onsite-ai-runbook.md](onsite-ai-runbook.md)
- 실제 툴 검증: [onsite-validation.md](onsite-validation.md)
- 스킬 규약: [skill-contract.md](skill-contract.md)
- 해석 데이터 HDF5: [cae-data-hdf5.md](cae-data-hdf5.md)
- 디자인 개편 계획: [design-renewal-plan.md](design-renewal-plan.md)
- PC 테스트 통로: [pc-test-bridge.md](pc-test-bridge.md)

---

## 1. 제품 개요
### 1-1. 목적
사내 CAE 엔지니어가 **말로 요청하면 AI 에이전트가 CAD/CAE 툴을 다룬다.** 다음 과정을 이어서 처리하고 결과를 회사 자산으로 남긴다.
- 모델링 → 해석 → 결과 추출
- DOE → 예측 모델 → 최적화
- 3D Simulator → 보고서

PIDO 툴의 스크립트 작성과 프로세스 연결을 사람 대신 AI가 맡는다.

### 1-2. 사용자
| 사용자 | 하는 일 | 특성 |
|---|---|---|
| CAE 엔지니어(프로) | 요청·승인·결과 확인 | AI 전문가가 아님. 화면 문구는 쉬운 한국어로 쓴다 |
| 프로젝트 관리자(master) | 멤버 관리, 프로젝트 이름·삭제 | 프로젝트마다 정해진다 |
| 시스템 관리자(admin) | 사용자 승인, PC Agent 배포·모니터링 | 사내 서버 운영 |
| 공유받은 사람 | 배포된 Simulator로 예측만 | 프로젝트 멤버가 아니어도 된다(로그인 필요) |

### 1-3. 구성
```
[브라우저] ──HTTP/WS──> [사내 서버(Linux·Windows): FastAPI + React 빌드본 + SQLite, 포트 7014]
                             │ 학습·최적화·Simulator·보고서, 프로젝트·대화·결과·해석 데이터(case.h5) 저장
                             │ WebSocket /ws/agent (사용자 토큰)
                             ▼
                      [사용자 PC: PC Agent]
                             ├─ AI CLI(Claude Code·Gemini CLI·Codex)를 ACP로 구동 ── MCP 서버(dds-flow·cae·study·live)
                             ├─ CAD/CAE 툴 실행(스킬 스크립트) · 케이스 실행(로컬/HPC) · 결과·case.h5 업로드
                             └─ 프로젝트 작업 폴더(DDS_Workspace\P0001_…)
```
- AI CLI와 CAD/CAE 툴은 **사용자 PC**에서 돈다.
- 서버에는 숫자 결과·이미지·3D 필드·보고서·case.h5만 올라간다. 큰 원본 결과 파일은 PC에 남는다.
- 사용자마다 PC Agent는 1개만 연결된다(마지막 연결 우선).
- 인터넷 없는 사내망이 전제다. 외부 CDN·글꼴·패키지를 받지 않는다(사내 pip·npm 미러만 쓴다).

---

## 2. 디자인 명세
### 2-1. 원칙
1. **Claude·Codex 데스크톱 앱 방식**을 따른다.
   - 왼쪽은 사이드바, 가운데는 대화, 오른쪽은 캔버스다.
   - 단계(depth)는 최소로 한다.
   - 같은 기능의 버튼은 한 곳에만 둔다.
2. **결과는 캔버스, 대화는 짧게.**
   - 대화에는 사람이 읽을 요약(3~5줄)과 승인·확인 카드만 남긴다.
   - AI의 생각과 툴 호출은 "작업 N건" 한 줄로 접어 둔다.
3. **상태는 한 곳에서 보인다.**
   - PC Agent 연결 상태는 사이드바 아래에 둔다.
   - 작업 흐름 진행은 흐름도와 대화 카드에 보인다.
4. **오류에는 원인과 할 일을 함께 쓴다.** 예: "PC Agent가 연결되어 있지 않습니다 → 내 PC에서 DDS Agent를 실행하세요".
5. **문구**
   - 한국어로 짧게 쓴다. "무엇을 하면 무엇이 된다"가 보이게 한다.
   - 영어 약어는 처음 나올 때 풀어 쓴다(예: "R²(예측이 실제를 얼마나 잘 따르는지)").
   - 호칭은 "프로"를 쓴다. "경영진 보고" 같은 표현은 쓰지 않는다.
6. **데스크톱(1440×900) 기준.** 모바일은 깨지지 않는 정도만 맞춘다.
7. **색만으로 뜻을 전하지 않는다.** 기호(μ·σ·±)나 글자를 함께 쓴다.
   - 호박색은 확인이 필요한 일(승인 대기·경고)이다.
   - 주홍은 주 동작·선택·링크·진행이고, 청록은 AI·출력 값이다.
8. **무대와 작업대.**
   - 무대(로그인, 첫 화면, 빈 화면, 보고서 표지, Simulator 머리): 큰 글자, 3D·선 그래픽, 차례로 드러나는 등장(0.4~0.8초).
   - 작업대(대화, 작업 흐름, 표, 설정): 같은 재료를 쓰되 차분하게. 움직임은 상태가 바뀔 때만(0.12~0.24초).
   - 재료: 종이색 바탕, 잉크, 주홍 하나, 화면을 나누는 얇은 선, 고정폭 머리말·숫자.
9. **움직임 규칙.**
   - 기본 CSS가 최종 모습이고 `@keyframes`는 시작만 정한다. '움직임 줄이기'면 그대로 정지 화면이 된다.
   - 새로 생긴 것만 등장한다. 기록을 열거나 새로 고칠 때, 대화를 바꿀 때는 재생하지 않는다.
   - 투명도·위치 이동만 움직인다. 눌러야 하는 요소 자체는 끝없이 움직이지 않는다(고리는 `::after`에).
   - 일하는 화면에 넣지 않는 것: 표 행 등장, 값이 올라가는 숫자, Plotly 전환, 해석 3D 자동 회전·변형, 패널 폭 애니메이션, AI 답변 한 글자씩.
   - 어디에도 넣지 않는 것: 스크롤 가로채기, 커스텀 커서, 영상 배경, 닫힘 애니메이션, GSAP·Framer Motion·Lenis 같은 새 라이브러리.

### 2-2. 토큰 (`frontend/src/design/tokens.css` — 단일 원본)
| 분류 | 값(밝은 테마) | 쓰는 곳 |
|---|---|---|
| 글꼴 | `--font` Pretendard Variable(앱에 포함) → 맑은 고딕 · `--font-mono` Geist Mono Variable(앱에 포함, OFL) → Consolas | 전체. 고정폭은 머리말·숫자·경로에. 고정폭에는 한글이 없어 대체 글꼴로 나오므로 한글 머리말은 `word-spacing`을 좁힌다 |
| 글자 크기 9단계 | `--fs-11` · `-12` · `-13` · `-14` · `-16` · `-20` · `-28` · **`-40` · `-64`(무대 전용)** | 11 머리말·눈금 · 12 메타·칩 · 13 사이드바·표 · 14 본문 · 16·20 구획 제목 · 28 화면 제목 · 40 첫 화면·표지·Simulator · 64 로그인 |
| 글자색 | `--ink` #1c1c1a · `--ink-2` #45443f · `--ink-3` #6a6860 · `--ink-4` #9a988e | 본문·보조·메타·자리 표시 |
| 바탕 | `--paper` #f4f3ee(앱, `--grain` 종이 무늬) · `--side-bg` #efede7 · `--panel` #fbfaf6 · `--panel-2` #f0eee8 · `--panel-3` #e7e5dd | 종이색. 구획은 선으로 나눈다 |
| 선 | `--line` #d8d6ce · `--line-2` #e6e4dc · `--line-strong` #b9b6ac(입력칸) | 구획선·입력칸 |
| 강조 | `--brand` #c2380d 글자·선택·링크 · `--brand-solid` #d63f10 주 버튼(어두운 테마 #ff5a2b에 어두운 글자 `--on-brand`) · `--brand-soft` | 주 동작 1개·선택·링크·진행에만 |
| 상태 | `--ok` · `--warn` · `--err` #b3122f · `--info` #0f766e (각 `-soft` 바탕 · `-ring` · `-ink` 글자, ok·warn은 `-solid` 점·막대) | 칩·알림. 호박색은 확인이 필요한 일, 빨강은 항상 아이콘·글자와 함께 |
| 데이터 의미 | `--mean` 청록(평균 μ) · `--sigma` 보라(산포 σ) · `--epi` 회청(모델 불확실성) | 그래프·Simulator. 출력(Output) 묶음 색도 `--mean` |
| 여백 | `--sp-1`~`-7` = 4·8·12·16·24·32·48 px | 새 규칙의 `gap`·`padding` |
| 둥글기 5단계 | `--r-xs` 2 · `--r-s` 3 · `--r-m` 6 · `--r-l` 10 · `--r-pill` 999 | 체크박스 · 버튼·입력·칩·흐름도 단계 · 카드 · 입력창·모달 · 알약(개수 표시 등). 원은 50% |
| 그림자 | 카드 없음 · `--shadow-pop` 메뉴·알림 · `--shadow-modal` 모달 | 떠 있는 것에만 |
| 움직임 | 조작 `--dur-fast` 120 · `--dur` 180 · `--dur-slow` 240 ms, 무대 `--dur-reveal` 600, 강조 `--dur-flash` 800, 맥박 `--dur-pulse` 1600, 기다림 `--dur-wait` 150 · `--ease`, 등장 `--ease-out` | 시간 하드코딩 금지(`test_motion_uses_tokens`). '움직임 줄이기'면 모두 꺼진다(`tokens.css` 한 곳) |
| 크기 | `--ctl-s` 28 · `--ctl` 32 · `--ctl-l` 40(버튼·입력 높이) · `--row-h` 34(표 한 줄) | |

- 다크 모드: `:root[data-theme="dark"]`에서 토큰만 다시 정의한다. 값은 모두 `tokens.css` 한 곳에 있다.
- CSS 층: `styles.css` → `layout.css` → `app.css` → `agentic.css` → **`modern.css`(마지막 층)**. 새 스타일은 `modern.css` 끝에 덧붙인다.
- 그래프·3D 보기는 `chartColors()`(`components/Plot.tsx`)와 `token()`(`design/token.ts`)으로 토큰 색을 읽는다.
- **자동 검사** `server/tests/test_design_tokens.py`: CSS 층에 hex 색·px 글자 크기·px 둥글기를 쓰면 실패한다. 없는 토큰을 쓰거나, 두 테마에서 글자 대비가 4.5:1 미만이어도 실패한다.
- 근거와 적용 범위: [design-renewal-plan.md](design-renewal-plan.md)

### 2-3. 레이아웃
| 영역 | 기본 폭 | 범위 | 단축키 | 비고 |
|---|---|---|---|---|
| 사이드바 | 256 px | 200~480 | Ctrl+B | 새 프로젝트·검색·프로젝트 목록, 아래에 PC Agent 상태·계정 메뉴(CAD·CAE 툴·설정·관리·어두운 화면·로그아웃). 접으면 아이콘만(CAD·CAE 툴·설정은 아래쪽) |
| 대화 | 나머지 | — | — | 가운데 열(최대 폭 고정), 아래 입력창 |
| 캔버스 | 560 px | 360~1100 | Ctrl+J | 끌어서 폭 조절·두 번 누르면 기본값, 닫으면 완전히 사라짐 |

### 2-4. 공통 구성 요소
| 요소 | 규칙 |
|---|---|
| 버튼 | `primary`(주홍, 화면당 주 동작 1개) · 기본 · `ghost` · `small`. 높이 32(small 28, big 40), 둥글기 3, 누르면 .98. 아이콘만 있는 버튼은 뜻이 분명할 때만(닫기·삭제 등) 쓰고 `title`을 단다 |
| 칩(상태 태그) | `chip ok/warn/err/info` — 둥글기 3, 테두리 없음, 옅은 바탕 + 진한 글자, 아이콘이 없으면 앞에 점 |
| 카드 | `panel` — 선 테두리·둥글기 6, 그림자 없음. 하나의 물건(대화 카드·입력창)일 때만 상자로 둔다. 작업 흐름의 결과·데이터·Simulator·툴은 상자 없이 선으로 나눈다 |
| 표 | 한 줄 34px, 머리글 고정·고정폭, 숫자 오른쪽 정렬·고정폭, 줄무늬·세로선 없음, 복사 버튼(엑셀 붙여넣기) |
| 입력칸 | 높이 32, 둥글기 3, `--line-strong` 선, 포커스되면 `--brand` 선 + 3px 고리 |
| 알림 | `notice-inline warn/info` — 원인 → 할 일 |
| 빈 화면 | `empty-hero` — 에이전트 기호 + 한 줄 제목 + 무엇을 하면 무엇이 되는지 + (있으면) 시작 버튼. 검색 결과 없음 등은 아이콘 |
| 에이전트 기호 | `components/AgentGlyph.tsx` — 고리처럼 놓인 점 8개. 대기·생각·작업·완료마다 다르게 움직인다. 첫 화면, 생각 표시, 보고서 작성 중에 같은 것을 쓴다 |
| 글자 로고 | `Wordmark`(같은 파일) — DDS + Conversa + 주홍 점 |
| 뼈대 화면 | `components/Skeleton.tsx` — 글 없는 회전 표시 대신. `--dur-wait` 뒤에 나타나 빠른 로딩에서는 보이지 않는다 |
| 토스트 | 성공·실패 한 줄 |
| 확인 창 | 삭제 등 되돌릴 수 없는 일에만(`Confirm`) |

---

## 3. 화면 명세
### 3-1. 로그인 (`/login`)
- 화면 전체를 선으로 나눈 한 장이다. 위: 글자 로고. 왼쪽: 큰 제목(`--fs-64`, 줄마다 등장)과 **실시간 3D 브래킷**(`components/HeroMesh.tsx`). 오른쪽: 로그인 입력(지연 없이 바로 나타남). 아래: 일하는 흐름 다섯 칸(1 요청 → 2 AI 계획 → 3 승인 → 4 실행 → 5 보고서, 주홍은 엔지니어, 청록은 AI). 차례로 불이 들어오고 입력을 시작하면 멈춘다.
- 3D는 두께·리브 높이가 천천히 바뀌며 형상·응력 색과 아래 값(두께·리브·질량·최대 응력)이 함께 바뀐다. 식은 데모 해석과 같고(`cae_adapters/mock.py`) "예시 모델"로 표시한다. WebGL을 못 쓰거나 '움직임 줄이기'면 정지 선 그림이다.
- PIN이 틀리면 입력 창이 짧게 흔들린다.
- 아이디와 6자리 숫자 PIN으로 로그인한다.
- 가입한 사람은 관리자가 승인한 뒤에 로그인할 수 있다.
- PIN을 5번 틀리면 잠긴다. 관리자가 PIN을 초기화하면 풀린다.

### 3-2. 첫 화면 (`/`, DashboardPage)
- 에이전트 기호, 큰 제목(`--fs-40`, 줄마다 등장), 설명 한 줄 아래에 **요청 입력창을 가운데** 둔다. 처음부터 입력창에 커서가 있다. 비어 있으면 예시 요청이 타이핑되고 입력을 시작하면 멈춘다(→ 키는 예시 전체를 받는다).
- 입력창 아래 **'옵션' 한 줄**에 프로젝트 이름·내 PC 작업 폴더를 접어 둔다("이름 자동 · 기본 작업 폴더"). 비워 두면 자동으로 정해지고, 폴더 오류가 나면 저절로 펼쳐진다.
- 그 아래 빠른 시작 5칸(선으로 나눔), 지원 툴 수 한 줄, 최근 프로젝트 줄(상자 없이, 올리면 화살표가 밀려 나옴)을 차례로 등장시킨다.
- 요청을 보내면 다음이 차례로 일어난다.
  1. 프로젝트가 자동으로 만들어진다. 이름은 요청 앞부분이고, AI가 첫 응답 때 짧은 이름으로 바꾼다.
  2. 작업 공간으로 이동한다.
- 첨부 파일은 프로젝트 작업 폴더 `inputs/`로 내려간다.
- PC Agent가 연결되지 않았으면 설치 안내를 먼저 보인다.

### 3-3. 작업 공간 (`/projects/:pid[/c/:sid]`, WorkspacePage)
머리글: 프로젝트 이름 / 대화 이름(대화 바꾸기·새 대화·이름 바꾸기·삭제) · 공유(멤버) · 캔버스 열기.

**대화(ChatView)**

| 항목 | 모양 |
|---|---|
| 내 메시지 | 오른쪽 말풍선 + 첨부 칩 |
| AI 답변 | 왼쪽 마크다운. 3~5줄 요약 |
| AI 생각·툴 호출 | 한 줄 묶음 "✓ 작업 N건 · 자세히". 진행 중이면 "스크립트 실행 중…", 실패가 있으면 "· 실패 n". 생각만 있는 묶음은 숨긴다 |
| 작업 흐름 카드 | 단계 번호 목록(1, 2, 3…)과 진행률. 승인 대기면 **[반려] [승인하고 실행]**. "흐름도" 버튼은 캔버스를 연다 |
| 실행 확인 카드 | "내 PC에서 실행해도 될까요?" — 쉬운 이름과 입력값 표, 허용·거부 |
| 결과 칩 | "결과 N개" 목록(4개 + 더 보기). 누르면 작업 흐름 탭의 그 단계 결과로 이동한다 |
| 진행 표시 | "OO 진행 중 · 3/7단계" 또는 "생각하는 중…" |

입력창(Composer)
- Enter로 보내고 Shift+Enter로 줄을 바꾼다.
- → 키로 상황별 추천을 받는다(회색 글자).
- 파일은 버튼·끌어 놓기·붙여넣기로 첨부한다.
- AI 에이전트를 고를 수 있다(Gemini·Claude·Codex·데모).
- 다른 화면이 입력창을 채울 수 있다(`prefillAgent`). 예: 데이터 탭의 'Output 추가'.

다른 멤버의 대화는 읽기 전용이다. 보내면 내 새 대화로 시작한다.

**캔버스(CanvasPanel) — 탭 4개**

| 탭 | 내용 |
|---|---|
| 작업 흐름 | 3-4절 |
| 보고서 | 3-5절 |
| 데이터·모델 | 3-6절 |
| 파일 | 내 PC 프로젝트 폴더 탐색. 폴더 열기, 결과 파일을 CAE 뷰어로 열기 |

- 실시간 따라가기: 에이전트가 새로 만든 것으로 탭이 자동으로 바뀐다. 사용자가 방금(8초 안에) 직접 탭을 골랐으면 바꾸지 않는다.
- 탭을 바꾸면 스크롤은 맨 위에서 시작한다.

### 3-4. 작업 흐름 탭
```
┌ 제목 [완료]  목표 · 단계 7/7 완료 · 오늘 11:16     (흐름이 여럿이면 위에 고르기)
├ 진행 막대
├ 흐름도(고정, 높이 최대 40vh) — 작은 노드: [번호] 이름 [상태] / 종류 · 결과 n
├ "단계를 누르면 그 단계의 결과로 이동합니다 · 결과 N개"
└ 단계별 결과(이 부분만 스크롤)
   [1단계] 이름 [종류·툴] [상태]  (실패면 '원인 찾기')
   설명(detail)
   결과 카드들(표·그래프·이미지·요약) — 각 카드에 시각·삭제
   … 마지막에 [기타] 단계에 묶이지 않은 결과
```
- **단계 번호**는 `frontend/src/flow/order.ts`의 `orderNodes`로 매긴다. 엣지 기준 위상 정렬이고, 같은 순위는 원래 순서다.
  - 서버 `report.order_nodes`도 같은 규칙이다.
  - 이 번호를 흐름도·단계별 결과·대화 카드·보고서가 함께 쓴다.
- 흐름도 노드를 누르면 그 단계 섹션으로 부드럽게 스크롤한다. 선택한 단계는 번호를 주홍으로, 머리글 선도 주홍으로 표시한다.
- 노드 상태 색
  - 완료: 초록 체크
  - 진행: 주홍 고리가 퍼진다(맥박)
  - 실패: 빨강
  - 대기: 회색 점
  - 건너뜀: 흐리게
- 승인 대기 흐름에는 "대화의 카드에서 승인하거나 반려하세요"를 띄운다.

### 3-5. 보고서 탭
- 보고서는 **AI가 작업 흐름·결과·스터디를 읽고 새로 쓴 문서**다. 결과를 모아 둔 것이 아니다.
- 머리글
  - 왼쪽: 작성 시각과 반영한 결과 수, 이전 판 고르기(판이 2개 이상일 때만)
  - 오른쪽: **[AI 보고서 작성]**(primary), Word | HTML
- 알림
  - 작성 중: "AI가 최신 결과로 보고서를 다시 쓰고 있습니다" + 탭 이름 옆 작은 회전 표시
  - 쓴 뒤 새 결과가 생기거나 바뀜: "새로 생기거나 바뀐 결과가 n개 있습니다 → 'AI 보고서 작성'으로 반영하세요"
- 본문(흰 문서 카드, 쪽 폭 최대 720px)
  - 제목과 마크다운(요약·목적과 조건·수행 내용·결과·결론과 권고·한계)
  - 결과는 그림·표로 들어간다. 캡션에 **"그림 1." / "표 1."** 번호가 자동으로 붙고, "단계에서 보기" 링크가 있다
- 보고서가 없을 때: 빈 화면 설명과 [AI 보고서 작성]. 결과가 하나도 없으면 버튼이 꺼진다.
- 버튼을 누르면
  1. "최신 결과로 보고서를 다시 작성해 줘"가 대화로 전송된다.
  2. 새 판이 오거나 에이전트 차례가 끝나면 작성 중 표시를 거둔다(15분이 지나도 거둔다).

- 화면 표현: 표지(작은 머리말 + 큰 제목 + 잉크 선), 상자 없는 본문, 그림 번호는 고정폭 주홍. 새 판이 오면 표지부터 다시 등장한다. Word·HTML 파일의 모양은 바꾸지 않는다.

### 3-6. 데이터·모델 탭
스터디가 여럿이면 위에서 고른다. 구역은 위에서부터 다음 순서다.

1. **현재 최적**
   - 설계변수 값을 큰 숫자로, 목적값을 주홍으로 보인다.
   - 케이스 진행 막대와 상태별 수, 실행 중·Output 추출 중 칩을 둔다.
   - 그 아래 "해석 데이터(HDF5) 68/68건 저장 · 서버 68건 2.8 MB · PC에만 n건" 줄이 있다.
2. **입력·출력**(두 열)

   | 열 | 표시 | 내용 |
   |---|---|---|
   | 입력 · Input | 주홍 표시 | 변수 이름과 범위 |
   | 출력 · Output | 청록 표시 | 이름 · 역할(목적·최소/최대, 제약 ≤ 값) · 설명(단위) · 값이 있는 완료 케이스 수(n/전체, 모자라면 주황) |

   - **[+ Output 추가]**는 입력창을 "'스터디' 스터디에 Output 추가: "로 채운다. 사용자가 이름을 적어 보낸다.
3. **예측 모델 · Simulator**
   - 최근 3개와 배포한 모델만 보인다. 모델마다 출력별 R² 칩(0.9 이상 초록, 0.7 이상 주황, 그 밖 빨강)과 학습 수를 둔다.
   - 배포된 모델은 [Simulator 열기], 아니면 [배포 요청]이다.
4. **케이스 표**
   - 머리글은 두 줄이다(입력 묶음 주홍 / 출력 묶음 청록 + 항목 이름). 둘 다 고정된다.
   - 열: ID · 출처(DOE·최적화·능동학습·직접) · 상태 · 입력들 · 출력들 · HDF5 · 폴더 열기
   - 출력 칸: 추출 중이면 작은 회전, 값이 없으면 빈칸
   - HDF5 칸: 서버에 있으면 크기 칩(눌러서 받기), PC에만 있으면 "PC · 크기"(위치는 툴팁), 오류면 빨강
   - 최적 행은 강조한다. 오류가 있으면 툴팁으로 보인다.

### 3-7. Simulator (`/simulator/:mid`, 공유 `/s/:token`)
- 설계변수 슬라이더로 예측 결과를 바로 본다(평균 ± 표준편차). [가운데로]는 모든 변수를 범위 가운데로 돌린다.
- 예측값은 크게(`--fs-40`, 고정폭) 보인다. 값이 오래됐을 때만 흐려지고(`--dur-wait` 뒤), 늦게 온 옛 응답은 버린다.
- 민감도: 한 변수만 바꿀 때의 변화 곡선. 주홍 점이
- 형상·필드 모델이 있으면 3D 분포와 불확실도를 보인다.
- 공유
  - 링크를 만들 때 기간과 메모를 정한다. 복사와 중지가 있다.
  - 공유받은 사람은 예측만 할 수 있다.

### 3-8. CAD·CAE 툴 (`/tools`)
- 툴 29종을 분야별 앱 아이콘으로 보인다. 상태는 셋이다: 바로 사용 · 일부만 찾음 · 찾지 못함(흐리게).
- 툴을 누르면 서랍이 열린다: 할 수 있는 일, 내 PC 실행 파일, 실행 파일 고르기, 자동으로 되돌리기.
- [다시 찾기]로 내 PC를 다시 훑는다. "내 PC에 있는 것만" 필터가 있다.

### 3-9. 설정 (`/settings`)
구획마다 왼쪽에 제목·설명, 오른쪽에 내용을 두고 옅은 선으로 나눈다(상자를 쌓지 않는다).
- 내 PC Agent
  - 연결 상태와 버전을 보인다.
  - 설치: 설치 파일 받기, 또는 PowerShell 한 줄
  - 토큰 발급·폐기. 토큰 원문은 발급할 때 한 번만 보인다.
- AI 에이전트: CLI별 설치·로그인 안내
- 내 PC 진단, 결과 뷰어 등록 상태, 로그인 PIN 바꾸기

### 3-10. 관리 (`/admin`, 관리자)
| 탭 | 내용 |
|---|---|
| 사용자 | 승인, 사용 중지, PIN 초기화 |
| Agent 배포 | 설치 파일 올리기, "서버 코드로 첫 버전 배포", 최신 지정, 변경 내용 |
| PC Agent 현황 | 연결 PC, 버전, 실행 중 케이스, 원격 업데이트 |

---

## 4. 기능 명세
각 기능은 다음 순서로 쓴다: 무엇을 → 누가·어떻게 → 처리 → 결과·화면 → 코드.

### 4-1. 계정·권한
- **무엇을:** PIN 로그인(PBKDF2 해시), 가입 승인제, 5회 실패 잠금
- **처리**
  - API 토큰(사용자 PC Agent·MCP용)은 해시만 저장한다.
  - 프로젝트 권한은 master(관리)와 member(사용)다.
  - 다른 멤버의 PC에서 해석한 케이스 폴더는 열 수 없다.
- **코드:** `routers/auth.py` · `deps.py` · `routers/projects.py`

### 4-2. 프로젝트·대화
- **처리**
  - 프로젝트: 만들기·이름 바꾸기·즐겨찾기·멤버·삭제
  - 대화: 프로젝트 안에 여러 개, 에이전트 바꾸기(이전 대화 요약을 넘겨 이어 감)
  - 첨부는 서버에 두었다가 PC Agent가 `inputs/`로 내려받는다.
  - 프로젝트 작업 폴더는 PC마다 다르다. 서버에 저장하지 않고 PC의 `index.json`에 둔다.
- **코드:** `routers/projects.py`·`sessions.py` · `agent/hub.py` · `agent/dds_agent/workspace.py`

### 4-3. 작업 흐름(Flow)
- **상태:** `review`(승인 대기) → `approved` → `in_progress` → `complete` | `failed` | `rejected`
- **처리**
  1. AI가 `propose_flow`로 단계(노드)와 연결(엣지)을 제안한다.
     - 노드 종류: cad·pre·solve·post·doe·train·optimize·deploy·task
     - 빠진 조건은 기본값을 정해 노드 detail에 적는다.
  2. 사용자가 승인하면 "진행하세요" 메시지가 간다.
  3. AI가 `update_node`로 단계 상태와 detail을 갱신한다.
  4. 노드 상태로 흐름 상태가 자동으로 정해진다.
- **결과:** 대화 카드와 작업 흐름 탭(3-4)
- **코드:** `routers/flows.py` · `mcp_servers/dds_flow.py` · `flow/FlowCanvas.tsx`·`order.ts`

### 4-4. 결과(아티팩트) 게시
- **종류:** table · scatter · line · bar · parallel · markdown · image
- **처리**
  - 같은 프로젝트에 같은 제목으로 다시 게시하면 갱신한다(upsert). 이때 `updated_at`이 바뀐다.
  - `flow_id`·`node_id`를 넣어야 그 단계 아래에 보인다.
  - 스터디가 자동으로 게시하는 결과(DOE 분포·케이스 표·수렴 이력·예측 정확도)는 그 스터디가 마지막으로 쓴 단계에 붙는다.
- **코드:** `study/service.publish_artifact` · `artifacts/ArtifactView.tsx`

### 4-5. AI 보고서
- **처리**
  1. 'AI 보고서 작성'(또는 대화 요청)을 받는다.
  2. AI가 `get_report_context`로 자료를 읽는다: 흐름(단계 번호)·결과 요약(표 12행·그래프 범위)·스터디(입력·출력 역할·최적·R²)·이전 판.
  3. 해석한 내용을 `write_report(title, body)`로 저장한다.
     - 본문 결과는 한 줄에 `![캡션](artifact:<id>)`로 넣는다.
     - 프로젝트에 없는 id를 넣으면 400 오류가 난다.
  4. 저장하면 새 판이 쌓이고 `report_updated` 이벤트가 간다.
- **Word·HTML:** 최근 판(또는 고른 판)으로 만든다.
  - 그림은 matplotlib으로 다시 그리고 "그림 n."·"표 n." 번호를 붙인다.
  - 보고서가 없으면 게시된 결과를 차례로 모은다(이전 방식).
- **코드:** `routers/reports.py` · `report.py` · `canvas/ReportView.tsx` · INSTRUCTIONS 1-0절

### 4-6. 스터디(설계 탐색)
- **흐름:** `create_study` → `generate_doe`(LHS·Sobol·random) → `run_cases` → `train_surrogate`(GP, 교차검증 R²·RMSE) → `optimize`(bo: EI×제약 확률 / al: 불확실도 최대) → `deploy_model`
- **제약:** 결과 제약을 만족하는 케이스 중에서 최적을 고른다.
- **실행 위치**
  - 케이스는 실행을 요청한 사용자의 PC Agent로 간다(`case.run`).
  - HPC는 `adapter_config.runner = "hpc"`다.
  - 개발용 mock은 서버에서 돈다.
- **어댑터:** `script`(툴 스크립트: params.json → 툴 실행 → results.json) · `mock`(예시 해석함수)
- **코드:** `routers/studies.py` · `study/*` · `cae_adapters/*` · `agent/dds_agent/executor.py`

### 4-7. 입력·출력과 Output 추가
- **입력:** 설계변수(variables)
- **출력:** 목적 → 제약 → 추가한 출력(`Study.outputs [{name, note, post}]`) → 그 밖의 결과 키 순서로 보인다.
- **Output 추가**("OO도 Output에 넣어 줘") — `add_outputs(study_id, outputs, post?)`

  | 대상 | 처리 |
  |---|---|
  | 이미 해석한 케이스 | 해석은 다시 하지 않는다. 그 케이스를 해석한 PC Agent에 `case.extract`를 보낸다. 케이스 폴더에서 추출 단계만 실행해 새 값을 기존 결과에 합친다 |
  | 이후 케이스 | 추출 단계가 `adapter_config.post` 끝에 붙어 해석할 때 함께 돈다 |
  | 추가할 때 실행 중이던 케이스 | 끝나는 대로 빠진 Output만 추출한다 |

  - 스크립트 어댑터는 단계마다 쓴 `results.json` 값을 모은다. 그래서 추출 스크립트가 새 키만 써도 기존 값이 지워지지 않는다.
  - 실패하면 케이스는 complete로 두고 오류를 "Output 추출 실패: …"로 남긴다.
  - 해석한 PC가 연결되지 않은 케이스는 건너뛴다(건너뛴 수를 알려 준다).
  - mock은 해석함수를 다시 계산한다(`adapter_config.outputs`로 처음 남길 출력을 정한다).
- **코드:** `study/service.add_outputs`·`extract_outputs_async` · `cae_adapters/script.py`·`mock.py` `extract()` · skill-contract 1-2절

### 4-8. 해석 데이터 저장(HDF5, case.h5)
- **무엇을:** 케이스마다 결과를 **빠짐없이, 압축해서** 한 파일에 담는다.
  - 담는 것: 메타·메쉬·스텝별 전 프레임 필드·시간 이력·작은 텍스트·이미지
  - manifest: 케이스 폴더의 모든 파일을 packed·raw·skipped로 적는다. 원본은 크기·sha256을 남긴다.
- **언제:** 케이스가 끝날 때와 Output 추가 추출 후, PC Agent가 묶는다.
  - 묶은 뒤 다시 읽어 검증한다.
  - `upload_max_mb`(기본 500) 이하면 서버에 올리고, 넘으면 PC에만 두고 정보만 보낸다.
- **쓰는 곳**
  - 3D 학습: npz가 없으면 case.h5에서 읽는다
  - Output 추가: 원본이 없을 때 case.h5에서 뽑는다
  - 데이터 탭 HDF5 열, `case_data` MCP
- **툴별 전체 추출 단계** (TODO(사내검증))
  - LS-DYNA `dyna_to_h5.py`
  - Nastran·OptiStruct `op2_to_h5.py`
  - Abaqus `abq_odb_to_npz.py`
- **코드·규약:** `cae_adapters/h5store.py` · [cae-data-hdf5.md](cae-data-hdf5.md)

### 4-9. 형상·필드(3D) 예측
- **입력:** 케이스 스크립트가 `fields/<이름>.npz`(points·values·triangles)를 남긴다.
- **처리**
  - PC Agent가 서버로 올린다.
  - `train_field_model`이 PCA+GP로 '설계변수 → 3D 형상·분포' 모델을 만든다.
  - 리메쉬된 케이스는 가장 촘촘한 메쉬 기준으로 보간한다.
- **코드:** `study/fieldmodel.py`

### 4-10. Live Link(열린 CAD/CAE 조작)
- **무엇을:** 실행 중인 툴(NX 등)에서 바로 작업한다.
  - 치수 바꾸기 `live_set_params`, 화면 `live_view`, 되돌리기 `live_undo`
  - 그 밖의 일은 `live_exec`. 캡처는 `live_capture`
- **처리:** NX는 세션 안 도우미 `ddslive_nx_ops.py`가 AI 호출 1번 + NX 실행 1번으로 끝낸다. 결과에 단계별 `ms`를 남긴다.
- **코드:** `mcp_servers/dds_live.py` · `agent-context/live/` · `.claude/skills/live-link-speedup`

### 4-11. PC Agent
- **설치·연결:** 설치 파일 또는 PowerShell 한 줄, 토큰으로 연결한다.
- **자동 업데이트:** 서버에 새 버전이 배포되면 유휴 시 업데이트한다. 같은 버전은 한 번만 배포한다.
- **스킬 동기화:** `agent-context`를 프로젝트 폴더로 복사한다.
- **그 밖:** 툴 자동 찾기(`toolscan`), 트레이 아이콘, HPC 실행(slurm·pbs 등)
- **코드:** `agent/dds_agent/*` · `installer/` · `scripts/install-agent.ps1`

### 4-12. AI 대화 정리 규칙(화면 쪽)
- 생각·툴 호출은 한 줄 묶음으로 접는다.
- 결과는 칩 목록만 보인다.
- 사람이 볼 필요가 없는 정보(경로·id·JSON)는 AI가 대화에 쓰지 않는다(8절).

---

## 5. 데이터 모델 (`server/app/models.py`, SQLite)
| 표 | 주요 열 | 비고 |
|---|---|---|
| users · api_tokens | username, pin_hash, is_admin, is_active, 잠금 | 토큰은 해시 |
| projects · project_members · project_favorites | uid(PC 폴더 확인용), name, description, role | |
| chat_sessions · chat_messages | agent, agent_session_id / role(user·agent·thought·tool·system), meta | |
| attachments | filename, sha256 | |
| flows | nodes[{id,label,type,tool,status,detail}], edges, status, version | |
| artifacts | kind, title, payload, flow_id, node_id, created_at, **updated_at** | 같은 제목이면 갱신 |
| **reports** | title, body(markdown + artifact embed), flow_id, created_by | 판이 쌓인다 |
| studies | variables, objectives, constraints, **outputs**, adapter, adapter_config | |
| cases | params, results, status, source, run_dir, run_by, images, fields, **archive**, error | archive는 case.h5 정보 |
| surrogate_models · field_models | outputs, metrics, n_train, deployed / field, n_nodes, shape_varies | |
| share_links | token, model_id, expires_at, enabled, uses | |
| agent_releases · agent_instances | 버전·파일 / 연결 PC·버전·상태 | |

새 열은 `database.ensure_columns()`가 기존 DB에 자동으로 추가한다(nullable만). 열을 지우거나 이름을 바꿀 때는 마이그레이션을 따로 쓴다.

---

## 6. 서버 API (주요)
| 분류 | 경로 |
|---|---|
| 인증 | `POST /api/auth/register·login` · `GET /api/auth/me` · `PUT /api/auth/pin` · `GET·POST·DELETE /api/tokens` · `GET·PUT /api/users` |
| 프로젝트 | `GET·POST /api/projects` · `GET·PUT·DELETE /api/projects/{p}` · 즐겨찾기 · 멤버 |
| 대화 | `GET·POST /api/projects/{p}/sessions` · `PUT·DELETE /api/sessions/{s}` · `POST …/prompt·cancel·permission` · `GET …/messages·status` · 첨부 |
| 작업 흐름 | `GET·POST /api/projects/{p}/flows` · `GET /api/flows/{f}` · `PUT …/status` · `PUT …/nodes/{n}` |
| 결과 | `GET·POST /api/projects/{p}/artifacts` · `DELETE /api/artifacts/{a}` · `POST /api/projects/{p}/files`(이미지) · `GET …/files/{path}` |
| 보고서 | `GET /api/projects/{p}/reports` · `GET …/reports/latest` · `GET /api/reports/{r}` · `POST /api/projects/{p}/reports` · `GET …/report-context` · `GET …/report?format=docx|html&report_id=` |
| 스터디 | `GET·POST /api/projects/{p}/studies` · `GET /api/studies/{s}`(counts·best·running·extracting) · `…/cases` · `…/doe` · `…/run` · `…/train` · `…/suggest` · `…/outputs` · `…/train_field` · `…/field-models` |
| 케이스 데이터 | `POST /api/cases/{c}/fields` · `POST·GET /api/cases/{c}/archive` · `GET …/archive/file` |
| 모델·Simulator | `GET /api/projects/{p}/models` · `GET /api/models/{m}/spec` · `POST …/deploy·predict·predict_batch` · 공유 `/api/models/{m}/shares`, `/api/shares/{t}/…` · 필드 `/api/field-models/{f}/mesh·predict` |
| PC Agent | `/api/agent/status·diagnose·install·tools…` · `/api/projects/{p}/local/folder·tree·open` · 배포 `/api/releases…` · `/api/instances…` · `/api/setup.exe`·`/api/install.ps1` |

실시간 이벤트(브라우저 `/ws`)
- `flow_updated` · `artifact_updated` · `report_updated`
- `study_updated` · `case_updated` · `model_updated`
- `project_updated` · `agent_event`(대화) · `agent_status`

PC Agent 메시지(`/ws/agent`, `server/app/agent/link.py` 머리 주석)
- 서버 → Agent: `session.new·load·prompt·cancel`, `permission.reply`, `case.run`, `case.extract`, `local.*`, `tools.*`, `agent.install·update`
- Agent → 서버: `hello`, `heartbeat`, `agent.event`, `agent.permission`, `case.status`(results·images·fields·archive, extract)

---

## 7. MCP 툴 (AI가 쓰는 것)
| 서버 | 툴 |
|---|---|
| dds-flow | `propose_flow` · `get_flow` · `update_node` · `publish_artifact` · `publish_image` · `set_project_info` · `get_report_context` · `write_report` · `make_report` |
| dds-cae | `case_contract` · `list_tools` · `project_folder` · `run_script` |
| dds-study | `list_adapters` · `create_study`(outputs) · `add_outputs` · `generate_doe` · `run_cases` · `get_study` · `case_data` · `train_surrogate` · `suggest_next` · `optimize` · `train_field_model` · `deploy_model` · `predict` |
| dds-live | `live_status` · `live_launch` · `live_info` · `live_set_params` · `live_view` · `live_undo` · `live_exec` · `live_result` · `live_capture` |

툴을 더하면 `frontend/src/chat/labels.ts`에 쉬운 이름도 더한다(대화의 "작업 N건" 묶음과 실행 확인 카드에 쓰인다).

---

## 8. AI 에이전트 동작 규칙 (요약 — 원본 `agent-context/INSTRUCTIONS.md`)
1. **묻지 말고 진행한다.**
   - 빠진 조건은 기본값을 정해 Flow detail에 적는다.
   - 진행할 수 없을 때만 한 번에 모아 묻는다.
2. **대화는 3~5줄로 쓴다.**
   - 쓰는 것: 한 일, 핵심 숫자, 다음 권고
   - 쓰지 않는 것: 경로, id, 툴 이름, JSON, 진행 중계
3. **실행 전에 Flow를 제안하고 승인을 받는다.** 승인 뒤에는 단계마다 `update_node`를 부른다.
4. **결과는 캔버스에 게시한다.** `flow_id`·`node_id`를 넣는다.
5. **보고서는 해석해서 새로 쓴다**(INSTRUCTIONS 1-0절).
6. **Output을 더 원하면 `add_outputs`를 쓴다.** script 어댑터는 추출 스크립트를 한 케이스에서 먼저 시험한다.
7. **결과는 빠짐없이 남긴다.** 툴별 '결과 전체 저장' 단계를 `post`에 붙인다(case.h5).
8. **툴 스킬을 먼저 읽고 그대로 쓴다.** 새 스크립트는 1회 시험 실행으로 검증한다. 확인 안 된 API는 지어내지 않는다(`TODO(사내검증)`).

---

## 9. 비기능 요구
| 항목 | 요구 |
|---|---|
| 사내망 | 외부 인터넷 없이 동작한다. 의존성은 사내 미러에서 받을 수 있는 것만 쓰고 버전을 고정한다(`requirements-*.txt`, `package-lock.json`) |
| 보안 | PIN·토큰은 해시로 저장한다. 비밀값과 `data/`는 커밋하지 않는다. 사용자 PC 실행은 확인 카드(허용/거부)를 거친다. 다른 사람의 PC 폴더는 열 수 없다 |
| 데이터 위치 | 큰 원본 결과는 PC에 둔다. 서버에는 숫자·이미지·필드·case.h5(크기 제한)·보고서만 둔다 |
| 성능 | 케이스 수백 건, 표·그래프 실시간 갱신. 이벤트마다 쿼리를 무효화한다(너무 잦으면 묶기를 검토한다) |
| 호환 | 서버는 Windows·Linux, PC Agent는 Windows(포터블 Python), 브라우저는 Edge·Chrome |
| 접근성 | 버튼에 이름(aria-label)을 붙이고, 색과 함께 글자·기호를 쓰고, 키보드로 쓸 수 있게(Enter·Esc·→) 한다 |

---

## 10. 수용 기준과 검증
### 10-1. 공통 검증(AGENTS.md 4절)
| 바꾼 것 | 실행 | 통과 기준 |
|---|---|---|
| Python | `.venv\Scripts\python -m pytest server/tests agent/tests -q` | 모두 passed(현재 58개) |
| 프론트 | `cd frontend; npm run build` | 오류 없음 |
| 화면 | 서버를 띄운 뒤 `npm run smoke -- --demo` | `PASS`. 스크린샷을 눈으로 확인한다(겹침·잘림·빈 화면). 어두운 테마는 `DDS_THEME=dark` |
| 움직임을 바꿨을 때 | `npm run smoke -- --reduced` | `PASS`(움직임 줄이기에서 도는 애니메이션 0개) |

### 10-2. 기능별 수용 기준
| 기능 | 기준 | 자동 테스트 |
|---|---|---|
| 단계 번호 | 흐름도·단계별 결과·대화 카드·보고서 '수행 내용'의 번호가 같다 | `test_step_order_and_body_split` |
| 작업 흐름 탭 | 흐름도는 고정, 결과만 스크롤. 단계를 누르면 그 단계 섹션으로 이동. 대화 결과 칩을 누르면 그 결과로 이동 | 스모크·데모 점검 |
| AI 보고서 | 버튼 → 새 판 생성 → 탭에 표시, 그림·표 번호, 새 결과 수 알림, Word·HTML이 같은 내용 | `test_ai_report` |
| Output 추가 | 이미 해석한 케이스 모두에 새 값(기존 값 유지), 이후 케이스에도 값, 표 열 순서 | `test_add_output_extracts_existing_and_later_cases`, `test_post_steps_merge_and_extract_existing` |
| HDF5 저장 | 케이스마다 case.h5, 검증 ok, manifest에 모든 파일, 서버 요약·받기, npz 없이 3D 학습 | `test_h5store.py`, `test_cae_to_h5.py`, `test_archive.py` |
| 대화 정리 | 생각·툴 호출이 한 줄로 접히고, 범위 표기(6~14)가 취소선이 되지 않는다 | 스모크 |
| 대화 움직임 | 기록을 열거나 새로 고쳐도 등장 움직임 없음, 새 항목만 등장. 턴 중 위로 올린 스크롤이 끌려 내려가지 않음 | 데모로 직접 |
| 규모 | 케이스 500행에서 표 스크롤·탭 전환이 끊기지 않음(이벤트 400ms 묶음, 표 줄 memo) | Playwright `page.route`로 부풀려 확인 |

### 10-3. 데모로 직접 확인(세탁기 시나리오)
1. `scripts\dev_live.cmd`(데모)를 띄운다.
2. 첫 화면에 "탈수 진동이 줄도록 스프링·댐퍼를 최적화해 줘. 탈수 1,200 rpm 기준, 현재 부품 범위 안에서 보고서까지 만들어 줘."를 보낸다.
3. 승인 → 허용 → 완료(1~2분)를 기다린다. 다음을 확인한다.
   - 작업 흐름 탭: 7단계, 결과
   - 보고서 탭: 그림 1~3, 표 1
   - 데이터 탭: 입력 4·출력 1, HDF5 68건
4. 데이터 탭 [Output 추가] → "바닥 전달력"을 보낸다. 출력 2개와 68/68을 확인한다.
5. 보고서 탭: '새 결과' 알림 → [AI 보고서 작성] → 새 판에 바닥 전달력 문장이 들어갔는지 확인한다.

---

## 11. 남은 일·주의
| 항목 | 상태 | 할 일 |
|---|---|---|
| 실제 툴 연동 | 스킬에 `TODO(사내검증)` 약 560곳 | [onsite-validation.md](onsite-validation.md) 순서, [pc-test-bridge.md](pc-test-bridge.md)로 사내 AI가 직접 시험 |
| HDF5 툴별 추출 | 작은 예시로만 확인(LS-DYNA·Nastran은 라이브러리, Abaqus는 미확인) | 사내 결과 파일로 크기·시간·읽기 확인, `upload_max_mb`와 서버 보관 정책 결정 |
| 디자인 개편 | 적용함. 여백·움직임 토큰은 일부 규칙에만 적용 | [design-renewal-plan.md](design-renewal-plan.md) 7절의 남은 곳 |
| 테마 바꿀 때 그래프 색 | `useTheme` 상태가 컴포넌트마다 따로라, 바꾼 직후에는 이미 그려진 그래프·3D 바탕 색이 그대로일 수 있다(새로 고치면 맞다) | 테마 상태를 앱 전체에서 함께 쓰게 정리 |
| 예측 모델 목록 | 최적화 반복마다 모델이 쌓인다(화면은 최근 3개만) | 오래된 모델 정리 정책 검토 |
| 이벤트 폭주 | 케이스·추출 이벤트마다 브라우저가 다시 조회한다 | 많은 케이스(수백)에서 묶어 보내기 검토 |
| 보고서 작성 중 표시 | 클라이언트 상태(새로 고치면 사라짐) | 필요하면 서버에 요청 상태 저장 |
