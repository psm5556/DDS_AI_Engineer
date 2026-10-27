---
name: physicsai-automation
description: Altair PhysicsAI(기하 딥러닝 기반 물리 예측) 연동 — DDS 스터디의 케이스(설계변수·결과값·3D 필드 fields/*.npz)를 PhysicsAI가 읽을 수 있는 메쉬+결과 데이터셋(VTK 파일과 목록 CSV, 학습/검증 나눔)으로 내보내고, HyperWorks 파이썬에서 PhysicsAI API를 확인·학습·예측하는 흐름을 안내한다. 사용자가 PhysicsAI로 형상→응력·변위 분포 예측 모델을 만들거나, DDS 예측 모델(GP·형상·필드)과 PhysicsAI 결과를 비교하자고 할 때 사용.
---

# Altair PhysicsAI 연동

## 1. 언제 쓰나 / 전제
- 형상이 크게 바뀌는(설계변수로 다 표현되지 않는) 경우, **메쉬 자체를 입력으로 받는 딥러닝 예측**이 필요할 때 쓴다.
- 설계변수로 형상이 정해지면 DDS 자체 예측 모델(GP·형상·필드, Simulator 3D)로 충분하다. 두 방식을 같은 데이터로 비교해 고른다.
- 전제
  - HyperWorks 2023.1 이상(PhysicsAI 포함)이 설치되고 라이선스가 있다. PC Agent가 `hw.exe`를 찾는다(HyperMesh와 같은 경로 키 `altair_hw`).
  - 학습 데이터는 DDS 스터디의 케이스 폴더에 있다. 각 케이스에는 `fields/<필드>.npz`(points·triangles·values)와 `case.json`(params·results)이 있다.
  - TODO(사내검증): PhysicsAI가 직접 읽는 파일 형식(h3d·op2·vtk 등). 이 스킬은 범용 VTK(legacy)로 내보낸다. 안 읽히면 HyperMesh/HyperView로 h3d로 바꾸는 단계를 넣는다.

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `physicsai_export` | `{python} {script} {args}` | `scripts/physicsai_dataset.py` — DDS 케이스 → PhysicsAI 데이터셋 |
| `physicsai_hw` | `{altair_hw} -b -f {script}` | HyperWorks 파이썬에서 PhysicsAI API 실행(학습·예측). TODO(사내검증) |

`physicsai_dataset.py` 인자:
- `--study <스터디 폴더>`: 예: `../../studies/S003_두께-리브-DOE`. 내 PC 파일 탭에서 경로를 확인한다.
- `--field stress`
- `--out physicsai_dataset`
- `--split 0.8`
- `--probe`: 지금 파이썬에서 PhysicsAI 관련 모듈을 찾아 `physicsai_probe.json`에 쓴다. HyperWorks 파이썬에서 실행한다.
```
run_script(tool="physicsai_export", script=<physicsai_dataset.py>, filename="physicsai_dataset.py",
           args=["--study", "../../studies/S003_bracket", "--field", "stress"])
```

## 3. 데이터셋 구조 (`physicsai_dataset/`)
- `cases/C0001.vtk` …: 케이스별 표면 메쉬와 `<필드>` 점 데이터(VTK legacy ASCII POLYDATA)
- `dataset.csv`: case_id, file, split(train/test), 설계변수…, 결과값…
- `README.txt`: 필드 이름·단위·케이스 수·만든 시각

## 4. 학습·예측 (PhysicsAI)
1. PhysicsAI(HyperWorks)에서 `dataset.csv`의 train 케이스로 학습하고, test 케이스로 정확도를 본다(필드 예측 오차, 최대값 오차).
2. 같은 test 케이스로 DDS 형상·필드 모델 정확도(학습 결과 카드의 R²)와 비교해 결론 카드에 남긴다(`dds-flow.publish_artifact`, markdown).
3. 예측을 자동화하려면 `--probe` 결과로 PhysicsAI 파이썬 API 이름을 확인한 뒤 `physicsai_hw` 스크립트를 작성한다. TODO(사내검증)

## 5. 실패 감지
- 스터디 폴더에 `fields/<필드>.npz`가 있는 완료 케이스가 2개 미만이면 실패.
- 케이스마다 점·삼각형 수가 달라도 된다(PhysicsAI는 메쉬별 입력). 다만 npz에 triangles가 없으면 그 케이스는 건너뛰고 기록한다.
