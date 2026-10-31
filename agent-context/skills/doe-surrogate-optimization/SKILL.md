---
name: doe-surrogate-optimization
description: DOE 설계, 케이스 반복 실행, Gaussian Process Surrogate 학습, 베이지안 최적화(BO), 능동학습(AL), 예측 Simulator 배포를 dds-study MCP 툴로 수행하는 방법. 사용자가 DOE, 근사모델, 최적화, 예측 모델을 요청할 때 사용.
---

# DOE → Surrogate → 최적화/능동학습 → Simulator

사용자가 이미 표나 해석 결과를 가졌으면 DOE부터 돌리지 않습니다. `existing-data-to-simulator` 스킬을 봅니다. 지난 스터디를 이어 가면 `list_studies`로 찾고, 신규 모델로 같은 흐름을 다시 하면 `clone_study`를 씁니다.

해석을 새로 돌리는 DOE·최적화는 Flow 승인을 받은 뒤 시작합니다. 기존 데이터로 학습하고 Simulator로 배포하는 일은 승인 없이 바로 합니다.

## 1. 스터디 만들기
```
dds-study.create_study(
  name="브래킷 경량화",
  variables=[{"name": "thickness", "lower": 2, "upper": 6}, {"name": "rib_h", "lower": 5, "upper": 20}],
  objectives=[{"name": "mass", "goal": "min"}],
  adapter="script",
  adapter_config={
    "tool": "nx", "script": "scripts/nx_param_update.py", "inputs": ["models/bracket.prt"],
    "post": {"tool": "abaqus_cae", "script": "scripts/abq_cae_param_run.py"},
    "timeout_sec": 3600
  })
```
- `timeout_sec`는 케이스 한 단계의 한계입니다. 넘으면 그 아래 솔버까지 모두 끝나고 결과가 남지 않습니다. 예상 해석 시간보다 넉넉히 줍니다.
- 변수 이름은 스크립트가 `params.json`에서 읽는 키와 같아야 합니다.
- 목적함수 이름은 스크립트가 `results.json`에 쓰는 키와 같아야 합니다.
- `inputs`와 `script` 경로는 **프로젝트 폴더 기준**입니다. 프로젝트 폴더 위치는 `dds-cae.project_folder`로 확인합니다.
- 제약조건(예: 응력 ≤ 250)은 `constraints=[{"name": "max_stress", "upper": 250}]`로 줍니다. 최적 케이스는 제약을 만족한 것 중에서 고르고, `train_surrogate`의 `outputs`에 제약 항목도 넣으면 제안할 때 제약 만족 확률을 곱합니다.
- 변수와 출력의 `note`에 쉬운 이름과 단위를 적습니다(`"note": "두께 [mm]"`). 판단 문장이 이 이름과 단위로 나옵니다.
- **목표가 둘 이상이면** `objectives`에 모두 적고 `train_surrogate(outputs=[...])`에 목표를 모두 넣은 뒤 `optimize(mode="bo")`를 씁니다. 첫 목표만 보지 않고 함께 좋아지는 쪽을 찾습니다. 항상 더 낫다고 말하지 않습니다(부딪힘이 작은 문제에서는 차이가 작습니다).
- 해석 없이 흐름을 검증할 때는 `adapter="mock"`, `adapter_config={"function": "beam"}`을 씁니다(외팔보: width, height → mass, deflection, max_stress).
- 불러온 표·해석 결과로 만든 스터디는 `adapter="data"`입니다. 돌릴 수 없고, 나중에 `set_study_adapter`로 해석 방법을 연결하면 이어 갑니다.
- DOE 계획만 필요하면 `generate_doe` 뒤 `export_doe`로 CSV를 넘깁니다. 스크립트는 케이스가 처음 끝날 때 PC Agent가 서버에 자동으로 올려 다른 멤버가 쓸 수 있습니다. 스크립트가 쓰는 파일이 더 있으면 `save_recipe(files=…)`로 더합니다.

## 2. DOE 크기
| 변수 수 | 초기 DOE (LHS) | BO 반복 |
|---|---|---|
| 1~3 | 10~20 | 10~15 |
| 4~6 | 30~50 | 15~25 |
| 7~10 | 60~100 | 25~40 |
- 해석 1건이 오래 걸리면 초기 DOE를 줄이고 능동학습(`al`)으로 보강합니다.
- `run_cases`는 케이스를 사용자 PC Agent로 보냅니다. `wait_sec` 안에 끝나지 않으면 `get_study`로 다시 확인합니다.
- 실패한 케이스는 `recent_failures`에서 오류를 보고 스크립트를 고친 뒤 다시 실행합니다(`run_cases(case_ids=[...])`).

## 3. 학습 품질 판단
- `train_surrogate`가 돌려주는 `quality`·`warnings`와 `study_insights`의 "이 예측을 믿어도 되나요?"를 봅니다(경계는 교차검증 R²와 학습 건수로 정해져 있습니다). `status: "running"`이면 실패가 아닙니다. 같은 인자로 다시 부릅니다.
  - **믿을 만함**: 최적화 진행
  - **참고용**: 해석 방법이 있으면 능동학습 `optimize(mode="al", iterations=5~10)`으로 보강한 뒤 다시 확인. 불러온 데이터 스터디(솔버 없음)는 돌릴 수 없으니 행을 더 달라고 합니다.
  - **믿기 어려움**: 변수 범위, 결과 추출 오류, 노이즈를 점검합니다("해석 결과에 이상한 값은 없나요?"와 케이스 표에서 확인).
  - **아직 판단 못 함**: 문장이 말하는 건수만큼 해석을 더 합니다. 불러온 데이터 스터디는 행을 더 달라고 합니다.

## 4. 최적화와 배포
- `optimize(mode="bo", iterations=N)`은 제안 → 실행 → 재학습을 반복하고 수렴 그래프를 게시합니다. 불러온 데이터 스터디에서는 해석 없이 모델로만 후보를 찾습니다(`verified=false`). 해석으로 확인되지 않은 예측이라고 말합니다.
- 끝나면 `deploy_model(model_id)`를 호출합니다. 웹앱 Simulator 화면에서 슬라이더로 즉시 예측(± 불확실도)할 수 있습니다.
- 마지막에 `publish_artifact(kind="markdown")`로 요약을 게시합니다. 요약에는 최적 변수, 목적값, 확인 해석(최적점에서 실제 해석 1회 권장), R²를 넣습니다.

## 5. 결과 설명
- 끝나면 `study_insights(study_id, publish=True, flow_id=…, node_id=…)`를 부르고, 받은 문장을 고치지 않고 인용합니다.
- 전문 지식이 없는 사람이 읽습니다. 질문형 제목, 상태 말(질문의 답), 한 문장 결론, 다음 할 일 순서로 쓰고 용어는 물어볼 때만 씁니다. `status`가 `computing`이면 인용하지 말고 다시 부릅니다.
- 예측값에는 "예측"을 붙입니다. 해석으로 확인한 값과 섞지 않습니다.
- 능동학습은 "추가 전후에 평균 오차가 얼마나 줄었는지"만 말합니다. 무작위와 비교한 값은 사용자 스터디에 없습니다.
- 수렴은 멈춤 조언을 조건과 함께 전합니다("예측 모델이 보기에는"). 판단 항목이 초록이 아니면 숨기지 않습니다.
