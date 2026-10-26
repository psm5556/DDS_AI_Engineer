# LS-DYNA 결과 추출 레퍼런스

## 1. 완료 판정
| 파일 | 위치 | 성공 문구 |
|---|---|---|
| `d3hsp` | 케이스 폴더 | `N o r m a l    t e r m i n a t i o n` |
| `messag` | SMP | 동일 |
| `mes0000`, `mes0001` … | MPP | 동일 |
- 글자 사이 공백 개수가 버전마다 달라서 **공백을 모두 지운 뒤 `Normaltermination` / `Errortermination`을 찾는다**(`dyna_case_run.termination_status()`).
- 오류 원인은 `*** Error` 줄에 있다. 메시지 번호(예: `*** Error 10246`)로 매뉴얼을 검색한다.
- 솔버 종료코드는 판정에 쓰지 않는다.
- 비정상 조짐: glstat의 `energy ratio`가 1에서 크게 벗어남, hourglass energy > internal energy의 10%, added mass 급증, 음의 체적(`negative volume`) 메시지.

## 2. ASCII 출력 (표준 라이브러리로 파싱, `dyna_ascii_parsers.py`)
| 파일 | 내용 | 파서 |
|---|---|---|
| `glstat` | 전체 에너지, 시간 간격, 추가 질량 | `parse_glstat()` → 시간별 dict 리스트 |
| `matsum` | 파트별 에너지/운동량 | `parse_matsum()` → {mat_id: [...]} |
| `rcforc` | 접촉면 힘 | `parse_rcforc()` → {(side, id): [(t, fx, fy, fz)]} |
| `nodout` | 절점 변위/속도/가속도 | `parse_nodout(nodes=[...])` |
- 숫자가 붙어서 출력되는 경우(`1.0E-03-2.0E-03`)가 있어서 공백 split 대신 E 표기 정규식으로 분리한다.
- TODO(사내검증): 사내 솔버 버전의 출력 샘플 하나로 파서를 반드시 검증한다(키 이름, master/main 표기, 회전 블록).

## 3. binout (lasso-python)
**플랫폼 venv에 설치해야 한다**: `pip install lasso-python`
```python
from lasso.dyna import Binout
b = Binout("binout*")              # MPP의 binout0000, binout0001 … 을 모두 읽음
b.read()                           # 최상위 목록 ['glstat', 'nodout', 'rcforc', ...]
b.read("glstat")                   # 변수 이름 목록
t  = b.read("glstat", "time")
ke = b.read("glstat", "kinetic_energy")
ids = b.read("nodout", "ids")      # 다중 ID 데이터는 ids 로 열을 식별
df = b.as_df("glstat", "kinetic_energy")   # pandas 필요
```
- 변수 이름은 반드시 `b.read("<dir>")`로 확인한다. TODO(사내검증): nodout(`x_displacement` …), rcforc(`x_force`, ids/side 구조)의 이름.

## 4. d3plot (lasso-python)
```python
from lasso.dyna import D3plot, ArrayType
d3 = D3plot("d3plot")                                   # d3plot01, 02 … 자동 연결
t   = d3.arrays[ArrayType.global_timesteps]             # (n_states,)
x   = d3.arrays[ArrayType.node_displacement]            # (n_states, n_nodes, 3) — 각 state의 현재 좌표
u   = x - x[0]                                          # 변위
ss  = d3.arrays[ArrayType.element_shell_stress]         # (n_states, n_shells, n_layers, 6)
eps = d3.arrays[ArrayType.element_shell_effective_plastic_strain]   # (n_states, n_shells, n_layers)
sol = d3.arrays[ArrayType.element_solid_stress]         # (n_states, n_solids, [n_ip,] 6)
nid = d3.arrays[ArrayType.node_ids]
pid = d3.arrays[ArrayType.part_ids]
```
- Mises는 6성분(xx, yy, zz, xy, yz, zx)으로 직접 계산한다(`dyna_lasso_extract.von_mises`).
- 응력은 d3plot 출력 설정(`*DATABASE_EXTENT_BINARY`)에 따라 없을 수 있다. 배열 목록은 `--list`로 확인한다.
- 대형 d3plot은 메모리를 많이 쓴다. TODO(사내검증): `D3plot(..., state_array_filter=[...])`처럼 필요한 배열만 읽는 옵션이 설치 버전에 있는지 확인한다.
- 파트 필터: `element_shell_part_indexes`는 `part_ids`의 인덱스다. TODO(사내검증).

## 5. 질량
- `d3hsp`의 질량 요약(파트별 질량, 총질량)이나 glstat의 `added mass` 줄을 쓴다. TODO(사내검증): 버전별 문구가 다르므로 사내 d3hsp에서 문구를 확인한 뒤 정규식을 작성한다(공백을 지운 뒤 찾기).
- 설계 비교용 질량은 형상과 두께로 계산할 수도 있다(쉘: 면적 × 두께 × 밀도).

## 6. 권장 results 키
`max_disp`, `node<id>_max_disp`, `max_contact_force_<id>`, `max_shell_vm`, `max_shell_eps`, `final_internal_energy`, `max_kinetic_energy`, `hourglass_ratio`, `percent_mass_increase`
