# CAD/CAE 스킬 ↔ DDS 플랫폼 연동 규약

모든 툴 스킬과 스크립트 템플릿은 이 규약을 지킨다. 그래야 에이전트(Gemini CLI)가 어떤 툴이든 같은 방식으로 실행하고, DOE와 최적화 루프에 연결할 수 있다.

## 1. 케이스 폴더 규약 (입출력)
스크립트는 **케이스 실행 폴더(cwd)** 에서 실행된다. 이 폴더에는 다음 파일이 있다.

| 파일 | 방향 | 내용 |
|---|---|---|
| `params.json` | 입력 | 설계변수 평탄 딕셔너리 `{"thickness": 2.5, "rib_height": 12.0}` |
| `case.json` | 입력(선택) | 케이스 메타 `{"case_id": 12, "study_id": 3, "inputs": {...}}` |
| `results.json` | **출력(필수)** | 숫자 결과 평탄 딕셔너리 `{"mass": 1.23, "max_mises": 210.5}`. 키 이름은 소문자_스네이크 |
| `images/*.png` | 출력(선택) | 캡처 이미지. 플랫폼이 아티팩트로 게시한다 |
| `run.log` | 자동 | 플랫폼이 표준출력/오류를 기록 |

- 스크립트는 경로를 하드코딩하지 않는다. 입력 모델 경로는 `params.json` 또는 `case.json`의 `inputs`, 혹은 커맨드라인 인자로 받는다.
- 결과 추출에 실패하면 `results.json`을 쓰지 않고 0이 아닌 종료코드로 끝낸다. 플랫폼은 이 케이스를 failed로 처리한다.
- 툴 내장 파이썬이 2.7일 수 있으므로(Abaqus 2023 이하 등) 해당 툴용 스크립트는 **py2/py3 겸용**으로 작성한다. f-string을 쓰지 않는다.

### 1-1. 필드·형상 데이터 (선택, 3D 학습·가시화용)
케이스 폴더의 `fields/<이름>.npz`(numpy `savez_compressed`)에 노드 데이터를 남기면, 플랫폼이 서버로 올린다. 서버는 이 데이터로 **형상·필드 모델**(설계변수 → 3D 형상 + 응력/변위 분포)을 학습하고 Simulator에서 3D로 보여 준다.

| 배열 | 형태 | 내용 |
|---|---|---|
| `points` | (N, 3) float | 노드 좌표. 형상이 바뀌면 바뀐 좌표 그대로(변형 형상을 보여 주려면 변형 후 좌표) |
| `values` | (N,) 또는 (N, 3) | 노드 값(예: von Mises, 변위 벡터) |
| `triangles` | (M, 3) int | 표면 삼각형(가시화용) |

- 노드 수·순서가 케이스마다 같으면(모핑·같은 토폴로지) 가장 정확하다. 리메쉬로 달라지면 서버가 가장 촘촘한 케이스의 메쉬를 기준으로 자동 보간한다(경계상자 정규화 좌표의 최근접 가중). 위상이 바뀌는 큰 형상 변화에는 맞지 않는다.
- 툴별 추출 스크립트: `abaqus-automation/scripts/abq_odb_fields.py`, `simcenter3d-automation/scripts/op2_fields.py`(OptiStruct 겸용), `lsdyna-automation/scripts/dyna_fields.py`, Ansys는 스킬의 PyDPF 예시
- 요소 값은 노드로 평균해서 저장한다. 표면 노드만 저장해도 된다(용량 절약).
- 툴별 추출 방법은 각 스킬의 "결과 추출" 절을 참고한다. 예를 들어 Abaqus는 odb의 표면 노드 좌표와 `S` 불변량, Nastran OP2는 pyNastran으로 추출한다.

### 1-1-1. 결과 전체 저장 (case.h5, HDF5)
케이스가 끝나면 PC Agent가 케이스 폴더를 `case.h5`(HDF5 압축)로 묶는다. 스키마와 규약은 **docs/cae-data-hdf5.md**.
- 자동으로 담기는 것: params·results·case.json, `fields/*.npz`, `history/*.csv`, 작은 텍스트(로그·입력 덱)·이미지. 큰 바이너리 결과는 manifest에 목록·크기·sha256만.
- 결과 파일 내용 전체를 담으려면: 플랫폼 python 스크립트는 `cae_adapters.h5store.CaseH5`로 `case.h5`에 바로 쓰고, 툴 내장 python(h5py 없음)은 `h5in/*.npz`(frames·time·labels·meta_json, 메쉬는 points·elements__<형식>)로 남긴다.
- `fields/*.npz`도 확장 키(`frames`(F,N,C)·`time`·`step`·`unit`·`components`)를 쓰면 모든 프레임이 담긴다.

### 1-2. Output 추가 추출 (나중에 출력 항목을 늘릴 때)
사용자가 해석을 마친 뒤 "OO도 Output에 넣어 줘"라고 하면 에이전트는 `dds-study.add_outputs`를 쓴다. 해석은 다시 돌리지 않는다.

| 대상 | 하는 일 |
|---|---|
| 이미 해석한 케이스 | 스터디 주인의 PC Agent가 자기가 돌린 케이스 폴더에서 추출 단계(`post`)만 실행한다(`case.extract`). 새 값은 기존 결과에 합쳐진다. 주인이 아니면 403이고, 다른 사람 PC에서는 돌지 않는다 |
| 이후 케이스 | 추출 단계가 스터디 `adapter_config.post` 끝에 붙어, 해석할 때마다 함께 실행된다 |
| Output을 추가할 때 실행 중이던 케이스 | 끝나는 대로 빠진 Output만 추출한다 |

- 스크립트 경로는 프로젝트 폴더 안의 상대 경로만 받는다. 절대 경로와 `..`는 서버와 PC Agent가 모두 거절한다.
- 추출 스크립트는 케이스 폴더(cwd)에서 돌고, 새 키만 `results.json`에 써도 된다. 플랫폼이 단계마다 쓴 값을 모으므로 앞 단계 값이 지워지지 않는다.
- 추출에 필요한 해석 결과 파일(odb·op2·d3plot 등)이 케이스 폴더에 남아 있어야 한다. 없으면 그 케이스는 `Output 추출 실패`로 표시되고 해석 결과(상태 complete)는 그대로 둔다.
- 다른 멤버가 돌린 케이스는 파일이 그 PC에 있어도 그 PC에서 돌리지 않는다(뽑지 않은 수를 알려 준다). 내 PC가 꺼져 있으면 건너뛴 수를 알려 준다.
- mock 어댑터는 추출 단계 없이 해석함수에서 다시 계산한다(`adapter_config.outputs`로 처음에 남길 출력을 정한다).

## 2. 실행 방법 (dds-cae MCP 툴)
- `run_script(tool, script, filename, args, dry_run, case_dir?)`: 에이전트가 작성한 스크립트를 해당 툴 배치 모드로 1회 실행한다(탐색, 디버깅, 단발 작업).
- 스터디 어댑터 `script`: 같은 스크립트를 DOE/최적화 케이스마다 반복 실행한다.
  ```json
  {"tool": "abaqus_cae", "script": "scripts/plate.py", "args": [], "inputs": ["models/plate.cae"], "outputs": ["max_mises", "mass"]}
  ```
  - 처리 순서: 케이스마다 `inputs` 파일 복사 → `params.json` 작성 → 툴 명령 실행 → `results.json` 읽기.

`tool` 키별 기본 명령 템플릿은 아래 표와 같다(`cae_adapters/tool_commands.py`). `{exe}` 자리에는 PC Agent가 찾은 실행 파일 경로가 들어가고(아래 '툴 경로'), `{script}`는 스크립트 파일, `{args}`는 추가 인자다. 사내 사정으로 명령을 바꿔야 할 때만 `agent.toml`의 `[tool_commands]`·`[tool_env.<tool>]`을 쓴다.

| tool 키 | 기본 명령 템플릿 |
|---|---|
| `nx` | `{nx_run_journal} {script} -args {args}` |
| `simcenter` | `{nx_run_journal} {script} -args {args}` |
| `abaqus_cae` | `{abaqus} cae noGUI={script} -- {args}` |
| `abaqus_python` | `{abaqus} python {script} {args}` |
| `abaqus_solver` | `{abaqus} {args}` (예: `job=model input=model.inp cpus=4 interactive`) |
| `lsdyna` | `{lsdyna} {args}` (예: `i=main.k ncpu=4 memory=200m`) |
| `lsprepost` | `{lsprepost} -nographics c={script}` |
| `ansys_python` | `{ansys_python} {script} {args}` (PyMAPDL/PyDPF/PyMechanical) |
| `ansys_wb` | `{ansys_runwb2} -B -R {script}` |
| `mapdl` | `{ansys_mapdl} -b -i {script} -o solve.out` |
| `altair_hm` | `{altair_hmbatch} -tcl {script}` |
| `altair_hv` | `{altair_hvp} {script}` |
| `optistruct` | `{altair_optistruct} {args}` |
| `starccm` | `{starccm} -batch {script} {args}` |
| `amesim` | `{amesim_python} {script} {args}` |
| `adams_view` · `adams_solver` · `adams_python` | `{adams} aview ru-standard b {script}` · `{adams} ru-standard {args}` · `{adams} python {script} {args}` |
| `recurdyn` · `recurdyn_solver` | `{recurdyn} {args}` · `{recurdyn_solver} {args}` |
| `moldflow_run` · `moldflow_result` · `moldflow_synergy` | `{moldflow_runstudy} {args}` · `{moldflow_studyrlt} {args}` · `{python} {script}`(Synergy COM) |
| `moldex3d` | `{moldex3d_python} {script} {args}` |
| `comsol_batch` · `comsol_python` | `{comsol} batch {args}` · `{python} {script}`(MPh) |
| `fluent` · `fluent_python` | `{fluent} 3ddp -g -t4 -i {script}` · `{ansys_python} {script}`(PyFluent) |
| `openfoam` | `{openfoam_shell} bash {script} {args}` (WSL) |
| `flotherm` | `{flotherm} -b {args}` (FloSCRIPT) |
| `ansa` · `meta` | `{ansa} -nogui -exec "load_script: '{script}'" -exec "main()"` · `{meta} -b -s {script}` |
| `msc_nastran` | `{msc_nastran} {args}` (예: `job.bdf scr=yes`) |
| `ncode` | `{ncode} {args}` |
| `catia_com` · `catia_batch` | `{python} {script}`(COM) · `{catia} -batch -macro {script}` |
| `solidworks_com` | `{python} {script}`(COM) |
| `creo_trail` · `creo_python` | `{creo} -g:no_graphics -i:rpc_input {script}` · `{python} {script}`(Creoson) |
| `lighttools_com` | `{python} {script}`(LightTools COM API, LTAPI4) |
| `cst_python` · `cst_batch` | `{python} {script}`(CST 파이썬 라이브러리) · `{cst} -m -r {args}` |
| `motionsolve` · `motionsolve_python` | `{motionsolve} {args}`(XML) · `{python} {script}` |
| `simlab` | `{simlab} -auto {script}`(녹화 저널) |
| `celsius` · `celsius_python` | `{celsius} {args}` · `{python} {script}` |
| `inspire_script` | `{altair_inspire} -script {script}`(Inspire 파이썬) |
| `physicsai_export` · `physicsai_hw` | `{python} {script}`(DDS 케이스→데이터셋) · `{altair_hw} -b -f {script}` |

| `python` | 플랫폼 venv 파이썬 |

### 툴 경로 — 사용자는 적지 않는다
- 지원 툴 목록·기본 설치 경로(`guess`)·설치 흔적 단어(`FIND`)는 `cae_adapters/catalog.py` 한 곳에 있다.
- PC Agent(`agent/dds_agent/toolscan.py`)가 설치할 때 한 번, 실행 중에는 설치 상태가 바뀔 때마다(5분마다 확인) 찾는다.
  - 찾는 곳: 기본 설치 경로와 같은 구조의 다른 드라이브·폴더, 설치된 앱 목록(레지스트리), 시작 메뉴 바로가기, 환경변수·PATH
  - 버전이 여러 개면 최신을 쓰고, 제거·업그레이드로 파일이 없어지면 다시 찾는다. 결과는 `%USERPROFILE%\.dds-agent\tools.json`
- 못 찾은 툴은 사용자가 웹앱 'CAD·CAE 툴' 화면에서 실행 파일을 고른다(자동보다 우선). 에이전트는 사용자에게 설정 파일을 고치라고 하지 않는다.
- 우선순위: 웹에서 고른 것 > `agent.toml [tools]`(관리자 배포용) > PATH > 자동으로 찾은 것

## 3. 스킬 폴더 구조 (Gemini CLI Agent Skill)
```
agent-context/skills/<skill-name>/
  SKILL.md            # frontmatter(name, description) + 본문
  references/*.md     # 상세 API 레퍼런스(필요 시 SKILL.md에서 링크)
  scripts/*           # 바로 고쳐 쓸 수 있는 템플릿 스크립트 (위 1번 규약 준수)
```
SKILL.md frontmatter:
```yaml
---
name: abaqus-automation
description: Abaqus/CAE 배치 자동화 — 파라미터 변경, 하중·경계조건·재료·메쉬 변경, 해석 실행, ODB 결과 추출, 뷰포트 이미지 저장. 사용자가 Abaqus 모델 자동화나 결과 추출을 요청할 때 사용.
---
```
본문 권장 목차는 다음과 같다(한국어).
1. 언제 쓰나 / 전제(버전, 라이선스, 내장 파이썬 버전)
2. 실행 방법(배치 명령, tool 키)
3. 파라미터(형상) 변경
4. 해석조건 변경(재료, 하중, 경계조건, 메쉬, 스텝, 솔버 옵션)
5. 해석 실행과 완료/실패 판정
6. 결과 추출(필드, 히스토리, 최대값/특정 위치, 반력, 질량 등) → results.json
7. 이미지 저장(컨투어, 변형 형상, 뷰 방향, 해상도)
8. 자주 하는 실수와 디버깅(로그 위치, 에러 패턴)
9. 사내 검증 필요 항목: 버전에 따라 API가 다르거나 확인이 필요한 부분을 `TODO(사내검증)`로 표시

## 4. 정확성 원칙
- 공식 문서에서 확인되는 API만 쓴다. 확신이 없는 호출은 지어내지 않는다. 대안을 함께 적고 `# TODO(사내검증)`를 단다.
- 템플릿 스크립트는 문법적으로 올바르고 실행 가능한 형태여야 한다.
