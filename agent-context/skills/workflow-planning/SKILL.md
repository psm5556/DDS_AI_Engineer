---
name: workflow-planning
description: 해석·긴 계산을 새로 돌리거나 사용자 모델을 바꾸는 요청을 작업 흐름(Flow) 그래프로 설계하는 방법. dds-flow.propose_flow를 호출하기 전에 사용. 조회·후처리·기존 데이터로 학습·Simulator 배포·보고서에는 Flow를 쓰지 않는다.
---

# 작업 흐름(Flow) 설계

Flow 승인은 **해석·긴 계산을 새로 돌리거나 사용자 모델을 바꾸는 일**, 다른 멤버가 만든 스크립트를 돌리는 일에만 받습니다. 조회·후처리·기존 데이터로 학습·Simulator 배포·보고서는 승인 없이 바로 합니다(이미 가진 데이터는 `existing-data-to-simulator` 스킬).

## 1. 요청을 단계로 쪼개기
| 노드 type | 의미 | 예시 label | tool |
|---|---|---|---|
| `cad` | 형상 파라미터 변경, 형상 내보내기 | NX 리브 높이·두께 변경 → STEP | nx |
| `pre` | 메쉬, 재료, 하중·경계조건 설정 | HyperMesh 메쉬 + 하중 설정 | altair / ansys / simcenter |
| `solve` | 해석 실행 | Abaqus 정적 해석 | abaqus / lsdyna / starccm ... |
| `post` | 결과 추출, 이미지 | 최대 Mises·변위 추출, 컨투어 캡처 | abaqus_python / lsprepost ... |
| `doe` | 실험계획 | LHS 30점 | — |
| `train` | Surrogate 학습 | GP 학습 (R² 확인) | — |
| `optimize` | 베이지안 최적화 / 능동학습 | BO 15회 (질량 최소, 응력 ≤ 250MPa) | — |
| `deploy` | 예측 Simulator 배포 | Simulator 배포 | — |
| `task` | 그 밖의 작업 | 결과 보고서 작성 | — |

- 한 케이스 안에서 반복되는 `cad → pre → solve → post`는 **하나의 script 어댑터 케이스**로 묶여 DOE마다 반복됩니다.
  - 그래프에서는 단계별 노드로 보여 줍니다.
  - detail에 "케이스마다 반복"이라고 적습니다.
- 첫 노드 앞에 **스크립트 시험 실행** 노드(`task`, "1회 시험 실행")를 두면 사용자가 안심합니다.
- 모델 구조를 읽는 inspect 스크립트는 제안 전에 돌려도 됩니다. 알아낸 부품·면 이름과 범위를 노드 detail에 적습니다.
- 지난 흐름을 신규 모델로 다시 하면 `list_flows`로 찾아 같은 단계로 제안합니다. 옛 모델과 달라진 점을 detail에 적고, `clone_study`로 새 스터디를 만듭니다. 옛 결과는 건드리지 않습니다.
- 다른 멤버가 만든 스크립트를 돌리면 detail에 "○○님이 만든 스크립트"라고 적습니다(`restore_recipe` 결과의 `created_by_name`).

## 2. 예시
```json
{
  "title": "브래킷 경량화 (NX → Abaqus)",
  "goal": "응력 250MPa 이하에서 질량 최소화",
  "nodes": [
    {"id": "inspect", "label": "모델 구조 파악", "type": "task", "tool": "nx"},
    {"id": "cad", "label": "NX 두께·리브 변경", "type": "cad", "tool": "nx", "detail": "케이스마다 반복"},
    {"id": "solve", "label": "Abaqus 정적 해석", "type": "solve", "tool": "abaqus", "detail": "케이스마다 반복"},
    {"id": "post", "label": "Mises·질량 추출", "type": "post", "tool": "abaqus_python", "detail": "케이스마다 반복"},
    {"id": "trial", "label": "1회 시험 실행", "type": "task"},
    {"id": "doe", "label": "LHS 24점", "type": "doe"},
    {"id": "train", "label": "GP 학습", "type": "train"},
    {"id": "opt", "label": "BO 12회", "type": "optimize"},
    {"id": "deploy", "label": "Simulator 배포", "type": "deploy"}
  ],
  "edges": [
    {"source": "inspect", "target": "cad"}, {"source": "cad", "target": "solve", "label": "STEP"},
    {"source": "solve", "target": "post", "label": "ODB"}, {"source": "post", "target": "trial"},
    {"source": "trial", "target": "doe"}, {"source": "doe", "target": "train"},
    {"source": "train", "target": "opt"}, {"source": "opt", "target": "deploy"}
  ]
}
```

## 3. 진행 규칙
- 제안하면 상태가 `review`(검토)가 됩니다. 사용자가 승인하면 "승인되었습니다" 메시지가 옵니다. 그때 `get_flow`로 상태가 `approved`인지 확인하고 시작합니다.
- 계획이 바뀌면 새 Flow를 제안합니다(같은 제목에 "v2"를 붙임).
- 노드 detail에는 사용자가 볼 핵심 숫자를 짧게 적습니다(예: "완료 24/24", "R²=0.94", "최적 질량 1.82kg").
