---
name: ncode-automation
description: HBK nCode DesignLife 피로 해석 배치 자동화. 플로(.flo) 텍스트 템플릿 치환(하중 배율, 생존확률, 평균응력 보정 계수 등 숫자 속성), ASCII 하중 시계열 배율 변경, DesignLife 배치 실행과 로그/출력 판정, 손상·수명 CSV 결과 추출(최대 손상, 최소 수명, 임계 노드), 손상 분포 그래프, Nastran 메쉬와 결합한 3D 손상 필드(npz) 저장을 다룬다. 사용자가 nCode/DesignLife 피로 수명 DOE, 하중 배율 스윕, FE 해석 뒤 피로 후처리 자동화, 손상 결과 추출이나 가시화를 요청할 때 사용한다.
---

# nCode DesignLife 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고, `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`, 선택으로 `fields/*.npz`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- FE 응력 결과(Nastran .op2, Abaqus .odb, Ansys .rst 등)와 하중 이력으로 피로 손상/수명을 계산하는 플로를 **설계변수만 바꿔 반복 실행**할 때.
- 하중 배율, 재료/생존확률 같은 피로 조건에 대한 민감도 DOE, 또는 FE 스터디 뒤의 피로 후처리를 자동화할 때.
- 플로(.flo)는 GUI에서 먼저 완성하고 한 번 손으로 돌려 결과 CSV가 나오는지 확인한다. 자동화는 이 플로를 **고정하고 숫자만 바꾼다**.
- 템플릿 스크립트는 모두 **플랫폼 venv 파이썬(Python 3)**에서 돈다. nCode 내장 Python(스크립트 글리프)은 쓰지 않는다. TODO(사내검증): 사내 버전의 내장 Python 버전과 스크립트 글리프 API.
- 라이선스: DesignLife 기본 기능과 분석 모듈(SN, EN, 용접, 진동 피로 등)이 별도 기능일 수 있다. 배치 실행도 같은 라이선스를 쓴다. TODO(사내검증).

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| 템플릿 치환 + 배율 + 배치 실행 + 판정 | `python` | `ncode_case_run.py` (내부에서 `ncode` 경로 호출) |
| 손상/수명 추출 + 그래프 | `python` | `ncode_extract.py`, `ncode_plots.py` |
| 3D 손상 필드 | `python` | `ncode_fields.py` |
| 배치 단독 | `ncode` | `{ncode} <args>` |

```json
{"tool": "python", "script": "scripts/ncode_case_run.py",
 "args": ["--flo", "bracket.flo", "--template", "bracket.flo", "--scale-csv", "loads/road.csv:load_scale",
          "--ncode-args", "{flo}", "--expect", "out/damage*.csv"],
 "inputs": ["models/bracket.flo", "models/bracket.op2", "models/loads/road.csv"],
 "post": {"tool": "python", "script": "scripts/ncode_extract.py",
          "args": ["--csv", "out/damage*.csv", "--target-life", "1.0", "--plot"]},
 "outputs": ["max_damage", "min_life", "life_margin"], "timeout_sec": 7200}
```
- 실행파일은 `--ncode`를 생략하면 환경변수 `DDS_TOOL_NCODE`(사용자가 웹앱 'CAD·CAE 툴' 화면에서 고른 `ncode` 경로)를 쓴다.
- TODO(사내검증): **DesignLife 배치 실행 명령행**(실행파일 이름, 플로 실행/종료 옵션)을 사내 설치본 문서("command line" 항목)로 확인하고 `--ncode-args`에 넣는다. 기본값 `{flo}`는 확인되지 않은 가정이다.
- `inputs`의 하위 폴더 구조(`loads/`)가 케이스 폴더에 그대로 복사되는지 확인한다. 플로 안의 파일 경로는 상대경로나 `{{__case_dir__}}`로 둔다(3-3).
- FE 해석과 피로를 한 케이스에서 하려면, FE 스킬의 실행 스크립트를 본 단계로 두고 `post`에서 `ncode_case_run.py`와 `ncode_extract.py`를 차례로 부르는 작은 래퍼 스크립트를 쓴다(어댑터의 `post`는 한 단계뿐이다).

## 3. 파라미터 변경
### 3-1. 플로 숫자 속성 (`--template`)
플로 파일이 텍스트(XML)이면 바꿀 속성값을 `{{name}}`으로 바꾼다. 찾기 쉽게 GUI에서 독특한 값(예: 1.23457)을 넣고 저장한 뒤 검색해 바꾼다. 바이너리 파일은 치환하지 않고 오류로 끝낸다. TODO(사내검증): 사내 버전의 .flo가 텍스트인지, 속성 저장 형식.
- 대상 예: 하중 배율(scale factor), 생존 확률(certainty of survival), 평균응력 보정 관련 계수, 재료 S-N 곡선 배율, 블록 반복 횟수.
- 선택형 속성(평균응력 보정 방법 등)은 숫자가 아니라서 플레이스홀더로 바꾸지 않는다. 경우마다 다른 플로를 쓰고 `--flo`로 고른다.
### 3-2. 하중 시계열 배율 (`--scale-csv FILE:PARAM`)
ASCII 시계열(첫 열 시간, 나머지 채널)의 채널 값에 `params[PARAM]`을 곱한다. 머리글 줄은 그대로 둔다. 바이너리 시계열(.s3t)은 이 방법을 쓰지 않고 플로의 배율 속성(3-1)을 쓴다.
### 3-3. 경로
`{{__case_dir__}}`는 케이스 폴더 절대경로(슬래시)로 바뀐다. 플로가 FE 결과나 출력 폴더를 절대경로로 저장하면 그 부분을 이것으로 바꾼다.
- params 키가 어느 템플릿/배율에도 쓰이지 않거나, 값이 없는 플레이스홀더가 남으면 실패로 처리한다.

## 4. 해석 조건 변경
| 대상 | 방법 |
|---|---|
| 하중 크기 | 3-2(ASCII) 또는 플로의 배율 속성(3-1) |
| 하중 이력 교체 | 시계열 파일을 inputs에 넣고 같은 이름으로 둔다(케이스마다 다른 파일이면 파일 이름 자체를 바꾸는 래퍼 필요) |
| 재료 | 재료 DB(.mxd)는 직접 고치지 않는다. 플로의 재료 배율/계수 속성을 쓴다. TODO(사내검증): 재료 매핑 속성 저장 형식 |
| 생존 확률, 안전율 | 해석 글리프의 숫자 속성(3-1) |
| FE 결과 교체 | FE 스터디의 결과 파일을 같은 이름으로 케이스 폴더에 둔다(상류 단계 출력) |

## 5. 실행과 완료 판정
`ncode_case_run.py`의 판정 순서다. 하나라도 걸리면 종료코드 1이다.
1. 표준출력(`ncode_stdout.txt`)과 실행 중 새로 생긴 `*.log`(하위 폴더 포함)에서 `ERROR_PATTERNS`(error:, fatal, exception, licence 실패 등)를 찾는다.
2. `--expect` 패턴(기본 `*.csv`)의 결과 파일이 실행 뒤 **새로 쓰였는지** 확인한다(이전 결과를 잘못 읽는 것 방지).
- 종료코드는 참고만 한다. TODO(사내검증): 실제 성공/실패 로그 문구로 `ERROR_PATTERNS`를 보정한다.

## 6. 결과 추출 → results.json
플로 끝에 **엔티티별 손상/수명 표를 CSV로 쓰는 출력 글리프**를 둔다(한 줄 = 노드나 요소, 열 = ID, Damage, Life). TODO(사내검증): 사내 버전에서 이 표를 파일로 쓰는 글리프 이름과 설정.
```
python ncode_extract.py --csv "out/damage*.csv" --target-life 1.0 --per-file --plot
```
- 머리글은 `damage`나 `life`가 들어간 첫 줄로 찾는다. 열 이름이 다르면 `--id-col`, `--damage-col`, `--life-col`로 준다.
- 출력 키: `max_damage`, `min_life`, `log10_min_life`, `n_entities`, `n_damage_ge_1`, `critical_id`, (`--target-life`) `life_margin`, (`--per-file`) `<파일>_max_damage`, `<파일>_min_life`.
- 무한 수명(`inf`)은 `1e30`으로 바꿔 쓴다. 최적화 목적함수에는 `log10_min_life`나 `max_damage`가 다루기 쉽다.

## 7. 이미지 저장 → images/
- `ncode_extract.py --plot` 또는 `ncode_plots.py --csv ...`: `damage_hist.png`(log10 손상 분포), `damage_top.png`(손상 상위 20개 엔티티, 손상 1 기준선).
- 3D 손상 컨투어 캡처는 GUI의 FE Display에서만 가능하다. TODO(사내검증): 배치에서 이미지를 저장하는 글리프/옵션. 대신 8절의 npz로 플랫폼 Simulator에서 3D로 본다.

## 8. 3D 필드 (fields/*.npz)
손상 CSV의 ID를 FE 메쉬(Nastran BDF)와 맞춰 표면 노드 필드로 저장한다.
```json
{"tool": "python", "script": "scripts/ncode_fields.py",
 "args": ["--bdf", "bracket.bdf", "--csv", "out/damage*.csv", "--location", "node", "--value", "log10_damage", "--name", "damage"]}
```
- 요소 기준 결과면 `--location element`(요소 값을 노드로 평균). 표에 없는 노드는 손상 0이다.
- 학습용으로는 `log10_damage`(0은 1e-12로 바닥 처리)가 범위가 좁아 낫다.
- FE 입력이 Abaqus/Ansys면 해당 스킬의 필드 스크립트로 좌표/표면을 만들고 같은 ID 매핑을 한다.

## 9. 흔한 오류와 대처
- `binary file, cannot template`: .flo가 바이너리다. 템플릿 방식 대신 ASCII 입력 배율(3-2)을 쓰거나 숫자별로 다른 플로를 준비한다.
- `expected output not written`: 플로가 돌지 않았거나 출력 경로가 케이스 폴더 밖(절대경로)이다. `{{__case_dir__}}`로 바꾼다.
- `no header with 'damage' or 'life'`: 출력 표 형식이 다르다. 파일을 열어 열 이름을 확인하고 `--damage-col` 등을 준다.
- 노드 ID 불일치(`no table IDs match`): 결과가 요소 기준인지, FE 모델과 같은 메쉬인지 확인한다.
- 단위: FE 응력 단위(MPa/Pa)와 재료 S-N 곡선 단위가 다르면 손상이 수십 자릿수 틀린다. 첫 케이스에서 GUI 결과와 대조한다.
- 로그 위치: `run.log`(플랫폼), `ncode_stdout.txt`, 케이스 폴더의 `*.log`.

## 파일
- `scripts/ncode_case_run.py`: (python) 템플릿 치환, 시계열 배율 → DesignLife 배치 → 판정
- `scripts/ncode_extract.py`: (python) 손상/수명 CSV → results.json, `--plot`이면 그림
- `scripts/ncode_plots.py`: (python) 손상 히스토그램, 상위 N 막대그래프
- `scripts/ncode_fields.py`: (python, pyNastran) BDF + 손상 CSV → fields/<name>.npz
- `references/designlife-batch-notes.md`
