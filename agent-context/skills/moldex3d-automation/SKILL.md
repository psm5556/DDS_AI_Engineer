---
name: moldex3d-automation
description: CoreTech Moldex3D 사출 성형 해석(충전, 보압, 냉각, 휨) 케이스 자동화. Moldex3D Python 환경 탐색, 공정조건 파라미터 반영(확인된 API 훅 또는 텍스트 템플릿), 사내 확인된 배치 명령 실행과 로그 판정, 내보낸 요약표/노드 값에서 충전 시간, 스프루 압력, 형체력, 사이클 시간, 휨 변위를 뽑아 results.json, images/, fields/로 남긴다. 사용자가 Moldex3D, 사출, 성형 해석, 게이트, 휨, 웰드라인, 공정조건 DOE를 요청할 때 사용한다. API·명령행은 버전별 확인이 필요하므로 먼저 탐색 스크립트를 돌린다.
---

# Moldex3D 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키), `images/*.png`, 선택적으로 `fields/<이름>.npz`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

> **주의**: 이 스킬을 쓸 때 Moldex3D의 Python API 모듈 이름, 배치 명령행, 파일 확장자를 확인하지 못했다. 템플릿은 확인된 부분(파라미터 검증, 텍스트 템플릿, 명령 실행·로그 판정, 내보낸 파일 파싱, 필드·이미지 저장)만 실제로 수행한다. 툴 호출은 사내에서 확인한 방법을 끼워 넣는 구조다. 지어낸 API를 쓰지 않는다. 확인 수준표: [references/moldex3d-notes.md](references/moldex3d-notes.md)

## 1. 언제 쓰나 / 전제
- 공정조건(용융/금형 온도, 충전 시간, V/P 전환, 보압, 냉각 시간), 게이트 후보, 두께 변형별로 충전·보압·냉각·휨 결과를 비교할 때 쓴다.
- 이미 해석한 런에서 내보낸 요약표와 노드 값만 정리할 때 쓴다(가장 확실한 경로).
- 공정조건 DOE만 필요하고 Moldex3D Expert 라이선스가 있으면 내장 DOE가 더 간단하다. 플랫폼 루프는 형상·게이트를 함께 바꾸거나 다른 툴과 연결할 때 쓴다.
- `moldex3d_python` 경로 키 기본값이 비어 있다. 사용자가 웹앱 'CAD·CAE 툴' 화면에서 Moldex3D가 제공하는 Python을 골라야 `moldex3d` 키를 쓸 수 있다. TODO(사내검증): 설치본의 Python 위치와 버전.
- Moldex3D Python 버전을 모르므로 `moldex3d` 키 스크립트는 **py2/py3 겸용**으로 쓴다(f-string 금지, 출력은 영문 ASCII).

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| API/환경 탐색(처음 한 번) | `moldex3d` | `{moldex3d_python} scripts/m3d_probe_api.py` → `api_probe.txt` |
| 파라미터 반영 + 배치 실행 + 로그 판정 | `moldex3d` | `{moldex3d_python} scripts/m3d_case_run.py --project ... --template a.tpl=a.txt -- <배치 명령>` |
| 내보낸 결과 → results/fields/images | `python` | `python scripts/m3d_extract.py --summary ... --node-csv ...` |

**A. 사내 확인된 배치 명령이 있을 때**
```json
{"tool": "moldex3d", "script": "scripts/m3d_case_run.py",
 "args": ["--project", "cover/cover_run1.<ext>", "--template", "cond.tpl=cond.txt",
          "--", "<사내확인 배치 실행파일>", "{project}"],
 "inputs": ["models/cover", "models/cond.tpl"],
 "post": {"tool": "python", "script": "scripts/m3d_extract.py",
          "args": ["--summary", "summary.csv", "--node-csv", "warp_nodes.csv", "--name", "warpage",
                   "--require", "fill_time_s,warpage_max"]},
 "outputs": ["fill_time_s", "max_sprue_pressure_mpa", "clamp_force_max_tonne", "warpage_max"],
 "timeout_sec": 28800}
```
- `--` 뒤 명령의 `{project}`, `{project_dir}`, `{case_dir}`, `{params 키}`는 `m3d_case_run.py`가 치환한다(어댑터 `args`는 치환되지 않는다).
- 배치 단계가 결과 요약표와 노드 값 파일(`summary.csv`, `warp_nodes.csv`)까지 내보내야 post가 동작한다. TODO(사내검증): 결과 내보내기를 배치/API로 할 수 있는지.

**B. 해석과 내보내기는 GUI/Computing Manager에서 하고 정리만 자동화**
- 내보낸 파일이 있는 폴더를 케이스 폴더로 두고 `run_script(tool="python", script="scripts/m3d_extract.py", args=["--summary", "summary.csv", "--node-csv", "warp_nodes.csv", "--name", "warpage"], case_dir=...)`를 케이스마다 1회 실행한다.
- 어댑터 `inputs`는 케이스별로 치환되지 않으므로 B는 스터디 어댑터가 아니라 단발 실행으로 쓴다. 결과는 스터디에 가져오기(import)로 넣는다.

## 3. 파라미터 변경 (설계변수)
| 설계변수 | 방법 |
|---|---|
| 공정조건 숫자 | (1) 확인된 API 호출을 `apply_params_api()`에 채우고 `PARAM_MAP`에 키 등록, 또는 (2) 해석조건을 텍스트로 저장/불러오기할 수 있으면 `{{key}}` 템플릿 |
| 게이트 위치 | 후보별 런/프로젝트를 미리 만들고 `--` 뒤 명령에 `cover/cover_g{gate_id}.<ext>`처럼 적어 고른다(`--project`는 치환되지 않는다). TODO(사내검증): 게이트 위치 API |
| 두께·리브·형상 | CAD 스킬(NX 등)에서 형상 변경 → Studio에서 재임포트·메쉬. 자동화 API는 TODO(사내검증). 형상 변형별 런을 미리 만드는 방식이 안전하다 |

- 어디에도 쓰이지 않은 숫자 params 키(PARAM_MAP, 템플릿 자리표시자, 명령 인자 어디에도 없음)는 실패로 처리한다(`STRICT_PARAMS`).
- `apply_params_api()`는 채우기 전까지 `NotImplementedError`를 낸다. 확인 전 호출로 잘못된 결과가 섞이는 것을 막는다.
- 바이너리 프로젝트 파일을 직접 고치지 않는다.

## 4. 해석 조건 변경
- 해석 순서(충전 F, 보압 P, 냉각 C, 휨 W)는 런 설정에 저장해 둔다. 휨까지 볼 때는 냉각을 포함한다(금형 온도 불균일 효과).
- 재료 등급은 등급별 런을 미리 만들거나 확인된 API로 바꾼다. 재료 교체 후에는 공정조건 권장 범위(용융/금형 온도)가 달라지므로 DOE 범위를 다시 잡는다.
- 메쉬(Solid/eDesign/BLM 등) 종류는 한 스터디 안에서 바꾸지 않는다. 결과 항목과 노드 수가 달라진다.

## 5. 실행과 완료 판정
- `m3d_case_run.py`는 배치 명령 출력을 `m3d_run.log`에 남기고 종료코드가 0이 아니면 실패로 본다.
- 이어서 실행 이후 수정된 `*.log`에서 `FAIL_PATTERNS`(기본 `error`, `failed`, `diverge`)를 찾으면 실패, `SUCCESS_PATTERNS`를 지정했는데 못 찾아도 실패로 본다. TODO(사내검증): 솔버 로그 파일 이름과 완료 문구로 패턴을 좁힌다(오탐 방지).
- 배치 명령이 작업을 큐(Computing Manager)에 넣고 바로 반환하는 형태라면 완료 대기가 따로 필요하다. 결과 파일 생성을 기다리는 루프를 추가한다. TODO(사내검증)
- 미충전은 해석이 정상 종료돼도 생긴다. 요약표의 충전율 항목을 키로 추가해 판정한다.

## 6. 결과 추출 → results.json
- `m3d_extract.py --summary`는 CSV/TSV(`항목, 값, 단위`)와 `항목 = 값 단위`/`항목: 값 단위` 텍스트를 모두 읽는다. 모든 항목은 `m3d_summary_all.json`에 저장하고 `KEY_PATTERNS`로 핵심 키를 뽑는다.
- 기본 키: `fill_time_s`, `max_sprue_pressure_mpa`, `max_injection_pressure_mpa`, `clamp_force_max_tonne`, `cycle_time_s`, `max_total_displacement_mm`, `max_vol_shrinkage_pct`. 단위 환산은 하지 않는다. 내보내기 단위를 키 단위에 맞춘다.
- `--node-csv`는 노드 값 최대/최소를 `<name>_max`, `<name>_min`으로 추가한다.
- `--require`로 필수 키를 지정한다. 없으면 실패다. `--merge`는 기존 results.json에 합친다.
- TODO(사내검증): 요약표 항목 이름(첫 케이스의 `m3d_summary_all.json`으로 정규식 보정), 웰드라인/에어트랩의 내보내기 형식.

## 7. 이미지 저장
- `m3d_extract.py --node-csv`가 matplotlib 3D 그림 `images/<name>_3d.png`를 만든다(삼각형이 있으면 면, 없으면 점).
- Studio의 결과 화면 캡처(컨투어, 시점, 범례 고정)는 API 확인 전까지 자동화하지 않는다. TODO(사내검증): 이미지 저장 API.
- 이미지 실패는 케이스 실패로 보지 않는다.

## 8. 3D 필드 (fields/*.npz)
- 노드 CSV 헤더에서 x/y/z 좌표 열(`X`, `X (mm)`, `coord_x` 등)과 값 열(`--value-col` 부분 문자열, 없으면 마지막 열)을 찾는다. `Node ID` 열이 있으면 `--tri-csv`(마지막 3열이 노드 번호)를 인덱스로 바꿔 triangles를 만든다.
- 출력: `fields/warpage.npz`(points, values, triangles). triangles 없이도 서버가 받는다.
- 표면 노드만 내보내면 용량이 줄고 가시화가 깔끔하다. 같은 메쉬를 쓰는 공정조건 DOE가 가장 정확하다.
- 학습: `dds-study.train_field_model(study_id, field="warpage")`.

## 9. 흔한 오류와 대처
- **`'moldex3d_python' 실행 파일을 이 PC에서 찾지 못했습니다`**: 사용자에게 웹앱 'CAD·CAE 툴' 화면에서 Moldex3D의 Python을 고르도록 안내한다.
- **`params key not used anywhere`**: PARAM_MAP, 템플릿, 명령 인자 중 한 곳에 연결한다. 의도적으로 안 쓰는 키면 `STRICT_PARAMS = False`.
- **`NotImplementedError: apply_params_api`**: 확인된 API로 훅을 채우거나 텍스트 템플릿 방식을 쓴다.
- **로그 판정 오탐**: 경고 문장에 `error`가 들어간 경우다. `FAIL_PATTERNS`를 솔버 로그 형식에 맞게 좁힌다.
- **`x/y/z columns not found`**: 내보낸 CSV 헤더를 확인하고 `read_node_csv`의 열 이름 후보에 추가한다.
- **라이선스 대기/실패**: 동시 실행 수를 라이선스 수 이하로 둔다. GUI가 토큰을 잡고 있으면 배치가 실패할 수 있다.
- 경로에 한글과 공백을 피한다.

## 사내 검증 필요 항목 (TODO(사내검증))
1. Moldex3D Python 실행파일 위치·버전, API 모듈 이름과 객체 모델, 라이선스.
2. GUI 없이 해석하는 배치 명령과 인자, 큐 제출형이면 완료 대기 방법.
3. 프로젝트/런/결과 파일 확장자, 해석조건 텍스트 저장/불러오기 가능 여부.
4. 결과 요약표·노드 값 내보내기 방법(배치/API)과 열 이름·단위·인코딩.
5. 게이트 위치, 재료, 형상 재임포트·메쉬 API. 이미지 캡처 API.

## 파일
- `scripts/m3d_probe_api.py`: (moldex3d) Python 환경과 Moldex3D 관련 모듈/파일 목록 → `api_probe.txt`
- `scripts/m3d_case_run.py`: (moldex3d) params 검증 → 템플릿 렌더 → (API 훅) → 배치 명령 → 로그 판정
- `scripts/m3d_extract.py`: (python) 요약표/노드 CSV → results.json, `fields/<name>.npz`, `images/<name>_3d.png`
- `references/moldex3d-notes.md`: 확인 수준표와 처음 확인 절차
