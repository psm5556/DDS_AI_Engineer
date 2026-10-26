# Abaqus .inp 템플릿 치환

Abaqus 입력은 **콤마 구분 자유형식**이다(한 줄 최대 256자). 필드 폭 제약이 없어서 텍스트 치환이 안전하다. 단, 절점 좌표는 바꿀 수 없으므로 형상 변경에는 쓸 수 없다. 두께, 재료, 하중, BC, 스텝, 출력 요청만 바꾼다.

## 템플릿 만들기
1. CAE에서 모델 완성 → `mdb.jobs['Job-1'].writeInput(consistencyChecking=OFF)` 또는 GUI Write Input.
2. 생성된 `Job-1.inp`를 `xxx_template.inp`로 복사하고 바꿀 값을 다음 두 방법 중 하나로 표시한다.

### 방법 1: 플레이스홀더
```
*Shell Section, elset=Set-Skin, material=Steel
{{thickness}}, 5
*Material, name=Steel
*Elastic
{{youngs_modulus}}, 0.3
*Cload
LOADPT, 2, -{{force}}
```
- `abq_inp_run.py`가 `params.json` 값으로 치환한다. 값이 없는 플레이스홀더가 남으면 오류로 종료한다.
- `-{{force}}`처럼 부호를 붙이면 음수 입력 시 `--1000.0`이 되어 오류가 난다. 부호는 params 값에 포함시키는 편이 안전하다.

### 방법 2: *PARAMETER (Abaqus 표준 기능)
```
*Heading
*PARAMETER
thickness = 2.5
force = 1000.
half_t = thickness / 2.
** ...
*Shell Section, elset=Set-Skin, material=Steel
<thickness>, 5
*Cload
LOADPT, 2, <force>
```
- `*PARAMETER` 블록의 `name = value` 줄은 `abq_inp_run.py`가 params.json 값으로 바꾼다. 수식 줄(`half_t = ...`)은 그대로 두면 Abaqus가 계산한다(Python 문법 표현식).
- 본문에서는 `<name>`으로 참조한다. 이름은 대소문자를 구분한다.
- `*PARAMETER`를 쓴 inp는 CAE로 import할 수 없다. 디버깅할 때는 플레이스홀더 방식이 편하다.
- Abaqus 자체 파라메트릭 스터디(`abaqus script=study.psf`)도 있지만 DDS 플랫폼에서는 DOE를 플랫폼이 관리하므로 쓰지 않는다.

## *INCLUDE로 분리
큰 메쉬는 `*INCLUDE, INPUT=mesh.inp`로 분리하고, 바뀌는 카드만 템플릿에 둔다. 스터디 `inputs`에 mesh.inp도 함께 넣어야 케이스 폴더로 복사된다.

## 실행과 판정
```
abaqus job=case input=case.inp cpus=4 interactive ask_delete=OFF
```
- `interactive`: 끝날 때까지 대기한다. `ask_delete=OFF`: 기존 결과를 묻지 않고 덮어쓴다.
- 판정: `case.sta`에 `THE ANALYSIS HAS COMPLETED SUCCESSFULLY`. 오류는 `case.dat`/`case.msg`의 `***ERROR`.
- 입력 문법만 확인: `abaqus job=case input=case.inp datacheck interactive`.

## 질량 (inp 방식)
- 필드 출력에 `EVOL`(요소 체적)을 추가하고(`*Element Output` 아래 `EVOL`), `abq_odb_extract.py --density 7.85e-9`로 질량을 구한다(단일 재료일 때).
- 여러 재료면 요소셋별로 `EVOL`을 합산해 각 밀도를 곱한다.
- TODO(사내검증): `.dat`의 모델 총질량 출력 여부와 문구(버전, `*Preprint` 설정에 따라 다르다).
