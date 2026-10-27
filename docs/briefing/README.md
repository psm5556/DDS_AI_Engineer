# 경영진 보고 자료

DDS AI Engineer를 경영진에게 쉽게 설명하는 자료다. 흐름 그림, 식당 비유, 실제 사례(브래킷 경량화)로 구성했다.

| 파일 | 쓰는 곳 |
|---|---|
| `DDS_AI_Engineer_경영진보고.html` | 브라우저로 바로 연다. 한 페이지 보고서(밝은·어두운 화면 모두 지원) |
| `DDS_AI_Engineer_경영진보고.pptx` | 발표용 11장. 장마다 발표자 노트(말할 내용)가 들어 있다. 도형은 PowerPoint에서 바로 고칠 수 있다 |

- 사례 숫자(1,178 g → 814 g, 31% 경량화)는 연습용 계산 모델(데모 시나리오, `cae_adapters/mock.py`의 bracket)로 낸 값이다.
  - 실제 해석 프로그램으로 확인하면 숫자를 바꾼다.
- 색의 뜻: 주황 = 사람이 하는 일, 청록 = AI 조수가 하는 일.

## 고쳐서 다시 만들기
- HTML: `src/briefing_body.html`(본문)을 고친 뒤 위의 html 파일에도 같은 내용을 반영한다.
- PPTX:
  1. `cd docs\briefing\src; npm install` — pptxgenjs를 받는다. 사내 npm 미러를 쓴다.
  2. `node make_assets.mjs` — 아이콘·브래킷 그림을 PNG로 만든다. `frontend`의 playwright-core와 설치된 Edge를 쓴다.
  3. `node build.js` — `..\DDS_AI_Engineer_경영진보고.pptx`를 다시 만든다.
- `korean_wrap.py`는 한글이 글자 중간에서 줄바꿈되는 문제를 고친다(build.js가 자동으로 부른다).
  - 원인: pptxgenjs가 글자에 중국어 문자 집합을 붙여서 생긴다.
