---
name: existing-data-to-simulator
description: 이미 가진 입력·출력 표(CSV·XLSX)나 해석을 끝낸 결과 폴더(odb·d3plot·op2 등)로 해석을 다시 돌리지 않고 예측 모델과 Simulator를 만드는 방법. 사용자가 "이 표로", "이미 해석한 결과로", "결과 폴더로" 모델이나 Simulator를 요청할 때 사용.
---

# 이미 가진 데이터 → 예측 모델 → Simulator

해석을 새로 돌리지 않습니다. 그래서 **Flow 승인 없이 바로 합니다.** 툴이 `missing`이어도 표와 이미 뽑은 값으로 하는 일은 그대로 진행합니다.

## 1. 표가 있을 때
회사의 CSV·엑셀 파일은 보안 프로그램으로 암호화되어 있어서 표는 **복사·붙여넣기로 받는 것이 기본**입니다. 대화 입력창에 붙여 넣은 표는 첨부 `inputs/붙여넣은_표_*.tsv`로 옵니다.
```
dds-study.inspect_table(path="inputs/붙여넣은_표_130512.tsv")   # 열 종류·역할 추측·경고. 저장하지 않는다
dds-study.import_table(path="inputs/붙여넣은_표_130512.tsv", outputs=["최대 응력"])   # 행은 MCP가 파일에서 직접 읽어 올린다
dds-study.train_surrogate(study_id=...)                 # 교차검증 R²와 품질
dds-study.deploy_model(model_id=...)
```
- **묻는 것은 어느 열이 결과(출력)인가 하나뿐입니다.** 요청에 적혀 있으면 묻지 않습니다. 없으면 `inspect_table`의 `outputs` 추측을 보여 주고 맞는지만 한 번 묻습니다. 나머지 숫자 열은 입력이고, 머리글·단위·상수·글자 열·나쁜 행은 도구가 알아서 처리합니다. 결과처럼 보이는 열이 입력에 들면 `warnings`로 돌아옵니다. 사용자에게 알리고, 그 값도 결과라고 하면 `outputs`에 넣어 다시 부릅니다. 최적화 목표나 입력 범위는 묻지 않습니다.
- **파일을 읽지 못하면 추측하지 않습니다.** "보안 프로그램으로 암호화되어 읽을 수 없습니다"가 오면 그대로 전하고, 엑셀에서 표를 복사해 입력창에 붙여 넣어 달라고 합니다. 다른 스크립트로 열어 보지 않습니다. `results.json`·`case.h5`·해석 폴더(2절)는 상관없습니다.
- 표를 스크립트로 파싱하거나 행을 도구 인자로 옮기지 않습니다. 토큰이 많이 들고 옮기다 값이 틀어집니다.
- 표 값을 보간해 DOE 케이스를 채우지 않습니다. 실제 데이터가 아닌 값으로 정확도만 높게 나옵니다.
- 역할 추측이 틀리면 `inputs`·`outputs`를 지정합니다. 하나만 주면 나머지를 채웁니다.
- 출력이 다른 파일에 있으면 `outputs_path`와 잇는 열 `key`를 줍니다. 엑셀 시트는 `sheet`, 제목 줄이 많으면 `header_row`.
- 목적이 분명하면 `objectives`·`constraints`를 함께 줍니다(나중의 `optimize`와 최적 설계 표시에 쓰입니다).
- **행을 더할 때:** `import_table(path, study_id=…)`. 같은 행은 중복되지 않고, 범위 밖 값은 범위를 넓히고 알립니다. 다시 학습해 배포하면 공유 링크가 새 버전을 따라갑니다.

사용자에게 그대로 알릴 것: 뺀 행과 이유(`excluded_count`·`excluded_by_reason`), 고정값(`fixed`, 값이 하나뿐인 열), 뺀 열(`removed_columns`, 글자 열), 범위 변경(`range_changes`), `warnings`.

글자 열(재질 A/B 등)은 입력으로 쓰지 못해 빠집니다. 값마다 결과가 크게 다르면 값별로 표를 나눠 스터디를 따로 만드는 것이 안전합니다.

## 2. 해석을 끝낸 폴더가 있을 때
```
dds-study.scan_results(folder=r"D:\runs\bracket")       # 읽기만 한다. 네트워크 경로(\\server\...)도 된다
```
결과의 `ready`·`extract`·`inputs`·`outputs`·`tables`를 보고 나눕니다.

| 찾은 것 | 할 일 |
|---|---|
| `ready` 케이스(입력값 + 결과값) | `import_cases(folder=…)` |
| 결과 파일만 있고 값이 없음(`not_ready`, `extract`에 스킬 표시) | 아래 3절로 값을 뽑아 `import_cases(cases=[…])` |
| 입력값을 찾지 못함 | 폴더 이름(`t=3.0_w=30`), `tables`의 표, 입력 덱에서 찾습니다. 그래도 없으면 변수 표를 달라고 합니다 |
| 폴더 전체를 요약한 표가 `tables`에 있음 | 그 표로 1절을 따릅니다. 가장 빠릅니다 |
| `fields`가 있음(`fields/*.npz`) | `import_cases`가 함께 올립니다. 그다음 `train_field_model` |

- 원본 폴더에 쓰지 않습니다. 작업 폴더로 바꾸지도 않습니다(AI 도구 지침 파일과 폴더가 생겨 사용자 폴더가 오염됩니다).
- `truncated`가 있으면 폴더가 많거나 공유 폴더가 느려 다 보지 못한 것입니다. 더 아래 폴더를 직접 지정해 다시 부릅니다.
- `run_dir`은 원본 경로로 기록만 됩니다. 그 경로는 이 PC에만 있으니 다른 멤버 PC에서는 원본에서 추가 추출을 못 합니다.

## 3. 결과 파일에서 값 뽑기
추출 스크립트는 툴 스킬의 `scripts/`에 있습니다. 먼저 한 케이스로 시험하고(`--list`가 있으면 이름부터 확인), 전체로 넓힙니다. 스크립트 본문을 그대로 `dds-cae.run_script`에 넘깁니다.

| 결과 | 스킬 | 스크립트 | tool 키 | 원본을 가리키는 인자 |
|---|---|---|---|---|
| Abaqus `.odb` | `abaqus-automation` | `abq_odb_extract.py` | `abaqus_python` | `--odb 경로` |
| LS-DYNA `d3plot`·`binout` | `lsdyna-automation` | `dyna_lasso_extract.py` | `python` | `--dir 폴더` |
| Nastran `.op2`·`.f06` | `msc-nastran-automation` | `nastran_op2_extract.py` | `python` | `--job 폴더/잡이름` |
| Ansys `.rst` | `ansys-automation` | `dpf_extract_rst.py` | `ansys_python` | `--rst 경로` |
| Adams `.res` | `adams-automation` | `adams_extract.py` | `python` | `--res 경로` |
| Fluent `.out`·`.srp` | `fluent-automation` | `fluent_extract.py` | `python` | `--out`·`--srp 경로` |
| RecurDyn 곡선 CSV | `recurdyn-automation` | `rd_extract.py` | `python` | `--csv 경로` |
| OpenFOAM `postProcessing/` | `openfoam-automation` | `of_extract.py` | `python` | 스크립트가 현재 폴더를 읽습니다. 복사본에서 돌립니다 |
| nCode 손상 표 | `ncode-automation` | `ncode_extract.py` | `python` | `--csv 경로` |

- 결과 파일을 `inputs`로 복사하지 않습니다. 수 GB가 복사될 수 있습니다. 경로를 `args`로 넘기면 스크립트는 실행 폴더(`runs/`)에 `results.json`을 쓰고 원본은 그대로입니다.
- 3D 필드나 결과 전체가 필요하면 각 스킬의 '결과 전체 저장' 절을 봅니다(`abq_odb_to_npz.py`, `dyna_to_h5.py`, `op2_to_h5.py`).
- 후처리는 읽기 작업이라 승인이 필요 없습니다. 케이스가 많아 오래 걸리면 건수와 예상 시간만 먼저 알립니다.
- 사내 버전에서 확인되지 않은 추출은 스크립트의 `TODO(사내검증)` 표시를 보고 사용자에게 그렇다고 알립니다.

뽑은 값을 모아 등록합니다.
```
dds-study.import_cases(cases=[
  {"params": {"t": 3.0, "w": 30.0}, "results": {"max_mises": 212.0}, "run_dir": r"D:\runs\bracket\t=3.0_w=30", "label": "t=3.0_w=30"},
  ...
])
```
값을 나중에 더 뽑으려면(Output 추가) `add_outputs`의 `post`에 추출 단계를 줍니다. 추출 스크립트는 작업 폴더에서 돌고 원본은 args의 `{source_dir}` 또는 환경변수 `DDS_SOURCE_DIR`로 찾습니다. 예: `{"tool": "abaqus_python", "script": "scripts/abq_odb_extract.py", "args": ["--odb", "{source_dir}/job.odb"]}`.

## 4. 학습과 배포
- `train_surrogate`가 `status: "running"`이면 실패가 아닙니다. 같은 인자로 다시 부르면 이어서 기다립니다. 행이 500개를 넘으면 고르게 줄여 학습하고 `sampled`로 알립니다.
- `quality`가 `poor`이면 배포는 막지 않지만 그 뜻을 쉽게 알립니다. 솔버가 없으니 능동학습을 권하지 않고, 행을 더 달라고 합니다.
- 입력 범위는 데이터의 최소~최대입니다. 이상치 경고가 있으면 값을 확인해 달라고 합니다. Simulator 슬라이더 범위가 그만큼 넓어집니다.
- 대화에는 3~5줄만: 등록한 행 수, 뺀 행과 이유, R², 경고, Simulator 탭을 열면 된다는 안내.

## 5. 다음 요청
| 요청 | 할 일 | 승인 |
|---|---|---|
| "최적 설계 찾아 줘" | `optimize`: 해석으로 확인되지 않은 후보만 나옵니다. 그렇게 말하고 확인 해석을 권합니다 | 없음 |
| "이걸로 확인 해석 돌려 줘" | `set_study_adapter`로 해석 방법을 연결 → Flow 제안 → `run_cases` | 있음 |
| "DOE 계획을 HPC 팀에 넘겨" | `export_doe`. 값을 받으면 `import_table(study_id=…)`가 그 점을 채웁니다 | 없음 |
| "외부 모델(joblib) 넣어 줘" | 받지 않습니다. 입력·출력 표를 달라고 합니다 | — |
