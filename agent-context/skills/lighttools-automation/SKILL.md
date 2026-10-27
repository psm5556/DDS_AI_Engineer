---
name: lighttools-automation
description: LightTools(Synopsys) 조명·광학 해석 자동화 — COM API(LTAPI4, pywin32)로 실행 중인 LightTools에 붙거나 새로 띄워 모델(.lts)을 열고, 데이터베이스 키(DbSet/DbGet)로 광원·렌즈·반사판의 위치·치수·광학 속성을 바꾼 뒤 광선 추적(시뮬레이션)을 돌려 수신기(Receiver)의 조도·광도·효율·균일도를 results.json으로, 조도 분포를 이미지와 fields/*.npz로 남긴다. 사용자가 LightTools 모델로 배광·조도 해석, 광학 설계 변수 DOE·최적화, 수신기 결과 추출, 열어 둔 LightTools에서 함께 작업하자고 할 때 사용.
---

# LightTools 자동화 (COM API · LTAPI4)

## 1. 언제 쓰나 / 전제
- 조명·광학 설계(헤드램프·백라이트·도광판·센서 광학 등)에서 **설계변수 → 광선 추적 → 수신기 지표**를 반복할 때 쓴다. PIDO 흐름의 **해석 단계**다.
- 전제
  - LightTools(Synopsys, 예전 ORA)가 설치되고 라이선스가 활성이어야 한다. COM 서버 `LightTools.LTAPI4`는 설치 시 등록된다. TODO(사내검증): 사내 버전의 ProgID(LTAPI4/LTAPI5)
  - COM은 **로그인한 대화형 데스크톱 세션**에서만 안정적이다. 서비스나 로그오프 상태 스케줄러에서는 실패한다.
  - 플랫폼 파이썬에 `pywin32`, `numpy`, `matplotlib`가 있어야 한다(PC Agent 기본 포함).
  - LightTools API는 **실행 중인 LightTools 하나**에 붙는다. 케이스를 병렬로 돌리지 않는다(스터디 병렬도 1).

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `lighttools_com` | `{python} {script} {args}` | pywin32로 LightTools COM 제어. 실행 중 세션 attach 가능 |

| 템플릿 | 용도 |
|---|---|
| `scripts/lt_case.py` | 케이스 실행. params.json → DbSet → 광선 추적 → 출력값·수신기 메시 → results.json, images/, fields/. **먼저 `inspect=1`**: 광원·수신기·부품 목록과 데이터 키를 `model_info.json`에, 채워 넣을 `lt_map.json` 초안을 쓴다. `probe_mesh=<키>`: 수신기 메시 크기·값 일부를 `mesh_probe.json`에(호출 형태 확인용) |

인자는 `key=value`다: `model=`, `map=`(기본 `lt_map.json`), `inspect`, `probe_mesh`, `attach`, `sim`, `strict`, `image`, `fields`, `save_as`, `open_cmd`, `sim_cmd`, `save_cmd`.
모델 파일은 `model=` → `case.json`의 `inputs` → 케이스 폴더의 `*.lts` 1개 순으로 찾는다.

```
run_script(tool="lighttools_com", script=<lt_case.py 내용>, filename="lt_case.py", args=["model=headlamp.lts", "inspect=1"])
```
스터디 어댑터:
```json
{"tool": "lighttools_com", "script": "scripts/lt_case.py",
 "args": ["model=headlamp.lts", "map=lt_map.json", "image=1", "fields=1"],
 "inputs": ["models/headlamp.lts", "models/lt_map.json"],
 "outputs": ["efficiency", "illum_max", "illum_uniformity"]}
```

## 3. 파라미터 변경 — `lt_map.json`
LightTools 데이터는 **데이터 키(문자열 경로) + 속성 이름**으로 읽고 쓴다(`DbGet(key, prop)` / `DbSet(key, prop, value)`).
설계변수 이름과 키를 잇는 표를 `lt_map.json`에 둔다. `lt_case.py inspect=1`이 초안을 만들고, 사용자와 함께 확정한다.
```json
{
  "params": {
    "lens_x":   {"key": "LENS_MANAGER[1].COMPONENTS[Components].SOLID[Lens_1]", "prop": "X_Position", "scale": 1.0},
    "reflector_tilt": {"key": "LENS_MANAGER[1].COMPONENTS[Components].SOLID[Reflector]", "prop": "Alpha", "scale": 1.0}
  },
  "outputs": {
    "flux_on_target": {"key": "LENS_MANAGER[1].ILLUM_MANAGER[Illumination_Manager].RECEIVERS[Receiver_List].SURFACE_RECEIVER[Target].FORWARD_SIM_FUNCTION[Forward_Simulation]", "prop": "TotalPower"}
  },
  "meshes": {
    "illum": {"key": "LENS_MANAGER[1].ILLUM_MANAGER[Illumination_Manager].RECEIVERS[Receiver_List].SURFACE_RECEIVER[Target].FORWARD_SIM_FUNCTION[Forward_Simulation].ILLUMINANCE_MESH[Illuminance_Mesh]"}
  },
  "source_power": {"key": "", "prop": "Power"}
}
```
- `params`의 값은 params.json 값 × `scale`로 쓴다(단위 변환용). 없는 params 키는 기본(`strict=1`) 실패다.
- 키·속성 이름은 **모델과 버전마다 다르다.** 반드시 `inspect=1` 결과나 LightTools의 데이터 키 복사 기능으로 확인한다. TODO(사내검증): 위 예시의 키 문자열·속성 이름(`X_Position`, `Alpha`, `TotalPower`)
- `source_power`를 주면 `efficiency = 첫 번째 출력 / 광원 출력`을 계산한다.

## 4. 광선 추적과 실패 감지
- 기본 명령: `BeginAllSimulations`(`sim_cmd=`로 바꿀 수 있다). TODO(사내검증): 명령 이름과 동기/비동기 여부 — 비동기라면 완료 대기 방법(상태 속성 폴링)을 확인한다.
- 실패 판정
  - COM 호출이 예외를 내거나, `DbSet`/`Cmd`의 상태 코드가 0(성공)이 아니면 실패다.
  - 출력값이 숫자가 아니거나, 수신기 메시가 모두 0이면 실패다(광선이 수신기에 닿지 않음 — 광선 수 부족·모델 오류).
- 실패 시 results.json을 쓰지 않는다. 시작할 때 이전 results.json을 지운다.

## 5. 결과 · 이미지 · 3D 필드
- `outputs`의 각 값 → results.json.
- `meshes`의 각 수신기 메시 → 아래를 results.json에 넣는다.
  - `<이름>_max`, `<이름>_mean`
  - `<이름>_uniformity`(평균÷최대): 균일도 지표
- `image=1`이면 `images/<이름>.png`(조도 분포 컬러맵)를 남긴다.
- `fields=1`이면 `fields/<이름>.npz`를 남긴다. 수신기 면을 평면 메시(points·triangles·values)로 만든 것이라 Simulator의 3D 보기에서 조도 분포 예측에 쓴다(`docs/skill-contract.md` 1-1절).
- 메시 값 읽기: `X_Dimension`·`Y_Dimension`과 경계(`Min_X_Bound` 등), 셀 값 `CellValue(i, j)`. TODO(사내검증): pywin32에서 인덱스 인자를 넘기는 `DbGet` 호출 형태와 반환 튜플 구조 — `lt_case.py probe_mesh=<키>`로 먼저 확인한다.

## 6. Live Link (열려 있는 LightTools와 함께)
- `attach=1`이면 사용자가 열어 둔 LightTools에 붙어, 지금 열린 모델에서 바로 값을 바꾸고 추적한다. 모델을 열거나 닫거나 저장하지 않는다.
- 사용자가 화면에서 결과를 보며 "렌즈를 1 mm 더 앞으로" 같은 요청을 이어 갈 수 있다.

## 7. DOE·최적화로 쓰기
1. `lt_case.py inspect=1`로 키를 확인하고 `lt_map.json`을 확정한다.
2. 기준 모델로 `lt_case.py`를 한 번 돌려 출력값이 그럴듯한지 사용자와 확인한다(광선 수에 따른 노이즈 포함).
3. DOE → 예측 모델 → 최적화. 광선 추적 결과는 **잡음이 있다**. 같은 설계를 두 번 돌려 차이를 보고, 예측 모델 잡음 수준을 정한다.
4. 균일도처럼 메시에서 나오는 지표는 수신기 해상도에 민감하다. 해상도를 바꾸지 않는다.

## 8. 자주 하는 실수
- 모델 파일을 원본 폴더에서 바로 열고 저장한다 → 케이스 폴더 복사본에서 열고, 저장은 `save_as=1`로 케이스 폴더에만 한다.
- 광선 수가 너무 적어 결과가 들쭉날쭉하다 → 기준 케이스에서 광선 수를 늘려 수렴을 확인한다(광선 수도 `lt_map.json`의 params로 둘 수 있다).
- 단위: LightTools 모델 단위(mm 등)를 확인하고 `scale`로 맞춘다.

## 9. 사내 검증이 필요한 것 (TODO(사내검증))
- COM ProgID(`LightTools.LTAPI4`)와 연결 방법(실행 중 세션 attach, 새로 띄우기 `lt.exe` 경로)
- 모델 열기·저장·추적 명령 문자열(`open_cmd`·`save_cmd`·`sim_cmd`)과 동기 여부
- 데이터 키 문자열 형식, 속성 이름, `DbList`/`ListNext`로 목록 읽는 방법
- 수신기 메시 셀 값 읽기(인덱스 인자) 호출 형태
- 대화상자(라이선스·저장 확인)가 뜨면 COM 호출이 멈추는지
