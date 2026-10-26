# Workbench 저널(IronPython) 레퍼런스

## 실행
```
runwb2 -B -R script.wbjn          # -B batch (no GUI), -R run journal
runwb2 -B -F project.wbpj -R script.wbjn   # open the project, then run
```
- Windows 경로 예: `C:/Program Files/ANSYS Inc/v242/Framework/bin/Win64/RunWB2.exe`
- 저널 언어는 **IronPython 2.7**이다. `json`, `os`, `codecs`는 쓸 수 있다. f-string과 `pathlib`은 쓸 수 없다.
- 녹화: GUI에서 **File > Scripting > Record Journal...**을 켜고 작업한다. 기록된 `.wbjn`이 해당 버전의 정확한 API다. 녹화 결과를 우선 참고한다.

## 프로젝트 열기/저장/아카이브
```python
Open(FilePath="C:/work/case_0001/bracket.wbpj")
Unarchive(ArchivePath="C:/work/case_0001/bracket.wbpz",
          ProjectPath="C:/work/case_0001/bracket.wbpj", Overwrite=True)
Save(Overwrite=True)
SaveAs(FilePath="C:/work/case_0001/solved.wbpj", Overwrite=True)   # TODO(사내검증) arguments per version
Archive(FilePath="C:/work/case_0001/solved.wbpz")                 # TODO(사내검증)
```

## 파라미터
```python
p = Parameters.GetParameter(Name="P1")            # input or output parameter
allp = Parameters.GetAllParameters()              # iterate: p.Name, p.DisplayText
dp = Parameters.GetDesignPoint(Name="0")          # "0" = Current
dp.SetParameterExpression(Parameter=p, Expression="12 [mm]")
Update()                                          # update Current design point (whole project)
UpdateAllDesignPoints()                           # all DPs in the table
v = Parameters.GetParameter(Name="P3").Value      # Quantity -> v.Value (float), v.Unit
Parameters.ExportAllDesignPointsData(FilePath="C:/work/dp.csv")
```
- 새 설계점을 추가하는 함수는 `Parameters.CreateDesignPoint()`다. 플랫폼은 케이스마다 Current DP 하나만 바꾸는 방식을 기본으로 한다. 케이스 폴더를 격리하고 병렬로 돌리기 쉽기 때문이다.
- 출력 파라미터의 단위는 프로젝트 단위계(Units 메뉴)를 따른다. 템플릿에서 단위를 확인하고 results.json 설명에 적는다.
- Expression에 단위를 안 붙였을 때의 해석은 `TODO(사내검증)` 항목이다. 녹화된 저널과 같은 형식을 쓴다.

## 시스템/컴포넌트 접근(Mechanical 연동)
```python
system = GetSystem(Name="SYS")                    # schematic system name (Properties > System ID)
model = system.GetContainer(ComponentName="Model")
model.Edit()                                      # opens Mechanical (hidden in -B?) TODO(사내검증)
model.SendCommand(Language="Python", Command="...Mechanical scripting...")
model.Exit()
```
- `SendCommand(Language="Python")`는 Mechanical의 IronPython 스크립팅(ExtAPI, Model, Graphics)을 실행한다. 2019 R3 이후 버전에서 쓸 수 있다. `TODO(사내검증)`
- 배치(-B) 모드에서 Mechanical 그래픽 export가 막힐 수 있다. 대안은 `.rst`를 PyDPF로 그리는 것이다.

## 해 파일(.rst) 위치
일반적으로 `<project>_files/dp0/SYS/MECH/file.rst`에 있다. 시스템 이름에 따라 `SYS-1` 같은 경로가 될 수 있다. `dpf_extract_rst.py`는 케이스 폴더 아래에서 가장 최근의 `*.rst`를 자동으로 찾는다.

## 실패 패턴
| 증상 | 원인/조치 |
|---|---|
| 출력값이 이전 케이스와 같음 | 업데이트 부분 실패. CSV에서 DP 상태를 확인한다. Mechanical 메시지(Solution Information)를 확인한다 |
| `GetParameter` 실패 | 이름이 P번호가 아님. `GetAllParameters()`로 출력해 확인한다 |
| 형상 재생성 실패 | 치수 범위가 CAD 구속을 위반함. DOE 범위를 좁히고, 사용자에게 실패 케이스를 보고한다 |
| 라이선스 대기로 멈춤 | 라이선스 큐. `run.log`에 시간이 기록되는지 확인한다 |
