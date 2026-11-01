# 설계 기록

집 PC에서 정한 설계와 사용자 결정 원본이다. 앞으로 할 일의 근거이므로 구현 전에 해당 문서를 읽는다.

**읽을 때 주의**
- 코드가 먼저다. 이미 구현한 부분은 코드와 `docs/product-spec.md`가 최신이다.
- 옛말이 남아 있다. 지금 화면 말로 바꿔 읽는다.

| 문서의 말 | 지금 말 |
|---|---|
| 프로젝트 | Project |
| 작업 | Task (코드 이름은 `Work`) |
| 작업 흐름 | Task Flow |
| `W01_` 폴더 | `T01_` 폴더 |
| `이 작업을 내 PC에서 이어서 하기`, 이어받기 | `내 PC로 복사해서 하기` |

| 문서 | 내용 | 지금 상태 |
|---|---|---|
| `DECISIONS-work.md` | Project > Task, 복사, 승인 뒤 끝까지 진행. **사용자 결정 원본** | 대부분 구현. 떠난 사람 처리는 보류 |
| `work-final.md`, `work-reader.md` | Task 구조 최종 설계와 검토 | 1~6, 8단계 구현. 7단계(입력 안 막기)와 9단계 일부가 남음 |
| `situ-final.md` | 상황 지도: 장치 M1~M8, 13단계 순서, 시험 X1~ | 표 `## 6`의 4·6·7·9·11단계가 남음 |
| `geom-final.md` | 비매개변수 형상 학습 | 구현 전. 7절 단계 1~5 |
| `market-final.md`, `DECISIONS-marketplace.md` | 스킬 마켓 첫 설계 | **대부분 뒤집힘.** 사용자는 관리자 관리 + 카드 + 토글을 원한다. `docs/market-package-spec.md` 맨 위가 최신 |
| `DECISIONS-insights.md` | 판단 도우미 결정 | 구현 |
| `DECISIONS-formulation.md`, `DECISIONS-later.md` | 문제 설정 카드, 미룬 결정 | 구현 전 |
| `DESIGN.md` | 진입 시나리오 설계 | 구현 |
