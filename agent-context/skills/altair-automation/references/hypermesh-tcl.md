# HyperMesh Tcl 배치 레퍼런스

## 실행
```
hmbatch -tcl script.tcl            # Windows: <install>/hm/bin/win64/hmbatch.exe
hmopengl -tcl script.tcl           # GUI 실행 + 스크립트(디버깅용)  TODO(사내검증) 실행파일 이름
```
- 스크립트는 cwd(케이스 폴더)를 `[pwd]`로 얻는다. Tcl에는 인자를 넘길 수 없으므로 `params.json`을 직접 읽는다(템플릿의 `dds_read_params`. 평탄한 JSON만 지원).
- 에러가 나면 `exit 1`로 끝낸다. 결과 파일이 없으면 플랫폼이 실패로 처리한다.
- **command.tcl**: GUI 작업은 모두 Tcl 명령으로 기록된다. 버전별 정확한 문법은 이 파일에서 복사한다. 저장 위치는 작업 폴더나 Documents이며, 설정에 따라 다르다.

## 명령 구분
- `*`로 시작하는 명령은 모델을 바꾼다(modify). 예: `*createmark`, `*setvalue`
- `hm_`로 시작하는 명령은 조회한다(query). 예: `hm_getvalue`, `hm_getmark`, `hm_info`, `hm_entityinfo`

## 마크(mark): 엔티티 선택
```tcl
*createmark elems 1 "by comp name" "shell_comp"      ;# mark 1 = elements of a component
set ids [hm_getmark elems 1]
*createmark props 1 "shell_prop"                     ;# by name
*clearmark elems 1
```
선택 방식 문자열("by comp name", "by id", "all", "displayed")의 이름은 버전마다 조금씩 다르다. `TODO(사내검증)`

## 값 변경
```tcl
*setvalue props name=shell_prop STATUS=1 95=2.5      ;# PSHELL T (OptiStruct)   TODO(사내검증)
*setvalue mats  name=steel      STATUS=1 1=210000    ;# MAT1 E                  TODO(사내검증)
set t [hm_getvalue props name=shell_prop dataname=thickness]   ;# query  TODO(사내검증) dataname
```
- 숫자 attribute ID(95, 1 ...)는 솔버 템플릿 기준이다. GUI에서 값을 한 번 바꾼 뒤 command.tcl에서 ID를 확인한다.
- 하중 크기와 방향을 바꾸는 명령은 load type(force, pressure)과 버전에 따라 다르다. GUI에서 기록한 명령을 쓰거나, export한 `.fem`에서 FORCE/PLOAD 카드를 수정한다(권장, `os_case_driver.py`). `TODO(사내검증)`

## 파일 입출력
```tcl
*readfile "C:/work/case_0001/plate.hm"               ;# TODO(사내검증) some versions: *readfile "<path>" 0
hm_answernext yes                                    ;# auto-answer overwrite prompt
*writefile "C:/work/case_0001/out.hm" 1              ;# TODO(사내검증)
set tpl [file join [hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR] feoutput optistruct optistruct]
hm_answernext yes
*feoutputwithdata $tpl "C:/work/case_0001/model.fem" 0 0 1 1 0   ;# 1 = export all  TODO(사내검증)
```
- `*feoutputwithdata`의 문법: `export_template filename reserved1 reserved2 export_type string_array number_of_strings`. 추가 옵션 문자열은 `*createstringarray`로 만든다(예: `CONNECTORS_SKIP`). 인자 값은 버전마다 다르므로 GUI export의 command.tcl과 비교한다.
- 구버전 명령은 `*feoutput "<template>" "<file>" 1 0 0`이다. `TODO(사내검증)`
- 덱을 읽어 들이는 명령은 `*feinputwithdata2`다(Import 시 command.tcl에서 확인). `TODO(사내검증)`

## 모핑(HyperMorph)
1. GUI에서 domain과 handle을 만들고 형상 변형(shape)을 만들어 저장한다.
2. shape를 배율 1.0으로 적용하는 작업을 command.tcl로 녹화한다.
3. 녹화된 명령에서 배율 인자를 `params.json` 값으로 바꾼다.
4. 적용 후 메쉬 품질을 확인한다. 여기서는 `hm_` 품질 조회를 쓰며 `TODO(사내검증)`이다.

shape 변수를 OptiStruct 형상최적화(DESVAR/DVGRID)로 export하면 HyperMesh 없이 OptiStruct 안에서 형상 변경을 할 수도 있다. 고급 기능이므로 사용자 확인이 필요하다.

## HyperMesh Python API(2022 이후)
- `import hm`, `import hm.entities as ent`, `model = hm.Model()` 형태의 API가 있다. 엔티티 속성을 파이썬 객체로 다룬다.
- 배치 실행 방식(`hmbatch`가 Python을 지원하는지, `-script`인지 `runhwx`인지)과 클래스, 속성 이름은 버전에 따라 다르다. **`TODO(사내검증)`**. 사내 표준이 정해질 때까지 Tcl 템플릿을 기본으로 쓴다.
