# Moldflow 배치 명령 (runstudy / studyrlt)

두 실행파일은 Moldflow Insight(Synergy) 설치 폴더의 `bin`에 있다(예: `C:\Program Files\Autodesk\Moldflow Insight 2025\bin`). 라이선스는 해석 토큰을 쓴다. 정확한 옵션은 설치본에서 `runstudy -help`, `studyrlt -help`로 확인한다.

## runstudy.exe — 스터디 해석
```
runstudy.exe cover_study.sdy
```
- 스터디(.sdy)에 저장된 해석 순서(예: Fill+Pack+Cool+Warp)를 그대로 해석한다. 결과 파일은 스터디와 같은 폴더에 생긴다.
- 작업 폴더(cwd)를 `.sdy`가 있는 폴더로 두고 파일 이름만 넘긴다. 경로에 한글/공백을 피한다.
- 종료코드 0을 1차 기준으로 쓰고, 해석 로그의 오류 줄을 2차 기준으로 쓴다(`mf_parse_output.py`).
- TODO(사내검증): 스레드 수, 임시 폴더, 원격 해석(클라우드/해석 서버) 옵션 이름. 결과 파일 이름 규칙(`.out`, `.mfr` 등과 해석 단계 번호).

## studyrlt.exe — 결과/로그 내보내기
```
studyrlt.exe cover_study.sdy -exportoutput        # 해석 화면 출력(.out)을 텍스트로
studyrlt.exe cover_study.sdy -xml <result_id>     # 결과 하나를 XML로
```
- `.out` 해석 로그는 그대로 읽는 텍스트가 아니다. `-exportoutput`으로 텍스트를 만든 뒤 파싱한다.
- `-xml`의 `<result_id>`는 결과 데이터 ID다(플롯 이름이 아니다). Synergy COM의 `plot.GetDataID()`로 확인할 수 있다.
- TODO(사내검증): `-exportoutput` 결과 파일 이름(템플릿은 실행 후 새로 생긴 `<study>*.txt`를 집는다), 단위 옵션(`-unit`), 내보내기 형식 옵션(`-exportpatran` 등) 존재 여부, `-xml` 출력 파일 이름과 구조.

## 해석 로그에서 자주 쓰는 줄 (영문 로그 기준, 예시)
```
Maximum injection pressure       (at      0.6532 s) =      39.0745 MPa
Time at the end of filling                           =       0.6812 s
Clamp force - maximum                                =       5.4321 tonne
Total weight (part + runners)                        =      12.3456 g
```
- 문구와 위치는 버전, 해석 종류, 언어마다 다르다. 첫 케이스의 `mf_output_all.json`(모든 `라벨 = 값 단위` 줄)을 보고 `mf_parse_output.py`의 `KEY_PATTERNS`를 맞춘다. TODO(사내검증)
- 오류/경고 줄은 `** ERROR nnnnn **`, `** WARNING nnnnn **` 형태로 가정한다. TODO(사내검증)
