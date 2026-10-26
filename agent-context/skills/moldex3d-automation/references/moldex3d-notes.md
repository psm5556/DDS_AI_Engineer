# Moldex3D 자동화 메모 (확인 수준 표시)

이 문서는 템플릿 작성 시 확인한 내용과 확인하지 못한 내용을 구분한다. **확인되지 않은 API 이름이나 명령행 옵션을 스크립트에 넣지 않는다.**

## 확인 수준
| 항목 | 내용 | 수준 |
|---|---|---|
| GUI | Moldex3D Studio(최근 버전의 통합 전·후처리 GUI) | 확인 |
| 해석 단계 | 충전(Filling), 보압(Packing), 냉각(Cooling), 휨(Warpage). 해석 순서를 런(Run)마다 지정 | 확인 |
| 내장 DOE | Moldex3D Expert(DOE/최적화 모듈). 라이선스가 있으면 공정조건 DOE는 이 모듈이 가장 간단 | 확인(라이선스 별도) |
| 구조해석 연계 | FEA Interface 모듈로 휨/잔류응력/섬유배향을 Abaqus, Ansys, Nastran, LS-DYNA 등에 내보냄 | 확인(라이선스 별도) |
| 작업 큐 | Computing Manager로 해석 작업을 로컬/원격에 제출 | 확인(명령행 사용법은 미확인) |
| Python/API 자동화 | Moldex3D API(Python 스크립팅)가 제공됨 | TODO(사내검증): 모듈 이름, 지원 버전, 라이선스, 객체 모델 |
| 배치 명령행 | 프로젝트/런을 GUI 없이 해석하는 실행파일과 인자 | TODO(사내검증): 실행파일 이름과 인자 |
| 프로젝트 파일 | 프로젝트·런·결과 파일 확장자 | TODO(사내검증): 사용 버전에서 확인 |
| 결과 내보내기 | Studio 결과 화면에서 노드 값/요약표를 텍스트(CSV 등)로 내보내기 | TODO(사내검증): 메뉴, 열 이름, 단위 표기 |

## 확인 절차 (처음 한 번)
1. `moldex3d_python`(agent.toml `[tools]`)에 Moldex3D가 제공하는 Python 실행파일을 지정한다. 별도 Python이 없으면 설치 폴더에서 `python.exe`를 찾는다.
2. `run_script(tool="moldex3d", script="scripts/m3d_probe_api.py")`로 `api_probe.txt`를 받는다. 모듈 이름, 공개 함수/클래스, 설치 폴더의 실행파일 목록이 나온다.
3. Moldex3D 설치 폴더의 API 문서/예제(설치본 Help 또는 CoreTech 고객 포털)에서 다음 호출을 확인한다.
   - 프로젝트/런 열기·저장, 해석조건(용융 온도, 금형 온도, 충전 시간, V/P 전환, 보압 프로파일, 냉각 시간) 변경
   - 해석 제출과 완료 대기, 결과 값/노드 데이터 내보내기
4. 확인한 호출만 `m3d_case_run.py`의 `apply_params_api()`에 채운다. 명령행 해석이 확인되면 `--` 뒤 배치 명령으로 넘긴다.
5. GUI에서 결과 요약표와 휨 노드 값을 한 번 내보내고, `m3d_extract.py`의 `KEY_PATTERNS`와 `--value-col`을 맞춘다.

## 텍스트 템플릿 방식
- 해석조건이 사람이 읽을 수 있는 텍스트 파일(사내 확인 필요)로 저장/불러오기가 되면, 그 파일에 `{{melt_temp}}` 같은 자리표시자를 넣어 `--template src.tpl=dst`로 렌더한다. 남은 자리표시자가 있으면 실패로 처리한다.
- 바이너리 프로젝트 파일은 직접 편집하지 않는다.

## 결과 지표(성형 해석 공통)
| 지표 | 키 예시 | 비고 |
|---|---|---|
| 충전 시간 | `fill_time_s` | 미충전이면 의미 없음. 충전율 키를 함께 둔다 |
| 최대 스프루/사출 압력 | `max_sprue_pressure_mpa` | V/P 전환 시점 압력과 구분 |
| 최대 형체력 | `clamp_force_max_tonne` | 사출기 용량 대비 판단 |
| 사이클 시간 | `cycle_time_s` | 냉각 해석 필요 |
| 휨 총 변위 최대 | `max_total_displacement_mm` 또는 노드 CSV의 `warpage_max` | 휨 해석 필요 |
| 체적 수축률 최대 | `max_vol_shrinkage_pct` | 싱크마크 지표 |
| 웰드라인/에어트랩 | 개수나 위치 | 내보내기 형식 확인 후 추가 |
