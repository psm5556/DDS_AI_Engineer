---
name: simlab-automation
description: Altair SimLab 자동화 — SimLab에서 녹화한 파이썬 저널(조작 기록)을 템플릿으로 삼아 {{변수}}(두께·메쉬 크기·하중·재료)를 채운 뒤 SimLab 배치(-auto)로 메쉬·해석 설정·솔버 실행·결과 내보내기를 반복하고, 저널이 남긴 결과 CSV를 results.json으로, 내보낸 그림을 images/로 정리한다. 사용자가 SimLab으로 메쉬·전처리·해석 프로세스 자동화, SimLab 모델 파라미터 DOE, 녹화한 SimLab 작업을 반복 실행하자고 할 때 사용.
---

# Altair SimLab 자동화 (녹화 저널 + 배치)

## 1. 언제 쓰나 / 전제
- SimLab의 "프로세스 기반" 전처리(형상 정리 → 메쉬 → 접촉·하중 → 솔버 → 결과)를 **같은 순서로 값만 바꿔** 반복할 때 쓴다.
- 전제
  - Altair SimLab이 설치되고 라이선스가 있다. PC Agent가 `SimLab*\bin\win64\SimLab.bat`을 기본 위치에서 찾는다.
  - SimLab의 **Record Script(저널 녹화)**로 한 번 작업을 녹화한 `.py`가 있다. SimLab 저널은 `simlab.execute(...)` 호출로 기록된다.
  - TODO(사내검증): 배치 실행 인자(`-auto` 외에 GUI 없이 실행하는 옵션)와, 저널 안의 파일 경로를 상대 경로로 바꿀 수 있는지

## 2. 실행 방법
| tool 키 | 명령 템플릿 | 용도 |
|---|---|---|
| `simlab` | `{simlab} -auto {script}` | 저널을 그대로 배치 실행 |
| `python` | `{python} {script} {args}` | `scripts/simlab_render_run.py` — 템플릿 채우기 → 배치 실행 → 결과 정리 |

`simlab_render_run.py` 인자:
- `--template journal.py.tmpl`
- `--out case_journal.py`
- `--results results.csv`: 저널이 쓰는 `이름,값` CSV
- `--require <파일>`: 반드시 생겨야 하는 결과 파일. 여러 개면 반복한다.
- `--timeout 7200`

```json
{"tool": "python", "script": "scripts/simlab_render_run.py",
 "args": ["--template", "bracket_journal.py.tmpl", "--results", "results.csv", "--require", "bracket.op2"],
 "inputs": ["models/bracket.x_t", "models/bracket_journal.py.tmpl"], "outputs": ["max_stress", "mass"]}
```

## 3. 템플릿 만들기
1. SimLab에서 Record Script를 켜고 한 번 작업한다. 녹화된 `.py`를 `<이름>.py.tmpl`로 저장한다.
2. 바꿀 값(예: `<Thickness>2.0</Thickness>`의 2.0)을 `{{thickness}}`로 바꾼다. 이름은 params.json 키와 같아야 한다.
3. 파일 경로는 케이스 폴더 기준 상대 경로로 바꾼다. `{{case_dir}}` 자리표시자를 쓰면 케이스 폴더 절대 경로로 채워진다.
4. 저널 끝에 결과를 `results.csv`(`이름,값` 줄)로 쓰는 짧은 파이썬을 붙인다. 예: 최대 응력·질량
   - SimLab 결과 조회 API로 읽거나, 솔버 결과 파일을 다른 스킬(예: `msc-nastran-automation`의 op2 추출)로 읽는다.
5. 남은 `{{…}}`가 있으면 실행 전에 실패한다.

## 4. 실패 감지 · 결과
- 실패 판정
  - SimLab 종료 코드가 0이 아님
  - 로그(`simlab.log`)에 `Error`/`Traceback`
  - `--require` 파일이 없음
  - 결과 CSV 없음
- results.json: CSV의 `이름,값` 줄 전부(숫자만)
- 저널이 `images/`에 그림을 내보내면 그대로 결과 이미지가 된다.

## 5. 사내 검증이 필요한 것 (TODO(사내검증))
- 배치 인자, 종료 코드, 로그 위치
- 녹화 저널을 다른 형상 파일로 다시 쓸 때 개체 이름·id가 바뀌어 실패하는지(형상이 바뀌는 DOE에서 특히)
- 결과를 숫자로 읽는 SimLab 파이썬 API 이름
