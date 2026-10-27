---
name: motionsolve-automation
description: Altair MotionSolve 다물체 동역학 자동화 — MotionSolve XML 모델(.xml)의 값(스프링 강성·감쇠·질량·하중·초기 속도 등)을 {{변수}} 자리표시자나 요소 id·속성 표(ms_map.json)로 바꾸고, motionsolve.bat로 배치 해석한 뒤 요청(Request) 결과 CSV에서 최대·최소·최종값을 results.json으로, 시간 이력 그래프를 이미지로 남긴다. 사용자가 MotionSolve(또는 MotionView에서 내보낸 XML) 모델로 기구 해석, 서스펜션·링크 파라미터 DOE·최적화, 하중·변위 이력 추출을 요청할 때 사용.
---

# Altair MotionSolve 자동화

## 1. 언제 쓰나 / 전제
- 링크·서스펜션·기어·도어 같은 기구에서 **설계값 → 동역학 해석 → 하중·변위·가속도 지표**를 반복할 때 쓴다.
- 전제
  - Altair HyperWorks 솔버(MotionSolve)가 설치되고 라이선스가 있다. 실행 파일은 `hwsolvers\scripts\motionsolve.bat`이고, PC Agent가 기본 위치에서 찾는다.
  - 모델은 MotionView에서 `Export → Solver Deck`으로 내보낸 **MotionSolve XML**을 쓴다. MotionView 세션 파일(.mdl)은 쓰지 않는다.
  - 결과를 숫자로 받으려면 XML에 요청(Request)이 있어야 한다. 결과 파일을 CSV로 내도록 설정하거나, `.plt`(텍스트)를 읽는다. TODO(사내검증): 사내 버전의 출력 파일 형식과 CSV 출력 설정 이름

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `motionsolve_python` | `{python} {script} {args}` | `scripts/ms_case.py` — 값 바꾸기 → 솔버 실행 → 결과 파싱 |
| `motionsolve` | `{motionsolve} {args}` | XML을 그대로 배치 해석 |

`ms_case.py` 인자(`key=value`):
- `model=<xml 또는 .xml.tmpl>`
- `map=ms_map.json`
- `inspect=1`: 요소·id·속성 목록을 `model_info.json`에 쓴다. **먼저 실행한다.**
- `results=<csv 패턴>`: 기본은 `*.csv`, 없으면 `*.plt`
- `image=1`, `strict=1`, `timeout=3600`

```json
{"tool": "motionsolve_python", "script": "scripts/ms_case.py",
 "args": ["model=door.xml", "map=ms_map.json"], "inputs": ["models/door.xml", "models/ms_map.json"],
 "outputs": ["hinge_force_max", "latch_disp_final"]}
```

## 3. 값 바꾸기 — 두 가지 방법
1. **자리표시자**: 모델을 `door.xml.tmpl`로 두고 바꿀 값 자리에 `{{k_spring}}`처럼 쓴다. params.json 키와 같은 이름으로 채운다. 남은 자리표시자가 있으면 실패다.
2. **요소 표**(`ms_map.json`): XML을 그대로 두고 요소 태그·id·속성을 지정한다.
   ```json
   {"params": {"k_spring": {"tag": "Force_SpringDamper", "id": "30101", "attr": "k"},
               "mass_door": {"tag": "Body_Rigid", "id": "30102", "attr": "mass", "scale": 1.0}},
    "outputs": {"hinge_force": {"column": "Hinge Force/Fz"}, "latch_disp": {"column": "Latch/Disp"}}}
   ```
   - `inspect=1`의 `model_info.json`에서 태그·id·속성 이름을 확인한다. 속성 이름은 XML 그대로다(대소문자 구분).
   - `outputs.column`은 결과 CSV의 열 이름(일부만 맞아도 됨)이다. 비우면 모든 열의 지표를 남긴다.

## 4. 결과 · 실패 감지
- 각 열마다 `<이름>_max`, `<이름>_min`, `<이름>_final`을 results.json에 넣는다. `image=1`이면 `images/<이름>.png`(시간 이력)를 남긴다.
- 실패 판정
  - 솔버 종료 코드가 0이 아님
  - 로그에 `ERROR`가 있거나 시뮬레이션이 끝까지 가지 못함(마지막 시간 < 지정 끝 시간)
  - 결과 파일 없음
- 실패 시 results.json을 쓰지 않는다.

## 5. DOE·최적화
- 적분 허용오차·출력 간격은 고정한다. 바꾸면 피크값이 흔들린다.
- 접촉·충돌이 있는 모델은 설계값에 따라 수렴하지 않는 케이스가 생긴다. 실패 케이스는 스터디에서 실패로 남기고(지우지 않음), 범위를 좁힐지 사용자와 정한다.

## 6. 사내 검증이 필요한 것 (TODO(사내검증))
- `motionsolve.bat` 인자(출력 폴더 지정 등)
- 결과 CSV를 내는 XML 설정과 `.plt` 텍스트 형식
- MotionSolve 파이썬 API(`msolve`)로 모델을 만드는 방식을 쓸지 여부(쓰려면 HyperWorks 파이썬으로 실행)
