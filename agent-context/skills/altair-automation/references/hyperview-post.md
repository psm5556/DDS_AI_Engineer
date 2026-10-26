# HyperView 후처리 자동화(Tcl hwi/post API)

> HyperWorks 2022 이전의 classic HyperView 기준이다. 2023 이후의 새 UI는 `runhwx` + Python(`hw` 모듈)을 권장할 수 있다. 사용할 버전에서 확인한다. `TODO(사내검증)`

## 실행
```
hw.exe -b -tcl script.tcl          # HyperWorks batch (no window)
hw.exe -tcl script.tcl             # GUI mode (more reliable screen capture)
```
- 계약의 `altair_hv` 키는 `{altair_hvp} {script}`다. `hvp`가 사내 설치에서 HyperView 배치 실행파일인지 확인한다. 아니면 `hw -b -tcl`로 등록할 것을 권장한다.
- 창이 없는 배치에서 캡처하면 이미지가 작거나 빌 수 있다(그래픽 드라이버 의존). 이 경우 GUI 모드로 열고 스크립트 끝에서 세션을 닫는다.

## 핸들 체인
```tcl
hwi OpenStack
hwi GetSessionHandle sess
sess GetProjectHandle proj
proj GetPageHandle page [proj GetActivePage]
page GetWindowHandle win [page GetActiveWindow]
win SetClientType "Animation"
win GetClientHandle post
set mid [post AddModel "case.h3d"]
post GetModelHandle mdl $mid
mdl SetResult "case.h3d"
mdl GetResultCtrlHandle res
res GetContourCtrlHandle cnt
cnt SetDataType "Element Stresses (2D & 3D)"
cnt SetDataComponent "vonMises"
cnt SetEnableState true
post SetDisplayOptions "contour" true
post Draw
sess CaptureScreen png "images/mises.png"        ;# TODO(사내검증) signature / CaptureScreenToSize
hwi CloseStack
```
- `hwi OpenStack`과 `hwi CloseStack`은 스크립트 안에서 만든 핸들을 정리한다.
- 데이터 타입과 성분 문자열은 Contour 패널에 보이는 이름 그대로 쓴다(솔버와 결과 형식마다 다름).
- 서브케이스와 시뮬레이션 선택: `res SetCurrentSubcase id`, `res SetCurrentSimulation idx`. `TODO(사내검증)`
- 뷰 방향은 `win GetViewControlHandle view` 다음에 `view Fit`. 방향 설정 메서드는 `TODO(사내검증)`이다.

## 숫자 추출
숫자는 `.op2`/`.out`(os_case_driver.py)에서 뽑는 것을 기본으로 한다. HyperView에서 꼭 뽑아야 하면 Query 컨트롤(`post GetQueryCtrlHandle`)로 엔티티 값을 CSV로 내보낸다. 녹화한 Tcl(HyperView의 command 기록)이나 Altair 예제로 확인한다. `TODO(사내검증)`

## HyperGraph(시간이력, Radioss T01)
HyperGraph도 같은 hwi 체인을 쓴다(`win SetClientType "Plot"`). 곡선 데이터를 내보내는 방법은 `TODO(사내검증)`이다.
