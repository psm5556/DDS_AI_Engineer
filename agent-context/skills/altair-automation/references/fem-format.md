# OptiStruct/Nastran .fem 형식과 직접 수정

## 덱 구조
```
$ comment
SUBCASE 1                      <- Case control (load/output requests)
  SPC = 1
  LOAD = 2
  DISPLACEMENT = ALL
  STRESS = ALL
  SPCFORCE = ALL
BEGIN BULK                     <- Bulk data (cards)
PSHELL  ...
ENDDATA
```
I/O 옵션(`OUTPUT`, `FORMAT`, `SCREEN` 등)은 Case control보다 위에 둔다.

## 필드 형식
| 형식 | 규칙 | 예 |
|---|---|---|
| small fixed | 1~8열은 카드명, 이후 8열씩 필드 2~9, 73~80열은 continuation | `PSHELL         1       1     2.5       1` |
| large fixed | 카드명 뒤에 `*`. 필드 폭 16, 한 줄에 데이터 필드 4개. continuation 줄은 `*`로 시작 | `PSHELL*               1               1             2.5` |
| free | 콤마 구분. 필드 폭 제한 없음(값은 16자 이내 권장) | `PSHELL,1,1,2.5,1,,1` |

- 한 덱 안에서 카드마다 형식을 섞어 써도 된다.
- 실수는 소수점을 포함해야 한다: `2.`, `210000.`, `7.85-9`(지수 E 생략 가능), `1.5E-3`
- 정수 필드(ID)에 소수점이 있으면 에러다.
- 탭 문자는 OptiStruct가 8칸 단위로 해석하지만, 스크립트로 열 위치를 계산하기 어렵다. 템플릿 대상 줄에는 탭을 쓰지 않는다.

## 주요 카드 필드 번호(필드 1 = 카드명)
| 카드 | 필드 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|
| PSHELL | PID | MID1 | **T** | MID2 | 12I/T³ | MID3 | TS/T |
| PSOLID | PID | MID | CORDM | ... | | | |
| MAT1 | MID | **E** | G | **NU** | **RHO** | A | TREF |
| FORCE | SID | G | CID | **F** | N1 | N2 | N3 |
| MOMENT | SID | G | CID | **M** | N1 | N2 | N3 |
| PLOAD4 | SID | EID | **P1** | P2 | P3 | P4 | ... |
| PLOAD2 | SID | **P** | EID1 | ... | | | |
| SPC1 | SID | C | G1 | G2 | ... | | |

MAT1에서 E, G, NU 가운데 둘만 주면 나머지 하나는 계산된다. 셋을 모두 주면 관계식 경고가 날 수 있다.

## 수정 전략
1. **카드 필드 편집**(`os_case_driver.py --mode cards`): 원본 덱을 그대로 두고 `CARD_RULES`로 지정한 필드만 바꾼다. small, large, free 형식을 모두 지원한다. 단, 대상 필드는 카드 첫 줄에 있어야 한다.
2. **플레이스홀더**(`--mode placeholder`): 대상 카드를 자유필드로 바꾸고 `{{key}}`를 넣는다. 고정필드 줄에 플레이스홀더가 있으면 열이 밀리므로 드라이버가 에러로 막는다.
3. **하중 배율**: 기준 덱의 하중을 그대로 두고 `mode: scale`로 곱한다. 여러 FORCE 카드가 같은 SID를 가지면 모두 함께 바뀐다.
4. 또 다른 방법은 `LOAD` 조합 카드(`LOAD SID S S1 L1 ...`)의 배율 S1을 바꾸는 것이다. 개별 카드를 건드리지 않아도 된다.

## 검증
- 수정한 덱을 원본과 diff해서 바뀐 줄만 확인한다.
- `optistruct case.fem -check`로 문법을 확인한다.
- 수정 후 첫 케이스는 `.out`의 질량 변화(두께와 밀도 변경 시)로 반영 여부를 확인한다.
