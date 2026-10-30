# CAE 해석 데이터 저장 — case.h5 (HDF5)

해석 케이스 하나의 결과를 **빠짐없이, 압축해서 한 파일**(`case.h5`)에 담는다. 원본 결과 파일(odb·d3plot·op2 등)이 지워져도 다음 일을 다시 할 수 있게 하는 것이 목적이다.
- Output 추가: 새 출력 항목 뽑기
- 형상·필드(3D) 학습
- 보고서
- 다른 과제에서 데이터 재사용

코드: `cae_adapters/h5store.py`(쓰기·읽기·묶기·검증) · 테스트: `agent/tests/test_h5store.py`, `agent/tests/test_cae_to_h5.py`, `server/tests/test_archive.py`

## 1. 언제 만들어지나
| 때 | 하는 일 |
|---|---|
| 케이스 해석이 끝남(완료·실패 모두) | PC Agent가 케이스 폴더를 `case.h5`로 묶는다(`executor._archive` → `pack_case`) |
| 묶은 직후 | 다시 열어 모든 데이터셋을 읽어 본다(`verify`). 결과는 manifest의 `checks` |
| 크기가 `upload_max_mb` 이하 | 서버로 올린다: `POST /api/cases/{id}/archive` → `data/files/project_<p>/h5/study_<s>/case_<c>.h5` |
| 크기가 그보다 큼 | PC 케이스 폴더에만 두고 정보(크기·sha256·담긴 필드)만 서버에 보낸다 |
| Output 추가로 다시 추출 | 같은 `case.h5`를 이어서 채우고 다시 올린다 |
| 서버 mock 실행(개발·테스트) | 서버가 바로 묶어 서버 저장소에 둔다 |

케이스 정보 `Case.archive`의 키:

| 키 | 뜻 |
|---|---|
| `stored` | `server` 또는 `pc` |
| `size` · `sha256` | 파일 크기와 해시 |
| `pc_path` | PC 케이스 폴더의 `case.h5` 위치 |
| `field_names` · `fields` · `frames` | 담긴 필드 이름, 필드 수, 프레임 수 |
| `history` | 시간 이력 수 |
| `raw` · `raw_size` | PC에 원본으로 남은 파일 수와 크기 |
| `ok` | 검증 통과 여부 |
| `packed_at` | 묶은 시각 |

## 2. 파일 구조 (스키마 `dds-cae-h5` v1)
```
case.h5
/                              attrs: format="dds-cae-h5", schema=1, created, packed_at, case_id, study_id, project_uid, adapter, tool, host
/meta                          attrs: params.<변수>, results.<출력>   · case_json(문자열)
/mesh/<part>/nodes             (N,3) 좌표
/mesh/<part>/node_ids          (N,)  노드 번호
/mesh/<part>/elements/<형식>    (M,k) 연결(노드 인덱스, 빈 자리 -1)
/mesh/<part>/element_ids/<형식> (M,)  요소 번호
/mesh/<part>/surface           (T,3) 외곽 표면 삼각형(3D 보기용)
/mesh/<part>/extra/<이름>       그 밖의 정적 배열(파트 번호·두께 등)
/steps/<step>/time             (F,)  프레임 시각(또는 모드 번호·하중 단계·주파수)
/steps/<step>/fields/<이름>     (F,N,C) 필드 값 — attrs: location(node|element|element_node|integration_point|global), part, components, unit, shape
/steps/<step>/labels/<이름>     (N,) 또는 (N,2) 행마다 노드·요소 번호
/history/<이름>                (T,K) 시간 이력 — attrs: columns, unit
/files/<경로>                  작은 텍스트·이미지 원문 바이트(로그·입력 덱·json·png)
/manifest                      JSON 문자열 — 아래 3절
```
- 압축: gzip(레벨 4) + shuffle, 청크는 프레임 하나(한 시각만 읽어도 빠르다).
- 자료형: 기본은 원래 자료형 그대로(손실 없음). `float32 = true`면 실수를 4바이트로 줄인다(크기 약 절반, 유효숫자 7자리).
- 읽는 법: HDFView, MATLAB `h5read`, Python `h5py`. 각 툴 형식으로 바꾸지 않고도 열 수 있다.

## 3. manifest — 빠짐없이 저장했는지 확인
`/manifest`에는 케이스 폴더의 **모든 파일**이 들어간다. 파일마다 다음 하나로 표시한다.

| 표시 | 뜻 |
|---|---|
| `packed` | case.h5 안에 내용이 들어 있다 |
| `raw` | 큰 바이너리 결과. 원본은 PC에 남고 크기·sha256·수정 시각만 적는다 |
| `skipped` | 담지 않은 파일(case.h5 자신, 임시 파일) |

`fields`는 어떤 npz에서 어떤 필드를 담았는지 적는다. `checks`는 검증 결과다(`ok`, 읽은 데이터셋 수, 오류).
- 원본 결과(`raw`)의 **내용까지** 담으려면 툴별 추출 단계를 붙인다(4절). 그러면 원본을 지워도 결과가 남는다.
- sha256은 `hash_max_mb`(기본 512 MB) 이하 파일만 계산한다(큰 파일은 시간이 오래 걸린다).

## 4. 케이스 스크립트가 남기는 것 → case.h5 위치
| 케이스 폴더 | case.h5 | 비고 |
|---|---|---|
| `params.json` · `results.json` · `case.json` | `/meta` | 기존 규약 그대로 |
| `fields/<이름>.npz` (points, values, triangles) | `/mesh/<이름>` + `/steps/final/fields/<이름>` | 기존 3D 학습 규약 그대로 |
| `fields/` 또는 `h5in/<이름>.npz` 확장 키 | `/steps/<step>/fields/…` | `frames`(F,N,C) · `time` · `step` · `location` · `unit` · `components` · `labels` · `node_ids` · `coordinates`(F,N,3 변형 좌표) · `elements__<형식>` · `element_ids__<형식>` · `meta_json`(name·step·part·location·components) |
| `history/<이름>.csv` (첫 줄 머리글, 숫자) | `/history/<이름>` | 시간 이력 |
| 작은 텍스트(로그·덱·json, 5 MB 이하)·이미지 | `/files/…` | 원문 그대로 |
| 그 밖의 파일 | manifest `raw` | 원본은 PC |

툴별 "결과 전체" 추출 단계(스터디 `adapter_config.post`에 붙인다):

| 툴 | 스크립트 | 담는 것 |
|---|---|---|
| LS-DYNA | `lsdyna-automation/scripts/dyna_to_h5.py` (플랫폼 python, lasso) | d3plot 모든 상태·모든 배열, 메쉬, binout 모든 이력 |
| Simcenter·MSC Nastran·OptiStruct | `simcenter3d-automation/scripts/op2_to_h5.py` (플랫폼 python, pyNastran) | OP2 모든 결과 표(서브케이스·모드·시간), 메쉬, 고유치 표 |
| Abaqus | `abaqus-automation/scripts/abq_odb_to_npz.py` (Abaqus python → `h5in/`) | 모든 스텝·프레임·필드 출력, 이력 출력, 인스턴스 메쉬 |
| 그 밖의 툴 | `fields/`·`h5in/` npz 확장 키, `history/*.csv` | 툴 파이썬에 numpy만 있으면 된다 |

플랫폼 python(PC Agent 파이썬)에서 도는 스크립트는 `cae_adapters.h5store.CaseH5`로 `case.h5`에 바로 써도 된다. `pack_case`는 이미 들어 있는 내용을 지우지 않고 이어서 채운다.

## 5. 쓰는 곳
- 서버 API
  - `GET /api/cases/{id}/archive`: 담긴 내용 요약(메타·메쉬·필드·이력·파일·원본 목록·검증)
  - `GET /api/cases/{id}/archive/file`: 파일 받기
- AI: `dds-study.case_data(case_id)`로 무엇이 담겼는지 본다. Output 추가 때 원본이 없으면 case.h5에서 뽑는 추출 스크립트를 쓴다.
- 형상·필드 학습(`train_field_model`): `fields/*.npz`가 서버에 없으면 서버의 case.h5에서 그 필드의 마지막 프레임을 읽는다(노드 필드).
- 웹 '데이터·모델' 탭: 케이스 표의 'HDF5' 열에 크기·위치를 보이고, 서버에 있으면 내려받는다.

## 6. 설정
PC Agent `agent.toml`:
```toml
[archive]
enabled = true        # 끄면 묶지 않는다
upload_max_mb = 500   # 이 크기 이하만 서버로 올린다(넘으면 PC에만)
text_max_mb = 5       # 원문을 담을 작은 텍스트·이미지 최대 크기
hash_max_mb = 512     # sha256을 계산할 원본 파일 최대 크기
compression = "gzip"  # gzip | lzf
level = 4             # gzip 압축 레벨(1~9)
float32 = false       # true면 실수를 4바이트로(손실 있음)
```
서버 `server.toml`: `archive_max_mb = 4096`(한 파일 최대 크기). 서버 디스크 사용량은 `data/files/project_*/h5/`에서 확인한다.

## 7. 사내 확인할 것 (TODO(사내검증))
- 실제 큰 모델의 case.h5 크기·묶는 시간: 원본 대비 압축률, PC 디스크·서버 디스크 여유
- 툴별 추출 스크립트: 사내 버전 결과 파일 읽기(lasso·pyNastran·Abaqus bulkDataBlocks)
- 업로드 크기 기준(`upload_max_mb`)과 서버 보관 기간 정책
