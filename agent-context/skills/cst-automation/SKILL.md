---
name: cst-automation
description: CST Studio Suite(Dassault Systèmes) 전자기 해석 자동화 — CST 파이썬 라이브러리(cst.interface·cst.results)로 프로젝트(.cst)를 열고 파라미터를 바꿔 히스토리를 다시 만든 뒤 솔버를 돌리고, S-파라미터·방사 패턴 등 1D 결과를 읽어 공진 주파수·최소 반사(dB)·-10 dB 대역폭을 results.json으로, 그래프를 이미지로 남긴다. 사용자가 CST로 안테나·필터·커넥터·EMC 해석, 형상 파라미터 DOE·최적화, S11/S21 결과 추출을 요청할 때 사용.
---

# CST Studio Suite 자동화 (CST 파이썬 라이브러리)

## 1. 언제 쓰나 / 전제
- 안테나·RF 부품·커넥터처럼 **형상 파라미터 → 전자기 해석 → S-파라미터 지표**를 반복할 때 쓴다.
- 전제
  - CST Studio Suite 2020 이상. 설치 폴더의 `AMD64\python_cst_libraries`에 `cst` 패키지가 있다. 스크립트가 PC Agent 설정의 CST 경로(`DDS_CST`)에서 찾아 `sys.path`에 넣는다.
  - CST 라이브러리가 지원하는 파이썬 버전과 플랫폼 파이썬 버전이 맞아야 한다. TODO(사내검증): 사내 CST 버전이 Python 3.12를 지원하는지. 안 되면 CST에 들어 있는 파이썬으로 `tool_commands`를 바꾼다.
  - 솔버 라이선스(토큰) 수만큼만 병렬로 돌린다. 기본은 병렬도 1이다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `cst_python` | `{python} {script} {args}` | `scripts/cst_case.py` — 파라미터 변경·솔브·결과 추출 |
| `cst_batch` | `{cst} -m -r {args}` | 프로젝트를 그대로 배치 솔브(파라미터 변경 없이) |

`cst_case.py` 인자(`key=value`):
- `project=<.cst>`: 없으면 case.json `inputs` → 케이스 폴더의 `*.cst` 1개 순으로 찾는다.
- `inspect=1`: 파라미터 목록과 결과 트리를 `model_info.json`에 쓰고 끝낸다. **먼저 실행한다.**
- `results=`: 읽을 결과 항목, 쉼표로 구분. 기본은 `1D Results\S-Parameters\S1,1`
- `solve=1`, `attach=0|1`, `image=1`, `strict=1`

```
run_script(tool="cst_python", script=<cst_case.py>, filename="cst_case.py", args=["project=patch.cst", "inspect=1"])
```
스터디 어댑터:
```json
{"tool": "cst_python", "script": "scripts/cst_case.py",
 "args": ["project=patch.cst", "results=1D Results\\S-Parameters\\S1,1"],
 "inputs": ["models/patch.cst"], "outputs": ["s11_min_db", "s11_fmin", "s11_bw10"]}
```
- `.cst`와 같은 이름의 결과 폴더도 함께 복사해야 할 수 있다. 결과 폴더가 없으면 CST가 다시 만든다. TODO(사내검증)

## 3. 파라미터 변경
1. `inspect=1`로 `model_info.json`의 `parameters`를 보고, 사용자와 설계변수를 정한다. 이름은 CST 파라미터 목록 이름 그대로 쓴다.
2. 순서: params.json 값을 그대로 넣는다(단위는 CST 프로젝트 단위) → `full_history_rebuild()`로 히스토리를 다시 만든다 → `run_solver()`
3. 넣는 방법: `StoreDoubleParameter`(VBA)를 먼저 시도하고, 안 되면 매크로 실행으로 넣는다. TODO(사내검증): CST 버전별로 되는 호출 형태
4. 없는 파라미터 이름은 기본(`strict=1`) 실패다.

## 4. 결과 · 지표
- 각 결과 항목(복소수 y(f))을 `|y|` dB로 바꿔 다음을 results.json에 넣는다. 키 앞부분은 항목 마지막 이름으로 만든다(`S1,1` → `s11`).
  - `<키>_min_db`: 최소 반사
  - `<키>_fmin`: 그때의 주파수 = 공진
  - `<키>_bw10`: 최소점을 포함하는 -10 dB 이하 연속 대역폭(없으면 0)
- `image=1`이면 `images/<키>.png`(dB 대 주파수)를 남긴다.
- 실패 판정: 솔버 예외, 결과 항목 없음, 데이터가 비어 있음. 실패 시 results.json을 쓰지 않는다.

## 5. DOE·최적화로 쓰기
- 메쉬 설정은 고정한다. 형상이 바뀌면 메쉬도 바뀌어 결과에 계단식 잡음이 생기므로, 기준 형상에서 메쉬 수렴을 먼저 확인한다.
- 목표 예: "2.45 GHz에서 S11 최소, 대역폭 ≥ 100 MHz" → 목적 `s11_min_db`(최소), 제약 `s11_bw10 ≥ 0.1`(GHz 단위 프로젝트일 때)

## 6. 사내 검증이 필요한 것 (TODO(사내검증))
- 라이브러리 경로·파이썬 버전 호환
- `DesignEnvironment()` 새 창 / `connect_to_any()` 연결
- 파라미터 쓰기 호출 형태
- 결과 트리 이름(언어 설정에 따라 다를 수 있음)
- 결과 폴더 복사 필요 여부
