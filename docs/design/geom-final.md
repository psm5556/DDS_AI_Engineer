# 파라미터 없는 형상 대응: 최종 설계 (예측·DOE·최적화)

저장소는 고치지 않았고 서버도 띄우지 않았다. 임시 폴더에서 실험만 하나 더 돌렸다(2-2절).

## 0. "파라미터가 없는데 어떻게 DOE·최적화를 하나요?" (10줄 답)

1. DOE에 필요한 것은 CAD 히스토리가 아니라 "숫자 몇 개를 바꾸면 형상이 바뀌는 규칙"이다.
2. 파일에 그 규칙이 없으면 AI가 새로 만든다. 메쉬 표면에 손잡이 12개를 자동으로 놓는다.
3. 손잡이를 mm 단위로 밀면 주변 메쉬가 부드럽게 따라온다(RBF 모핑). 이 이동량이 곧 설계변수다.
4. 고정·하중·접촉 부위는 움직이지 않게 잠그고, 노드 번호가 유지되므로 경계조건도 그대로 간다.
5. 엔지니어는 3D에서 슬라이더로 극단 형상을 보고 승인한다. 손잡이의 뜻은 AI의 추정이기 때문이다.
6. 그 뒤로는 일반 스터디와 같다. LHS로 뽑고, 실제 솔버로 돌리고, GP를 학습하고, 베이지안 최적화를 한다.
7. 해석 전에 뒤집힌 요소를 검사해 망가진 형상은 솔버 없이 제외한다.
8. 최적안은 실제 솔버로 다시 확인하고, 덱·STL·변형 지도로 돌려준다.
9. 실험(직접 만든 선형 FEM, 해석 64회)에서 최대 응력 -32.8%, 메쉬를 새로 만들어 재확인하니 -28.8%였다.
10. 못 하는 것도 분명하다. 리브·구멍 추가 같은 위상 변경은 안 되고, 결과는 편집 가능한 CAD가 아니며, 결과 파일만 있으면 DOE를 할 수 없다.

## 1. 세 설계 평가 (5점 만점, 제 판단)

| 기준 | user-first | mvp-now | wow-first |
|---|---|---|---|
| 사용자 감탄 | 4 | 3 | **5** |
| 정직성·과학적 타당성 | **5** | 4 | 4 |
| 라이선스·GPU 없이 지금 검증 | 3 | **5** | 3 |
| 기존 코드와의 맞물림 | 4 | **5** | 4 |
| 사내 GPU 경로 | 4 | 4 | 4 |
| 합계 | 20 | 21 | 20 |

**각 설계의 약점**
- **user-first:** STEP 예측이 gmsh(GPLv2+, 법무 미확인)에 기댄다.
- **mvp-now:** 범위 밖 입력에 경고만 하고 값을 보여 주는 경우가 많다. 화면 경험도 약하다.
- **wow-first:** 입력 등급을 두 벌 학습해야 해서 비용이 크고, 시나리오 일부가 뒤로 밀린다.

**최종안의 조합**
| 가져온 곳 | 내용 |
|---|---|
| mvp-now (뼈대) | GeoSpace 형식, 어댑터 `prepare_geometry` 이음새, `mock_fe`로 7개 시나리오 끝까지 검증, 표면 입력 + 복셀 FE |
| wow-first (킥) | 승인 카드, 응력 부위 클릭, 탐색 공간 다듬기, `infeasible` 상태, 기존 MCP 서버에 툴 추가 |
| user-first (정직성) | 값/순위 전용/배포 안 함 등급, 거절 시 숫자 없음, `force` 없음, 손잡이 변수 이름표 |

## 2. 이번에 직접 확인해서 바뀐 것

### 2-1. 코드 확인
| 확인한 것 | 설계에 미치는 영향 |
|---|---|
| `requirements-agent.txt`에 scipy·numpy·h5py는 있고 scikit-learn은 없다 | PC에서 도는 `morph.py`는 sklearn을 쓸 수 없다. k-means는 `scipy.cluster.vq.kmeans2`로 쓴다 |
| `service.suggest`가 `existing`을 넘기지 않는다 | 이미 해석한 점과 실패한 점을 피하지 않는다. 고친다 |
| `bayesopt.suggest` 후보는 상자 전체 Sobol뿐이다 | 12차원 모서리 근처에 가기 어렵다(2-2절) |
| `_run_on_server`는 `project_dir`를 넘기지 않는다 | 서버 mock에서 모핑을 돌리려면 고친다 |
| `FieldViewer3D`에 클릭 기능이 없다 | Raycaster `onPick`을 더한다 |
| 실험 특징(`feats.py`)은 고정 프레임, L=200, 고정부·하중부까지의 측지 거리를 쓴다 | CAD만 넣는 예측은 경계조건 틀과 정렬 없이는 **이 수치를 재현하지 못한다** |
| 조사 메모의 줄 번호 일부가 동시 작업으로 밀렸다 | 이 문서는 함수 이름으로 가리킨다 |

### 2-2. 저장소 BO를 12손잡이에 그대로 쓸 수 있는가 (새 실험)
같은 메쉬, 같은 FEM에서 DOE 40점 뒤 6개씩 4라운드, 해석 24회를 더 했다.

| 방식 | 기준 대비 최대 응력 비율 (DOE 시드 3 / 7) | 범위 끝에 붙은 손잡이 |
|---|---|---|
| DOE만 | .880 / .834 | - |
| A. 저장소 그대로 | **.727 / .727** | 1 / 1 |
| B. 로그 변환 + 상위 5개 주변 후보 + `existing` | **.663 / .681** | 8 / 6 |

**해석**
- 저장소 BO로도 개선은 되지만 후보를 전역에서만 뽑아 모서리에 못 간다. B를 쓰기로 정한다.
- **근거가 약하다.** 메쉬 하나, 선형 정적 문제다. A는 두 시드에서 후보 풀이 같아 같은 값이 나왔으므로 독립 반복이 아니다.
- B는 범위 끝 손잡이가 많다. 범위를 더 넓힐 여지가 있다는 뜻이고, 진짜 최적이 아니라는 뜻이기도 하다.

스크립트: `C:\Users\psm55\AppData\Local\Temp\claude\C--Users-psm55-Git-DDS-AI-Engineer-frontend-src\e6e513a2-b37b-41b8-ae34-e76b6f9f764c\scratchpad\exp_bo\bo_compare.py`

## 3. 입구 하나, 경로 자동 판별

`inspect_geometry(path)`가 파일 상태를 보고 경로와 이유를 한 줄로 낸다.

| 가진 것 | `route` | 시나리오 | 위상 변경 | 돌려주는 것 |
|---|---|---|---|---|
| 히스토리 있는 CAD | `cad_params` | S7 기존 경로 | 가능 | CAD |
| `.inp`·`.bdf`·`.k` | `deck_morph` | S3 | 불가 | 덱·STL·변형 지도 |
| STEP·STL | `cad_dumb_morph` (하중 입력 후) | S2 → S3 | 불가 | 메쉬·STL |
| 파일 + 배포된 형상 모델 | `predict` | S1 | - | 예측 + 판정 |
| 메쉬 구조가 같은 옛 케이스 | `latent` | S4 → S3 | 불가 | 슬라이더 Simulator, 덱·STL |
| 메쉬가 다른 옛 케이스 | `import_train` | S4 | - | 예측 모델 |
| 결과 파일만 (odb·d3plot·op2) | `train_only` | **DOE 거절** | - | 학습 재료 |
| 경계조건을 읽지 못한 덱 | `needs_bc` | 사용자 입력 후 재판별 | - | - |
| 위상 변경이 필요한 요청 | 모핑 거절 | 위상최적화 툴 스킬 제안 | - | - |

## 4. 핵심 부품과 알고리즘

처음 값은 전부 판단값이다. 임계값은 `cae_adapters/geom/defaults.py` 한 곳에 모으고 사내에서 다시 잰다.

### 4-1. GeoSpace: `x(a) = P0 + Σ aᵢUᵢ`
RBF 모핑과 PCA 잠재공간이 같은 형식을 쓴다. `aᵢ`는 일반 `Variable`이어서 DOE·GP·BO·`train_field_rom`을 그대로 탄다.

**손잡이 제안 (`propose_handles`)**
1. 대상: 잠금 영역을 뺀 표면 노드
2. 특징: `[좌표/대각선, 0.125 × 법선]`
3. `kmeans2`, 기본 12개. 군집 중심에 가장 가까운 노드가 손잡이, 면적 평균 법선이 방향
4. `hotspots`가 있으면 그 주변에 5개를 몰아 놓는다

실험에서는 축마다 다른 가중치를 손으로 줬다. 위 일반식은 아직 시험하지 않았으므로 1단계에서 재확인한다.

**변형 계산**
- 기본: 3차 RBF `φ=r³` + 1차 다항식. 실험과 같다.
- 국부 손잡이: Wendland C2. 다른 부위가 움직이지 않게 영향 반경을 대각선의 0.3배로 시작한다.
- 앵커: 잠금 노드에서 먼 점 우선으로 최대 200개
- `U`는 한 번만 계산해 `basis.npz`에 저장(float32, 2만 노드씩 나눠 계산). 케이스마다 행렬 곱만 한다.
- 잠금 노드는 `U=0`으로 강제한다. 그래야 이동 0을 보장한다.
- 2차 요소 중간 노드: `x_mid = (x_a+x_b)/2 + (x0_mid-(x0_a+x0_b)/2)`. 원래 곡률을 유지한다.

**자동 잠금:** 고정·하중 노드, TIE·COUPLING·접촉 세트, 사용자가 지정한 영역

**범위 자동 조정 (`safe_amplitude`)**
1. 시작 범위: `min(0.015 × 대각선, 0.4 × 최소 국부 두께)`
2. 무작위 200점 + 축별 극단을 품질 검사
3. 실패하면 0.7배로 줄인다(최대 8회)

**품질 검사 (`quality.py`)**
- 솔리드: `det(J)/det(J0) ≥ 0.2`, 요소 부피비 0.3~3
- 쉘: 법선 회전 60° 미만, 면적비 0.3~3
- 파트 간 거리: 침투하면 실패

### 4-2. 덱 좌표 재작성 (`deck_write.py`)
읽기용 `deck.py`는 주석을 버려서 쓸 수 없다. 줄 단위 재작성기를 따로 만들고, 형식별 첫 구현을 `.inp`로 한다.

- 좌표 줄만 바꾼다. 움직이지 않은 노드 줄은 바이트까지 그대로다.
- 쓴 뒤 다시 읽어 `|x_읽음 - x_기대| ≤ 형식 분해능`을 확인하고, 틀리면 실패 처리한다. 좌표가 조용히 틀리는 경우가 가장 위험하기 때문이다.
- 인스턴스 이동·회전은 역변환해 파트 좌표로 쓴다.
- **추측하지 않고 이유와 함께 거절:**

| 형식 | 거절 조건 |
|---|---|
| Abaqus | `*NODE, INPUT=`, `*PARAMETER`, `*SYSTEM`, 인스턴스 2개 이상인 파트 변형, 찾지 못한 `*INCLUDE` |
| Nastran·OptiStruct | `CP≠0`인 GRID |
| LS-DYNA | 지원하지 않는 `*NODE` 형식 |

### 4-3. 어댑터 이음새
`CaeAdapter.prepare_geometry(params, run_dir, config, project_dir)`를 더한다. `ScriptAdapter`와 `MockAdapter`가 입력 복사 뒤에 호출한다.
- 케이스 폴더의 덱 **복사본**을 같은 이름으로 덮어쓴다. 뒤 단계는 파라미터 스터디와 똑같다.
- AI가 모핑 스크립트를 쓰지 않으므로 틀릴 곳이 줄어든다.
- 품질 불합격은 `CaseOutcome("infeasible", error=...)`로 솔버 없이 끝난다.

```jsonc
// Study.adapter_config
{"tool": "abaqus", "script": "...", "post": [...],     // 시험할 때는 {"function": "fe_deck"}
 "geometry": {"space": "geom/s12/space.json", "deck": "inputs/model.inp"}}

// <프로젝트>/geom/s12/space.json + basis.npz (PC에 둔다. 다른 PC에서는 손잡이로 다시 계산)
{"schema": "dds-geomspace/1", "kind": "rbf",           // rbf | latent
 "source": {"file": "model.inp", "sha256": "...", "format": "inp", "length_unit": "mm"},
 "handles": [{"var": "h01", "at": [0,0,0], "dir": [0,0,1], "lower": -3, "upper": 3,
              "label": "왼쪽 구멍 둘레(추정)", "kernel": "cubic"}],
 "frozen": {"node_sets": ["FIX", "TIP"], "reason": "경계조건·하중"},
 "limits": {"min_jac_ratio": 0.2, "vol_ratio": [0.3, 3.0]},
 "parent": {"study_id": null, "case_id": null}}

// 서버로 올리는 preview.npz
// 축소 표면(최대 5만 삼각형) + 표면 영향 행렬 → 브라우저가 슬라이더를 바로 계산한다
```

### 4-4. BO 보강
`bayesopt.suggest`에 인자를 더한다. **`geometry_mode`가 `params`가 아닐 때만 켠다.** 기존 스터디 동작은 그대로 둔다.
- `existing`: 해석한 점 전체
- `avoid`: `infeasible` 점, 정규화 거리 0.15 이내 감점
- 주변 후보: 상위 5개 주변에 각 400개, σ = 범위의 0.1배
- 로그 변환: `Surrogate`에 `log_outputs`를 더하고, EI는 로그 공간에서 계산한다

옛 joblib 모델은 `getattr` 기본값으로 읽는다.

**DOE 점수:** 기본은 지침대로 변수 × 8~10. 해석이 비싸면 변수 × 4로 시작해 R² 0.9 미만이면 능동학습으로 보강한다. 실험은 40점(변수 × 3.3)이었으나 선형 정적이라 그대로 믿지 않는다.

### 4-5. 공간 다듬기 (`refine_design_space`)
- 대리모델 민감도 비중 3% 미만인 손잡이는 뺀다.
- 가장 민감한 손잡이는 둘로 나눈다.
- 범위 끝에 닿은 손잡이는 `safe_amplitude`를 통과하면 1.5배로 넓힌다.
- 최적안을 새 기준 덱으로 삼아 새 스터디를 만든다(`parent_study_id`). 한 번에 손잡이는 12개까지다.

### 4-6. 같은 메쉬 묶음의 잠재공간 (`build_latent_space`)
- 표면 좌표를 PCA(에너지 99%, 최대 8모드) 해서 점수를 변수로 쓴다. 범위는 관측 최소·최대 ±10%.
- `kind=latent`로 같은 GeoSpace에 넣어 DOE·덱 재작성·해석을 재사용한다.
- 분산이 큰 축이 민감한 축은 아니다. 화면과 지침에 적는다.

### 4-7. 형상 입력 모델 (S1·S4)
**점별 모델:** `HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, max_leaf_nodes=63, min_samples_leaf=40, l2_regularization=1.0, early_stopping=False)`. 출력은 `log1p(응력)`과 변위를 따로 둔다.

**입력 점은 표면 노드로 통일한다.** 학습은 `train.h5`의 표면 노드, 예측은 입력 표면이다. STL만 있어도 된다. 표면 삼각형이 크면 학습 메쉬 중앙값의 2배 이하로 나눈다.

| 특징 | 내용 | 비고 |
|---|---|---|
| 형상 | 정렬 좌표, 법선, 다중 스케일 점유값·기울기, 전역 6개 | 학습·예측이 같은 코드 |
| 조건 | 고정부·하중부까지의 거리 | 경계조건 틀에서 나옴 |
| 저충실도 물리 | 거친 FE의 응력·변위 | 기본 복셀 h=4 mm(약 1초), 정밀 h=2.5 mm(약 6.6초), gmsh가 있으면 거친 사면체 |

**결정 사항**
- 모델은 `coarse_kind`를 하나만 갖는다. 학습과 다른 종류로 예측하지 않는다.
- 거친 FE가 실패하면 낮은 등급으로 내려가지 않고 거절한다. 모델을 두 벌 두지 않는다.
- 변형 좌표는 입력에 쓰지 않는다(`geometry_source="field_points"` 거절). 답이 입력에 섞이기 때문이다.
- 응력 최대값은 고정·하중 부위를 빼고 계산한다.

**샘플 수별 동작**
| 상황 | 동작 |
|---|---|
| 같은 메쉬 4개 이상 | 기존 PCA+GP |
| 다른 메쉬 15개 미만 | 모델 없음. 유사 형상 찾기만 |
| 다른 메쉬 15~29개 | 시험 모델, 배포 불가 |
| 다른 메쉬 30개 이상 | 판정 통과 시 배포 |

### 4-8. 경계조건 틀 + 정렬
CAD에는 하중·고정·재료가 없다. 그래서 모델은 **하중 조건 하나에 묶이고**, 조건을 새 형상으로 옮기는 단계가 필수다. `shape-learning-plan.md` 6절에 빠져 있는 단계다.

```jsonc
// FieldModel.bc_template
{"frame": "canonical", "length_unit": "mm", "idealization": "solid",
 "material": {"E": 210000, "nu": 0.3},
 "patches": [{"kind": "fixed", "dof": [1,1,1,1,1,1], "center": [0,0,0], "normal": [-1,0,0], "area": 3600},
             {"kind": "load", "force": [0,0,-1000], "center": [200,30,20], "normal": [1,0,0], "area": 1200}]}
```

**정렬 (`align.py`)**
1. 원래 좌표가 학습 틀에 대각선 2% 안으로 맞으면 그대로 쓴다.
2. 아니면 단위 후보(0.001·0.0254·1·1000) × 주축 회전 24개 + ICP 30회(5,000점). 거울상은 금지한다.
3. 1, 2위 점수 차가 10% 안이면 그림 2~4개로 사용자에게 묻는다.

기존 `align_to_reference`는 쓰지 않는다. 같은 형상에서도 9.58 mm 어긋났다.

**틀 옮기기:** 법선 30°로 표면을 영역 성장해 면 후보를 만들고, 패치마다 중심 거리 ≤ 대각선의 5%, 법선 차 ≤ 15°, 면적비 0.7~1.3을 만족하는 후보를 찾는다.
- 하나로 정해지면 통과, 애매하면 면을 클릭하게 한다, 없으면 거절한다.
- **이 알고리즘은 아직 시험하지 않았다.** 실험은 모든 부품의 경계조건 위치가 같았다. 이 예측 경로의 가장 큰 불확실성이다.

### 4-9. 신뢰 판정 (`geoguard.py`)

**모델 배포 등급.** 형상 그룹 시험을 쓰고, 부품군이 둘 이상이면 부품군 통째로 뺀다.

| 등급 | 조건 | 화면 |
|---|---|---|
| 값 | 시험 형상 ≥10, 최대 응력 오차 중앙값 ≤10%, 90%점 ≤25%, 순위 상관 ≥.9, 기준선 2개를 이김 | MPa + 범위 |
| 순위 전용 | 순위 상관 ≥.8, 최근접 형상을 이김 | 기준 대비 %와 순위, 분포는 상대 색 |
| 배포 안 함 | 그 밖 | 이유와 더 해석할 형상 |

- 기준선: 최근접 형상 복사, 거친 FE 단독. 같은 형상의 재메쉬 차이(잡음 바닥, 실험 약 8%)를 함께 표시한다.
- 실험 최고 모델도 부품군 A 중앙값 .103이라 **값 등급을 넘지 못한다.** 합성 데이터의 데모는 "순위 전용"으로 나온다.
- 부품군이 하나면 "처음 보는 부품군에 대한 근거 없음"을 표시한다.

**예측 판정**

| 판정 | 조건 | 화면 |
|---|---|---|
| `refuse` | 단위·자세 미확정, 틀 옮기기 실패, 쉘·솔리드 불일치, 거친 FE 실패, 복셀 부피비 0.9~1.1 밖이거나 끊어짐, 범위 밖 점수 > 1.5τ | **숫자 없음.** 이유 + 다음 할 일 |
| `caution` | τ < 점수 ≤ 1.5τ, 또는 최대 응력 위치가 본 적 없는 부위 | 참고값, 구간 없음, 부위 지도 |
| `ok` | 그 밖 | 값·구간 + "예측이며 해석이 아닙니다" |

- **범위 밖 점수:** 점유 격자 PCA(16차원) 복원 오차. τ는 5겹 교차검증 p95.
- **측정값:** AUROC .924~.931이지만, 처음 보는 부품군의 **절반(6/12)이 경고 없이 통과**했다. 부품군 한 쌍, 12 대 12 시험이다.
- **구간:** 부품군 제외 시험 오차 q90. 실험에서 ±59%로 넓다. 범위 밖에서는 보장이 깨지므로 `caution`에서 보여 주지 않는다.
- **트리 앙상블 편차는 쓰지 않는다.** AUROC .347, 노드 상관 .195였다. 이 모델에서는 "불확실도" 토글이 "본 적 없는 부위"(학습 표면까지의 거리)로 바뀐다. 이 지도와 실제 오차의 관계는 3단계에서 잰다.
- API에 `force`가 없다. AI도 거절된 값을 말로 추측하지 못하게 지침과 시험으로 막는다.

### 4-10. 근거 표시
모든 결과에 `evidence`를 붙인다: `predicted | solved_mock | solved`.
- `predicted`는 내보낼 수 없다.
- `solved_mock`은 내보낼 수 있지만 "연습용 해석기 결과"라고 표시한다. 시연 수치가 실제 해석으로 오해받지 않게 하기 위해서다.
- 단일 솔리드이고 gmsh가 있으면 독립 재메쉬 확인도 한다.

## 5. 구현 명세

### 5-1. 새 파일
| 경로 | 내용 |
|---|---|
| `cae_adapters/geom/defaults.py` | 임계값 모음 |
| `cae_adapters/geom/space.py` | `GeoSpace`, `propose_handles`, `safe_amplitude`, `from_snapshots`, `refine` |
| `cae_adapters/geom/quality.py` | 품질 검사 |
| `cae_adapters/geom/femodel.py` | `.inp`(기존 `deck.py`)·`.bdf`·`.k` → `FeModel(nodes, elements, fixed, forces, unsupported)` |
| `cae_adapters/geom/deck_write.py` | 좌표 재작성 |
| `cae_adapters/geom/geomio.py` | STL 읽기 통합(CAD 스킬 3곳 중복), `write_stl` |
| `cae_adapters/geom/faces.py` | 면 후보 번호 |
| `cae_adapters/geom/align.py` · `bc_template.py` | 정렬·경계조건 틀 |
| `cae_adapters/geom/voxel.py` · `features.py` | 복셀 FE, 점별 특징 |
| `cae_adapters/geom/mesher.py` | gmsh 별도 프로세스 실행(시간 제한). 없으면 할 일을 알려 주는 오류 |
| `cae_adapters/mock_fe.py` | 사면체·육면체 선형 탄성, `.inp`의 경계조건·하중 적용. `mock`에 `function:"fe_deck"`으로 등록 |
| `cae_adapters/mock_shapes.py` | gmsh 없이 메쉬 구조가 다른 부품군 A·B 생성 |
| `server/app/study/shape_model.py` · `geoguard.py` · `triage.py` · `shape_index.py` | 학습·예측, 판정, 묶음 정리, 유사 형상 |
| `server/app/routers/geometry.py` | 5-3절 API |
| `requirements-geometry.txt` | gmsh 고정. `requirements-dev.txt`에 포함, 운영은 법무·미러 확인 후 |
| `agent-context/skills/design-space/SKILL.md` | 모핑 스킬 |
| `scripts/e2e_geometry.py` · `scripts/bench_geometry/` | S1~S7 점검, 실험 코드 재현(임시 폴더에서 1단계에 옮긴다) |

### 5-2. 고칠 곳과 DB
- **`schemas.py`**
  - `Variable`에 `label`, `kind`(`param|handle|pca`), `meta`. 지금은 모르는 필드가 버려진다.
  - `StudyCreate`에 `geometry_mode`. 변수 0개는 `free`에서만 허용하고, 다른 모드는 만들 때 거절한다.
  - `Objective`에 `transform`.
- **`dataset._split`:** `Case.geometry.group` 기준 분할. 학습 분할이 비면 `failed`. 지금은 변수 없는 샘플 40개가 모두 test로 간다.
- **`service.py`**
  - `FINAL`·`CASE_STATUS_KO`에 `infeasible`, `CASE_SOURCE_KO`에 `import`·`verify`
  - `suggest`에 4-4절 인자, `_run_on_server`에 `project_dir`
  - `deploy_version` 앞에 등급 검사
- **`SimulatorPage.tsx`, `Field3DPanel.tsx`:** `spec.input_mode`로 분기. 지금은 변수 0개면 예측을 부르지 않는다.
- **`/models/{id}/predict`:** 형상 입력 모델이면 400 + "형상 파일을 넣어 예측합니다".

**DB** (nullable 컬럼, 자동 추가)

| 대상 | 추가 |
|---|---|
| `studies` | `geometry_mode`(`params|morph|latent|free`), `geometry_space_id`, `parent_study_id` |
| `cases` | `geometry`(`{sha, group, family, evidence, quality, source_file}`), `status`에 `infeasible`, `source`에 `import`·`verify` |
| `field_models` | `kind`(`rom|pointwise|neural`), `input_mode`, `dataset_id`, `bc_template`, `gate`, `coarse_kind` |
| 새 테이블 | `geometry_spaces`, `geometry_uploads`, `shape_predictions`, `case_imports`, `training_jobs` |

**Simulator:** 새 구조를 만들지 않는다. `SurrogateModel(algo="geometry")`로 두면 버전·되돌리기·공유·목록을 그대로 쓴다. 사용자 PC 절대 경로는 서버에 저장하지 않는다.

### 5-3. API (`schemas.py`와 테스트를 같이 바꾼다)
| 메서드·경로 | 용도 |
|---|---|
| `POST /api/projects/{pid}/geometry-spaces` · `GET /api/geometry-spaces/{id}` | 공간 등록·미리보기 |
| `POST /api/models/{id}/geometries` | 파일 업로드, 202 + 번호, 비동기 준비 |
| `GET /api/geometries/{gid}` · `/mesh` · `DELETE` | 단계(`reading·meshing·aligning·bc·features·predicting`), `needs_input` |
| `POST /api/geometries/{gid}/confirm` | 단위·자세·면 확인 |
| `POST /api/models/{id}/geometries/{gid}/predict` | → 5-4절 응답 |
| `POST /api/geometries/{gid}/verify` | 프로젝트 대화에 해석 흐름 제안(멤버만) |
| `POST /api/shares/{token}/geometries` · `/predict` | 공유 링크. 해석 버튼 없음, 파일은 올린 사람만 열람, 처음 30일 보관 |
| `POST /api/projects/{pid}/imports` · `GET /api/imports/{id}` · `POST /api/imports/{id}/commit` | 가져오기·감사·확정 |
| `POST /api/studies/{id}/cases/import` | 완료 케이스 생성(이어서 기존 archive 업로드) |
| `POST /api/datasets/{id}/train-geometry` · `GET /api/training/jobs/{id}` | 학습·진행률 |
| `GET /api/field-models/{id}/report` | 기준선 비교, 등급 |
| `GET /api/cases/{id}/shape?format=stl|deck|map` | `predicted`면 403 |

`GET /api/models/{id}/spec`에 `input_mode`, `accepts`, `template`, `grade`를 더한다.

### 5-4. 예측 응답
```jsonc
{"verdict": {"level": "ok|caution|refuse", "reasons": ["원인 → 할 일"], "ood_score": 0, "ood_threshold": 0},
 "grade": "value|rank",
 "evidence": "predicted",
 "outputs": [{"name": "max_stress", "value": 0, "low": 0, "high": 0, "relative_pct": 0}],  // refuse면 null
 "field": {"points_b64": "", "values_b64": "", "triangles_b64": "", "unseen_mm_b64": ""}, // refuse면 표면만
 "conditions": "A면 고정·B면 1000 N 조건의 예측입니다",
 "timing": {"meshing": 0, "features": 0, "predict": 0}}
```

### 5-5. PC Agent (`handle()`과 서버 호출을 짝으로, `__version__` 올림)
| 메시지 | 용도 |
|---|---|
| `geometry.prepare` | 서버가 STEP을 못 읽을 때 PC에서 변환·메쉬 |
| `morph.preview` | 극단 형상과 품질 |
| `local.scan_cases` · `case.import` | 폴더 훑기, `case.h5`로 묶어 올리기 |

**STEP 처리 순서:** 서버 gmsh → 요청자의 PC Agent → "STL로 내보내 넣으세요". PC Agent 없는 공유 링크 사용자에게는 첫 번째만 해당한다.

### 5-6. MCP (기존 서버에 추가해 연결 설정을 늘리지 않는다)
| 서버 | 툴 | 역할 |
|---|---|---|
| `dds_cae` | `inspect_geometry(path)` | 종류, 메쉬 통계, 경계조건 상태, `route`와 이유 |
| | `list_faces(path)` | 면 후보 번호 |
| | `prepare_cad_deck(path, fixed, loads, material, size)` | STEP·STL → `.inp` |
| | `propose_design_space(path, goal, n_handles=12, hotspots=None, frozen=None, amplitude="auto", parent_case_id=None)` | 손잡이·범위·품질 → `space_id` |
| | `preview_morph(space_id, params=None)` | 품질과 STL |
| | `export_design(case_id, formats)` | 덱·STL·변형 지도. `predicted` 거절 |
| `dds_study` | `create_morph_study(space_id, objectives, constraints, solver)` | 기존 `create_study` 호출 |
| | `refine_design_space(study_id)` | 4-5절 |
| | `build_latent_space(study_id)` | 4-6절 |
| | `verify_designs(study_id, case_ids\|params, top_k, remesh=False)` | 실제 해석 |
| | `import_cases(folder, name)` · `import_status(id)` · `commit_import(id, groups)` | 가져오기 |
| | `train_shape_model(dataset_id, target)` · `training_status(job_id)` | 계획 문서의 이름 유지 |
| | `predict_shape(model_id, path)` | 판정 포함, 덮어쓰기 인자 없음 |
| | `screen_shapes(study_id, n, top_k)` | 후보 거르기, 항상 미검증 |
| | `find_similar_shapes(path)` | 유사 형상, 프로젝트 간은 관리자 승인 |

`generate_doe`, `run_cases`, `optimize`, `train_surrogate`, `train_field_model`, `deploy_model`은 그대로 쓴다. `build_dataset`에는 `condition`, `group_by`만 더한다.

### 5-7. AI 지침 (`INSTRUCTIONS.md` "2-1. 파라미터 없는 형상" + 스킬)
1. "파라미터가 없어 못 합니다"라고 답하기 전에 `inspect_geometry`를 부른다.
2. 파라미터 CAD는 기존 경로로 간다. 모핑으로 우회하지 않는다.
3. 묻는 것은 목표, 파일에 없는 하중·재료, 건드리면 안 되는 부위를 한 번에. 모르는 값은 지어내지 않는다.
4. 기준 해석을 먼저 하고, 경계조건 근처 특이점은 집중부에서 뺀다.
5. 손잡이 이름에 "추정"을 붙이고 3D 카드로 **승인을 받은 뒤** 시작한다.
6. 본 DOE 전에 기준 + 극단 2건을 먼저 돌린다.
7. `케이스 수 × 회당 시간 ÷ 라이선스 수`를 미리 알린다.
8. `infeasible`이 DOE의 20%를 넘으면 범위를 줄인다. 손잡이의 1/3 이상이 범위 끝이면 "진짜 최적이 아니다"라고 쓴다.
9. 금지: 해석 없는 결과를 최적안이라 부르기, `refuse`를 숫자로 풀어 말하기, 예측을 해석이라 부르기, 모핑으로 위상이 바뀐다고 말하기, 설계 의도를 복원했다고 말하기, "안전합니다".
10. 제조 조건·구배·조립 간섭은 입력이 없으면 고려하지 않았다고 적는다.

**함께 고칠 문서** (안 고치면 에이전트가 거절하거나 계획이 어긋난다)
- `abaqus-automation/SKILL.md`, `references/inp-templating.md`의 "메쉬 바뀌는 형상 변경 불가"
- `shape-learning-plan.md` 2절: 신경망 생성 모델 제외는 유지, 모핑·선형 잠재공간 허용
- `shape-learning-plan.md` 6절: 경계조건 틀 단계 추가
- `product-spec.md` 3-7절, `AGENTS.md` 지도, `local-data.md`, `cae-data-hdf5.md`

### 5-8. 화면 (토큰만, `modern.css` 끝에 덧붙임, 같은 버튼을 두 곳에 두지 않음)
| 화면 | 내용 | 버튼 |
|---|---|---|
| 대화: 설계 공간 카드 | 표면 + 손잡이 화살표 + 회색 잠금 영역, 슬라이더로 즉시 변형(서버 호출 없음), 변수 표(이름·범위), 케이스 수·예상 시간. 표면 클릭으로 손잡이 추가 | 기존 Flow 승인 하나 |
| 3D 결과 | 응력 부위 클릭 → 입력창에 좌표 칩(K2) | 없음 |
| 대화: 해석으로 확인한 개선 | 전·후 3D, 솔버 값, 예측 대 실제, 범위 끝 손잡이 수, `evidence` 표시, "CAD로 되돌리는 일은 별도 작업입니다" | 형상 받기 |
| Simulator 형상 모드 | 슬라이더 자리에 파일 넣기(무대), 단계 표시, 학습 형상 윤곽 위 자세·고정·하중 색 표시, 판정 띠, 값·구간 또는 순위, 조건 문구, 여러 파일이면 순위 표 | 실제 해석으로 확인 |
| 데이터 탭 | 가져오기 감사 표(조건별 건수·제외 이유·편향 경고), 등급 표(기준선·잡음 바닥) | 묶음 확정 |

손잡이 Simulator는 화면이 바뀌지 않는다. 변수 이름 대신 `label`만 보여 준다.

## 6. 테스트
| 대상 | 통과 기준 |
|---|---|
| `agent/tests/test_deck_write.py` | `a=0`이면 바이트 동일, 좌표 칸 외 변경 없음, 다시 읽은 값 일치, 인스턴스 회전 역변환, 거절 조건마다 이유 |
| `agent/tests/test_geospace.py` | 잠금 노드 이동 정확히 0, 노드 수·순서 유지, `a`에 선형, 자동 범위에서 뒤집힘 0, 범위를 키우면 불합격, 중간 노드 식, sklearn import 없음 |
| `agent/tests/test_mock_fe.py` | 반력 합 = 하중, 외팔보가 기준 범위 안, 모핑하면 응력 변화 |
| `agent/tests/test_voxel_align.py` | 부피비 검사, 끊어짐 검출, 회전·이동·단위 변경 복원, 거울상 거절 |
| `server/tests/test_bayesopt_morph.py` | 12변수 합성 함수에서 주변 후보가 전역만보다 나음, `infeasible` 주변 재제안 안 함, `params` 모드 결과가 기존과 동일 |
| `server/tests/test_geometry_s3_s5.py` | 덱 → 공간 → DOE → BO → 확인, 기준선보다 개선, 내보낸 STL과 케이스 좌표 일치, 미확인 내보내기 403 |
| `server/tests/test_geometry_s1_s4.py` | 같은 형상이 학습·시험에 갈리지 않음, 다른 경계조건 분리, `not_extracted`를 0으로 두지 않고 제외, 업로드 → 응답 형식 |
| `server/tests/test_geometry_guard.py` | 판정 표의 모든 경로, 8개 형상 학습 거절, ×25.4 단위 입력 처리, 하중 면 없음 거절, 기준선 못 이긴 모델 배포 거절, **거절 응답에 값 없음** |
| `server/tests/test_no_answer_leak.py` | 특징에 변형 좌표가 들어가면 실패 |
| `server/tests/test_geometry_s7.py` | 파라미터 스터디 전체 동작 동일, 변수 0개 `params` 스터디 생성 거절 |
| `server/tests/test_geometry_wording.py` | 지침·화면에 "안전합니다"류와 "메쉬 변경 불가" 옛 문구 없음 |
| 기존 | `pytest server/tests agent/tests -q`, `test_tool_catalog_consistent`, `test_design_tokens.py` |
| `scripts/e2e_geometry.py` | 실제 PC Agent 연결로 S1~S7. gmsh가 있으면 STEP·재메쉬 포함. 측정표 출력 |
| 화면 | `npm run build`, `npm run smoke -- --demo`, `--reduced`, `demo_agent` 새 시나리오, 스크린샷 직접 확인 |

gmsh 시험은 `importorskip`으로 이유를 표시하고 넘어간다. STL·덱 경로는 항상 돈다. **mock 정확도는 합격 기준으로 쓰지 않는다.** 시험은 "로직이 맞는가, 기준선보다 나은가"만 본다.

## 7. 단계

### 7-1. 지금 CPU로 구현 (라이선스·GPU 없음, 크기는 코드를 읽고 어림한 값)
진행 중인 미커밋 변경(`main.py`, `hub.py` 등)이 커밋된 뒤 시작한다.

| 단계 | 내용 | 시나리오 | 크기 | 완료 기준 |
|---|---|---|---|---|
| 1 | `mock_fe`, `mock_shapes`, `space`, `quality`, `deck_write(.inp)`, `prepare_geometry`, `infeasible`, BO 보강, 설계 공간 카드, 내보내기, `evidence`, 문구 수정 | S3·S5·S7 | 중~대 | 위 시험, 저장소 코드로 기준 대비 개선 재현 |
| 2 | 응력 부위 클릭, `refine`, `.bdf`·`.k`, `build_latent_space` | S3·S4 일부 | 중 | 왕복·mock 해석, 3분 데모 완주. **여기까지가 첫 공개** |
| 3 | 가져오기·감사, 그룹 분할, `training_jobs`, `shape_model`, `geoguard` | S4·S6 | 대 | 거절 시험 전부 통과, 측정표 |
| 4 | Simulator 형상 모드, `align`, `bc_template`, `voxel`, 공유 링크 | S1·S6 | 대 | 회전·단위 변경 입력 처리, 범위 밖 거절, 얇은 형상 시험 결과 문서화 |
| 5 | `geomio`·`faces`·`mesher`, `prepare_cad_deck`, 해석으로 답 받기 → 재학습, `screen_shapes`, `find_similar_shapes` | S2·S1·S5 | 중 | STEP → 확인까지(gmsh 있을 때), 재학습 전후 오차 측정 |

**S1을 4단계에 두는 이유:** 범위에서 빼는 것이 아니라 의존 순서다. S1은 3단계의 데이터셋과 판정이 있어야 의미가 있고, 1단계의 모핑 DOE 결과가 첫 학습 데이터가 된다. S1을 먼저 보고 싶다면 3·4단계를 1단계 뒤로 당길 수 있다.

### 7-2. 실제 툴 검증 (`scripts/pc_verify.py`로 사용자 PC에서 직접, 사람에게 눌러 달라고 하지 않음)
| 항목 | 통과 기준 |
|---|---|
| Abaqus 작은 모델의 모핑 덱 | 솔버 정상 종료, 고정 노드 수·하중 합계가 원본과 같음, `a=0` 결과가 원본 해석과 같음 |
| Nastran·LS-DYNA 재작성 덱 | 같은 기준 |
| 실제 CATIA·NX STEP 20개 | 읽기·메쉬 성공률이 문서에 적힘(지금까지 OCC가 쓴 파일만 시험) |
| 추출기(odb·op2·d3plot) | 최댓값·좌표가 원본과 일치 |
| ANSA `apply_morph`, HyperMorph | 지금 미구현·GUI 절차. `space.kind`로 연결, 쓸지 판단 |
| NX·Creo 직접 편집 | 샘플 10개 재생성 성공률. 낮으면 모핑 유지 |
| 실제 옛 케이스 20건 이상 | 감사 표가 사람 판단과 맞음 |
| Linux 서버·사내 미러 | gmsh 설치, 헤드리스(`libGL` 등) 실행 |

### 7-3. 사내 GPU (계획 문서 0·2·3·4단계)
- **이음새:** `FieldModel.kind="neural"`, `training_jobs`, 같은 응답 형식, 같은 판정표.
- **입력:** `train.h5`의 표면·`node_attrs`. 거친 FE 결과를 `/geometry/node_attrs/coarse_*`로 넣어 쓴 경우와 안 쓴 경우를 비교한다. `train.h5` 형식은 바꾸지 않는다.
- **배포 조건:** GeoTransolver가 PCA+GP, GBDT, 거친 FE 단독을 모두 이겨야 한다. 계획 문서의 판정 기준을 넓힌다.
- **앙상블 편차:** 범위 밖 탐지 성능을 재서 좋을 때만 불확실도로 표시한다. 트리에서는 실패했다.
- **GPU에서만 답할 수 있는 것:** 노드 수별 메모리, 처음 보는 형상 정확도, 수천만 행에서 GBDT를 이기는지.
- **서로 다른 CAD의 잠재공간 DOE:** 템플릿 메쉬를 모핑으로 맞추는 방식을 실제 데이터로 시험한다. 정합 잔차와 유효 형상 비율을 공개하고, 통과할 때만 넣는다.

## 8. 사용자에게 말해야 할 한계

**파라미터 없는 DOE·최적화**
1. 위상을 못 바꾼다. 리브 추가, 구멍 제거는 안 된다.
2. 결과는 덱·STL·변형 지도다. 제조용 CAD로 되돌리는 일은 사람 몫이다.
3. 손잡이의 뜻은 추정이고, 제조 조건은 입력 없이는 모른다.
4. 해석 비용은 그대로다. 케이스 수 × 시간 ÷ 라이선스 수를 줄이지 못한다.
5. 재작성한 덱을 실제 솔버가 받는지 아직 한 번도 시험하지 않았다.

**CAD만 넣는 예측**
6. CAD만으로는 물리 문제가 정의되지 않는다. 답은 항상 "학습한 하중 조건에서의 예측"이다.
7. 실험 최고 조합도 최대 응력 오차 중앙값 약 10%, 최악 41%(A) / 13%(B)다. **후보 순위용이지 합격 판정용이 아니다.**
8. 그 조합은 거친 해석을 쓴다. 형상 특징만으로는 부족했다(중앙값 .34~.54, 조합에 따라 다름). 그래서 "즉시 예측"이 아니라 수 초~수십 초다. 5.7초는 깨끗한 STEP, 약 2만 노드, 이 PC 기준이다.
9. 범위 밖 판정은 놓친다. 처음 보는 부품군의 절반이 경고 없이 통과했다. 경고가 없어도 안전하다는 뜻이 아니다.
10. 쉘·판금·사출처럼 얇은 부품은 복셀 FE가 깨질 가능성이 크다. 그 경우 거절되고, 쉘 예측은 지금 범위 밖이다. 세탁기 부품이 주력이면 이 항목이 가장 큰 위험이다.
11. 학습에 없던 현상(좌굴·파단·새 접촉)은 예측하지 못한다.
12. 옛 데이터는 합격 설계 근처에 몰려 있고 사람마다 이상화가 달라 그대로 쓰기 어렵다.

**모든 수치의 전제**
- 선형 정적, 하중 조건 1개, 고정 프레임, OCC가 쓴 STEP, 직접 만든 솔버(이론 대비 처짐 .874, 응력 .769)다.
- 거친·정밀 해석의 물리가 같아 메쉬 보정만 배웠을 수 있다. 비선형·접촉·낙하에는 근거가 없다.
- 같은 형상을 재메쉬하면 최대 응력이 약 8% 달라진다. 10% 기준은 이 잡음에 가깝다.
- 필요한 케이스 수는 판단값(부품군 하나, 분포 경향 100~500개)이고 사내 시범 전에는 모른다.

## 9. "wow"를 어떻게 말할 것인가

**주장을 고치길 권한다.** CAD 입력 예측과 모핑+DOE는 상용 제품에도 있다(업체 정보는 기억 기반이고 시장 조사가 아니며, 도입 판단 전에 확인이 필요하다). "사람도 못 한다"는 전문가에게 반박당한다.

**실제 차별점:** 파일과 목표 한 문장 → AI가 설계 공간 구성 → 실제 툴로 DOE → 해석으로 재확인한 새 형상 파일 반환 → 모르면 숫자 대신 해석으로 답함 → 사내 이력 축적. 이 전체를 묶은 제품은 제가 아는 범위에 없다.

**시연 장면**
| 순서 | 장면 | 가능 시점 |
|---|---|---|
| 1 | 덱 + "응력 줄여 줘, 무게는 그대로" → 손잡이가 뜨고 슬라이더로 즉시 변형 → 승인 → 개선안 | 1단계 |
| 2 | 응력 부위 클릭 → 그 자리 작은 DOE → 확인된 개선 | 2단계 |
| 3 | 탐색 공간이 스스로 다듬어짐 | 2단계 |
| 4 | CAD를 넣으면 순위와 "본 적 없는 부위" 지도, 범위 밖이면 숫자 없이 실제 해석으로 답하고 모델 갱신 | 4·5단계 |

**주장 전에 잴 값:** 셋업 시간(전문가 대비), 모핑 통과율·덱 거절률, 해석으로 확인한 개선폭(재메쉬 후), 부품군 제외 시험의 최대 응력 오차(중앙값·최악), 범위 밖 경고 누락률, 가속 경로의 해석 횟수 절감. 측정 전에는 대외 수치로 쓰지 않는다.

## 10. 사용자 결정 필요
1. **주력 솔버와 모델이 솔리드인지 쉘인지.** 기본 가정은 Abaqus 솔리드다. 쉘이 주력이면 S1 전에 중립면·쉘 경로와 GPU 단계를 앞당겨야 한다.
2. **gmsh 사용(GPLv2+, 법무).** 권장: 별도 프로세스로 격리하고 PC Agent 쪽부터 쓴다. 안 되면 STL·덱 경로와 PC의 CAD 변환만 남는다.
3. **시범 부품과 하중 조건 하나.** 경계조건 틀의 첫 시험 대상이다.
4. **값 표시 기준 10% / 25% / .9** 를 담당 엔지니어와 확정한다.
5. **거절 시 숫자 숨김 유지.** 권장은 유지다. 현장 반발이 있으면 거절 비율을 보고 다시 본다.
6. **대외 문구**를 측정 기반으로 바꾸는 데 동의하는지.

## 근거 파일
- 비교 실험: `C:\Users\psm55\AppData\Local\Temp\claude\C--Users-psm55-Git-DDS-AI-Engineer-frontend-src\e6e513a2-b37b-41b8-ae34-e76b6f9f764c\scratchpad\exp_bo\bo_3.log`
- 비교 실험: `C:\Users\psm55\AppData\Local\Temp\claude\C--Users-psm55-Git-DDS-AI-Engineer-frontend-src\e6e513a2-b37b-41b8-ae34-e76b6f9f764c\scratchpad\exp_bo\bo_7.log`
- 모핑 실험 코드: `C:\Users\psm55\AppData\Local\Temp\claude\C--Users-psm55-Git-DDS-AI-Engineer-frontend-src\e6e513a2-b37b-41b8-ae34-e76b6f9f764c\scratchpad\exp\opt_morph.py`
- 예측 실험 코드: `C:\Users\psm55\AppData\Local\Temp\claude\C--Users-psm55-Git-DDS-AI-Engineer-frontend-src\e6e513a2-b37b-41b8-ae34-e76b6f9f764c\scratchpad\exp\ml.py`
- 복셀 실험 코드: `C:\Users\psm55\AppData\Local\Temp\claude\C--Users-psm55-Git-DDS-AI-Engineer-frontend-src\e6e513a2-b37b-41b8-ae34-e76b6f9f764c\scratchpad\exp_voxel\vox_ml.py`

임시 폴더라 사라질 수 있으니 1단계 시작 때 `scripts/bench_geometry/`로 옮긴다.