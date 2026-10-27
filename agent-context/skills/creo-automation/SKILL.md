---
name: creo-automation
description: PTC Creo Parametric 자동화 — Creoson(JSON over HTTP, creopyson)으로 모델 열기, 파라미터·치수 변경, 재생성 실패 감지, 질량특성 측정, STEP/STL 내보내기를 하고, 라이선스 하나로 GUI 없이 도는 trail 파일 배치(parametric -g:no_graphics)와 mapkey를 다룬다. 사용자가 Creo(Pro/E) 모델 파라미터·relations 변경, Creo 형상으로 DOE·최적화 케이스 생성, .prt 질량특성·STEP/STL 추출, 열어 둔 Creo에서 함께 작업을 요청할 때 사용.
---

# Creo Parametric 자동화 (Creoson / trail / mapkey)

## 1. 언제 쓰나 / 전제
- 파라메트릭 Creo 모델(.prt/.asm)의 파라미터나 치수를 바꿔 재생성하고, 질량특성이나 형상 파일을 얻을 때 쓴다. PIDO 흐름의 **형상 단계**다.
- 방법 세 가지

| 방법 | 장점 | 단점 |
|---|---|---|
| **Creoson + creopyson** (권장) | 파라미터·치수·재생성·질량·내보내기를 API로 호출. 열린 세션에 붙음 | CreosonServer(Java) 설치·실행 필요. J-Link 기반 |
| trail 파일 배치 | 추가 설치 없음. `-g:no_graphics`로 GUI 없이 실행 | 녹화 결과가 버전·UI 언어·설정에 민감. 오류 처리 어려움 |
| mapkey | 사용자 GUI에서 반복 작업 단축 | 단독 배치 수단은 아님. Creoson `interface_mapkey`로 호출 |

- 전제
  - Creo Parametric 4.0 이상, 라이선스 필요. Creoson은 J-Link(Java) 옵션이 설치돼 있어야 한다. TODO(사내검증): 사내 Creo 설치에 J-Link 포함 여부
  - CreosonServer를 실행해 둔다(기본 포트 9056). 파이썬에 `pip install creopyson`. Creo 7 이상은 `creo_version=7`처럼 버전을 알려 준다. TODO(사내검증)
  - Creoson으로 새로 띄우는 Creo는 `nitro_proe_remote.bat`(CreosonSetup이 만드는 시작 배치)로 시작한다. COM이 아니므로 대화형 세션 제약은 약하지만, 그래픽이 필요한 이미지 캡처는 데스크톱 세션에서만 된다.
  - **좀비 프로세스**: Creo 본체는 `xtop.exe`다. 스크립트가 띄운 경우만 `kill_creo()`로 끝낸다. trail 타임아웃 뒤 남은 `xtop.exe`는 사용자 동의 후 정리한다. 사용자 세션은 끝내지 않는다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `creo_python` | `{python} {script} {args}` | Creoson 스크립트, trail 치환 드라이버 |
| `creo_trail` | `{creo} -g:no_graphics -i:rpc_input {script}` | 고정 trail 파일을 그대로 1회 재생(파라미터 치환 없음) |

경로 키 `creo`(기본 `parametric`)는 PC Agent가 `...\Creo 9.0.x.0\Parametric\bin\parametric.exe` 같은 설치 위치에서 자동으로 찾는다. 플랫폼은 이를 환경변수 `DDS_CREO`로도 넘긴다.

| 템플릿 | 용도 |
|---|---|
| `scripts/creo_creoson_case.py` | Creoson 케이스 실행. `inspect=1`이면 파라미터·치수 목록만 `model_info.json`에 쓴다(**먼저 실행**) |
| `scripts/creo_trail_case.py` | trail 템플릿의 `@@이름@@`를 params로 치환 → `parametric -g:no_graphics` 실행 → 나온 STL로 결과 계산 |
| `scripts/stl_to_fields.py` | STL → `fields/*.npz` 변환과 STL 형상량(순수 파이썬+numpy) |

### 2-1. Creoson 스터디 어댑터
```json
{"tool": "creo_python", "script": "scripts/creo_creoson_case.py",
 "args": ["model=bracket.prt", "export_step=1", "export_stl=1", "fields=1"],
 "inputs": ["models/bracket.prt"],
 "outputs": ["mass", "volume", "cog_z"]}
```
- Creoson은 Creo 세션 하나를 쓰므로 **병렬 실행하지 않는다**(스터디 병렬도 1). 케이스가 끝나면 모델을 세션에서 지운다(`file_erase`). 그래야 다음 케이스가 메모리의 옛 모델을 쓰지 않는다.

### 2-2. trail 배치
1. Creo에서 같은 작업을 한 번 한다: 모델 열기 → Tools → Parameters에서 값 변경 → Regenerate → File → Save As → Save a Copy(STL). 그동안 trail이 기록된다(작업 폴더의 `trail.txt.N`).
2. 기록된 trail에서 값 부분을 `@@thickness@@`처럼 바꾸고, 파일 경로를 케이스 폴더 상대 경로로 바꾼다. 끝에 종료 명령(`~ Command `ProCmdExit``)이 있어야 Creo가 끝난다. TODO(사내검증)
3. 스터디 어댑터:
```json
{"tool": "creo_python", "script": "scripts/creo_trail_case.py",
 "args": ["trail=bracket_trail.txt", "stl=export/bracket.stl", "fields=1"],
 "inputs": ["models/bracket.prt", "models/bracket_trail.txt"],
 "outputs": ["volume", "area"]}
```
- trail은 UI 언어·창 배치·config.pro 설정이 달라지면 깨진다. 녹화와 같은 환경에서만 재생한다.
- 질량은 trail에서 바로 얻기 어렵다. 이 템플릿은 STL로 부피·면적·중심을 계산한다. 질량이 필요하면 `volume × 밀도`로 구하거나 Creoson을 쓴다.

## 3. 파라미터 변경
1. `inspect=1`로 파라미터(`parameter_list`)와 치수(`dimension_list`) 목록을 본다. 설계변수를 사용자와 확정한다.
2. params.json 키 규칙: 그대로 쓰면 **모델 파라미터**(`parameter_set`)다. `d:` 접두어면 **치수**(`d:d12` → `dimension_set("d12", 값)`)다.
3. 파라미터 방식을 권장한다. relations(`d12 = THICKNESS`)로 치수를 구동하게 해 두면 이름이 안정적이다. relations로 구동되는 치수를 직접 바꾸면 재생성 때 덮어써진다.
4. `parameter_set`은 없는 이름이면 **새 파라미터를 만들 수 있다**. 템플릿은 목록에 없는 키를 막고(`strict=1`이면 실패), `no_create=True`를 준다. TODO(사내검증): 인자 이름
5. relations 자체를 바꾸려면 `file_relations_get` / `file_relations_set`을 쓴다. TODO(사내검증)
6. Family Table 인스턴스는 generic의 테이블 값이 우선한다.

## 4. 모델 업데이트와 실패 감지
- `file_regenerate(file_=mdl)`가 오류를 돌려주면 실패다. creopyson은 Creoson 오류를 예외로 올린다.
- TODO(사내검증): Creo가 실패 피처를 남긴 채(Resolve 모드 없이) 성공을 돌려주는 경우가 있다. 그래서 질량특성 부피가 0이거나 없으면 실패로 판정한다. 필요하면 `feature_list`의 status(failed/suppressed)도 확인한다.
- trail 방식은 재생성이 실패하면 보통 STL이 생기지 않거나 trail이 멈춰 타임아웃이 난다. 둘 다 실패로 처리한다. 진단은 케이스 폴더의 `std.out`과 새 `trail.txt.N`을 본다.
- 템플릿은 시작할 때 이전 results.json을 지운다.

## 5. 측정(질량특성) → results.json
```json
{"volume": 52600.0, "mass": 0.412, "area": 18800.0, "density": 7.83e-6,
 "cog_x": 0.0, "cog_y": 12.3, "cog_z": 40.1, "n_params_applied": 2}
```
- `file_massprops(file_=mdl)` 결과를 쓴다. **단위는 모델 단위계**를 따른다(mmNs·mmKs 템플릿이면 mm 기반). 모델 단위를 사용자와 확인하고 outputs 이름을 정한다.
- TODO(사내검증): 반환 dict 키(`volume`, `mass`, `surface_area`, `density`, `gravity_center{x,y,z}`)와 관성 키 이름. 템플릿은 키가 없으면 그 항목만 빼고 쓴다.
- 밀도는 Creo 재료나 `PRO_MP_DENSITY` 파라미터를 따른다. 지정이 없으면 질량이 부피와 같게 나올 수 있다.

## 6. 형상 내보내기(STEP/STL)
- Creoson: `interface_export_file("STEP", filename, dirname, file_=mdl)`. TODO(사내검증): `"STL"` 타입 지원 여부(Creoson 버전별). 미지원이면 STL 저장 mapkey를 만들어 `interface_mapkey`로 실행한다.
- trail: Save a Copy → STL 단계를 녹화해 둔다. STL 공차(chord height, angle control)도 대화상자 값으로 trail에 남는다.
- STEP AP214/AP242는 config.pro의 `step_export_format`을 따른다. TODO(사내검증)
- **3D 형상 학습**: `fields=1`이면 STL을 `stl_to_fields.py`로 `fields/shape.npz`(`points`, `triangles`, `values`)로 바꾼다. 플랫폼이 이를 업로드해 설계변수 → 3D 형상 모델을 학습한다(규약 1-1). `values`는 기본 0(형상만)이고, `--values dist_centroid|x|y|z`로 기하량을 넣을 수 있다. STL 형상량 `stl_volume`, `stl_area`, `stl_cx..`, `stl_bbox_*`가 results.json에 추가된다.

## 7. 이미지 저장 → images/
- Creoson `interface_export_image("JPEG", filename, height, width, dpi)`로 저장하고 Pillow로 `images/iso.png`로 바꾼다. 그래픽 창이 있는 세션에서만 된다. 실패해도 케이스는 성공으로 둔다. TODO(사내검증): 저장 폴더(Creo 작업 폴더), 뷰 방향 지정(`view_activate`로 저장된 뷰 사용)
- `-g:no_graphics` trail 배치에서는 이미지가 없다. STL을 내보내 플랫폼 3D 뷰로 본다.

## 8. Live Link (열어 둔 Creo와 함께 모델링)
- Creoson은 실행 중인 Creo 세션에 붙는다. `attach=1`이면 **활성 모델**(`file_get_active`)에 작업한다. 모델을 열거나 지우거나 저장하지 않는다.
- TODO(사내검증): 사용자가 일반 바로가기로 띄운 Creo에도 Creoson이 붙는지(J-Link 비동기 연결 조건, `PRO_COMM_MSG_EXE` 환경변수). 안 되면 `nitro_proe_remote.bat`로 Creo를 띄워 달라고 안내한다.
- 진행은 `live-modeling` 스킬 원칙을 따른다. 한두 개 파라미터씩 바꾸고 사용자 화면으로 확인받는다. dds-live MCP에는 아직 Creo 어댑터가 없어 `run_script(tool="creo_python", args=["attach=1", ...])`로 실행한다. TODO
- 반복 GUI 작업은 mapkey로 만들어 두고 `interface_mapkey`로 부르면 사용자도 같은 단축키로 재현할 수 있다.

## 9. 흔한 오류와 대처
| 증상 | 원인/대처 |
|---|---|
| `ConnectionError` (localhost:9056) | CreosonServer 미실행. CreosonSetup으로 서버를 시작한다 |
| `is_creo_running` False | Creo가 Creoson 연결 가능한 방식으로 시작되지 않음. `start_creo=<bat>` 사용 |
| 파라미터를 바꿨는데 형상 그대로 | relations·Family Table이 덮어씀. 치수가 아니라 원천 파라미터를 바꾼다 |
| 다음 케이스가 이전 형상 사용 | 세션에 같은 이름 모델이 남음. `file_erase` 확인 |
| trail이 멈춤(타임아웃) | 녹화와 다른 대화상자(덮어쓰기 확인, 재생성 실패 Resolve). `std.out`, 새 trail 확인 |
| 파일이 `.prt.5`처럼 쌓임 | Creo 버전 저장 방식. 정리는 `purge` 유틸리티 또는 config `file_open_default_folder` 설정 |
| xtop.exe가 남음 | 사용자 동의 후 작업 관리자에서 정리 |
- **모르는 API는 지어내지 않는다.** Creoson 함수는 `creopyson` 문서(함수 이름 = Creoson 명령 `<command>_<function>`)와 CreosonServer의 JSON 도움말(`creoson_help`)에서 확인한다. trail·mapkey는 Creo에서 직접 녹화한 것만 쓴다.

상세: [references/creo-notes.md](references/creo-notes.md)
