---
name: openfoam-automation
description: OpenFOAM(오픈소스 CFD) 자동화를 Windows PC의 WSL에서 수행한다. 케이스 사전(dictionary)을 텍스트 템플릿(*.tmpl)이나 foamDictionary -set으로 바꾸고, blockMesh/snappyHexMesh 메쉬, simpleFoam/pimpleFoam/foamRun 해석, 병렬(decomposePar/mpirun), postProcess 함수 객체(forces, forceCoeffs, surfaceFieldValue) 출력의 postProcessing/*.dat 파싱으로 results.json, 잔차 그래프, 표면 필드(fields/*.npz)를 만든다. 사용자가 OpenFOAM 케이스의 파라메트릭/DOE/최적화, WSL 실행, 결과 추출, wslpath 경로 변환을 요청할 때 사용한다.
---

# OpenFOAM 자동화 스킬 (WSL)

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키)과 `images/*.png`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 정상/비정상 비압축·열유동 CFD를 라이선스 없이 반복 실행할 때 쓴다(DOE, 최적화).
- 실행 환경: Windows의 **WSL2 + 리눅스용 OpenFOAM**. 배포판이 두 계열이다.
  - ESI(openfoam.com, `openfoam2312` 등): `/usr/lib/openfoam/openfoam2312/etc/bashrc`
  - Foundation(openfoam.org, `openfoam11` 등): `/opt/openfoam11/etc/bashrc`. v11부터 `foamRun -solver incompressibleFluid`와 `foamPostProcess`를 쓴다.
  - 두 계열은 함수 객체 문법·출력 파일 이름이 조금 다르다. 사내 표준 배포판을 하나로 정한다. TODO(사내검증)
- WSL의 비대화형 bash는 `~/.bashrc`를 읽지 않는다. 그래서 `of_run.sh`가 OpenFOAM `etc/bashrc`를 직접 source한다(`--bashrc`로 지정 가능).
- **경로**: Windows `C:\work\case1` = WSL `/mnt/c/work/case1`. WSL 안에서는 `wslpath -u 'C:\work\case1'`, 반대는 `wslpath -w /home/u/case`. `wsl.exe`는 Windows 현재 폴더를 WSL 경로로 바꿔 시작하므로 케이스 폴더(cwd)에서 그대로 돈다.
- `/mnt/c`(9P 파일시스템)는 느리다. 큰 메쉬·병렬 해석은 `--linux-copy`로 WSL 홈(ext4)에서 돌리고 결과만 되돌린다.
- 줄바꿈: bash 스크립트와 사전 파일은 **LF**여야 한다. `of_prepare.py`가 실행 전에 CRLF를 LF로 바꾼다. 템플릿 폴더의 `.gitattributes`는 `*.sh`를 LF로 고정한다.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| 템플릿 렌더, foamDictionary 목록, LF 정리 | `python` | `of_prepare.py` (Windows 쪽) |
| 메쉬·해석·후처리 | `openfoam` | `{openfoam_shell} bash of_run.sh args` (`openfoam_shell` 기본값 `wsl`) |
| 결과 파싱·그래프·필드 | `python` | `of_extract.py` |

```json
{"tool": "python", "script": "scripts/of_prepare.py",
 "args": ["--set", "0/U|boundaryField/inlet/value|uniform ({{inlet_velocity}} 0 0)",
          "--set", "system/controlDict|endTime|{{iterations}}", "--default", "iterations=500"],
 "inputs": ["models/duct_case"],
 "post": [{"tool": "openfoam", "script": "scripts/of_run.sh", "args": ["--np", "4", "--linux-copy"]},
          {"tool": "python", "script": "scripts/of_extract.py",
           "args": ["--avg-last", "50", "--map", "forces_force_total_x=drag", "--map", "outletavg_areaaverage_p=p_out"]}],
 "outputs": ["drag", "p_out"], "timeout_sec": 14400}
```
- `inputs`에 케이스 **폴더**를 주면 어댑터가 `<케이스 실행 폴더>/duct_case/`로 통째로 복사한다. 세 스크립트 모두 cwd에 `system/`이 없으면 `system/`을 가진 **하위 폴더 하나**를 OpenFOAM 케이스 루트로 쓴다. `params.json`, `results.json`, `images/`, `fields/`는 cwd(케이스 실행 폴더)에 둔다.
- 플랫폼의 `DDS_CASE_DIR`을 WSL로 넘기려면 `agent.toml [tool_env.openfoam]`에 `WSLENV = "DDS_CASE_DIR/p"`를 둔다(`/p`는 경로 변환). 보통은 cwd 상속으로 충분하다.
- `{openfoam_shell}` 기본값 `wsl`은 기본 배포판을 쓴다. 특정 배포판은 `agent.toml [tool_commands]`에서 `openfoam = ["wsl", "-d", "Ubuntu-22.04", "bash", "{script}", "{args}"]`처럼 덮어쓴다.
- 템플릿: `scripts/of_prepare.py`, `scripts/of_run.sh`, `scripts/of_extract.py`, 상세는 [references/openfoam-notes.md](references/openfoam-notes.md)

## 3. 파라미터(형상) 변경
- **텍스트 템플릿**: 바꿀 파일을 `system/blockMeshDict.tmpl`처럼 `.tmpl`로 두고 값을 `{{name}}`으로 쓴다. `of_prepare.py`가 렌더 후 `.tmpl`을 지운다. 값 없는 플레이스홀더는 실패 처리한다.
  ```
  vertices ( (0 0 0) ({{length}} 0 0) ({{length}} {{height}} 0) (0 {{height}} 0) ... );
  ```
- blockMeshDict는 `#calc`/`#eval`(ESI) 같은 사전 내 계산을 쓸 수 있다. 원본 변수만 템플릿으로 두면 파생 치수를 사전이 계산한다. TODO(사내검증): 배포판별 `#calc`(컴파일러 필요)·`#eval` 지원.
- **STL 형상**(snappyHexMesh): 형상이 바뀌면 CAD에서 STL을 다시 만들어 `constant/triSurface/`에 넣는다(CAD 스킬과 연결). 위치·크기만 바뀌면 `surfaceTransformPoints`로 처리할 수 있다.

## 4. 해석조건 변경
`foamDictionary <파일> -entry <a/b/c> -set "<값>"`이 표준 방법이다(`--set "파일|엔트리|값"`).
| 대상 | --set 예 |
|---|---|
| 입구 속도 | `0/U|boundaryField/inlet/value|uniform ({{u_in}} 0 0)` |
| 출구 압력 | `0/p|boundaryField/outlet/value|uniform 0` |
| 동점성 | `constant/transportProperties|nu|{{nu}}` (ESI). org v11은 `constant/physicalProperties|nu|...` |
| 난류 모델 | `constant/momentumTransport|RAS/model|kOmegaSST` (org), `constant/turbulenceProperties|RAS/RASModel|kOmegaSST` (ESI) |
| 반복 수 | `system/controlDict|endTime|{{iterations}}` |
| 수렴 기준 | `system/fvSolution|SIMPLE/residualControl/p|1e-4` |
| 이완 계수 | `system/fvSolution|relaxationFactors/fields/p|0.3` |
| 메쉬 해상도 | `system/blockMeshDict.tmpl`의 셀 수 `({{nx}} {{ny}} 1)` |
| 병렬 수 | `of_run.sh --np 8` (decomposeParDict의 numberOfSubdomains를 자동 설정) |

- 엔트리 경로를 모르면 WSL에서 `foamDictionary 0/U -keywords`, `foamDictionary 0/U -entry boundaryField -keywords`로 확인한다.

## 5. 실행과 완료 판정
`of_run.sh` 순서: OpenFOAM 환경 → `foam_set.txt` 적용 → 메쉬(`auto`: polyMesh 있으면 생략, snappyHexMeshDict 있으면 snappy) → `checkMesh` → 솔버(controlDict `application`, v11은 `foamRun`) → `--post` 함수 → `--vtk`.
- 각 단계 로그는 `log.<이름>`이다. 종료코드가 0이 아니거나 `FOAM FATAL`이 있으면 실패한다.
- 솔버 로그 마지막에 `End`가 없으면 실패(발산, 강제 종료)로 본다. `nan`/`Floating point exception` 문구는 경고로 남긴다.
- `checkMesh`의 `Mesh OK`가 없으면 경고만 한다. 품질 기준을 강제하려면 스크립트에서 실패로 바꾼다.
- WSL 실행 파일 오류(`bash: of_run.sh: $'\r': command not found`)는 CRLF 문제다. `of_prepare.py`를 먼저 실행한다.

## 6. 결과 추출 → results.json
결과는 controlDict `functions`의 **함수 객체**로 해석 중에 기록한다(예시는 references). `of_extract.py`가 읽는다.
- `postProcessing/<함수>/<시작시간>/<파일>.dat`: 마지막 값 또는 `--avg-last N` 평균. 키 = `<함수>_<열>`(같은 폴더에 .dat가 여럿이면 `<함수>_<파일>_<열>`). 벡터는 `_x/_y/_z`.
  - ESI `forces` → `force.dat`(`total_x` …), `moment.dat`. org/구버전 `forces.dat`의 `((p) (v) (porous))` 형식도 펼친다.
  - `forceCoeffs` → `coefficient.dat`(`Cd`, `Cl` …), `surfaceFieldValue` → `areaAverage(p)` 열 → `..._areaaverage_p`.
- 솔버 로그: `iterations`, `end_time`, `converged`(SIMPLE `converged in`), `final_residual_<필드>`.
- `--map 원래키=결과키`로 이름을 정하고 `--only-mapped`로 정리한다. 처음에는 `run_script`로 한 번 돌려 전체 키 목록을 확인한다.

## 7. 이미지 저장 → images/
- `of_extract.py`가 `images/residuals.png`(초기 잔차, 로그 스케일)와 `images/pp_<함수>.png`(함수 객체 이력)를 만든다.
- 컨투어 그림은 ParaView 배치(`pvbatch`, `pvpython`)가 필요하다. WSL에 ParaView가 있으면 `foamToVTK` 결과로 그릴 수 있다. 이 스킬 범위 밖이며 TODO(사내검증)이다. 대신 표면 필드를 `fields/*.npz`로 남겨 플랫폼 3D 뷰어로 본다.

## 8. 3D 필드 (fields/*.npz)
- `surfaces` 함수 객체를 `surfaceFormat raw`로 설정하면 `x y z 값` 텍스트가 나온다. `of_extract.py --surface-raw "postProcessing/surfaces/*/p_wall.raw" --field-name pressure`가 마지막 시간 파일을 읽어 `fields/pressure.npz`(points, values; 벡터는 크기)를 쓴다.
- 평평한 면은 `--triangulate xy`로 삼각형을 붙인다. 굽은 3D 벽은 points만 저장한다.
- 출력 파일 이름·폴더 구조는 배포판마다 다르다(`p_wall.raw`, `wall/p.raw` 등). glob 패턴으로 맞춘다. TODO(사내검증)
- raw 값이 면 중심인지 점인지는 `interpolate true`(점) 설정에 따른다. 케이스마다 점 개수가 같도록 메쉬 토폴로지를 유지하면 학습 정확도가 높다.

## 9. 흔한 오류와 대처
| 증상 | 원인 / 대처 |
|---|---|
| `OpenFOAM environment not found` | `--bashrc /usr/lib/openfoam/openfoam2312/etc/bashrc` 지정 |
| `$'\r': command not found` | CRLF. `of_prepare.py`를 먼저 실행하거나 `sed -i 's/\r$//' of_run.sh` |
| `system/ not found` | cwd와 하위 폴더 어디에도 `system/`이 없거나, `system/`을 가진 하위 폴더가 둘 이상 |
| `FOAM FATAL IO ERROR ... keyword ... undefined` | 배포판별 키워드 차이(`nu` 위치, `RASModel`/`model`) |
| 결과 dat 없음 | controlDict `functions` 누락, `libs` 이름 오류. `log.<solver>`에서 함수 객체 경고 확인 |
| 매우 느림 | `/mnt/c`에서 실행 중. `--linux-copy` 사용 |
| `mpirun` 실패 | WSL에 openmpi 미설치 또는 root 실행. 일반 사용자로 실행하고 `--np 1`로 먼저 확인 |
| 발산 (`End` 없음) | 메쉬 품질(`log.checkMesh`), 초기 이완 계수, 1차 기법으로 시작 |

### 사내 검증 필요 항목 (TODO(사내검증))
1. 사내 표준 배포판(ESI/Foundation)과 버전, 설치 경로(`etc/bashrc`).
2. `wsl.exe`가 Windows cwd(긴 경로, 네트워크 드라이브 포함)를 WSL 경로로 바르게 넘기는지.
3. `surfaces` raw 출력 경로·이름과 forces 출력 파일 이름.
4. WSL 메모리·코어 제한(`.wslconfig`)과 병렬 실행 시 동시 케이스 수.
