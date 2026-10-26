# Adams 데이터셋(.adm)과 ACF 요약

## 데이터셋(.adm) 문법
- 1번 줄은 제목이다(무시된다).
- statement 형식: `NAME/id, ARG=값, ARG=값`. 이어지는 줄은 `,`로 시작한다. `!` 뒤는 주석이다.
- 값이 여러 개인 인자는 콤마로 나열한다: `QP=x, y, z`, `IP=ixx, iyy, izz`.
- 마지막은 `END`다.
- `FUNCTION=` 식은 statement의 마지막 인자로 둔다(식 안의 콤마 때문).

## 자주 바꾸는 statement
| statement | 주요 인자 | 비고 |
|---|---|---|
| `UNITS/` | FORCE, MASS, LENGTH, TIME | 단위계 확인용 |
| `PART/id` | MASS, CM, IP, QG, REULER, VX/VY/VZ | `GROUND`는 id 1이 일반적 |
| `MARKER/id` | PART, QP, REULER | 하드포인트 좌표 |
| `JOINT/id` | REVOLUTE, TRANSLATIONAL, SPHERICAL, FIXED …, I, J | |
| `MOTION/id` | JOINT, ROTATION/TRANSLATION, FUNCTION | 구동 |
| `SFORCE/id` | TRANSLATION/ROTATION, I, J, ACTIONONLY, FUNCTION | 단일 힘 |
| `SPRINGDAMPER/id` | TRANSLATION/ROTATION, I, J, K, C, LENGTH, FORCE, KT, CT, ANGLE, TORQUE | |
| `BUSHING/id` | I, J, K, C, KT, CT | 벡터 인자 |
| `VARIABLE/id` | FUNCTION | 상태변수 |
| `REQUEST/id` | DISPLACEMENT/VELOCITY/ACCELERATION/FORCE, I, J, RM 또는 F1~F8=식 | 결과 출력 |
| `ACCGRAV/` | IGRAV, JGRAV, KGRAV | 중력 |
| `RESULTS/` | XRF | XML 결과(.res) |

자주 쓰는 함수: `TIME`, `STEP(TIME,t0,h0,t1,h1)`, `DX/DY/DZ(i,j,k)`, `DM(i,j)`, `VR(i,j)`, `FX/FY/FZ/FM(i,j)`, `AX/AY/AZ(i,j)`, `IMPACT(...)`, `AKISPL(...)`.

## ACF (Adams Command File)
```
case.adm            <- 1번 줄: 데이터셋 이름
case                <- 2번 줄: 출력 파일 접두어 (case.res, case.msg, case.req ...)
INTEGRATOR/GSTIFF, ERROR=1.0E-3
SIMULATE/STATIC
SIMULATE/DYNAMIC, END=2.0, STEPS=400
STOP
```
- `SIMULATE/DYNAMIC|KINEMATIC|STATIC`, `END=`(절대 시간) 또는 `DURATION=`, `STEPS=` 또는 `DTOUT=`.
- `DEACTIVATE/MOTION, ID=1` 같은 명령으로 구간별 조건을 바꿀 수 있다. TODO(사내검증): ACF에서 statement 값을 바꾸는 명령(예: `SFORCE/1, FUNCTION=...`)이 지원되는 statement 목록.

## 출력 파일
| 파일 | 내용 |
|---|---|
| `.msg` | 메시지(경고, 오류, 진행) |
| `.res` | 결과(`RESULTS/XRF`이면 XML) |
| `.req` | 리퀘스트 결과(ASCII). 형식이 복잡해 템플릿은 쓰지 않는다 |
| `.gra` | 그래픽(애니메이션) |
| `.out` | 표 형식 출력 |

## TODO(사내검증)
- XRF의 StepMap/Data/Step 구조와 Component id 규칙(adams_extract.py가 가정).
- .msg 오류 블록 문구와 정상 종료 문구.
- 솔버 명령행: `mdi ru-standard i case.acf exit` vs `mdi -c ru-standard i case.acf exit`.
