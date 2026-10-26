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

## 2. 실행 방법 (dds-cae MCP 툴)
- `run_script(tool, script, filename, args, dry_run, case_dir?)`: 에이전트가 작성한 스크립트를 해당 툴 배치 모드로 1회 실행한다(탐색, 디버깅, 단발 작업).
- 스터디 어댑터 `script`: 같은 스크립트를 DOE/최적화 케이스마다 반복 실행한다.
  ```json
  {"tool": "abaqus_cae", "script": "scripts/plate.py", "args": [], "inputs": ["models/plate.cae"], "outputs": ["max_mises", "mass"]}
  ```
  - 처리 순서: 케이스마다 `inputs` 파일 복사 → `params.json` 작성 → 툴 명령 실행 → `results.json` 읽기.

`tool` 키와 명령 템플릿은 `config.toml`의 `[tool_commands]`에 정의한다. `{exe}`는 `[tools]`의 실행파일 경로, `{script}`는 스크립트 파일, `{args}`는 추가 인자다.

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
| `python` | 플랫폼 venv 파이썬 |

## 3. 스킬 폴더 구조 (Gemini CLI Agent Skill)
```
gemini-extension/skills/<skill-name>/
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
