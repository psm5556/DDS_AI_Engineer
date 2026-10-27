---
name: inspire-automation
description: Altair Inspire 설계·위상 최적화 자동화 — Inspire 안의 파이썬(inspire 모듈)으로 모델을 열고 변수(Variables)를 바꿔 구조 해석이나 위상 최적화(Topology Optimization)를 돌린 뒤, 최대 변위·응력·질량을 results.json으로, 최적 형상을 STL/Parasolid로, 화면 캡처를 이미지로 남긴다. 사용자가 Inspire로 위상 최적화, 경량 설계안 탐색, 하중·설계 공간·질량 목표를 바꿔 가며 여러 안을 비교하자고 할 때 사용.
---

# Altair Inspire 자동화 (Inspire 파이썬)

## 1. 언제 쓰나 / 전제
- 설계 공간·하중·지지 조건을 정한 모델에서 **질량 목표·두께 제약·하중을 바꿔 가며 위상 최적화 안**을 여러 개 만들거나, 변수 기반 구조 해석을 반복할 때 쓴다.
- 결과 형상(STL)은 CAD 스킬(NX 등)로 넘겨 재설계하고, 구조 해석 스킬로 검증한다.
- 전제
  - Altair Inspire(2021 이상)가 설치되고 라이선스가 있다. PC Agent가 기본 위치에서 `Inspire.exe`를 찾는다.
  - 스크립트는 **Inspire 안의 파이썬**으로 실행된다(`inspire` 모듈은 플랫폼 파이썬에 없다). 케이스 폴더는 환경변수 `DDS_CASE_DIR`로 받는다.
  - TODO(사내검증): 배치(화면 없이) 실행 인자. 이 스킬은 `Inspire.exe -script <py>`를 가정한다.
  - TODO(사내검증): Inspire 파이썬 API 이름은 버전마다 다르다. **반드시 `inspect=1`로 실제 API를 먼저 확인**하고, 스크립트의 호출 이름을 맞춘다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `inspire_script` | `{altair_inspire} -script {script}` | `scripts/inspire_case.py` |

인자는 스크립트에 직접 넘기기 어려우므로 케이스 폴더의 `inspire_job.json`으로 준다.
```json
{"model": "bracket.stmod", "mode": "inspect",
 "variables": {"thk": "thickness"},
 "run": "optimize", "mass_target": 0.3,
 "export": ["shape.stl"], "image": true}
```
- `mode`: `inspect`면 API·모델 정보를 `api_probe.json`에 쓴다. `case`면 케이스를 실행한다.
- `variables`: params.json 키 → Inspire 변수 이름
- `run`: `analyze`(구조 해석) 또는 `optimize`(위상 최적화)

## 3. 순서
1. **API 확인** (`mode: inspect`). 아래 목록과 모델의 변수·파트·하중 목록을 `api_probe.json`에 남긴다.
   - `dir(inspire)`
   - 모델 객체의 메서드
   - `inspire` 아래 하위 모듈 이름
2. `api_probe.json`을 보고 `inspire_case.py`의 `API` 표(열기·변수 설정·해석·최적화·내보내기 함수 이름)를 실제 이름으로 고친다. 고친 내용은 스킬에도 반영한다(다음 사람을 위해).
3. 기준 케이스 1개를 돌려 사용자가 결과(형상·최대 변위)를 눈으로 확인한다.
4. DOE·비교 안: params.json(질량 목표·최소 두께·하중 크기)을 바꿔 가며 반복한다.

## 4. 결과 · 실패 감지
- results.json에 넣는 값(이름은 Inspire 결과 이름)
  - `disp_max`·`stress_max`·`mass`
  - 최적화면 `mass_fraction`
- `export`의 형상 파일과 `images/view.png`(화면 캡처)를 남긴다.
- 실패 판정: Inspire 파이썬 예외(스크립트가 `inspire_error.txt`를 남김), 결과값 없음. 실패 시 results.json을 쓰지 않는다.

## 5. 사내 검증이 필요한 것 (TODO(사내검증))
- 배치 실행 인자와 실행 후 Inspire가 스스로 종료되는지(종료 함수 이름)
- 파일 열기, 변수 설정, 해석·최적화 실행·완료 대기, 결과 읽기, 형상 내보내기 API 이름
- 위상 최적화 결과(밀도 → 형상) 내보내기 방식(PolyNURBS·STL)
