# Creo 자동화 노트 (Creoson / trail / mapkey)

- **[확인]**: creopyson 문서나 녹화 결과에서 흔히 보이는 호출이다.
- **TODO(사내검증)**: Creoson·Creo 버전에 따라 다를 수 있다.

## 1. Creoson 구성
- CreosonServer: Creo J-Link를 감싼 Java 서버다. JSON 요청을 HTTP(기본 `http://localhost:9056/creoson`)로 받는다.
- CreosonSetup 도구로 Creo 설치 경로를 지정하면 `nitro_proe_remote.bat`이 만들어진다. 이 배치로 띄운 Creo에 Creoson이 붙는다.
- 요청 형식(creopyson 없이도 가능):
  ```json
  {"sessionId": "...", "command": "parameter", "function": "set",
   "data": {"file": "bracket.prt", "name": "THICKNESS", "value": 2.5}}
  ```
  첫 요청은 `{"command": "connection", "function": "connect"}`로 sessionId를 받는다. [확인]

## 2. creopyson 호출
```python
import creopyson
c = creopyson.Client(ip_adress="localhost", port=9056)   # [확인] 인자 이름 철자 'ip_adress'
c.connect()                                              # [확인]
c.start_creo("C:/creoson/nitro_proe_remote.bat")         # [확인] TODO(사내검증) retries/use_desktop
c.creo_set_creo_version(7)                               # TODO(사내검증) Creo 7+ 필요 여부
c.is_creo_running()                                      # [확인]
c.creo_cd("C:/case")                                     # [확인] 작업 폴더
c.file_open("bracket.prt", dirname="C:/case", display=True)   # [확인]
c.file_get_active()                                      # [확인] TODO(사내검증) 반환 형식
c.parameter_list(file_="bracket.prt")                    # [확인] [{"name","type","value",...}]
c.parameter_set("THICKNESS", 2.5, file_="bracket.prt", no_create=True)  # TODO(사내검증) no_create
c.dimension_list(file_="bracket.prt")                    # [확인]
c.dimension_set("d12", 2.5, file_="bracket.prt")         # [확인]
c.file_relations_get(file_="bracket.prt")                # TODO(사내검증)
c.file_regenerate(file_="bracket.prt")                   # [확인]
c.file_massprops(file_="bracket.prt")                    # [확인] TODO(사내검증) 키 이름
c.feature_list(file_="bracket.prt", status="failed")     # TODO(사내검증) 실패 피처 조회
c.interface_export_file("STEP", filename="bracket.stp", dirname="C:/case/export", file_="bracket.prt")  # [확인]
c.interface_export_image("JPEG", filename="iso.jpg", height=6, width=8, dpi=100)  # [확인]
c.interface_mapkey("~ Command `ProCmdRegenPart`;")       # [확인] mapkey 문자열 실행
c.file_save(file_="bracket.prt"); c.file_close_window(file_="bracket.prt"); c.file_erase(file_="bracket.prt")
c.kill_creo()                                            # TODO(사내검증)
c.disconnect()
```

## 3. trail 파일
- Creo는 세션마다 작업 폴더에 `trail.txt.N`을 기록한다. 이 파일을 `-i:rpc_input`(또는 `parametric trail.txt`)로 재생하면 같은 작업을 반복한다.
- `parametric.exe -g:no_graphics -i:rpc_input trail.txt`: 그래픽 없이 재생한다. TODO(사내검증): 사내 버전에서 옵션 이름과, 그래픽 의존 단계(뷰·이미지)가 있으면 실패하는지
- 편집 요령
  - 대화상자 입력값이 줄 단위로 기록된다. 바꿀 값만 `@@name@@`으로 바꾼다.
  - 절대 경로는 케이스 폴더 기준으로 바꾼다(드라이버가 cwd를 케이스 폴더로 실행).
  - 끝에 종료 명령이 없으면 Creo가 떠 있는 채로 남아 타임아웃이 난다.
  - `!` 로 시작하는 줄은 주석/정보다. TODO(사내검증)
- 덮어쓰기 확인 대화상자를 피하려고 드라이버는 출력 STL을 미리 지운다.

## 4. mapkey
- config.pro의 `mapkey <키> ~ Command \`ProCmd...\`;` 형식이다. GUI에서 File → Options → Mapkeys로 녹화한다.
- Creoson `interface_mapkey(script)`로 실행할 수 있다. 녹화한 mapkey 본문 문자열을 그대로 넘긴다.

## 5. 질량특성 메모
- 재료 지정 또는 `PRO_MP_DENSITY` 파라미터가 있어야 질량이 의미가 있다.
- 단위는 모델 단위계(Setup → Units)를 따른다. mmNs 템플릿이면 질량 단위가 tonne이다. TODO(사내검증)
- relations에서 `MP_MASS("")`, `MP_VOLUME("")` 같은 질량특성 함수를 파라미터로 받아 두면 `parameter_list`로도 읽을 수 있다. TODO(사내검증): 함수 이름과 재생성 시 갱신 조건
