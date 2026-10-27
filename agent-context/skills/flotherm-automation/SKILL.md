---
name: flotherm-automation
description: Siemens Simcenter Flotherm(전자장비 냉각) 배치 자동화. GUI에서 녹화한 FloSCRIPT(XML)에 {{플레이스홀더}}를 넣어 부품 발열량, 히트싱크 형상, 팬 유량·팬 커브, 격자·반복 수를 케이스마다 바꾸고 `flotherm -b`로 해석한 뒤, 내보낸 결과 표(CSV)에서 부품별 최대 온도와 여유(margin)를 results.json으로, 막대 그래프를 images/로 저장한다. 사용자가 Flotherm(.pack/프로젝트) 열해석의 파라메트릭/DOE/최적화, FloSCRIPT 작성, 부품 온도 추출을 요청할 때 사용한다. FloEFD는 간단히 다룬다.
---

# Simcenter Flotherm 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 보드·함체 냉각 모델(부품 발열, 히트싱크, 팬, 통풍구)을 바꿔 가며 부품 최대 온도를 비교할 때 쓴다(DOE, 최적화).
- 자동화 수단은 **FloSCRIPT**다. FloSCRIPT는 GUI 조작을 기록한 XML 명령 목록이며, 배치 모드에서 다시 실행할 수 있다.
  - FloXML은 **모델 정의**(형상·물성)를 기술하는 별도 XML 형식이다. 모델을 처음부터 만들 때 쓰고, 이 스킬은 기존 프로젝트 변경을 다룬다.
- **요소·속성 이름을 지어내지 않는다.** 이 스킬의 FloSCRIPT 템플릿은 순서만 보여 주는 뼈대다. 실제 명령은 사내 Flotherm GUI에서 **FloSCRIPT 녹화**로 얻는다(프로젝트 열기 → 값 변경 → 해석 → 결과 표 내보내기 → 저장). 녹화 메뉴 위치는 버전마다 다르다. TODO(사내검증)
- 라이선스: Flotherm 솔버 토큰. 동시 케이스 수를 라이선스 수 이하로 둔다.
- 경로 키 `flotherm`은 PC Agent가 설치 위치에서 자동으로 찾는다. 못 찾으면 사용자가 웹앱 'CAD·CAE 툴' 화면에서 `flotherm.bat`을 고른다. TODO(사내검증): 설치 폴더의 배치 진입점(`flotherm.bat` 등)과 환경 변수 설정 필요 여부.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| FloSCRIPT 렌더 (+ 선택 실행) | `python` | `flotherm_prepare.py` (`DDS_FLOTHERM` 경로 사용) |
| FloSCRIPT 배치 실행 | `flotherm` | `{flotherm} -b case_script.xml` |
| 결과 표 파싱, 그래프 | `python` | `flotherm_extract.py` |

`flotherm` 키는 `{script}` 없이 `args`만 받는다. 렌더된 파일 이름을 `args`에 고정해 쓴다.

**A. 렌더 → flotherm 키 → 추출 (권장)**
```json
{"tool": "python", "script": "scripts/flotherm_prepare.py", "args": ["--set", "project_name=board"],
 "inputs": ["models/board.pack", "models/board_case.xml.tmpl"],
 "post": [{"tool": "flotherm", "args": ["case_script.xml"]},
          {"tool": "python", "script": "scripts/flotherm_extract.py",
           "args": ["--csv", "component_temps.csv", "--only", "U1_CPU", "--only", "U2_GPU", "--limit", "95"]}],
 "outputs": ["tmax_u1_cpu", "tmax_u2_gpu", "margin_min"], "timeout_sec": 7200}
```
`flotherm` 키 템플릿이 이미 `-b`를 넣으므로 `args`에는 렌더된 파일 이름만 쓴다.
**B. 드라이버가 직접 실행**: `flotherm_prepare.py --run --require component_temps.csv` 하나로 렌더·실행·판정을 하고 post에서 추출만 한다.
- `.pack`을 프로젝트로 여는(unpack) 방법은 녹화로 확인한다. TODO(사내검증)
- 템플릿: `scripts/floscript_template.xml.tmpl`(뼈대), `scripts/flotherm_prepare.py`, `scripts/flotherm_extract.py`. 상세: [references/floscript-notes.md](references/floscript-notes.md)

## 3. 파라미터 변경
- 녹화한 FloSCRIPT에서 바꿀 숫자를 `{{cpu_power}}`처럼 플레이스홀더로 바꾸고 파일 이름을 `*.xml.tmpl`로 저장한다.
- `flotherm_prepare.py`가 `params.json`(우선) 또는 `--set` 값으로 치환하고, XML 특수문자를 이스케이프하며, 렌더 결과가 올바른 XML인지 검사한다. 값 없는 플레이스홀더는 실패 처리한다(XML 주석 안은 예외).
- 대표 설계변수
  | 변수 | 대상 객체 (녹화로 확인) |
  |---|---|
  | 부품 발열량 [W] | Source / Component의 power 속성 |
  | 히트싱크 핀 높이·수·두께 | Heat Sink SmartPart의 형상 속성 |
  | 팬 유량 또는 팬 커브 | Fan SmartPart(고정 유량 또는 P-Q 커브 점) |
  | 통풍구 개구율 | Perforated Plate / Vent 속성 |
  | 주위 온도 | Model Setup의 ambient |
- 단위는 프로젝트 단위계를 따른다. 녹화 값과 같은 단위로 넣는다.

## 4. 해석 조건 변경
- 격자: 형상이 바뀌면 격자를 다시 만든다. 녹화한 스크립트에 재격자 단계가 들어 있는지 확인한다. 격자 제약(Grid constraint)을 객체에 붙여 두면 형상 변경 시 격자가 따라간다.
- 반복 수·수렴 기준: Solver Control의 outer iteration 수를 플레이스홀더로 둔다(`{{iterations}}`).
- 난류 모델, 복사, 중력 방향: Model Setup에서 바뀌므로 기준 프로젝트에 고정하는 것을 권장한다.
- 정상/비정상(transient) 전환은 템플릿을 별도로 녹화한다.

## 5. 실행과 완료 판정
- B 방식: 종료코드 + `--require` 결과 파일 존재로 판정한다. 콘솔 출력은 `flotherm_console.log`에 남는다. 결과 파일은 실행 전에 지워 이전 값으로 오판하지 않는다.
- A 방식: `flotherm_extract.py`가 결과 표를 못 찾거나 `--only` 부품이 없으면 실패한다.
- TODO(사내검증): 발산·미수렴 시 종료코드, 솔버 로그 파일 위치와 오류 문구. 확인되면 `flotherm_prepare.py`에 로그 검사를 추가한다.

## 6. 결과 추출 → results.json
- FloSCRIPT 마지막에 결과 표(부품별 최대/평균 온도, Monitor Point 값)를 **CSV로 내보내는 명령**을 녹화해 둔다. 파일 이름은 `component_temps.csv`처럼 상대 경로로 고정한다. TODO(사내검증): 표 내보내기 명령과 열 이름.
- `flotherm_extract.py`
  - 헤더 행을 자동으로 찾는다(이름 열 `name`, 값 열 `max.*temp`; `--name-col`, `--value-col` 정규식으로 변경).
  - 키: `tmax_<부품 snake>`, `tmax_overall`, `--limit L`이면 `margin_min = L - tmax_overall`.
  - `--only U1_CPU`로 관심 부품만 남기고, 표에 없으면 실패한다.
  - Monitor Point 표는 `--name-col "monitor" --value-col "temperature" --prefix tmon`처럼 쓴다.

## 7. 이미지 저장 → images/
- `flotherm_extract.py`가 `images/component_tmax.png`(부품 최대 온도 막대, 허용 온도 선)를 만든다.
- 온도 컨투어 그림은 Flotherm 후처리(Visual Editor/Results)에서 이미지 저장을 녹화해 FloSCRIPT에 넣는다. TODO(사내검증): 배치 모드 이미지 저장 지원 여부.

## 8. 3D 필드 (fields/*.npz)
- Flotherm 결과 필드를 텍스트로 내보내는 방법(표면 온도 점 데이터)은 확인되지 않았다. TODO(사내검증)
- 확인되면 `x, y, z, temperature` CSV로 내보낸 뒤 `fluent-automation/scripts/fluent_extract.py`의 `export_field` 방식을 그대로 쓰면 `fields/temperature.npz`(points, values)를 만들 수 있다.

## 9. 흔한 오류와 대처
| 증상 | 원인 / 대처 |
|---|---|
| 실행 파일을 못 찾음 | 사용자에게 웹앱 'CAD·CAE 툴' 화면에서 Flotherm 실행 파일을 고르도록 안내 |
| 렌더 실패 `no value for placeholder` | params.json 키와 템플릿 이름 불일치. `--set`으로 기본값 지정 |
| XML 파싱 오류 | 녹화 파일 수정 중 태그 손상. 플레이스홀더는 속성 값 안에만 넣는다 |
| 값이 바뀌지 않음 | 녹화 스크립트가 객체를 이름이 아닌 위치로 선택. 객체 이름 기반 선택으로 다시 녹화 |
| 결과 CSV 없음 | 내보내기 명령 누락, 또는 경로가 절대 경로. 상대 경로로 고친다 |
| 열 이름 미일치 | `--name-col`, `--value-col`을 표 머리글에 맞춘다(cp949 CSV도 읽는다) |

### FloEFD
FloEFD는 CAD 내장형(NX, Creo, CATIA, SolidWorks) 유동·열 해석이다. 형상 변경은 각 CAD 스킬(`nx-cad-automation` 등)로 하고, 해석 실행·결과 추출은 CAD 내 FloEFD API나 Batch Run 기능을 쓴다. TODO(사내검증): 사내 FloEFD 버전의 API·배치 실행 방법. 확인 전에는 이 스킬의 FloSCRIPT 방식을 적용하지 않는다.

### 사내 검증 필요 항목 (TODO(사내검증))
1. `flotherm -b <FloSCRIPT.xml>` 호출 형식, 진입 배치 파일, 종료코드 의미.
2. FloSCRIPT 녹화 메뉴, 객체 선택·속성 변경·해석·표 내보내기·저장 명령의 정확한 요소 이름.
3. `.pack` 열기와 프로젝트 저장 위치.
4. 배치 모드 이미지 저장과 필드 데이터 내보내기.
