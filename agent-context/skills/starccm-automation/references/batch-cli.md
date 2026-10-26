# STAR-CCM+ 배치 실행, 명령행, 라이선스

## 기본 형태
```
starccm+ -batch <macro.java> [옵션] <model.sim>
```
- Windows의 실행 파일은 대개 `<설치경로>\STAR-CCM+<버전>\star\bin\starccm+.exe`이고, Linux는 `.../star/bin/starccm+`이다. 플랫폼 `config.toml`의 `[tools] starccm`에 전체 경로를 적는다.
- 여러 매크로를 순서대로 돌리려면 `-batch a.java,b.java`처럼 쉼표로 이어 쓴다. TODO(사내검증): 사용하는 버전에서 이 형식이 지원되는지 확인한다.
- 매크로 없이 sim에 저장된 설정 그대로 실행하려면 `-batch run`을 쓴다. 메쉬부터 하려면 `-batch mesh,run`이다.

## 자주 쓰는 옵션
| 옵션 | 의미 |
|---|---|
| `-np N` | 병렬 프로세스 수 |
| `-machinefile file` | 클러스터 노드 목록 |
| `-mpi <intel/openmpi>` | MPI 종류. 버전 기본값이 있으므로 문제가 있을 때만 지정한다 |
| `-power` | Power Session 라이선스 사용 |
| `-podkey KEY` | Power-on-Demand 키 |
| `-licpath port@host` | 라이선스 서버. 예: `1999@flex.cd-adapco.com`(POD), 사내 서버는 `27000@lic-server` 등 |
| `-batch-report` | 배치 실행 요약 리포트(HTML) 생성. TODO(사내검증) |
| `-new` | 새 sim으로 시작(sim 파일 대신 사용) |

옵션 순서는 대체로 자유롭다. 다만 sim 파일은 마지막에 두는 것을 관례로 한다.

## 플랫폼 tool 명령 예시
- 기본 키: `starccm` = `{starccm} -batch {script} {args}`이고, args 예시는 `["-np","8","model.sim"]`이다.
- 사내 라이선스 옵션이 항상 같다면 다음 키를 추가해 args를 짧게 할 수 있다.
  - `starccm_power` = `{starccm} -power -podkey {starccm_podkey} -licpath {starccm_licpath} -batch {script} {args}`
- 매크로 없는 단순 재실행용 키:
  - `starccm_run` = `{starccm} -batch run {args}`

## 환경
- 라이선스 환경변수: `CDLMD_LICENSE_FILE`(`port@host`)를 쓰고, POD를 쓰면 `-podkey`/`-licpath`를 추가한다. TODO(사내검증): 사내에서 실제로 쓰는 변수와 서버를 확인한다.
- 해석 도중 라이선스가 끊기면 STAR-CCM+는 대기하거나 종료한다. 대규모 DOE에서는 동시 케이스 수를 라이선스 수 이하로 제한한다.

## 로그와 실패 판정
- 표준출력이 플랫폼 `run.log`에 기록된다. 매크로의 `sim.println()` 출력도 여기에 나온다.
- 매크로 컴파일 오류가 나면 로그에 `.java:<줄번호>: error: ...` 형식으로 찍힌다.
- 성공 판정은 `results.json` 존재 여부로 한다. 템플릿은 모든 단계가 끝난 뒤에만 이 파일을 쓴다.
