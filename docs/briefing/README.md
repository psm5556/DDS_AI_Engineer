# DDS AI Engineer 소개 자료

DDS AI Engineer를 쉽게 설명하는 발표 자료다.

| 파일 | 쓰는 곳 |
|---|---|
| `DDS_AI_Engineer_소개.html` | 발표용 슬라이드 10장(브라우저로 바로 연다). 기존 DDS와 비교하고, 드럼세탁기 탈수 진동 사례 하나를 실제 웹앱 화면과 움직이는 데모로 보여 준다 |
| `DDS_AI_Engineer_소개.pptx` | 이전 판 11장(브래킷 경량화 사례). 발표자 노트가 들어 있고, 도형은 PowerPoint에서 바로 고칠 수 있다 |

## HTML 슬라이드 쓰는 법
- 넘기기
  - → 키·Space·PageDown: 다음 장면. 장면을 다 보면 다음 장으로 넘어간다(PPT 애니메이션과 같다).
  - ← 키: 이전 장면.
  - 프레젠터 리모컨도 같은 키를 보내므로 그대로 쓸 수 있다.
- 기능 키
  - F: 전체 화면
  - P: 자동 재생/멈춤
- 아래 막대의 다음 버튼에는 다음에 일어날 일이 적혀 있다(예: "승인하고 해석").
- 각 장의 내용

  | 장 | 주제 | 내용 |
  |---|---|---|
  | 1 | 표지 | 한 문장 요약과 흐름 |
  | 2 | 비교 ① 기존 DDS | 같은 과제를 5단계(모델 준비 → 해석 자동화 → DOE → 학습·최적화 → Simulator·보고서)로 기존 DDS와 AI Engineer가 차례로 진행하고, 달라지는 점 4가지를 정리 |
  | 3 | 데모 ① 실제 웹앱 화면 | 실제 웹앱에서 찍은 화면으로 요청 입력 → AI 계획(Flow) → 승인·허용 → 해석 진행 → 결과 요약. 장면마다 해당 부분을 확대하고 강조한다 |
  | 4 | 데모 ② 3D Simulator 예측 | 세탁기 단면(탈수 중)과 Simulator. 1,400 rpm 예측 → AI가 해결안 탐색 → 검증 해석. 슬라이더 세 개를 발표 중에 직접 움직일 수 있다 |
  | 5 | 비교 ③ 과제가 끝난 뒤 | 관리자 템플릿·개인 PC 스크립트와 프로젝트에 쌓이는 자산. 다음 과제로 잇는 예시 |
  | 6 | 안심 | 회사 안 그림(엔지니어 PC·사내 HPC·사내 서버), 엔지니어 승인 |
  | 7 | 다음 단계 | 현황과 요청 사항 |
  | 8 | 참고: 지원 툴 | AI가 다루는 CAD·CAE 툴 29종을 분야별로(`cae_adapters/catalog.py`와 같은 목록) |
  | 9 | 참고: 구조와 실행 흐름 | 회사 안 구성(엔지니어 브라우저·사내 서버·엔지니어 PC·사내 HPC)과 외부 AI 서비스. 요청 → AI 계획 → 승인 → 해석 실행(PC·HPC) → 결과 모으기·학습 → Simulator·보고서 6단계를 장면마다 해당 경로를 강조해 보여 준다 |
  | 10 | 참고: 상용 툴 AI와 비교 | 상용 CAD·CAE 회사가 내놓는 MCP 서버·AI 어시스턴트(툴 하나 안에서 동작)와 DDS AI Engineer(여러 툴을 엮어 과제를 끝내고 자산으로 남김)를 6가지로 비교한다. 마지막 장면은 비용: 툴 라이선스는 어느 쪽이든 필요하고, 차이는 그 위의 AI 비용이다 |

- 10장의 회사별 내용·가격은 2026년 10월 공개 자료 기준이다. 발표 전에 다시 확인한다.
  - Ansys Engineering Copilot: 쓰려면 AnsysGPT AI+ 라이선스가 필요하다([요구 사항](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/copilot_ug/copilot_ug_requirements.html)). 공식 MCP는 [CFX-MCP](https://glama.ai/mcp/servers/ansys/pycfx-mcp)
  - Autodesk Fusion: 공식 MCP 3종. Fusion Compute MCP는 공개 베타이고 Fusion 라이선스와 Fusion Team 허브가 필요하다. 정식 가격은 아직 없다([블로그](https://www.autodesk.com/products/fusion-360/blog/fusion-compute-mcp/), [engineering.com](https://www.engineering.com/autodesk-announces-fusion-mcp-servers-and-more-ai-updates/))
  - Dassault Systèmes: AI 동료(AURA·LEO·MARIE)는 Blue Token으로 과금한다. 25,000개 묶음이 연 $1,250이고 작업할 때마다 차감된다([SOLIDWORKS](https://www.solidworks.com/solution/ai-tokens), [engineering.com](https://www.engineering.com/solidworks-users-spend-blue-tokens-on-ai-drawings-insights-and-more/))
  - Siemens NX: 공식 MCP 서버를 확인하지 못했고 커뮤니티 제작만 있다. Design Copilot NX(도움말·학습용)의 가격 자료는 찾지 못했다

- 숫자: 모든 숫자는 이해를 돕기 위한 예시다. 해당 장에 "예시" 표시가 있다.
  - 예: 사람 설정 32개 → 승인 1번, 기간 8일 → 2일, 진동 4.2 → 2.6 mm, 1,400 rpm 3.2 → 2.9 mm
  - 시범 운영에서 실제 값으로 바꾼다.
- 색의 뜻: 주황 = 엔지니어가 하는 일, 청록 = AI가 하는 일.
- 사내망에서 웹 글꼴을 못 받으면 맑은 고딕으로 보인다.

## 고쳐서 다시 만들기
- HTML
  1. `src/briefing_body.html`(본문)을 고친다. 3장 사진은 `src/shots/*.webp`에 있다.
  2. `.venv\Scripts\python docs\briefing\src\build_html.py` — 사진을 넣어 `DDS_AI_Engineer_소개.html` 한 파일로 만든다.
- 3장 사진 다시 찍기(웹앱 화면이 바뀌었을 때)
  - 데모 에이전트의 세탁기 시나리오로 찍는다. 요청에 '세탁기'나 '탈수'가 들어가면 이 시나리오가 돈다(`agent/dds_agent/demo_agent.py`, 해석은 `cae_adapters/mock.py`의 `washer`).
  - 순서
    1. `scripts\dev_live.py --agent demo`로 서버와 PC Agent를 띄운다. 빈 데이터 폴더(`--data`)를 쓰면 사이드바가 깨끗하다.
    2. 첫 화면에 아래 요청을 넣고 승인·허용까지 진행하며 1440×900 화면을 찍는다.
       - 요청: "탈수 진동이 줄도록 스프링·댐퍼를 최적화해 줘. 탈수 1,200 rpm 기준, 현재 부품 범위 안에서 보고서까지 만들어 줘."
    3. 찍은 화면을 WebP로 줄여 `src/shots/`에 같은 이름으로 넣는다: `home`·`review`·`perm`·`run1`~`run5`·`done`, 그리고 입력창만 자른 `typed`.
  - 확대·강조 위치는 촬영 화면 좌표로 본문 3장 스크립트(`VIEW`·`BOX`)에 적혀 있다. 화면 배치가 바뀌면 함께 고친다.
- PPTX(이전 판):
  1. `cd docs\briefing\src; npm install` — pptxgenjs를 받는다. 사내 npm 미러를 쓴다.
  2. `node make_assets.mjs` — 아이콘·브래킷 그림을 PNG로 만든다. `frontend`의 playwright-core와 설치된 Edge를 쓴다.
  3. `node build.js` — `..\DDS_AI_Engineer_소개.pptx`를 다시 만든다.
- `korean_wrap.py`는 PPTX에서 한글이 글자 중간에서 줄바꿈되는 문제를 고친다(build.js가 자동으로 부른다).
  - 원인: pptxgenjs가 글자에 중국어 문자 집합을 붙여서 생긴다.
