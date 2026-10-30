---
name: doe-surrogate-optimization
description: DOE 설계, 케이스 반복 실행, Gaussian Process Surrogate 학습, 베이지안 최적화(BO), 능동학습(AL), 예측 Simulator 배포를 dds-study MCP 툴로 수행하는 방법. 사용자가 DOE, 근사모델, 최적화, 예측 모델을 요청할 때 사용.
---

# DOE → Surrogate → 최적화/능동학습 → Simulator

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
- 제약조건(예: 응력 ≤ 250)은 현재 단일 목적 BO만 지원합니다. 그래서 다음 중 하나로 처리합니다.
  - 페널티 방식: 스크립트에서 `mass_penalized = mass + 1e3*max(0, stress-250)`처럼 계산해 목적함수로 둡니다.
  - 최적화 뒤 표로 제약 만족 여부를 보고합니다.
- 해석 없이 흐름을 검증할 때는 `adapter="mock"`, `adapter_config={"function": "beam"}`을 씁니다(외팔보: width, height → mass, deflection, max_stress).

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
- `train_surrogate`가 돌려주는 교차검증 R²를 봅니다.
  - **0.9 이상**: 최적화 진행
  - **0.7~0.9**: 능동학습 `optimize(mode="al", iterations=5~10)`으로 보강한 뒤 다시 확인
  - **0.7 미만**: 변수 범위, 결과 추출 오류, 노이즈를 점검합니다(케이스 표 아티팩트에서 이상치를 확인).

## 4. 최적화와 배포
- `optimize(mode="bo", iterations=N)`은 제안 → 실행 → 재학습을 반복하고 수렴 그래프를 게시합니다.
- 끝나면 `deploy_model(model_id)`를 호출합니다. 웹앱 Simulator 화면에서 슬라이더로 즉시 예측(± 불확실도)할 수 있습니다.
- 마지막에 `publish_artifact(kind="markdown")`로 요약을 게시합니다. 요약에는 최적 변수, 목적값, 확인 해석(최적점에서 실제 해석 1회 권장), R²를 넣습니다.
