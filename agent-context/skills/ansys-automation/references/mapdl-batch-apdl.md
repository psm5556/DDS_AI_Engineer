# MAPDL 클래식 배치 + APDL 템플릿 레퍼런스

## 명령행
```
ANSYS242.exe -b -i input.inp -o solve.out -j file -np 4 -dir C:/work/case_0001
```
| 옵션 | 의미 |
|---|---|
| `-b` | 배치 모드 |
| `-i` / `-o` | 입력 / 출력(로그) 파일 |
| `-j` | jobname(결과는 `file.rst`, 에러는 `file.err`) |
| `-np` | 코어 수(SMP). DMP는 `-dis` |
| `-dir` | 작업 폴더 |
| `-p` | 라이선스 제품 코드(예: `ansys`, `meba`) `TODO(사내검증)` |

실행파일 이름(`ANSYS242.exe` 또는 `MAPDL.exe`)과 경로(`.../v242/ansys/bin/winx64/`)는 버전에 따라 다르다. `TODO(사내검증)`

## 파라미터 전달
APDL은 JSON을 읽을 수 없다. 그래서 `mapdl_batch_driver.py`가 params.json을 `params.inp`로 바꾸고, 템플릿이 `/INPUT,params,inp`로 이를 읽는다.
- 템플릿 앞부분에 기본값을 정의하고 그 뒤에 `/INPUT`을 둔다. 이렇게 하면 누락된 키는 기본값으로 남는다.
- 이름 규칙: 영문자로 시작, 32자 이하, 끝에 `_` 금지. APDL 명령 이름(`FORCE`, `TYPE`, `REAL` 등)과 겹치면 안 된다.

## 결과 → 파일
```
NSORT,S,EQV
*GET,SMAX,SORT,0,MAX
*CFOPEN,dds_results,txt
*VWRITE,SMAX
('max_mises ',E20.10)
*CFCLOSE
```
- `*VWRITE`의 다음 줄은 반드시 포트란 포맷이다. 입력파일(배치)에서만 동작한다.
- 자주 쓰는 `*GET`:

| 값 | 명령 |
|---|---|
| 최대 von Mises | `NSORT,S,EQV` 후 `*GET,..,SORT,0,MAX` |
| 최대 변위 | `NSORT,U,SUM` 후 `*GET,..,SORT,0,MAX` |
| 반력 합 | `NSEL`, `ESLN`, `FSUM` 후 `*GET,..,FSUM,0,ITEM,FZ` |
| 부피 | `ETABLE,EVOL,VOLU`, `SSUM` 후 `*GET,..,SSUM,0,ITEM,EVOL` |
| 특정 절점 변위 | `*GET,UZ1,NODE,nid,U,Z` |
| 고유진동수 | `*GET,F1,MODE,1,FREQ` (모달 해석 후 POST1) |

## 이미지
```
/SHOW,PNG          ! subsequent plots -> jobname000.png, jobname001.png ...
/GFILE,1200        ! pixel resolution
/RGB,INDEX,100,100,100,0   ! white background
/RGB,INDEX,0,0,0,15
/VIEW,1,1,1,1      ! iso view
PLNSOL,S,EQV
/SHOW,CLOSE
```
이미지 파일 번호는 플롯 순서대로 매겨진다. 파일명을 정하려면 드라이버에서 순서에 맞춰 이름을 바꾼다.

## 실패 판정
- `solve.out`에 `*** ERROR ***`이 있으면 실패다. 경고(`*** WARNING ***`)는 기록만 한다.
- 결과 txt가 없으면 실패다. 템플릿 끝까지 실행되지 않았다는 뜻이다.
- 수렴 실패: `solve.out`에서 `Solution not converged`를 찾는다.
- 배치 종료코드: 정상 0, 에러 시 비 0이다(예: 8). 버전에 따라 다르므로 보조 지표로만 쓴다. `TODO(사내검증)`
