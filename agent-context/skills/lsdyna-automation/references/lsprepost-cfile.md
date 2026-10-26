# LS-PrePost command file(cfile) 배치 이미지

## 실행
```
lsprepost -nographics c=dds_images.cfile
```
- tool 키 `lsprepost`(명령 템플릿 `{lsprepost} -nographics c={script}`). `dyna_case_run.py --lsprepost <exe>`는 같은 명령을 서브프로세스로 실행한다.
- 배치 렌더링은 OpenGL 환경이 필요할 수 있다. Linux 서버에서는 Xvfb 같은 가상 디스플레이를 쓰는 경우가 있다. TODO(사내검증): 사내 배치 PC/서버에서 `-nographics`로 PNG가 실제로 저장되는지 확인한다. 안 되면 `-nographics` 없이 실행해 본다.

## 명령을 확인하는 가장 확실한 방법 (필수)
1. LS-PrePost GUI에서 d3plot 열기 → 원하는 fringe, state, 뷰 → File > Print(PNG 저장)를 한 번 수행한다.
2. LS-PrePost를 종료하면 작업 폴더(또는 홈)에 **`lspost.cfile`**이 남는다. 이 파일에 방금 한 조작이 명령으로 기록돼 있다. GUI 하단 메시지 창에도 명령이 표시된다.
3. 그 내용을 복사해 경로만 바꿔서 템플릿으로 쓴다.

## 템플릿 명령 (scripts/dyna_images.cfile)
| 명령 | 의미 | 확인 상태 |
|---|---|---|
| `open d3plot "path/d3plot"` | d3plot 열기 | 확인됨(LS-OPT 예제) |
| `ac` | 자동 중심/맞춤 | TODO(사내검증) |
| `anim last` 또는 `state N;` | 마지막 state로 이동 | TODO(사내검증) |
| `fringe 7` | 유효 소성변형률 | 확인됨 |
| `fringe 9` | von Mises 응력 | TODO(사내검증): fringe 번호 표 |
| `pfringe` | fringe 표시 적용 | TODO(사내검증) |
| `print png "file.png" LANDSCAPE nocompress gamma 1.000 opaque enlisted "OGL1x1"` | PNG 저장 | TODO(사내검증): 옵션 문자열은 버전별로 lspost.cfile 기록을 따른다 |
| 뷰: `top`, `front`, `right`, `iso`, `rx 30` 등 | 뷰 방향 | TODO(사내검증) |
| `quit` | 종료 | 확인됨(LS-OPT 예제) |

- 경로는 슬래시(`/`)로 쓰고 따옴표로 감싼다. 한글과 공백 경로는 피한다.
- 범례 범위 고정(DOE 비교용) 명령도 GUI 기록으로 확인한다(Fringe Range 패널). TODO(사내검증).

## 대안: 이미지 없이 데이터만
LS-OPT에서 쓰는 방식처럼 cfile로 곡선을 xypair 파일로 저장할 수 있다. 아래는 LS-OPT 지원 페이지의 예제를 줄 단위로 나눈 것이다. TODO(사내검증): 줄 구분과 인자를 확인한다.
```
open d3plot d3plot
ascii rcforc open rcforc 0
ascii rcforc plot 4 Ma-1
xyplot 1 savefile xypair out_force.txt 1
deletewin 1
quit
```
수치 결과는 이 방식보다 파이썬 파서(`dyna_ascii_parsers.py`, `dyna_lasso_extract.py`)를 쓰는 편이 더 단순하고 안정적이다.

## 대안: 파이썬 렌더링
LS-PrePost를 쓸 수 없으면 lasso-python의 d3plot 배열로 matplotlib 산점도나 단면 그림을 그려 `images/`에 저장할 수 있다(품질은 낮다). TODO(사내검증): lasso `D3plot.plot()`(HTML 3D 출력)은 PNG가 아니므로 플랫폼 아티팩트로는 쓸 수 없다.
