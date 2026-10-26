# PyMAPDL(ansys-mapdl-core) 레퍼런스

## 실행
```python
from ansys.mapdl.core import launch_mapdl
mapdl = launch_mapdl(run_location="C:/work/case_0001/mapdl_work", jobname="file",
                     nproc=2, override=True, loglevel="ERROR")      # exec_file="...ANSYS242.exe"
...
mapdl.exit()
```
- 실행파일을 지정하는 방법은 세 가지다. `exec_file` 인자, 환경변수 `PYMAPDL_MAPDL_EXEC`, 또는 PyMAPDL이 자동으로 탐지한 경로다.
- 이미 떠 있는 MAPDL 서버(gRPC)에 붙을 때는 `launch_mapdl(start_instance=False, ip=..., port=50052)`를 쓴다.
- 예외는 `try/finally`로 감싸고 반드시 `mapdl.exit()`를 호출한다. 그렇지 않으면 라이선스와 프로세스가 남는다.

## APDL 명령 대응
명령은 소문자 메서드로 호출한다. `/` 명령은 `slash` 접두사를 붙이고, `*` 명령은 접두사 없이 쓴다.

| APDL | PyMAPDL |
|---|---|
| `/PREP7`, `/SOLU`, `/POST1`, `FINISH` | `mapdl.prep7()`, `mapdl.slashsolu()`, `mapdl.post1()`, `mapdl.finish()` |
| `ET,1,SOLID186` | `mapdl.et(1, "SOLID186")` |
| `MP,EX,1,2e5` | `mapdl.mp("EX", 1, 2e5)` |
| `BLOCK,...`, `ESIZE`, `VMESH,ALL` | `mapdl.block(...)`, `mapdl.esize(h)`, `mapdl.vmesh("ALL")` |
| `NSEL,S,LOC,X,0` / `D,ALL,ALL` / `F,ALL,FZ,v` | `mapdl.nsel("S","LOC","X",0)`, `mapdl.d("ALL","ALL")`, `mapdl.f("ALL","FZ",v)` |
| `*GET,N,NODE,0,COUNT` | `n = mapdl.get("N", "NODE", 0, "COUNT")` (반환값 사용) |
| `THK = 2.5` | `mapdl.parameters["THK"] = 2.5` |
| `/INPUT,model,mac` | `mapdl.input("model.mac")` |
| 임의 명령 | `mapdl.run("/SHOW,PNG")` |

## 후처리
```python
mapdl.post1(); mapdl.set("LAST")
seqv = mapdl.post_processing.nodal_eqv_stress()          # numpy, node-averaged von Mises
usum = mapdl.post_processing.nodal_displacement("NORM")
sx   = mapdl.post_processing.nodal_component_stress("X")
nnum = mapdl.mesh.nnum                                    # node numbers (same order)
# reaction at selected supports
mapdl.nsel("S","LOC","X",0); mapdl.esln("S",0); mapdl.fsum()
rz = mapdl.get("RZ", "FSUM", 0, "ITEM", "FZ"); mapdl.allsel()
# volume -> mass
mapdl.etable("EVOL","VOLU"); mapdl.ssum(); v = mapdl.get("V","SSUM",0,"ITEM","EVOL")
```
- 결과 파일만 따로 읽을 때는 PyDPF(`dpf_extract_rst.py`)를 쓴다.

## 이미지
```python
mapdl.post_processing.plot_nodal_eqv_stress(off_screen=True, savefig="images/mises.png",
                                            cpos="iso", show_edges=False)
mapdl.post_processing.plot_nodal_displacement("NORM", off_screen=True, savefig="images/disp.png")
mapdl.eplot(off_screen=True, savefig="images/mesh.png")
```
- `savefig`, `off_screen`, `cpos`는 PyVista 플로터 옵션이다. OpenGL이 없는 서버에서는 실패할 수 있으므로 try로 감싼다.
- PyVista를 쓸 수 없으면 APDL 방식을 쓴다: `mapdl.show("PNG")`, `mapdl.plnsol("S","EQV")`, `mapdl.show("CLOSE")`. `run_location`에 PNG가 생성된다. `TODO(사내검증)`: gRPC 원격 세션에서는 파일 다운로드(`mapdl.download`)가 필요하다.

## 에러
- `MapdlRuntimeError`: APDL 명령 에러(예: 메쉬 실패). 케이스 실패로 처리한다.
- `MapdlExitedError`: 프로세스가 죽은 경우. 메모리와 라이선스를 확인한다.
- 로그는 `run_location/file.err`, `file.out`(있는 경우)에 남는다.
