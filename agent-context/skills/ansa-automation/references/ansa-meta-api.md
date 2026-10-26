# ANSA / META API 요약

표기: **확인** = 공식 스크립팅 문서에 있는 이름으로 템플릿이 직접 호출한다(인자 세부는 버전별로 다를 수 있다). **TODO(사내검증)** = 이름이나 동작을 설치 버전 도움말에서 확인한 뒤에만 쓴다.

## 1. 배치 실행
```
ansa64.bat -nogui -exec "load_script: 'ansa_batch_deck.py'" -exec "main()"
meta_post64.bat -b -s meta_contour.ses
```
- `-exec`는 순서대로 실행된다. 첫 번째로 스크립트를 읽고 두 번째로 `main()`을 부른다.
- 스크립트에 인자를 넘기지 않는다. 입력은 cwd의 `params.json`, `case.json`, `ansa_config.json`이다. 플랫폼은 `DDS_CASE_DIR` 환경변수도 넘긴다.
- 종료: `ansa.session.Quit()` TODO(사내검증). 종료코드 전달 TODO(사내검증).

## 2. 모듈
```python
import ansa
from ansa import base, constants        # 확인
from ansa import session                # 종료 등 TODO(사내검증)
from ansa import batchmesh              # Batch Mesh TODO(사내검증)
from ansa import morph                  # 모핑 TODO(사내검증)
```

## 3. base (확인)
| 호출 | 용도 |
|---|---|
| `base.Open(path)` | .ansa DB 열기 |
| `base.InputNastran(filename=path)` / `base.InputAbaqus(...)` / `base.InputLSDyna(...)` | 솔버 덱 import. 추가 키워드는 버전별 |
| `base.OutputNastran(filename=out)` / `base.OutputAbaqus(...)` / `base.OutputLSDyna(...)` | 솔버 덱 export. 추가 키워드(include, 형식)는 버전별 |
| `base.SaveAs(path)` | DB 저장 |
| `base.SetCurrentDeck(constants.NASTRAN)` | 현재 덱 |
| `base.GetEntity(deck, "PSHELL", pid)` | ID로 엔티티 1개(없으면 None) |
| `base.CollectEntities(deck, None, "PSHELL")` | 타입으로 전체 수집 |
| `base.GetEntityCardValues(deck, ent, ("T", "MID1"))` | 필드 라벨 → 값 dict |
| `base.SetEntityCardValues(deck, ent, {"T": 2.5})` | 필드 값 쓰기. 템플릿은 다시 읽어 검증한다 |

엔티티 타입 이름과 필드 라벨은 덱마다 다르다. Nastran은 카드 이름(`PSHELL`, `PSOLID`, `MAT1`, `FORCE`)과 필드 이름(`T`, `E`, `NU`, `RHO`)이 대체로 같다. LS-DYNA/Abaqus는 GUI 카드에서 라벨을 확인한다. TODO(사내검증)

## 4. 모핑 / 메쉬 / 품질 (TODO(사내검증))
- 모핑: GUI에서 만든 모핑 파라미터 또는 Optimization Task Design Variable을 배치에서 값만 바꿔 적용한다. 함수 이름은 `ansa.morph` 도움말에서 확인한다.
- Batch Mesh: DB에 저장된 세션을 실행한다. 템플릿은 `batchmesh.RunAllMeshingScenarios`가 있을 때만 호출한다.
- 품질: off element 개수 조회 API를 확인해 기준 초과 시 실패 처리한다.
- 질량: 질량 계산 API 확인. 대안은 솔버의 `PARAM,GRDPNT,0` 질량(Nastran 스킬)이다.

## 5. META 세션 (TODO(사내검증))
템플릿 `scripts/meta_contour.ses`의 줄 형식:
```
read geom Nastran case.op2
read dis Nastran case.op2 1 Displacements
read onlyfun Nastran case.op2 1 Stresses,VonMises,MaxofTopBottom
view default isometric
options fringebar on
write png images/vm.png
exit
```
- 정확한 문법은 META GUI의 세션 기록 결과로 맞춘다. 결과 이름(`Stresses,VonMises,...`)은 META가 OP2에서 보여 주는 이름을 그대로 쓴다.
- 범례 범위 고정, 변형 배율, 곡선(CSV) export 명령도 기록으로 얻는다.
- META Python 스크립팅으로 같은 작업을 할 수도 있다. 배치 실행 방법(`-b -s script.py` 가능 여부)은 TODO(사내검증)이며, 가능하면 별도 tool 키 대신 같은 `meta` 키를 쓴다.
