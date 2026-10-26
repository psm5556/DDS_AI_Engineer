# LS-DYNA 키워드 편집 레퍼런스

## 1. 형식 규칙 (가장 흔한 실수의 원인)
- 키워드 줄은 `*`로 시작한다. `$`로 시작하는 줄은 주석이다.
- **고정폭 필드**: 대부분의 카드는 한 줄에 8개 필드, 필드당 **10자**(80열)다. 값은 자기 필드 안에 있어야 한다. 한 글자만 밀려도 다른 필드 값으로 읽히거나 오류가 난다.
  - 예: `-1.2345e-05`는 11자라서 10자 필드에 들어가지 않는다. `-1.235E-5`로 줄여야 한다. `fmt_field()`가 자동으로 줄여준다.
  - 일부 카드는 필드 폭이 16/20자이거나(노드 좌표 `*NODE`는 I8, 3E16.0 …) 필드 수가 다르다. 매뉴얼의 카드 표를 확인한다.
- **LONG 형식**: `*KEYWORD LONG=Y`이면 파일 전체가 필드당 20자다. 키워드 끝에 `+`를 붙이면(`*SECTION_SHELL+`) 그 카드만 20자, `-`를 붙이면 10자다.
- **자유형식**: 한 줄 안에서 값을 콤마로 구분할 수 있다(`1,2,0.833,5`). 한 카드 줄 안에서 고정폭과 콤마를 섞지 않는다. 템플릿 치환에서 폭 문제를 피하는 쉬운 방법이다.
- 정수 필드(I)에 `1.0`을 넣으면 오류가 날 수 있다. ID, 플래그, 개수는 정수로 쓴다.
- 문자 인코딩: 한글 주석이나 경로는 피한다. 파일은 latin-1로 읽고 쓰면 바이트를 그대로 보존한다.

## 2. *PARAMETER (권장 파라미터화 방법)
```
*PARAMETER
$#   prmr1      val1     prmr2      val2     prmr3      val3     prmr4      val4
R      thk       1.5R      vel   -13.889I      nip         5
*PARAMETER_EXPRESSION
$#   prmr1   expression
R    thk2    &thk*2.0
```
- 카드 형식: (이름 A10, 값 10자) × 4쌍. 이름 필드의 첫 글자는 타입이다: `R` 실수, `I` 정수, `C` 문자.
- 사용: 다른 카드의 필드에 `&thk`라고 쓴다. 부호를 바꾸려면 `-&thk`를 쓴다. 참조는 정의된 뒤에 와야 하므로 `*PARAMETER`를 파일 앞쪽(또는 맨 처음 `*INCLUDE` 파일)에 둔다.
- `*PARAMETER_EXPRESSION`: (이름 A10, 수식 A70). 함수(sin, sqrt 등)와 다른 파라미터를 쓸 수 있다.
- `*PARAMETER_LOCAL`은 그 파일(인클루드) 안에서만 유효하다. `*PARAMETER_DUPLICATION`은 같은 이름이 중복 정의될 때의 처리 방식(첫 값/마지막 값/오류)을 정한다.
- 이름 길이는 이름 필드(10자)에서 타입 문자를 뺀 범위다. params.json 키가 길면 `dyna_case_run.py`의 `NAME_MAP`으로 짧은 이름에 매핑한다. TODO(사내검증): 밑줄 포함 이름, 대소문자 처리.
- 값 필드도 10자이므로 `fmt_field()`로 서식화한다(자동 처리됨).
- **DDS 권장 구조**: 설계변수를 `*PARAMETER` 하나에 모으고 모델 카드에서는 `&이름`으로만 참조한다. 그러면 스크립트는 `*PARAMETER` 값만 바꾸면 되므로 카드 위치나 폭 문제가 사라진다.

## 3. *INCLUDE 구조
```
*KEYWORD
*INCLUDE
params.k
*INCLUDE
mesh.k
*INCLUDE
materials.k
*END
```
- 상대경로는 실행 폴더(또는 포함한 파일 위치) 기준이다. `*INCLUDE_PATH`(또는 `*INCLUDE_PATH_RELATIVE`)로 검색 경로를 추가할 수 있다.
- **스터디 `inputs`에 모든 인클루드 파일을 넣어야** 케이스 폴더로 복사된다. 큰 공용 메쉬를 매번 복사하기 싫으면 `*INCLUDE_PATH`로 공용 폴더(읽기 전용)를 가리키되, 케이스마다 바뀌는 파일은 반드시 케이스 폴더에 둔다.
- 트리 확인: `python dyna_keyword_tools.py includes main.k`

## 4. 자주 바꾸는 카드 (필드 순서, 0부터)
| 카드 | 필드 |
|---|---|
| `*SECTION_SHELL` 카드1 | 0 SECID, 1 ELFORM, 2 SHRF, 3 NIP, 4 PROPT, 5 QR/IRID, 6 ICOMP, 7 SETYP |
| `*SECTION_SHELL` 카드2 | 0 T1, 1 T2, 2 T3, 3 T4, 4 NLOC, 5 MAREA, 6 IDOF, 7 EDGSET |
| `*MAT_ELASTIC` (001) | 0 MID, 1 RO, 2 E, 3 PR, 4 DA, 5 DB, 6 K |
| `*MAT_PIECEWISE_LINEAR_PLASTICITY` (024) 카드1 | 0 MID, 1 RO, 2 E, 3 PR, 4 SIGY, 5 ETAN, 6 FAIL, 7 TDEL |
| 〃 카드2 | 0 C, 1 P, 2 LCSS, 3 LCSR, 4 VP |
| `*CONTROL_TERMINATION` | 0 ENDTIM, 1 ENDCYC, 2 DTMIN, 3 ENDENG, 4 ENDMAS |
| `*CONTROL_TIMESTEP` | 0 DTINIT, 1 TSSFAC, 2 ISDO, 3 TSLIMT, 4 DT2MS, 5 LCTM, 6 ERODE, 7 MS1ST |
| `*BOUNDARY_PRESCRIBED_MOTION_NODE/SET/RIGID` | 0 ID(NID/NSID/PID), 1 DOF, 2 VAD(0 속도, 1 가속도, 2 변위), 3 LCID, 4 SF, 5 VID, 6 DEATH, 7 BIRTH |
| `*INITIAL_VELOCITY_GENERATION` 카드1 | 0 ID, 1 STYP(1 파트셋, 2 파트, 3 노드셋), 2 OMEGA, 3 VX, 4 VY, 5 VZ, 6 IVATN, 7 ICID |
| `*INITIAL_VELOCITY` 카드1 / 카드2 | 0 NSID, 1 NSIDEX, 2 BOXID, 3 IRIGID, 4 ICID / 0 VX, 1 VY, 2 VZ, 3 VXR, 4 VYR, 5 VZR |
| `*DEFINE_CURVE` 카드1 | 0 LCID, 1 SIDR, 2 SFA(가로축 배율), 3 SFO(세로축 배율), 4 OFFA, 5 OFFO, 6 DATTYP |

- `_TITLE` 옵션이 붙으면 카드1 앞에 제목 줄(80자)이 하나 더 있다. 도구 함수는 이를 건너뛴다.
- `*SECTION_SHELL`에서 ICOMP=1이면 카드2 뒤에 적층 각도 카드(B1~B8, NIP/8줄)가 온다.
- 하중 크기는 보통 `*DEFINE_CURVE`의 SFO(세로축 배율)나 `*BOUNDARY_PRESCRIBED_MOTION`의 SF로 바꾸는 것이 가장 간단하다. 곡선 데이터 점은 건드리지 않는다.
- 재료 카드 형식은 MAT 번호마다 다르다. 매뉴얼(Vol. II) 카드 표를 확인하고 바꾼다.

## 5. 해석 시간, 시간 간격, 질량 스케일링
- 종료 시간: `*CONTROL_TERMINATION` ENDTIM. 출력 간격(`*DATABASE_BINARY_D3PLOT` DT, `*DATABASE_GLSTAT` DT)도 함께 맞춘다.
- 시간 간격 배율: `*CONTROL_TIMESTEP` TSSFAC(기본 0.9).
- **질량 스케일링**: `*CONTROL_TIMESTEP` DT2MS < 0 이면 시간 간격이 |DT2MS|×TSSFAC보다 작은 요소에만 질량을 추가해서 그 시간 간격을 유지한다. 추가 질량은 glstat의 `added mass`, `percentage increase`와 d3hsp에서 확인한다. 전체 질량의 수 % 이상 증가하거나 충격부에 몰리면 결과를 신뢰할 수 없다. DOE 케이스마다 `percent_mass_increase`를 results에 함께 기록하는 것을 권장한다.
- 정밀도/재현성: SMP는 `ncpu=-N`(음수)으로 실행하면 일관성(consistency) 옵션이 켜져 CPU 수와 무관하게 같은 결과가 나온다(대신 느리다). DOE 비교에 권장한다.

## 6. 출력 요청
```
*DATABASE_GLSTAT
$#      dt    binary      lcur     ioopt
   1.0E-04         3
*DATABASE_RCFORC
   1.0E-04         3
*DATABASE_NODOUT
   1.0E-04         3
*DATABASE_HISTORY_NODE
      1001      1002
*DATABASE_BINARY_D3PLOT
   1.0E-03
```
- BINARY 필드: 1 = ASCII 파일, 2 = binout, 3 = 둘 다. TODO(사내검증): 버전, SMP/MPP별 기본값과 동작(MPP는 binout 위주).
- nodout은 `*DATABASE_HISTORY_NODE`에 지정한 절점만 기록한다.
