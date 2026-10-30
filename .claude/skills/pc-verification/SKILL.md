---
name: pc-verification
description: Linux 서버에서 DDS Conversa를 개발하는 AI가 사용자 PC의 실제 CAD/CAE 툴 연동을 스스로 시험하고 고치는 방법. 서버에는 툴이 없으므로 scripts/pc_verify.py로 PC Agent를 통해 시험 스크립트·Live Link·웹앱 대화를 실행하고 결과를 받아 온다. "실제 툴에서 확인", "NX에서 되는지 시험", "사내검증", "TODO(사내검증)", "Live Link 연동 검증" 요청이나 툴 연동 코드를 고친 뒤에 사용.
---

전체 내용은 [docs/pc-verification.md](../../../docs/pc-verification.md)에 있다. 먼저 읽는다.

핵심 순서: `pc_verify.py targets` → 고치기 → pytest → `deploy` → `suite --tool <툴>` 또는 `chat "요청"` → `logs`·`live`·`run`으로 원인 확인 → 반복 → `TODO(사내검증)` 제거.

사람에게 눌러 보고 결과를 알려 달라고 하지 않는다. `targets`가 비어 있을 때만 PC 연결과 허용 설정을 부탁한다.
