---
name: celsius-automation
description: Cadence Celsius(Celsius Thermal Solver · Celsius EC Solver) 전자 열해석 자동화 — 모델·조건 파일 템플릿의 {{변수}}(소비 전력·방열판 치수·팬 유량·주변 온도·재료 열전도도)를 채우고 사내에서 정한 배치 명령으로 솔브한 뒤, 내보낸 결과 표(CSV)에서 부품별 최고 온도·전체 최고 온도·허용 온도 여유를 results.json으로, 막대그래프를 이미지로 남긴다. 사용자가 Celsius로 PCB·패키지·전자장비 냉각 해석, 열 설계 파라미터 DOE·최적화, 부품 온도 추출을 요청할 때 사용.
---

# Cadence Celsius 자동화 (템플릿 + 배치 + 결과 표)

## 1. 언제 쓰나 / 전제
- 전자장비·PCB·패키지의 **열 설계값 → 열 해석 → 부품 최고 온도·여유**를 반복할 때 쓴다(Flotherm 스킬과 같은 흐름).
- 전제
  - Cadence Celsius가 설치되고 라이선스가 있다. PC Agent 설정 `[tools] celsius`에 배치 실행 파일을 지정한다. 기본 위치 탐지는 TODO(사내검증)
  - Celsius는 제품(Thermal Solver: Allegro·Sigrity 연동 / EC Solver: 장비 냉각 CFD)마다 배치 방식이 다르다. **사내에서 쓰는 배치 명령을 한 번 확정**해 `--cmd`로 넘긴다. TODO(사내검증)
  - 결과 표는 GUI나 배치 스크립트로 CSV(부품 이름, 최고 온도 열이 있는 표)로 내보내게 한다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `celsius_python` | `{python} {script} {args}` | `scripts/celsius_case.py` — 템플릿 채우기 → 배치 → 결과 표 |
| `celsius` | `{celsius} {args}` | 배치 명령을 그대로 실행 |

`celsius_case.py` 인자:
- `--template 원본.tmpl=결과파일`: 여러 개면 반복한다.
- `--cmd "{exe} -batch {project}"`: 배치 명령 형식. `{exe}`=`DDS_CELSIUS`, `{project}`=`--project` 값, `{이름}`=params 값
- `--project model.xxx`
- `--results temps.csv`
- `--limits celsius_limits.json`: 부품별 허용 온도 `{"U1": 105, "*": 85}`
- `--timeout`

```json
{"tool": "celsius_python", "script": "scripts/celsius_case.py",
 "args": ["--template", "board.tmpl=board.xml", "--project", "board.xml", "--cmd", "{exe} -batch {project}", "--results", "temps.csv"],
 "inputs": ["models/board.tmpl"], "outputs": ["tmax_overall", "margin_min"]}
```

## 3. 결과 · 실패 감지
- 결과 표의 열 이름을 자동으로 찾는다.
  - 부품 이름 열: `name`/`component`/`part`/`부품`
  - 온도 열: `tmax`/`max`/`temperature`/`온도`
- results.json에 넣는 값
  - `tmax_<부품>`
  - `tmax_overall`: 전체 최고 온도
  - `limits`가 있으면 `margin_min`(허용 − 실제 중 최솟값, 음수면 초과)
- 실패 판정: 종료 코드 ≠ 0, 결과 표 없음·비어 있음, 남은 `{{…}}`. 실패 시 results.json을 쓰지 않는다.

## 4. 사내 검증이 필요한 것 (TODO(사내검증))
- 제품별 배치 명령·인자·종료 코드와 결과 내보내기 방법(배치 스크립트 언어)
- 모델 파일 중 텍스트로 바꿀 수 있는 형식(템플릿으로 쓸 수 있는지)
- 설치 경로와 실행 파일 이름
