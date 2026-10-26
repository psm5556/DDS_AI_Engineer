# Nastran BDF 형식과 주요 카드

## 1. 덱 구조
```
NASTRAN ...            $ (선택) NASTRAN 문
SOL 101                $ 실행 제어 (Executive Control)
CEND
TITLE = plate          $ Case Control
SUBCASE 1
  SPC = 1
  LOAD = 10
  DISPLACEMENT = ALL
  STRESS(VONMISES) = ALL
  SPCFORCES = ALL
BEGIN BULK             $ Bulk Data
PARAM,POST,-1          $ OP2 출력
PARAM,GRDPNT,0         $ 질량(GPWG) 출력
...
INCLUDE 'mesh.bdf'
ENDDATA
```
- 편집기의 카드 규칙은 `BEGIN BULK`~`ENDDATA` 사이에만 적용된다. `BEGIN BULK`가 없는 파일(include)은 전체가 Bulk다.
- regex 규칙의 `section`: `control`(BEGIN BULK 앞, 기본), `bulk`, `all`.

## 2. 필드 형식
| 형식 | 1행 | 연속행 | 데이터/줄 |
|---|---|---|---|
| small | 1~8열 카드명, 9~72열 8칸×8, 73~80열 연속 표시 | 1~8열 연속 표시(`+xx`) 또는 공백 | 8 |
| large | `NAME*` + 16칸×4 + 73~80열 연속 표시 | `*xx` + 16칸×4 | 4 |
| free | `NAME,f2,f3,...` | `,`/`+`로 시작 | 8 (large free `NAME*,`는 4) |
- 한 카드 안에서 줄마다 형식이 달라도 된다. 편집기는 줄 단위로 판단한다.
- 실수 표기: `2.`, `2.5`, `7.85E-9`, `7.85-9`(암시 지수), `2.+5`. 정수 필드에 실수를 쓰거나 실수 필드에 소수점 없는 정수를 쓰면 오류 또는 다른 값으로 읽힌다.
- 빈 필드는 기본값이다. 자유필드에서는 `,,`로 비운다.
- `$` 뒤는 주석이다. 연속행 사이 주석 줄은 카드를 끊지 않는다.

## 3. 논리 필드 번호 (편집기 `field`)
1행 QRG 번호 그대로(1=카드명, 2..9), 연속행은 10부터 이어진다(small 기준 한 줄 8개, large는 두 줄이 small 한 줄).

| 카드 | 필드 |
|---|---|
| `GRID` | 2 ID, 3 CP, 4 X1, 5 X2, 6 X3, 7 CD, 8 PS |
| `PSHELL` | 2 PID, 3 MID1, 4 T, 5 MID2, 6 12I/T³, 7 MID3, 8 TS/T, 9 NSM, 10 Z1, 11 Z2, 12 MID4 |
| `PSOLID` | 2 PID, 3 MID, 4 CORDM |
| `PBARL`/`PBEAML` | 2 PID, 3 MID, 4 GROUP, 5 TYPE, 10 DIM1, 11 DIM2, ... (치수는 연속행) |
| `PBAR` | 2 PID, 3 MID, 4 A, 5 I1, 6 I2, 7 J, 8 NSM |
| `MAT1` | 2 MID, 3 E, 4 G, 5 NU, 6 RHO, 7 A, 8 TREF, 9 GE, 10 ST, 11 SC, 12 SS |
| `FORCE`/`MOMENT` | 2 SID, 3 G, 4 CID, 5 F(M), 6 N1, 7 N2, 8 N3 |
| `PLOAD4` | 2 SID, 3 EID, 4 P1, 5 P2, 6 P3, 7 P4 |
| `LOAD` | 2 SID, 3 S, 4 S1, 5 L1, 6 S2, 7 L2 ... |
| `SPC1` | 2 SID, 3 C, 4 G1, 5 G2 ... |
| `EIGRL` | 2 SID, 3 V1, 4 V2, 5 ND, 6 MSGLVL, 7 MAXSET, 8 SHFSCL, 9 NORM |
| `DESVAR` (SOL 200) | 2 ID, 3 LABEL, 4 XINIT, 5 XLB, 6 XUB, 7 DELXV |
- 필드 정의는 MSC Nastran QRG(Quick Reference Guide)로 최종 확인한다. 버전에 따라 추가 필드가 있을 수 있다.

## 4. SOL별 최소 Case Control
| SOL | Case Control | Bulk |
|---|---|---|
| 101 선형 정적 | `SPC`, `LOAD`, `DISP/STRESS/SPCF = ALL` | 하중, 구속 |
| 103 고유진동 | `SPC`, `METHOD = n` | `EIGRL,n,,,ND` |
| 105 좌굴 | `SUBCASE 1`: `SPC`,`LOAD` / `SUBCASE 2`: `SPC`,`METHOD`,`STATSUB = 1` | `EIGRL` (양의 근만 찾으려면 V1=0.) |
| 200 최적화 | `DESOBJ`, `DESSUB`/`DESGLB`, `ANALYSIS = STATICS` | `DESVAR`, `DVPREL1`, `DRESP1`, `DCONSTR`, `DOPTPRM` |

## 5. 실행 키워드 (MSC)
| 키워드 | 의미 |
|---|---|
| `scr=yes` | 스크래치 DB(.MASTER/.DBALL) 남기지 않음 |
| `old=no` | 이전 출력의 버전 사본(.f06.1 등) 만들지 않음 |
| `batch=no` | 포그라운드 실행(끝날 때까지 대기) |
| `mem=4gb` | 메모리 |
| `smp=4` / `parallel=4` | 공유메모리 병렬. TODO(사내검증): 버전별 이름 |
| `out=dir/name` | 출력 파일 위치/이름 |

## 6. f06 메시지
- `*** USER FATAL MESSAGE nnnn` / `*** SYSTEM FATAL MESSAGE nnnn` → 실패. 번호로 원인을 찾는다(9050 특이 강성, 2025/2101류 요소 기하, 316 카드 형식 오류).
- `USER WARNING MESSAGE` → 실패 아님. 다만 9050 직전 경고나 큰 EPSILON은 구속 부족 신호다.
- 정상 종료 → 끝부분 `* * * END OF JOB * * *`. FATAL이 있어도 찍힐 수 있다.
- 카드 형식 오류는 Bulk 에코에서 해당 줄과 함께 `USER FATAL MESSAGE 316`(ILLEGAL DATA ON BULK DATA ENTRY) 등으로 나온다. TODO(사내검증): 버전별 번호.
