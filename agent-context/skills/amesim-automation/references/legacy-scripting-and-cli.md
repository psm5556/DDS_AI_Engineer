# Simulation Scripting(`amesim` 모듈), 명령행, 파일, 환경

## 1. Simulation Scripting (`amesim` 모듈)
**이름 확인 상태.** Siemens 예제 스크립트의 용어집, Volupe 튜토리얼, 중국어 사용자 예제에서 아래 이름을 확인했다. **시그니처(인자 순서, 반환 형식)는 확인하지 못했다**. 전부 TODO(사내검증)이다.

| 함수 | 용도(출처 기준) |
|---|---|
| `ameputp` | 서브모델이나 슈퍼컴포넌트의 파라미터 값 설정 |
| `amegetsimopt` | 실행 옵션 객체 가져오기(종료시간, 출력간격, 최대 스텝, 허용오차) |
| `amerunsingle` | 단일 실행(실행 옵션을 인자로 전달) |
| `ameloadvarst` | 지정한 변수의 시계열 결과와 변수 이름 로드 |
| `ameputbatch` / `amerunbatch` | 파라미터 dict로 배치(Study Manager) 세트를 만들고 실행 |
| `amegetparamnamefromui` / `amegetvarnamefromui` | UI 표기에서 고유 파라미터 이름, 변수 이름 얻기 |
| `amereadgp` | Global Parameter 읽기(`amesim.amereadgp(sys_name, run_id)`, Siemens KB) |

- **`amesetp`, `amegetp`, `ameloadt`, `amerun`은 확인되지 않은 이름이다.** 쓰지 않는다.
- 전제: 모델을 먼저 체크, 컴파일, 언팩해야 한다. Siemens 커뮤니티 답변에 따르면 `AMECirChecker`(옵션 `-g`는 컴파일 포함)와 `AMELoad`로 처리한다. 예: `AMECirChecker -g model.ame`. TODO(사내검증): 정확한 옵션.
- 아래는 의사코드다. 인자는 설치본 help로 확인한 뒤에 쓴다.
  ```python
  # import amesim
  # sname = "model"                      # 확장자 없는 시스템 이름(언팩된 폴더 기준) -- TODO(사내검증)
  # amesim.ameputp(sname, "k@elect01", 10.0)        # TODO(사내검증)
  # opt = amesim.amegetsimopt(sname)                # TODO(사내검증): 속성 이름(finalTime/printInterval?)
  # amesim.amerunsingle(sname, opt)                 # TODO(사내검증)
  # res = amesim.ameloadvarst(sname, ["x1@mass_friction2port"])   # TODO(사내검증): 반환 구조
  ```
- 새 스크립트는 `ame_apy`(Circuit API)로 작성한다. Simulation Scripting은 기존 사내 스크립트를 유지보수하거나 Runtime 라이선스만 있을 때 쓴다.

## 2. 명령행
- Python 스크립트: `AMEPython script.py [args]`. 경로는 `%AME%\win64\AMEPython.exe`이다.
- 모델 체크와 컴파일: `AMECirChecker -g model.ame`(TODO(사내검증))
- 언팩: `AMELoad model.ame`(TODO(사내검증))
- 컴파일된 모델 실행 파일(`<model>_.exe`)을 직접 실행하는 방식이나 `AMERun` 같은 별도 실행기는 공개 자료에서 확인하지 못했다. TODO(사내검증). 플랫폼에서는 Python API 방식만 쓴다.
- GUI 없이 배치 API를 써야 한다. Siemens KB에 따르면 라이선스가 하나뿐이면 GUI의 Python interpreter에서는 API를 쓸 수 없고 명령 프롬프트 실행을 권한다.

## 3. 모델 관련 파일 (TODO(사내검증): 버전별 확장자)
| 파일 | 내용 |
|---|---|
| `model.ame` | 모델 아카이브(스케치, 서브모델, 파라미터, 결과를 묶은 파일) |
| `model_.data` | 파라미터 값(언팩 시) |
| `model_.results` | 시뮬레이션 결과(바이너리) |
| `model_.sim` 등 | 실행 설정 |

- `.ame`을 열거나 실행하면 같은 폴더에 작업 파일이 생긴다. 반드시 케이스 폴더의 복사본으로 작업한다.

## 4. 환경 설정
- 공개 래퍼가 쓰는 설정을 따랐다. TODO(사내검증): 사내 설치에 맞춰 확인한다.
  ```bat
  set AME=C:\Program Files\Simcenter\2021.1\Amesim
  set PATH=%AME%;%AME%\win64;%AME%\win32;%PATH%
  rem 라이선스 (사내 값)
  set LMS_LICENSE=<port@server>
  set SC1D_LICENSING_TYPE=<사내 설정값>
  "%AME%\win64\AMEPython.exe" run_case.py model.ame
  ```
- 플랫폼 `config.toml` 권장값은 다음과 같다.
  - `[tools] amesim_python = 'C:\Program Files\Simcenter\<ver>\Amesim\win64\AMEPython.exe'`
  - 플랫폼이 툴별 환경변수 기능을 지원하면 `AME`와 `PATH`를 같이 지정한다. 지원하지 않으면 위 내용을 담은 `.bat` 래퍼를 `amesim_python`으로 지정한다(`%*`로 인자 전달).
- 라이선스:
  - Simulation Scripting은 Runtime 라이선스가 필요하다.
  - Circuit API는 GUI와 Libraries 라이선스가 필요하고, `AMEInitAPI`에서 체크아웃된다.
  - 실행 중 라이선스는 프로세스가 끝나거나 `AMECloseAPI()`가 호출될 때까지 점유된다.
