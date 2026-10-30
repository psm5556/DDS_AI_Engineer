# 형상 학습 계획: case.h5 → 학습 데이터 → GPU 서버

작성: 2026-10-30. 상태: **계획. 코드는 아직 바꾸지 않았다.**

## 1. 결론
- **데이터:** `case.h5`는 손실 없는 보관본으로 두되 재료·경계조건·하중을 더한다(스키마 v2). 케이스마다 학습 전용 HDF5 `train.h5`를 따로 만든다. 큰 모델도 서버로 올릴 수 있게 하는 핵심이다.
- **방법:** 기존 PCA+GP를 유지하고, GeoTransolver를 더한다. PCA+GP는 설계변수가 같고 메쉬 구조가 같은 경우에 쓰고, GeoTransolver는 메쉬가 달라지거나 처음 보는 형상에 쓴다.
- **판정:** 같은 시험 케이스에서 신경망이 PCA+GP를 이기지 못하면 배포하지 않는다.
- **GPU:** GPU 3개로 앙상블 학습(한 GPU에 모델 하나), 나머지 1개로 예측을 맡는다. 4개를 묶어도 메모리가 합쳐지지는 않는다.
- **PhysicsNeMo:** GeoTransolver 학습에 쓴다. PCA+GP는 PhysicsNeMo 없이 지금 서버에서 돈다. 웹 서버와 같은 파이썬 환경에는 넣을 수 없어(3절) Linux 컨테이너의 별도 학습 작업자로 돌리고, 학습기는 규약으로 분리해 바꿀 수 있게 한다(6절).
- **가장 큰 위험:** GPU가 아니라 **해석 케이스 수와 하중·경계조건 정보**다(8절).

## 2. "형상 학습"으로 풀 문제
| 문제 | 입력 → 출력 | 방법 | 처음 기준(케이스 수) |
|---|---|---|---|
| A. 같은 메쉬 구조, 치수만 변화 | 설계변수 → 형상 + 분포 | PCA+GP(지금 있음) | 4~50개는 이것만 |
| B. 메쉬가 달라지거나 처음 보는 형상 | 메쉬 좌표 + 조건 → 분포 | GeoTransolver | 50~200개는 A와 비교, 200개 이상이거나 메쉬가 섞이면 주력 |
| C. 시간에 따라 변하는 해석(낙하·충돌) | 메쉬 + 조건 → 전 구간 변위·응력 | GeoTransolver 한 번에 예측(one-shot) | B와 같음 |

케이스 수 기준은 판단으로 정한 처음 값이고, 5단계에서 실제 결과로 고친다.

**모델을 고른 이유**
- GeoTransolver: 낙하 시험 예제가 우리 문제에 가장 가깝다(초기 형상 + 조건 스칼라 → 전 구간 변위·Von Mises). v2.2.0에서 정식 모듈이 되었고, 불확실도 추정(concrete dropout)도 있다.
- MeshGraphNet은 뒤로 미룬다. 판 변형 예제는 평균 1,271노드, 1,000개 샘플에 H100 8개로 20시간이 걸렸다(샘플당 400스텝). 한 스텝씩 굴리는 방식은 비용이 크다. GeoTransolver가 접촉·국부 응력을 못 맞출 때만 꺼낸다.

**넣지 않는 것**
- 물리 방정식 손실(PINN): 접촉·비선형·복잡 형상에 세우기 어렵고, 해석 데이터가 이미 있다.
- 형상을 새로 만들어 내는 생성 모델, 여러 서버에 걸친 학습, 사용자가 만지는 하이퍼파라미터 화면.

## 3. 확인한 사실 (2026-10-30)
| 항목 | 내용 | 계획에 미치는 영향 |
|---|---|---|
| L20S | **공개 자료에서 이 이름을 찾지 못했다.** L20은 48 GB GDDR6 ECC, PCIe 4.0 x16, 수동 냉각, FP32 59.8 TFLOPS. L40S도 48 GB에 91.6 TFLOPS | 장당 48 GB로 가정하고 0단계에서 실제 값을 확인한다 |
| GPU 연결 | L20 자료에 고속 연결(NVLink) 언급이 없다 | 여러 GPU로 한 모델을 학습하면 PCIe로 통신한다고 가정 |
| PhysicsNeMo | GitHub 최신 v2.2.2, 문서·컨테이너 26.08, Apache 2.0 | 버전을 고정한다 |
| 요구 환경 | Python ≥3.11, <3.15 · torch ≥2.13 · numpy ≥2.2.6 · h5py ≥3.16 | 서버는 `numpy==1.26.4`(`requirements-server.txt:8`)라 **같은 환경 불가** |
| 운영체제 | CUDA 12 백엔드는 Linux 전용. 문서에 Windows 지원 언급 없음. 컨테이너는 Docker + NVIDIA Container Toolkit | 학습은 Linux 컨테이너로 |
| 데이터 형식 | 충돌 예제는 d3plot → Curator → VTP·Zarr. 충돌 예제에 사용자 리더 규약이 있다(`(srcs, dsts, point_data)`, `coords` [T,N,3]). h5py는 기본 의존성이다. 릴리스 노트에 HDF5 언급은 없다 | HDF5 리더를 직접 만든다. 막히면 Zarr 캐시(8절) |
| 배치 | 충돌 예제는 노드 수가 샘플마다 달라 `batch_size=1` | GPU당 메모리 계획을 샘플 1개 기준으로 |

**확인하지 못한 것:** 집 PC에 해당 GPU가 없어 PhysicsNeMo를 실행해 보지 않았다. 위 내용은 문서·코드를 읽은 결과다. 노드 수별 메모리와 학습 시간 공식 수치도 찾지 못했다. 낙하 시험 예제의 학습 20개는 설정 예시이며, 그만큼이면 충분하다는 뜻이 아니다.

## 4. 지금 코드에서 막히는 곳
| # | 위치 | 문제 | 고침 |
|---|---|---|---|
| 1 | `executor.py:121`, `service.py:381` | 500 MB를 넘는 `case.h5`는 PC에만 남고, 학습 대상에서 **아무 표시 없이 빠진다** | `train.h5`를 PC에서 만들어 올린다. 빠진 케이스 수와 이유를 보여 준다 |
| 2 | `h5store.py:206` | `read_snapshot`은 필드 하나, 프레임 하나만 읽는다 | 여러 필드·프레임을 읽는 샘플 생성기 |
| 3 | `h5store.py:224` | `coordinates`가 있으면 변형된 좌표를 형상으로 쓴다. 입력에 답이 섞인다 | 입력은 `/mesh/*/nodes`(변형 전), 변위는 출력 |
| 4 | `h5store.py:249` | 파트 이름 기본값이 필드 이름이라 필드마다 메쉬를 따로 저장하고, 필드끼리 노드가 맞는다는 보장이 없다 | 샘플에서 메쉬 하나로 합치고 검사한다 |
| 5 | `fieldmodel.py:153` | 요소·적분점 값을 받지 못한다 | 생성기에서 노드로 평균한다 |
| 6 | 스키마 `unit`, 필드 이름 | 툴마다 이름(`node_displacement` 등)과 단위계가 달라 스터디를 섞을 수 없다 | 표준 이름표와 단위 환산(mm·MPa) |
| 7 | 스키마 전체, `h5store.py:316` | **하중·경계조건·재료가 구조화되어 있지 않다.** 입력 덱 원문으로만 들어가고, 5 MB가 넘는 덱은 그마저 PC에만 남는다 | `case.h5` 스키마 v2로 저장하고 툴별 추출기를 만든다(5절). 시범 학습은 데이터셋 하나 = 하중 조건 하나로 한다 |
| 8 | `studies.py:347`, `service.py:393` | 학습이 HTTP 요청 안에서 끝난다. 진행률·취소·재시작 복구가 없다 | 작업 대기열과 별도 작업자 |
| 9 | `studies.py:365`, `models.py:247` | 예측 모델이 서버 프로세스에 joblib으로 올라가고, 모델 종류 구분이 없다 | `kind` 컬럼 추가, 신경망은 예측 작업자로 넘긴다 |

## 5. 데이터 설계
```
[PC]  케이스 폴더 → case.h5 (보관, 손실 없음, 원본 그대로)
                  → train.h5 (학습, 고른 필드·프레임만) ──올리기──┐
[서버]                                                           ▼
      data/training/samples/project_<p>/study_<s>/case_<c>.train.h5
      data/training/datasets/<id>/manifest.json  (샘플·sha256·분할·통계, 만들면 고치지 않는다)
```

**`case.h5` 스키마 v2 (v1에 `+` 부분을 더한다)**
```
case.h5
├── meta/                      설계변수 · 결과 스칼라
│   └── + units                길이 · 힘 · 응력 · 시간 단위
├── mesh/<part>/               nodes · elements/<형식> · surface
│   ├── + element_material/<형식>   (M,)  요소별 재료 번호
│   └── + thickness            쉘 두께
├── + materials/<id>           재료 모델 이름 · E · ν · 밀도 · 물성표(소성·고무 등)
├── + boundary/<step>/<part>/  fixed_mask (N,3) · prescribed_u (N,3)
├── + loads/<step>/
│   ├── <part>/nodal_force     (N,3)
│   ├── <part>/pressure        면 목록 + 값
│   └── body                   중력 · 회전 속도 · 축
├── steps/<step>/              time · fields/<이름>
├── history/ · files/ · manifest
```
- 재료·경계조건·하중마다 추출 상태를 manifest에 적는다: `ok`(추출함) · `none`(해당 조건 없음) · `not_extracted`(추출 못 함). 빈칸으로 두면 "고정 조건 없음"과 구분되지 않는다.
- v1 파일은 그대로 읽고, 세 항목을 `not_extracted`로 본다.
- 정보원은 입력 덱(`.inp`, `.k`, `.bdf` 등)이다. 해석이 끝난 뒤에는 원본이 지워질 수 있으므로 묶을 때 함께 추출한다.

**검토했지만 쓰지 않는 구조** (2026-10-30, 외부 AI 제안 `Geometry·Material·BoundaryConditions·Loads·Results·Metadata`). 재료·경계조건·하중을 구조화한다는 생각과 단위 표기만 가져왔다.
| 제안 | 쓰지 않는 이유 |
|---|---|
| `Connectivity (E,K)` 하나 | 요소 형식이 섞이면 K가 달라진다. 파트·형식별 저장 유지 |
| `Stress (T,E,IP,6)` | 형식마다 적분점 수가 다르고 쉘은 두께 방향 점도 있다. 행마다 요소·적분점 번호를 붙이는 방식 유지 |
| `Results/` 한 묶음 | 정적 + 모드처럼 스텝이 여러 개면 시간의 뜻이 섞인다. `/steps/<step>/` 유지 |
| `NodeMaterialId` | 재료는 요소에 속한다. 파트 경계 노드는 재료가 두 개일 수 있다 |
| `YoungModulus`·`PoissonRatio`만 | 밀도가 없어 모드·동해석에 부족하고, 소성·고무 재료를 담지 못한다 |
| 스텝 구분 없는 `Force`·`FixedMask`, 칸 없는 체적력 | 스텝별 하중, 면 압력, 드럼 회전의 원심력을 담지 못한다 |
| 설계변수·이력·파일 목록 없음 | DOE와 연결되지 않고 "빠짐없이 저장"을 확인할 수 없다 |

**`train.h5` (스키마 `dds-train-h5` v1, 케이스 하나 = 샘플 하나)**
```
/                     attrs: format, schema, case_id, study_id, project_uid, tool, source_sha256, length_unit, stress_unit
/geometry/nodes       (N,3) float32  변형 전 좌표
/geometry/surface     (T,3) int32    3D 보기용
/geometry/normals     (N,3) float32  표면 법선
/geometry/edges       (E,2) int32    MeshGraphNet을 쓸 때만
/geometry/node_attrs/<이름>  (N,)    두께·파트 번호·재료 번호(요소 값을 노드로 옮긴 것)
/inputs/fixed_mask    (N,3) uint8    고정 표시
/inputs/prescribed_u  (N,3) float32  강제 변위
/inputs/nodal_load    (N,3) float32  절점 하중 + 압력을 노드로 옮긴 값
/conditions           attrs: params.<변수>, global.<중력·회전 속도·재료 스칼라>, extraction(항목별 추출 상태)
/targets/time         (F,)
/targets/<표준 이름>   (F,N,C) float32  노드 기준. attrs: unit, components, source_field
/qc                   attrs: 노드 수, 필드별 최소·최대, NaN 수, 검사 결과
```

**압축 결정**
- float32 + gzip(4) + shuffle, 청크는 프레임 하나. `CaseH5`의 방식과 같다.
- 비트 반올림은 처음에는 끄고, 실제 데이터로 잰 뒤 정한다. 응력 집중부의 최댓값이 판단 기준이라 먼저 손대지 않는다.
- zstd 플러그인은 쓰지 않는다. 새 의존성이 필요하고, 아래 수치로 보면 gzip도 학습 속도를 막지 않는다.

**측정 (합성 데이터, 20만 노드 × 20프레임 × 6성분, 이 PC, 단일 스레드)**
| 방식 | 크기 | 한 프레임 읽기 | 최대 상대 오차 |
|---|---|---|---|
| float64 gzip4 (지금 기본) | 165.6 MB | 51.8 ms | 0 |
| **float32 gzip4 (채택)** | 78.7 MB | 26.0 ms | 3.1e-08 |
| float32 lzf | 87.7 MB | 13.3 ms | 3.1e-08 |
| float32 비트 반올림 16비트 gzip4 | 59.0 MB | 30.2 ms | 3.9e-06 |
| float32 비트 반올림 12비트 gzip4 | 46.6 MB | 40.2 ms | 6.3e-05 |

합성 데이터라 **압축률은 실제 해석과 다르다.** 방식 간 차이와 읽기 시간의 크기만 참고한다. 이 조건이면 샘플 1,000개가 약 80 GB다. 실제 크기는 1단계에서 잰다.

**학습 데이터 처리 규칙**
- 프레임 고르기: 정적 해석은 마지막, 시간 해석은 등간격 N개.
- 고유모드: 부호와 순서가 케이스마다 뒤집히므로 MAC로 맞춘 뒤 학습한다.
- 응력: `log1p` 변환(낙하 시험 예제와 같음). 응력 집중부 오차에 가중치를 둔다.
- 적분점 응력은 `case.h5`에 보관하고, `train.h5`에는 노드 평균값을 넣는다. 100만 요소 × 8점이면 float32 무압축으로 프레임당 192 MB이고, 모델 출력은 노드 기준이다.
- 추출 상태가 `not_extracted`인 입력은 학습에서 빼고, 어떤 입력을 썼는지 모델 정보에 남긴다.
- 분할: **케이스 단위로 나눈다.** 같은 케이스의 프레임이 학습과 시험에 나뉘면 안 된다. 설계 공간 가장자리 케이스도 시험에 일부 남긴다.
- 통계(평균·표준편차)는 학습 분할에서만 계산한다.
- HDF5 파일은 DataLoader 작업자마다 따로 연다. 파일이 샘플별로 나뉘어 있어 경합이 적다.
- 프로젝트를 넘어 데이터를 섞으려면 해당 프로젝트 관리자의 승인을 받는다.

## 6. 시스템 구조
```
[브라우저]  데이터·모델 탭 · Simulator
    │
[웹 서버: FastAPI, numpy 1.26, 지금 환경]
    │  데이터셋·작업·모델 기록, 진행 이벤트, 예측 요청 전달   ※ 작업자는 HTTP로만 접근(DB 직접 접근 없음)
    ├──────────────────────────┐
[학습 작업자: Linux 컨테이너]     [예측 작업자: Linux 컨테이너]
 GPU 0·1·2, PhysicsNeMo 고정     GPU 3, 모델을 올려 두고 응답
    └──── data/training (같은 서버면 마운트, 다른 서버면 NFS) ────┘
```
- 새 코드는 `trainer/`(자체 requirements·Dockerfile)에 두고 서버 코드에서 import하지 않는다.
- 학습기 규약: 입력은 데이터셋 `manifest.json`과 `train.h5`, 출력은 모델 파일·`metrics.json`, 예측 응답은 지금 Simulator 형식(`points`·`values`·`uncertainty`)이다. 이 규약만 지키면 PhysicsNeMo가 아닌 학습기로 바꿔도 서버와 화면은 그대로다.
- 작업자는 `GET /api/training/jobs/next`로 일을 받고 진행률을 보고한다. 서비스 토큰을 쓴다.
- 새 테이블 `training_jobs`, `training_datasets`. `field_models`에는 `kind`(rom|neural)와 `dataset_id`를 더한다.
- 체크포인트에서 이어서 학습하고, 모델 파일은 안전한 방식(`weights_only`)으로 읽는다.
- GPU 서버가 없으면(개발 PC 포함) PCA+GP만 동작하고, 화면에 이유를 보여 준다.

**4개 GPU를 쓰는 방식**
| 용도 | 할당 | 이유 |
|---|---|---|
| 학습 | GPU 0·1·2에 모델 하나씩(앙상블 3개) | 구조가 단순하고, 지금 Simulator가 보여 주는 불확실도를 그대로 유지할 수 있다 |
| 예측 | GPU 3 고정 | 학습 중에도 Simulator가 바로 응답해야 한다 |
| 한 모델이 너무 느릴 때 | GPU 0·1·2 DDP(`torchrun`) | 빨라질 뿐 메모리는 늘지 않는다 |
| 메쉬가 한 GPU에 안 들어갈 때 | 학습 때 노드 샘플링, 활성화 체크포인팅, 혼합 정밀도 | 예측은 전체 노드를 나눠서 계산 |

수동 냉각 카드 4장이 장시간 최대 부하로 돈다(장당 275~300 W로 표기). 서버 풍량과 온도를 0단계에서 확인한다.

**사용 흐름**
1. 사용자: "이 스터디로 응력 예측 모델 만들어 줘" → 에이전트가 흐름을 제안하고 승인받는다.
2. 데이터셋을 만들고 빠진 케이스와 이유를 보여 준다.
3. 케이스 수·메쉬 상태로 방법을 자동으로 고르고, 필요하면 두 방법을 모두 돌려 비교한다.
4. 데이터·모델 탭에 진행률, 끝나면 비교 결과(PCA+GP 대 신경망)와 배포 여부가 보인다.
5. Simulator에서 슬라이더로 설계변수를 바꾸거나 새 메쉬를 올려 예측한다.
   - 메쉬 구조가 같으면 기존 형상 PCA로 슬라이더 값 → 좌표를 만들어 넣는다.
   - 메쉬가 다르면 PC Agent가 해석 없이 CAD·메쉬만 만들어 넣는다. 해석보다 훨씬 빠르게 후보를 거를 수 있다.

## 7. 실행 단계 (단계마다 통과 기준을 넘어야 다음으로)
| 단계 | 할 일 | 통과 기준 |
|---|---|---|
| **0. GPU 서버 확인** | `nvidia-smi -L`, `nvidia-smi topo -m`, 드라이버·OS·Docker, 이미지 반입(`docker save/load` 또는 사내 미러), 공식 낙하 시험 예제를 4 GPU로 실행, 노드 수별 메모리 측정, 온도 확인 | 예제 학습이 끝까지 돌고, 카드 이름·메모리·노드 한계 값이 이 문서에 적힘 |
| **1. 학습 데이터** | `case.h5` 스키마 v2(v1 파일도 읽기), 사내에서 가장 많이 쓰는 툴 1종의 입력 덱 추출기, `train.h5` 생성기, PC Agent `case.train_sample`, 업로드 API, 표준 이름표·단위 환산, 데이터셋 manifest, 4절 1~7번 수정. `docs/cae-data-hdf5.md` 갱신, `__version__` 올림 | pytest. 실제 툴 1종의 작은 모델로 `train.h5` 생성, 최댓값·좌표가 원본과 일치, 고정 노드 수·하중 합계가 입력 덱과 일치, 추출 못 한 항목이 `not_extracted`로 표시, 500 MB 넘는 케이스도 학습 대상에 들어감 |
| **2. 작업 기반** | 작업 테이블·API, 가짜 학습기, 진행률·취소·이어 하기, 데이터·모델 탭 표시, MCP(`build_dataset`, `train_shape_model`, `training_status`) | pytest(torch 없이), `npm run smoke -- --demo`·`--reduced`, 작업자를 죽였다 살려도 이어짐 |
| **3. PhysicsNeMo 학습** | HDF5 리더, 정적 해석 + 필드 하나 + 마지막 프레임부터, 앙상블, 시험 지표, PCA+GP 비교, 배포 판정 | GPU 서버에서 mock 브래킷 데이터로 학습 끝, 판정 근거가 보고서로 나옴(mock은 물리가 단순해 정확도 판단에는 쓰지 않음) |
| **4. 예측·Simulator** | 예측 작업자, `kind`별 분기, 기존 3D 응답 형식 재사용, 메쉬 업로드, 불확실도·학습 범위 밖 경고, "예측이며 해석이 아님" 표시 | 슬라이더 응답 시간 측정, 학습 범위 밖 입력에 경고, 스모크 통과 |
| **5. 실제 데이터 시범** | 드럼 세탁기 부품 1종, 하중 조건 1개, DOE 100~300개, 엔지니어 검토, 후보를 해석으로 다시 확인하고 학습에 추가 | 8절의 합격 기준 통과, 또는 왜 안 되는지와 필요한 케이스 수가 정리됨 |
| **6. 넓히기** | 시범 결과가 좋을 때만: 시간 이력, 여러 하중 조건을 섞은 학습, 나머지 툴의 추출기, 스터디 간 학습, MeshGraphNet | 항목별로 계획을 따로 세움 |

## 8. 합격 기준과 위험
**처음 합격 기준(5단계에서 담당 엔지니어와 확정)**
- 시험 케이스에서 최대 응력 오차 10% 이내, 최대 변위 오차 5% 이내.
- 최대 응력 위치 오차가 요소 몇 개 이내(요소 크기에 맞춰 정한다).
- 평균 오차(상대 L2)는 참고값으로만 쓴다. 평균이 좋아도 응력 집중부를 놓칠 수 있다.
- 해석을 대체하지 않는다. 후보를 거르는 데 쓰고, 고른 설계는 반드시 해석으로 확인한다.

| 위험 | 대응 |
|---|---|
| 케이스 수가 모자람 | PCA+GP로 먼저 쓰고, 능동학습(`suggest_next`)으로 필요한 곳만 해석. 보관한 `case.h5`를 다시 활용 |
| 입력 덱 추출이 툴·버전마다 실패함(실제 툴 미검증) | 툴 1종부터 한다. 못 뽑은 항목은 `not_extracted`로 표시하고 학습 입력에서 뺀다 |
| 다른 하중 조건에 일반화되지 않음 | 시범은 조건별로 데이터셋을 나눈다. 학습하지 않은 조건에 쓰면 경고 |
| PhysicsNeMo를 사내에서 쓸 수 없거나 맞지 않음 | PCA+GP는 그대로 동작한다. 학습기 규약(6절)을 지키는 순수 PyTorch 구현으로 바꾼다 |
| PhysicsNeMo가 자주 바뀜(v2.2.0에서 `guard_config` 제거 등) | 이미지 태그 고정, 레시피를 `trainer/`로 복사해 관리 |
| 레시피가 HDF5 리더와 맞지 않거나 읽기가 느림 | `train.h5`를 원본으로 둔 채 서버에서 Zarr 캐시 생성(Mesh 데이터파이프가 Zarr 지원). 원본 형식은 그대로 |
| 사내망에 이미지가 없음 | 외부 PC에서 받아 `docker save`로 반입. 불가하면 사내 pip 미러로 설치 |
| 서버 OS가 Windows | GPU 학습만 Linux 환경으로 분리(WSL2는 0단계에서 시험). 웹 서버는 그대로 |
| 모델을 지나치게 믿음 | 불확실도, 학습 범위 밖 경고, "예측" 표시, 해석 확인 필수 |
| 서버 저장 용량 | 샘플 크기를 1단계에서 잰 뒤 보관 정책을 정한다 |

## 9. 참고 자료
- [NVIDIA PhysicsNeMo GitHub](https://github.com/NVIDIA/physicsnemo) · [릴리스](https://github.com/NVIDIA/physicsnemo/releases) · [릴리스 노트](https://docs.nvidia.com/physicsnemo/latest/release-notes) · [설치 문서](https://docs.nvidia.com/physicsnemo/latest/getting-started/installation.html)
- 예제: [충돌](https://github.com/NVIDIA/physicsnemo/tree/main/examples/structural_mechanics/crash) · [낙하 시험](https://github.com/NVIDIA/physicsnemo/tree/main/examples/structural_mechanics/drop_test) · [판 변형](https://github.com/NVIDIA/physicsnemo/tree/main/examples/structural_mechanics/deforming_plate)
- GPU: [L20 사양(TechPowerUp)](https://techpowerup.com/gpu-specs/l20.c4206) · [L20 대 L40S 비교](https://flopper.io/compare/nvidia-l40s-48gb-vs-nvidia-l20-48gb/spec-sheet) · [HPE L20 판매 사양](https://global.channelonline.com/acme/ccs/Products/overview/M025311571)
- 저장소: [cae-data-hdf5.md](cae-data-hdf5.md)(case.h5 규약) · `server/app/study/fieldmodel.py`(PCA+GP)
