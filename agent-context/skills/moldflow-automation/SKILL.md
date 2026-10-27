---
name: moldflow-automation
description: Autodesk Moldflow Insight/Synergy 사출 성형 해석 배치 자동화. Synergy COM API(pywin32)로 공정조건(용융/금형 온도, 사출 시간, 보압·보압 시간, 냉각 시간)과 물성 필드를 바꾸고, runstudy.exe로 Fill+Pack+Cool+Warp 해석을 돌리고, studyrlt.exe 로그 내보내기와 결과 플롯에서 충전 시간, V/P 전환 압력, 형체력, 휨 변형량, 웰드라인, 에어트랩, 체적 수축률을 뽑아 results.json과 images/, fields/로 남긴다. 사용자가 사출, 성형 해석, Moldflow, 게이트 위치, 휨(warpage), 웰드라인, 공정조건 DOE/최적화를 요청할 때 사용한다.
---

# Moldflow 자동화 스킬

모든 스크립트는 `docs/skill-contract.md` 규약을 따른다. 케이스 폴더(cwd)에서 `params.json`을 읽고 `results.json`(숫자, 소문자_스네이크 키), `images/*.png`, 선택적으로 `fields/<이름>.npz`를 쓴다. 실패하면 `results.json`을 쓰지 않고 0이 아닌 코드로 종료한다.

## 1. 언제 쓰나 / 전제
- 공정조건, 게이트 후보, 두께 변형 스터디를 바꿔 가며 충전·보압·냉각·휨 결과를 비교할 때(DOE, 최적화, 민감도).
- 해석이 끝난 스터디에서 값, 그림, 휨 필드만 뽑을 때.
- 구성 요소는 세 가지다.

| 구성 | 역할 | 비고 |
|---|---|---|
| Synergy COM (`synergy.Synergy`) | 프로젝트/스터디 열기, 속성 편집, 결과 플롯 값·이미지 | Synergy GUI 라이선스. 플랫폼 venv(Python 3.12 + pywin32)에서 호출 |
| `runstudy.exe` | 스터디 배치 해석 | 해석 라이선스 |
| `studyrlt.exe` | 해석 로그(.out) 텍스트 내보내기, 결과 XML | `.out`은 바로 읽는 텍스트가 아니다 |

- **ID를 추측하지 않는다.** 속성 type/ID, 필드 ID, 플롯 이름은 Synergy 매크로 녹화(.vbs)에서 옮긴다. 상세: [references/synergy-api.md](references/synergy-api.md), [references/batch-cli.md](references/batch-cli.md).
- 스크립트 콘솔 출력은 영문 ASCII로 쓴다. 단위계는 COM에서 `SetUnits("Metric")`로 고정한다.

## 2. 실행 방법 (tool 키)
| 작업 | tool 키 | 명령 |
|---|---|---|
| COM 수정 + 배치 해석 + 로그 내보내기 | `moldflow_synergy` | `python scripts/mf_case_run.py ...` (플랫폼 python 3.12 + pywin32) |
| 배치 해석만 | `moldflow_run` | `runstudy.exe cover.sdy` |
| 로그/결과 내보내기만 | `moldflow_result` | `studyrlt.exe cover.sdy -exportoutput` |
| 로그 파싱 → results.json | `python` | `python scripts/mf_parse_output.py` |
| 플롯 값·이미지·휨 필드 | `moldflow_synergy` | `python scripts/mf_synergy_post.py ...` |

경로 키는 `moldflow_runstudy`, `moldflow_studyrlt`이다(PC Agent가 자동으로 찾는다). Python 스크립트는 환경변수 `DDS_TOOL_MOLDFLOW_RUNSTUDY`, `DDS_TOOL_MOLDFLOW_STUDYRLT`로 경로를 받는다.

**A. 공정조건 DOE (프로젝트 폴더 통째 복사, COM 수정)**
```json
{"tool": "moldflow_synergy", "script": "scripts/mf_case_run.py",
 "args": ["--project", "cover/cover.mpi", "--study-name", "cover_fpcw", "--sdy", "cover/cover_fpcw.sdy"],
 "inputs": ["models/cover"],
 "post": [
   {"tool": "python", "script": "scripts/mf_parse_output.py", "args": ["--require", "fill_time_s,max_injection_pressure_mpa"]},
   {"tool": "moldflow_synergy", "script": "scripts/mf_synergy_post.py",
    "args": ["--project", "cover/cover.mpi", "--study-name", "cover_fpcw",
             "--field-plot", "Deflection, all effects:Deflection", "--field-name", "warpage"]}],
 "outputs": ["fill_time_s", "max_injection_pressure_mpa", "clamp_force_max_tonne", "warp_deflection_max_mm"],
 "timeout_sec": 14400}
```
**B. 미리 만든 스터디 선택(게이트 후보, 형상 변형) — COM 없이**
```json
{"tool": "moldflow_synergy", "script": "scripts/mf_case_run.py",
 "args": ["--sdy-pattern", "cover_g{gate_id}.sdy", "--no-com"],
 "inputs": ["models/cover_g1.sdy", "models/cover_g2.sdy", "models/cover_g3.sdy"],
 "post": {"tool": "python", "script": "scripts/mf_parse_output.py", "args": []},
 "outputs": ["fill_time_s", "max_injection_pressure_mpa"]}
```
**C. 고정 스터디 한 번(배치 키만 사용)**
```json
{"tool": "moldflow_run", "args": ["cover_fpcw.sdy"], "inputs": ["models/cover_fpcw.sdy"],
 "post": [{"tool": "moldflow_result", "args": ["cover_fpcw.sdy", "-exportoutput"]},
          {"tool": "python", "script": "scripts/mf_parse_output.py", "args": []}]}
```
- `args` 값은 템플릿 치환되지 않는다. 경로는 케이스 폴더 기준 상대경로로 적는다.
- TODO(사내검증): `.sdy` 하나만 복사해서 `runstudy`가 도는지(B, C). 안 되면 A처럼 프로젝트 폴더를 통째로 `inputs`에 넣는다.

## 3. 파라미터 변경 (설계변수)
| 설계변수 | 방법 |
|---|---|
| 공정조건·물성 숫자 | `mf_case_run.py`의 `PROP_MAP`에 `{"type", "id", "field", "index", "scale"}`로 매핑 → COM `PropertyEditor` 편집 |
| 게이트 위치 | 후보별 스터디를 미리 만들고 `--sdy-pattern "cover_g{gate_id}.sdy"`로 고른다(정수 변수) |
| 두께, 리브, 형상 | CAD 스킬(NX 등)에서 형상 변경 → 스터디 재임포트·메쉬는 매크로 녹화 호출을 옮긴다. 또는 형상 변형별 스터디를 미리 만들어 B 방식 |
| Midplane/Dual Domain 두께 속성 | 속성 편집(`PROP_MAP`)으로 가능. 필드 ID는 녹화로 확인 |

- PROP_MAP 찾는 순서: (1) Synergy에서 매크로 녹화 시작 → 공정조건 대화상자에서 값 하나 변경 → 녹화 종료, (2) `.vbs`의 `FindProperty(type, id)`와 `FieldValues field, DVec`를 옮긴다, (3) `--dump-prop <type>`으로 같은 type의 모든 필드를 `mf_props_dump.txt`에 찍어 교차 확인한다.
- 매핑되지 않은 숫자 params 키가 있으면 실패로 처리한다(`STRICT_PARAMS`). DOE 결과가 조용히 틀어지는 것을 막는다.
- 변경 후 되읽기 값을 로그에 남긴다. `CommitChanges`를 빠뜨리면 반영되지 않는다.
- 사출 위치(게이트) 노드를 COM으로 직접 옮기는 방법, CAD 재임포트·메쉬 생성 호출은 TODO(사내검증): 녹화 매크로에서 확인한 뒤에만 쓴다.

## 4. 해석 조건 변경
- 공정조건: 용융 온도, 금형 온도, 충전 제어(사출 시간/유량/속도 프로파일), V/P 전환(% 충전 체적), 보압 프로파일(압력 %·시간), 냉각 시간. 모두 공정조건 속성의 필드다. 프로파일은 평탄 배열이므로 `index`로 원소를 지정한다.
- 재료 등급: 재료 DB의 등급 교체는 녹화 매크로 호출을 옮긴다. 등급별 스터디를 미리 만들어 B 방식으로 고르는 것이 가장 견고하다.
- 해석 순서(Fill / Fill+Pack / Cool+Fill+Pack+Warp 등): 스터디에 저장해 둔다. COM 변경 호출은 TODO(사내검증).
- 휨 해석은 냉각(Cool) 결과가 있어야 금형 온도 불균일 효과가 반영된다. 휨까지 볼 때는 Cool+Fill+Pack+Warp를 쓴다.
- 메쉬 종류(Midplane, Dual Domain, 3D)에 따라 결과 플롯과 로그 문구가 다르다. 한 스터디 안에서는 바꾸지 않는다.

## 5. 실행과 완료 판정
- `mf_case_run.py`는 `runstudy` 출력을 `mf_runstudy.log`에 남긴다. 종료코드가 0이 아니면 실패다.
- 이어서 `studyrlt <sdy> -exportoutput`으로 해석 로그 텍스트를 만들고 `mf_screen_output.txt`로 복사한다. 새 `.txt`가 없으면 실패다.
- `mf_parse_output.py`는 `** ERROR` 줄이 있으면 실패로 본다(`--ignore-errors`로 끌 수 있다). `** WARNING`은 무시한다. TODO(사내검증): 오류 줄 형식.
- 미충전(short shot)은 해석이 정상 종료돼도 생긴다. 충전 시간이 비정상적으로 짧거나 로그에 미충전 메시지가 있으면 사내 기준으로 판정 키를 추가한다.

## 6. 결과 추출 → results.json
두 경로가 있으며 함께 쓸 수 있다(post 순서대로 병합).

| 출처 | 스크립트 | 기본 키 |
|---|---|---|
| 해석 로그 텍스트 | `mf_parse_output.py` | `fill_time_s`, `vp_switch_time_s`, `max_injection_pressure_mpa`, `clamp_force_max_tonne`, `total_weight_g`, `shot_volume_cm3`, `cycle_time_s`, `max_deflection_mm` |
| 결과 플롯(COM) | `mf_synergy_post.py` | `fill_time_max_s`, `vp_pressure_max_mpa`, `flow_front_temp_min_c`, `vol_shrinkage_max_pct`, `warp_deflection_max_mm`, `weld_line_nodes`, `air_trap_nodes` |

- 로그 파서는 모든 `라벨 = 값 단위` 줄을 `mf_output_all.json`에 따로 저장한다. 첫 케이스에서 이 파일을 보고 `KEY_PATTERNS` 정규식을 맞춘다. 단위는 MPa, tonne, s, mm, g, cm^3로 환산한다.
- 플롯 통계는 마지막 독립변수(최종 시점) 값의 max/min이다. 플롯 이름은 `--list-plots`(→ `mf_plots.txt`)로 확인해 `PLOT_STATS`를 고친다. 한글 UI면 이름이 한글이다.
- 웰드라인·에어트랩은 위치 결과라 "표시된 엔티티 수"를 지표로 쓴다. 외관 위치 판정이 필요하면 영역 좌표 필터를 추가한다.
- `--require`로 꼭 필요한 키를 지정한다. 없으면 실패로 처리해 잘못된 0이 DOE에 섞이지 않게 한다.

## 7. 이미지 저장
- `mf_synergy_post.py`가 `IMAGE_PLOTS`의 플롯을 `Viewer.ShowPlot` → `Viewer.SaveImage`로 `images/<키>.png`에 저장한다.
- 휨 필드를 저장하면 matplotlib 3D 그림(`images/<name>_3d.png`)도 함께 만든다. COM 이미지가 실패해도 남는 대체 그림이다.
- 이미지 실패는 경고만 남긴다(`--strict`가 아니면 케이스 실패 아님).
- TODO(사내검증): 뷰 방향·배율·범례 범위 고정 API(DOE 비교 그림용), 해상도 지정. 확인 전에는 스터디에 저장된 뷰를 쓴다.

## 8. 3D 필드 (fields/*.npz)
- `--field-plot "Deflection, all effects:Deflection" --field-name warpage` → `fields/warpage.npz`(points: 노드 좌표, values: 휨 변형량, triangles: 삼각형 요소).
- Midplane/Dual Domain 메쉬는 삼각형이 그대로 표면이다. 3D(사면체) 메쉬는 triangles 없이 점 필드만 저장된다(서버는 triangles가 없어도 받는다).
- 같은 메쉬를 쓰는 공정조건 DOE는 노드 순서가 같아 가장 정확하다. 게이트·형상 변경으로 리메쉬되면 서버가 보간한다.
- points는 변형 전 좌표다. TODO(사내검증): 메쉬 순회 함수(`GetFirstNode`, `GetNodeCoord`, `GetFirstTri`, `GetElemNodes`)와 벡터 변형 결과 API. 노드 수가 많으면 COM 왕복으로 수 분 걸린다.
- 학습: `dds-study.train_field_model(study_id, field="warpage")`.

## 9. 흔한 오류와 대처
- **`pywintypes.com_error: Invalid class string`**: Synergy가 COM 등록되지 않았다. Synergy를 관리자 권한으로 한 번 실행하거나 설치를 복구한다. 32/64비트 불일치도 확인한다.
- **COM 호출이 멈춤**: Synergy 대화상자(라이선스, 저장 확인)가 떠 있다. 케이스마다 `Quit`하고, 남은 `synergy.exe` 프로세스를 정리한다. DOE 동시 실행 수는 라이선스 수 이하로 둔다.
- **값이 안 바뀜**: `CommitChanges` 누락, 다른 속성 ID(같은 type의 다른 세트), 단위계 착오. 되읽기 로그와 `--dump-prop` 결과를 비교한다.
- **`runstudy` 즉시 실패**: 경로의 한글/공백, 프로젝트 밖으로 꺼낸 스터디의 참조 누락, 라이선스. `mf_runstudy.log`를 본다.
- **결과 키 누락**: 로그 문구가 다르다. `mf_output_all.json`에서 실제 라벨을 찾아 `KEY_PATTERNS`를 고친다.
- **플롯 없음**: 해석 순서에 해당 단계가 없거나(예: Warp 없음) 이름이 다르다. `--list-plots`로 확인한다.

## 사내 검증 필요 항목 (TODO(사내검증))
1. `OpenItemByName(name, "Study")` 인자, 녹화 매크로의 `SAInstance` 연결 방식, `Quit` 인자.
2. pywin32에서 `FieldValues` 쓰기 호출 형태(`FieldValues(fid, dv)` 또는 `SetFieldValues`).
3. 공정조건/재료/두께 속성의 type·ID·필드 ID(녹화로 확보), 해석 순서·게이트·CAD 재임포트 API.
4. `runstudy` 옵션과 결과 파일 규칙, `studyrlt -exportoutput` 출력 파일 이름, `-xml` 구조.
5. 로그 라벨 문구와 오류 줄 형식(버전/언어별), 플롯 이름.
6. `Viewer.SaveImage` 형식·해상도, 메쉬 순회 함수.

## 파일
- `scripts/mf_case_run.py`: (moldflow_synergy) params → COM 속성 편집·저장 → runstudy → studyrlt -exportoutput → `mf_screen_output.txt`
- `scripts/mf_parse_output.py`: (python) 해석 로그 텍스트 → results.json, `mf_output_all.json`
- `scripts/mf_synergy_post.py`: (moldflow_synergy) 플롯 통계 병합, 플롯 PNG, `fields/<name>.npz`
- `references/synergy-api.md`, `references/batch-cli.md`
