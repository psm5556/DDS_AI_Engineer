---
name: fluent-automation
description: Ansys Fluent 배치 자동화. TUI 저널(.jou) 템플릿에 params.json 값을 넣어 `fluent 3ddp -g -t4 -i run.jou`로 실행하고, 경계조건·물성·반복 수 변경, 리포트 파일(*.out)과 표면 적분(*.srp) 파싱으로 results.json 작성, 잔차·리포트 이력 그래프와 표면 필드(fields/*.npz) 저장을 다룬다. PyFluent(ansys-fluent-core)는 대안으로 다룬다. 사용자가 Fluent(.cas/.cas.h5) CFD·열유동 해석의 파라메트릭/DOE/최적화, 저널 작성, 결과 추출을 요청할 때 사용한다.
---

# Ansys Fluent 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 준비된 `.cas.h5`(메쉬와 설정 포함)를 기준으로 경계조건·물성·반복 수를 바꿔 가며 반복 해석할 때 쓴다. 형상 변경은 메쉬를 다시 만들어야 하므로 Fluent Meshing 저널이나 Workbench(`ansys-automation` 스킬)로 처리한다.
- 자동화 경로
  - **TUI 저널 + 텍스트 템플릿 (권장)**: Fluent가 받는 것은 TUI 명령 텍스트뿐이다. `{{name}}` 플레이스홀더를 치환해 `run.jou`를 만든다. 가장 단순하고 버전 호환이 좋다.
  - **PyFluent**: `ansys-fluent-core`가 깔린 파이썬(`ansys_python`)에서 세션을 띄워 settings API로 조작한다. API 이름이 릴리스마다 바뀌므로 버전 고정이 필요하다. [references/pyfluent.md](references/pyfluent.md)
- **TUI 프롬프트 순서는 버전과 모델 설정에 따라 다르다.** 정확한 명령은 GUI에서 `File → Write → Start Journal`로 한 번 조작을 기록하거나, 콘솔에서 명령을 단계별로 입력해 확인한다. 이 문서의 모든 `TODO(사내검증)`은 이 방법으로 확인한다.
- 모델 준비 원칙: 결과값은 **Report Definition**(면적평균 온도, 질량유량, 압력강하, 힘)으로 만들고 **Report File**을 켜 둔다. Report File 경로는 상대 경로(파일 이름만)로 지정한다. 절대 경로면 다른 PC 케이스 폴더에서 실패한다.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| 고정 저널 실행 | `fluent` | `{fluent} 3ddp -g -t4 -i script.jou` |
| 저널 렌더 + Fluent 실행 + 판정 | `python` | `fluent_render_run.py` (`DDS_FLUENT` 경로 사용) |
| 결과 파싱, 그래프, 필드 | `python` | `fluent_extract.py` |
| PyFluent 스크립트 | `fluent_python` | `{ansys_python} script.py args` |

`fluent` 키는 `{script}`를 그대로 실행하므로 케이스별 값을 넣을 수 없다. DOE에는 A(드라이버) 또는 B(렌더 → 고정 래퍼)를 쓴다. 차원·코어 수를 바꾸려면 A를 쓰거나 `agent.toml [tool_commands]`의 `fluent` 템플릿을 덮어쓴다.

**A. 파이썬 드라이버 (권장)**
```json
{"tool": "python", "script": "scripts/fluent_render_run.py",
 "args": ["--set", "iterations=500", "--np", "8", "--require", "outlet_temp.srp"],
 "inputs": ["models/duct.cas.h5", "models/run_template.jou"],
 "post": {"tool": "python", "script": "scripts/fluent_extract.py", "args": ["--avg-last", "50", "--map", "outlet_temp=t_out"]},
 "outputs": ["t_out", "mass_flow", "pressure_drop"], "timeout_sec": 14400}
```
**B. 렌더 후 tool 키 `fluent`로 실행**
```json
{"tool": "python", "script": "scripts/fluent_render_run.py", "args": ["--no-run", "--set", "iterations=500"],
 "inputs": ["models/duct.cas.h5", "models/run_template.jou"],
 "post": [{"tool": "fluent", "script": "scripts/fluent_wrapper.jou"},
          {"tool": "python", "script": "scripts/fluent_extract.py", "args": []}],
 "outputs": ["outlet_temp"]}
```
- B는 Fluent 오류를 종료코드로만 알 수 있다. 판정은 `fluent_extract.py`가 값을 못 찾으면 실패하는 것으로 대신한다. TODO(사내검증): Windows에서 런처가 `-wait` 없이 바로 반환되면 B가 결과 파일 전에 다음 단계로 넘어간다. 이 경우 A를 쓴다.
- 템플릿: `scripts/run_template.jou`(저널 예시), `scripts/fluent_wrapper.jou`, `scripts/fluent_render_run.py`, `scripts/fluent_extract.py`

## 3. 파라미터 변경
- 저널 템플릿의 `{{inlet_velocity}}`가 `params.json`의 `inlet_velocity`로 치환된다. 값 우선순위는 `params.json` > `--set` 기본값 > 내장값(`case_file`)이다.
- 명령 줄에 값 없는 플레이스홀더가 남으면 렌더가 실패한다(주석 줄은 예외).
- **Named input parameter** 방식: GUI에서 BC 값 칸을 파라미터로 지정하고 이름을 붙여 두면 BC 프롬프트 대신 파라미터만 바꾸면 된다. TUI 문법은 `/define/parameters/input-parameters/...`이다. TODO(사내검증): 버전별 하위 명령과 인자.
- 형상 치수 변경은 Fluent 솔버에서 할 수 없다. 메쉬 모핑(Adjoint/Mesh morpher)이나 재메쉬가 필요하다.

## 4. 해석조건 변경
| 대상 | TUI 예 (TODO(사내검증): 프롬프트/키워드) |
|---|---|
| 입구 속도 | `/define/boundary-conditions/set/velocity-inlet inlet () vmag no 5 quit` |
| 입구 온도 | `/define/boundary-conditions/set/velocity-inlet inlet () temperature no 300 quit` |
| 벽 열유속 | `/define/boundary-conditions/set/wall heater () heat-flux no 5000 quit` |
| 출구 압력 | `/define/boundary-conditions/set/pressure-outlet outlet () gauge-pressure no 0 quit` |
| 물성 | `/define/materials/change-create` 대화형(프롬프트 많음). 녹화한 줄을 그대로 템플릿화 |
| 난류 모델 | `/define/models/viscous/kw-sst yes` |
| 초기화 | `/solve/initialize/hyb-initialization` 또는 `/solve/initialize/initialize-flow` |
| 반복 수 | `/solve/iterate {{iterations}}` |
| 수렴 기준 | `/solve/monitors/residual/convergence-criteria ...` (방정식 순서대로 값) |
| 비정상 | `/solve/set/time-step 1e-3` 후 `/solve/dual-time-iterate {{n_steps}} {{max_iter_per_step}}` |
| 코어 수 | 런처 `-t8` (드라이버 `--np 8`) |

- `set` 문법은 `영역 목록 ()` 다음에 `키워드 no 값` 쌍을 쓰고 `quit`로 끝낸다(`no`는 "프로파일/파라미터 사용?" 질문에 대한 답).
- 저널에서 한 줄이 틀리면 이후 입력이 엇갈려 엉뚱한 값이 들어갈 수 있다. 새 템플릿은 반드시 `run_script`로 한 번 돌리고 `fluent.trn`을 확인한다.

## 5. 실행과 완료 판정
- 드라이버 명령: `<fluent> 3ddp -g -t4 -i run.jou` (+ Windows `-wait`, 선택 `-hidden`). TODO(사내검증): `-wait`, `-hidden` 지원.
- 판정 순서: 종료코드 → `fluent.trn`과 `fluent_console.log`에서 `Error:`, `Divergence detected`, `floating point exception`, `invalid command` → `--require` 파일 존재. 하나라도 걸리면 exit 1.
- 저널은 `/file/start-transcript fluent.trn`으로 시작하고 `/exit yes`로 끝낸다. `/exit`가 없으면 Fluent가 입력 대기로 멈춘다.
- 수렴 여부: 트랜스크립트의 `solution is converged`를 `converged`(1/0)로 남긴다. 반복 수를 다 써도 수렴하지 않은 케이스를 실패로 볼지는 스터디 정책으로 정한다.

## 6. 결과 추출 → results.json
`fluent_extract.py`가 케이스 폴더의 파일을 읽는다.
- `*.out` (Report File): 열마다 마지막 값, `--avg-last 50`이면 마지막 50행 평균(진동 수렴 대비). 키는 리포트 정의 이름의 snake(`outlet-temp` → `outlet_temp`).
- `*.srp` (`/report/surface-integrals/... yes file.srp`): `Net` 값은 파일 이름 키, 표면별 값은 `<파일>_<표면>`.
- `fluent.trn`: `iterations`(마지막 반복 번호), `converged`.
- `--map outlet_temp=t_out`으로 키를 바꾸고, `--only-mapped`로 필요한 키만 남긴다.
- 힘: `/report/forces/wall-forces` 또는 Force Report Definition + Report File을 쓴다(후자 권장).

## 7. 이미지 저장 → images/
- 기본: `fluent_extract.py`가 `images/residuals.png`, `images/report_<name>.png`(이력 그래프), `--ascii` 지정 시 `images/field_<name>.png`(3D 산점도)를 만든다. 그래픽 드라이버와 무관하므로 가장 안정적이다.
- Fluent 컨투어: 케이스에 Graphics object(예: `contour-temp`)를 만들어 두고 `/display/objects/display contour-temp`, `/display/save-picture images/x.png`. TODO(사내검증): `-g` 배치에서 오프스크린 렌더링 지원 여부. 안 되면 `-gu` 또는 `-driver null` 등 버전 문서를 확인한다.

## 8. 3D 필드 (fields/*.npz)
- 저널에서 표면 노드값을 CSV로 내보낸다: `/file/export/ascii heater_surface.csv heater () yes temperature () no` TODO(사내검증): 프롬프트 순서.
- `fluent_extract.py --ascii heater_surface.csv --field-name temperature --value-col temperature`가 `x/y/z-coordinate` 열과 값 열을 읽어 `fields/temperature.npz`(points, values)를 쓴다. 평평한 면이면 `--triangulate xy`로 Delaunay 삼각형을 붙인다(굽은 면에는 쓰지 않는다).

## 9. 흔한 오류와 대처
| 증상 | 원인 / 대처 |
|---|---|
| Fluent가 끝나지 않음 | 저널 끝 `/exit yes` 누락, 또는 프롬프트 순서가 틀려 입력 대기. `fluent.trn` 마지막 줄 확인 |
| `invalid command` | 버전마다 TUI 경로가 다르다. 콘솔에서 상위 메뉴를 입력해 하위 명령 목록을 본다 |
| 리포트 파일 없음 | Report File 경로가 절대 경로, 또는 Report File이 꺼져 있음 |
| `Divergence detected in AMG solver` | 초기화, 경계조건 단위, URF를 확인한다. 1차 기법으로 시작 후 2차로 전환 |
| 라이선스 대기/실패 | 동시 케이스 수를 줄인다. `fluent_console.log`에서 license 확인 |
| 한글 경로 오류 | 케이스 폴더와 파일 이름에 한글·공백을 피한다 |
| 값이 이전 케이스와 같음 | `/file/read-case-data`로 이전 해를 읽고 초기화를 안 함. `read-case` + 초기화 사용 |

### 사내 검증 필요 항목 (TODO(사내검증))
1. 사내 버전의 `set` BC 키워드(`vmag`, `temperature`, `heat-flux`, `gauge-pressure`)와 named input parameter TUI.
2. `/report/surface-integrals`, `/file/export/ascii`의 프롬프트 순서.
3. Windows 런처의 `-wait`, `-hidden` 옵션, `-g` 배치에서의 `save-picture`.
4. PyFluent 버전과 settings API 경로(references/pyfluent.md).
