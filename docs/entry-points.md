# 어느 시점에서든 진입: 시나리오와 검증

사용자는 처음부터 시작하지 않을 수 있다. 표만 있거나, 해석을 이미 끝냈거나, 지난 과제를 신규 모델로 다시 하거나, 동료의 작업을 이어받는다. 이 문서는 그 진입점을 시나리오로 적고, 무엇이 통과이고 어디서 시험하는지를 적는다.

**표 입력은 붙여넣기가 기본이다.** 회사의 CSV·엑셀 파일은 보안 프로그램으로 암호화되어 있어 서버와 PC 어느 쪽도 읽지 못한다. 파일을 읽는 경로는 암호화되지 않은 파일과 결과 폴더(`results.json`·`case.h5`)에 쓰고, 읽지 못하면 붙여넣기를 안내한다.

## 1. 시험하는 세 층

| 층 | 위치 | 재는 것 | 재지 못하는 것 |
|---|---|---|---|
| API | `server/tests/test_entry_points.py` | 서버 동작, 검증 문구, 버전·공유·권한 | MCP·PC Agent |
| MCP 함수 | `agent/tests/test_mcp_entry_points.py` | 도구 인자·반환, 데모의 도구 순서, 지침 일관성 | 프로세스 사이 전달, 실제 PC Agent |
| 실제 프로세스 | `scripts/e2e_scenarios.py` | uvicorn + PC Agent(사람마다 하나) + 데모 CLI(ACP) + MCP stdio. 측정값을 표로 남긴다 | **실제 AI의 판단** |
| 실제 AI | `scripts/pc_verify.py chat` | 실제 Claude·Gemini·Codex가 규칙대로 하는지 | 자동으로 돌지 않는다. 사용자 PC에서 돌린다 |

데모 CLI는 정해진 규칙으로 도구를 부른다. 그래서 e2e는 "길이 뚫려 있는가"를 증명하고, "AI가 그 길을 고르는가"는 증명하지 못한다. 후자는 4절의 명령으로 잰다.

## 2. 시나리오

| ID | 상황 | 통과 기준 | API 시험 | MCP 시험 |
|---|---|---|---|---|
| E0 | 처음부터 전체(대조군) | Flow 제안 뒤 멈춤, 승인 전 스터디 0개. 승인 뒤 전 단계 완료, 권한 요청 1회, 배포 | `server/tests/test_api.py` `test_doe_train_bo_on_server_mock` | `test_demo_flow_review_does_not_block_other_requests` |
| E1 | 엑셀에서 복사한 표 붙여넣기 → Simulator | PC Agent·AI 꺼진 채 미리보기·등록·학습·배포·예측. 해석 0건, 대화 0개, 처음 보는 점 오차 15% 이내. 수식 오류 행은 그 출력에서만 빠지고 고른 출력은 모델에 남는다 | `test_paste_to_simulator_without_agent_only_outputs_given`, `test_paste_*`, `test_e1_default_training_keeps_chosen_output_with_one_bad_cell` | `test_e1_table_to_simulator_without_agent_or_solver`, `test_pasted_table_attachment_*` |
| E1a | 입력창의 표를 AI로 보내기 | **묻는 것은 출력 하나뿐.** 답을 받기 전에는 등록하지 않고, 출력을 적으면 묻지 않는다. 행은 첨부 파일로 가고 도구 인자에 없다. Flow 0개, 도구 순서 inspect → import → train → deploy | — | `test_demo_pasted_table_*`, `test_demo_other_request_after_the_question_*`, `test_demo_table_request_*`, `test_table_read_is_not_done_by_passing_rows_to_the_ai` |
| E1b | 엑셀 복사 형식과 암호화 파일 | 천 단위 쉼표·퍼센트·빈 행·따옴표 안 줄바꿈·병합 셀 빈칸·수식 오류·머리글 없음·제목 줄을 알아서 읽는다. 암호화된 CSV·XLSX는 `inspect_table`·`import_table`·서버·대화 모두 깨진 표나 500 없이 "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요". 스터디가 생기지 않는다 | `test_paste_reads_what_excel_copies`, `test_encrypted_*`, `test_tabular_*` | `test_encrypted_table_*`, `test_demo_encrypted_table_gets_the_paste_answer_in_chat` |
| E2 | 해석 폴더 N개 → Simulator | 케이스 N건, 재해석 0건, 원본 파일 해시 변화 0, 다시 돌리기는 400 | `test_e2_folders_register_and_never_rerun` | `test_e2_scan_then_import_*` |
| E2b | 입력·출력 파일 분리 | 번호 열로 이어 출력 있는 행만 등록, 결합 결과 보고 | `test_e2b_inputs_and_outputs_in_two_files` | `test_e2b_outputs_in_a_separate_file` |
| E3 | 붙여 넣은 표 + 붙여 넣은 행 추가 | 새 버전 v2, 범위 넓힘, 공유 링크가 v2를 가리킴. 후속 턴 첫 문단을 잃지 않음 | `test_e3_add_rows_*` | `test_e3_add_rows_*` |
| E3b | 같은 표 두 번 붙여넣기 | 화면 경로·대화 경로 모두 스터디 1개, 행 그대로, 중복 수 보고, 새 버전 0 | `test_e3b_same_table_twice_no_duplicates` | `test_e3b_*` |
| E4 | 붙여 넣은 표 모델 → 최적화 | 후보만, 전부 `verified=false`, "확인되지 않은 예측" 문구, 케이스 증가 0 | `test_e4_*` | `test_e4_*` |
| E5 | 나쁜 표 붙여넣기 | NaN·Inf·엑셀 오류는 이유와 함께 제외, 상수는 고정값, 문자 열은 뺌, 이상치 경고, 3행 미만·빈 글은 400. 한두 행은 "3행 이상"이 이유로 나온다. 어디서도 500 없음, NaN 저장 없음. 대화에서도 이유와 할 일 | `test_e5_*`, `test_train_errors_are_plain_and_never_500` | `test_e5_*`, `test_tool_failure_text_reaches_the_ai` |
| E6 | Simulator → Output 추가 → 되돌리기 | 재해석 없이 전 케이스 추출, 새 버전에 새 Output, 옛 버전으로 되돌리면 예측 그대로 | `test_e6_*`, `test_report_outputs.py` | `test_demo_flow_*` |
| E7 | 결과만 → 보고서 | Flow 0개, 권한 요청 0, Word를 받음. 결과가 없으면 무엇이 필요한지 안내 | `test_e7_*`, `test_report_outputs.py` `test_ai_report` | `test_demo_report_from_existing_results_needs_no_flow` |
| E8 | 단순 조회 | Flow 0개, 읽기 도구만, 케이스 변화 0 | `test_e8_*` | `test_demo_simple_question_*` |
| E9 | 툴 missing + 붙여 넣은 표 | 툴이 없어도 표 → 배포, 데이터 작업에 툴 확인이 끼지 않음. 없는 툴로 해석하면 실패 이유와 할 일이 남음 | `test_e9_*` | `test_e9_*` |
| E10 | Flow 검토 중 다른 요청 | 질문·표 요청을 바로 처리, Flow는 검토 중 그대로, 뒤늦게 승인해도 끝까지 | — | `test_demo_flow_review_does_not_block_other_requests` |
| E11 | 세션 유지 | 후속 턴 CLI 재시작·load 0, 재연결·서버 재시작 재시작 0, CLI 사망 뒤 복구 | `agent/tests/test_sessions_stack.py` | `scripts/e2e_sessions.py` |
| E12 | DOE 계획만 → 나중에 | CSV 받기, 해석 방법 없이 돌리면 이유와 할 일, 밖에서 해석한 값 채움(**엑셀을 거쳐 자릿수가 줄어도** case_id 또는 자릿수로 같은 점을 찾고 입력값은 계획의 정확한 값을 지킴), 연결 뒤 남은 점만 돌림(가져온 값은 다시 안 돌림) | `test_e12_*`, `test_imported_rows_are_not_rerun_after_connecting`, `test_doe_fill_back_*` | `test_e12_*`, `test_demo_plan_and_connect_later` |
| E13 | 필드 결과 → 3D Simulator | 스칼라 없어도 `{필드}_max`를 만들어 배포, 3D 예측, 원본 변화 0 | `test_e13_*` | `test_e13_*` |
| E14 | 다른 프로젝트 스터디 | 내가 멤버인 프로젝트에서 찾고 설정만 복제. 멤버가 아니면 403 | `test_e14_*` | `test_e14_*` |
| E15 | 신규 모델, 같은 흐름 | 새 모델 없으면 먼저 부탁. 달라진 점을 승인 카드에, 승인 전 해석 0. 새 스터디 v1, **옛 케이스·모델·설정·범위·공유 링크 예측이 전부 그대로** | `test_e15_*` | `test_e15_*`, `test_demo_same_flow_again_*` |
| E15b | 신규 모델이 실제로 쓰임 | 입력 모델의 값이 답을 정하는 스크립트로 시험. 새 스터디 `adapter_config.inputs`가 새 파일, 해석 값이 새 모델에서 나옴, 옛 케이스·설정 불변 | `test_clone_recipe_reports_from_its_own_config` | `test_clone_with_new_model_*`, `test_demo_new_model_clone_uses_the_attached_model` |
| E16 | 다른 멤버가 내 PC 없이 이어받기 | 아래 3절 | `test_e16_*`, `test_recipe_*`, `test_other_members_script_*` | `test_e16_*`, `test_recipe_*`, `test_demo_takeover_*`, `test_script_is_shared_*` |
| E16b | 실행 중에 내 PC가 꺼짐 | 실행 중으로 남은 이유가 보이고(`stalled`), 멤버의 중지·넘겨받기는 403. 잠깐 끊겨도 솔버가 끝나지 않고 끊긴 사이의 결과까지 받는다. 실행한 사람이 중지하면 다시 연결될 때 솔버를 끝내고 정리된 상태 유지. 다시 돌리기가 실패해도 이전 값 유지, 다른 사람의 스크립트는 내가 승인한 흐름 없이 돌지 않음 | `test_cases_on_a_dead_pc_*`, `test_reconnecting_agent_*`, `test_failed_rerun_keeps_previous_results`, `test_case_stop.py` | `test_case_link_drop.py` |
| PV | `pc_verify chat` 자체 | 첨부, 같은 대화 이어 가기, 합격 판정과 종료 코드, 측정값 저장, 다른 대화의 Flow를 승인하지 않음, `--no-approve` | — | `test_pc_verify_*` |
| E1c | 표로 Simulator를 만드는 동안 | 등록·학습·배포는 서버 작업 하나다. 새로고침해도 끝까지 가고 돌아온 화면에 '만드는 중'·'만들었습니다 [열기]'가 보인다. 그동안 입력창은 AI로 보내지 않고 이유를 말한다. 둘째 표는 받지 않았다고 말하고 끝난 뒤 바로 묻는다. AI가 일하는 중에 붙여 넣으면 지금 할 수 있는 일을 말한다. 프로젝트를 지우면 그 작업의 알림이 사라지고 다시 시도하라고 하지 않는다. 등록 중에 서버가 다시 시작되면 작업이 만든 프로젝트가 비어 있을 때 지운다 | `server/tests/test_table_job.py` | `frontend/e2e/table_job.mjs`, `frontend/e2e/waiting.mjs` |
| UI | 브라우저, PC Agent 없음 | 표를 받는 파일 선택이 없다. 첫 화면·데이터 탭의 붙여넣기 창, 입력창 붙여넣기(요청 글에 행이 들어가지 않음)로 Simulator까지. 묻는 것은 출력 칩뿐. 넓은 Simulator의 **프로젝트 열기**와 끝난 표 작업 알림의 **열기**가 같은 스터디·버전의 Simulator 탭을 열고(대화가 있는 프로젝트도) 새로고침 뒤에는 강제하지 않는다. 공유 링크에는 그 버튼이 없다 | `frontend/e2e/smoke.mjs` | — |
| UId | 브라우저 + 실제 Agent·데모 CLI | 전체 과정(CAD→보고서), 입력창의 표를 AI로 보내 Simulator까지, DOE CSV 받기 | `frontend/e2e/smoke.mjs --demo` | — |

## 3. E16: 내 PC가 꺼져 있을 때 멤버가 할 수 있는 일

| 일 | 어디서 | 결과 |
|---|---|---|
| 결과·표·케이스 값 조회, 재학습, Simulator 배포·공유, 보고서 | 서버 | PC 없이 된다 |
| 재해석 | **멤버 PC** | 서버의 스크립트 묶음을 받아서 돌린다. 묶음은 해석이 처음 끝날 때 PC Agent가 자동으로 올린다. 받기만 하고, 승인 카드와 권한 요청에 "○○님이 만든 스크립트"로 보여 준 뒤에 돌린다. **서버가 확인한다:** 내가 승인한 흐름이 없으면 403이다. 끝난 케이스를 다시 돌리다 실패하면 이전 값이 남는다 |
| 실행 중에 내 PC와 끊긴 케이스 | 서버 | 데이터 탭에 이유가 보인다. 중지와 그 케이스 다시 돌리기는 실행한 사람만 한다(멤버는 403). 잠깐 끊긴 것이면 솔버는 계속 돌고 다시 연결되면 결과를 받는다. 내가 중지했으면 다시 연결될 때 솔버를 끝낸다 |
| 결과를 전하기 전에 내 PC Agent가 다시 시작 | 내 PC | 서버가 먼저 묻고, 그 PC의 `case.json`이 끝났다고 하면 그 결과를 받는다. 끝나지 않았을 때만 중지로 처리한다 |
| 입력 모델이 필요한 재해석 | 멤버 PC | 대화 첨부로 서버에 있는 파일만 받을 수 있다. 없으면 어떤 파일이 필요한지 안내하고 돌리지 않는다 |
| 내 PC 케이스에서 Output 추가 | 내 PC | 멤버는 하지 못한다. "○○님만 추가할 수 있습니다 → ○○님에게 요청하거나 '내 PC로 복사해서 하기'"라고 이유와 할 일을 말한다. 남의 PC에서 스크립트를 돌리지 않는다. 주인이 하면 주인의 PC에 있는 케이스만 뽑고, 멤버 PC의 케이스는 뽑지 않았다고 말한다. 서버의 `case.h5`로 대신 뽑는 경로는 없다(3D 학습은 서버 사본으로 된다) |

## 4. 실제 AI로 재기

e2e는 AI의 판단을 재지 못한다. 사용자 PC에서 아래를 돌려야 "AI가 표에 모델 파일을 묻지 않는다", "조회에 Flow를 제안하지 않는다"를 알 수 있다. 사용법은 [pc-verification.md](pc-verification.md).

```bash
python3 scripts/pc_verify.py chat "이 표로 바로 Simulator 만들어 줘" --agent claude --attach 표.tsv \
    --expect deployed,no_cases_run,flows=0,flows_approved=0,tool:import_table,no_tool:run_cases --max-first-reply 600
python3 scripts/pc_verify.py chat "이 표로 바로 Simulator 만들어 줘" --agent claude --attach 붙여넣은_표_1.tsv --no-approve --expect flows=0,questions=1,no_tool:import_table   # 출력만 묻고 멈추는가
python3 scripts/pc_verify.py chat "이 프로젝트에 모델 몇 개 있어?" --agent claude --session <대화 번호> --expect flows=0,no_cases_run,permissions=0
```

## 5. 돌리는 법과 결과

```powershell
.venv\Scripts\python scripts\e2e_scenarios.py            # 전부. --only E1,E3 --out 폴더 --doc(아래 표를 바꾼다)
.venv\Scripts\python -m pytest server/tests agent/tests -q
```
실제 Claude·Gemini·Codex·CAD를 띄우지 않고, 임시 폴더와 빈 포트를 쓰며, 창을 띄우지 않는다.

**돌리지 못한 것**
- 실제 AI CLI: 위 표의 모든 "대화"는 데모 CLI다. 실제 AI가 좁아진 승인 규칙을 따르는지는 아직 재지 않았다.
- 실제 CAD/CAE: 해석은 mock 또는 짧은 파이썬 스크립트다. 실제 결과 폴더의 odb·d3plot 추출은 재지 않았다.
- 두 PC: E16의 두 PC Agent는 한 컴퓨터의 두 프로세스다. 작업 폴더·토큰·계정은 다르지만 실제로 다른 컴퓨터는 아니다.
- 실제 암호화 파일: 사내 보안 프로그램이 만든 파일로는 재지 않았다. 알 수 없는 앞머리와 글자가 아닌 바이트로 흉내 냈다. 사내에서 실제 파일로 한 번 확인한다.
- 실제 엑셀 클립보드: 복사 형식은 엑셀이 내는 글을 손으로 만들어 넣었고, 브라우저에는 합성 붙여넣기 이벤트로 넣었다. 사용자 PC의 클립보드는 건드리지 않았다.
- `train_field_model`은 아직 긴 요청 하나다. 필드가 크면 Codex 도구 시간 제한에 걸릴 수 있다.
- 표 작업을 돌리는 중 서버가 다시 시작되는 경우는 함수 호출로만 시험했다(`test_restart_resumes_*`). 실제 프로세스 재시작으로는 재지 않았다.

<!-- 결과표 시작 -->
| ID | 결과 | 걸린 시간 | 확인 수 | 측정값 |
|---|---|---:|---:|---|
| E0 | 통과 | 41.2초 | 7 | cases=26; best_f=0.739; r2={"f": 0.988}; propose_sec=8.25; run_sec=31.43 |
| E1 | 통과 | 1.6초 | 14 | rows=25; solved=0; chats=0; fixed={"재질": "SS400", "하중": 1000.0}; r2={"최대 응력": 0.984, "효율": 1.0}; quality="good"; pred_error_pct=1.05; total_sec=0.99 |
| E1a | 통과 | 33.3초 | 11 | questions=1; first_reply_sec=7.98; answer_turn_sec=3.36; flows=0; r2={"최대 응력": 0.975, "효율": 1.0} |
| E1b | 통과 | 18.4초 | 26 | read={"천 단위 쉼표": {"행": 12, "등록": 12, "제외": 0}, "퍼센트": {"행": 12, "등록": 12, "제외": 0}, "빈 행·빈 열": {"행": 12, "등록": 12, "제외": 0}, "따옴표 안 줄바꿈": {"행": 12, "등록": 12, "제외": 0}, "병합 셀 빈칸": {"행": 12, "등록": 12, "제외": 0}, "수식 오류": {"행": 12, "등록": 10, "제외": 2}, "머리글 없음": {"행": 12, "등록": 12, "제외": 0}, "제목 줄·단위·한글": {"행": 12, "등록": 12, "제외": 0}}; encrypted_status={"암호화.xlsx": 400, "암호화.csv": 400}; encrypted_reply="보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요" |
| E2 | 통과 | 12.7초 | 6 | cases=16; solved=0; original_changed=0; r2={"s": 0.914}; turn_sec=10.44 |
| E2b | 통과 | 13.0초 | 3 | registered=12; join={"key": "id", "matched": 12, "only_left": ["12", "13"], "only_right": [], "duplicates_left": [], "duplicates_right": []}; excluded=0 |
| E3 | 통과 | 16.2초 | 4 | versions=[1, 2]; rows_v2=20; share_model_version=2; r2_v2={"stress": 0.975} |
| E3b | 통과 | 21.5초 | 6 | duplicates=12; models=1 |
| E4 | 통과 | 14.3초 | 5 | candidates=3; verified=false; cases_added=0; best_pred=7.708; best_true=8.176 |
| E5 | 통과 | 15.1초 | 44 | chat_reply="표를 불러오지 못했습니다. 데이터 행이 1개뿐입니다. 예측 모델에는 3행 이상이 필요합니다 → 행을 더 복사해 붙여 넣으세요" |
| E6 | 통과 | 73.7초 | 6 | cases=68; cases_resolved_for_output=0; version_with_output=10; rolled_back_to=9; flow_sec=50.69 |
| E7 | 통과 | 31.3초 | 4 | flows=0; reports=[1, 1]; turn_sec=9.09 |
| E8 | 통과 | 14.4초 | 5 | flows=0; tools=["list_models", "list_studies"]; first_reply_sec=[10.66, 0.28] |
| E9 | 통과 | 17.8초 | 6 | missing_tools=28; ready_tools=0; r2={"stress": 0.944}; missing_tool_error="명령 실패(None): abaqus cae noGUI= --\n실행 파일을 찾을 수 없습니다: abaqus ([WinError 2] 지정된 파일을 찾을 수 없습니다) → 웹앱 'CA" |
| E10 | 통과 | 47.0초 | 4 | question_sec=0.28; table_sec=3.01; flow_status_during="review"; studies=2 |
| E11 | 통과 | 63.8초 | 6 | first_turn_sec=8.78; next_turn_sec=[0.28, 0.28, 0.28]; follow_up={"turns": 3, "process_starts": 0, "session_new": 0, "session_load": 0}; reconnect={"process_starts": 0, "session_load": 0}; server_restart={"process_starts": 0, "session_load": 0}; cli_death={"process_starts": 1, "session_load": 1, "session_new": 0} |
| E12 | 통과 | 33.1초 | 9 | csv_rows=8; filled=4; solved_later=4; run_before_connect=400; reply_after_connect="해석 방법을 연결했습니다. 대기 중인 4점을 돌리는 작업 흐름을 올렸습니다. **승인**을" |
| E13 | 통과 | 15.8초 | 6 | cases=8; auto_output="mises_max"; r2={"mises_max": 1.0}; field_nodes=40 |
| E14 | 통과 | 12.0초 | 5 | found="지난 브래킷"; copied_cases=0; outsider_status=403 |
| E15 | 통과 | 59.7초 | 14 | old_cases=26; new_cases=26; old_changed=0; new_model_version=1; old_share_pred=5.918 |
| E15b | 통과 | 31.6초 | 8 | new_cases=4; model_factor=100.0 |
| E16b | 통과 | 44.9초 | 12 | released=3; kept_results={"y": 2.432393213732834, "z2": 2.9253833041493293} |
| E16 | 통과 | 93.6초 | 29 | member_cases=4; owner_cases=6; versions=[1, 2, 3]; member_pred_y_at_1_1=1.991; output_note="이어받지 못했습니다. 이 Output은 박과장님 PC의 해석 결과에서 뽑아야 합니다. 박과장님만 추가할 수 있습니다 → 박과장님에게 요청하거나 '내 PC로 복사해서 하기'로 다시 해석하세요."; card="박과장님이 만든 스크립트: scripts/run.py, scripts/extract.py 이 PC에서 박과장님이 만든 스크립트 실행  "; permission="박과장님이 만든 스크립트로 4케이스 해석" |
| PV | 통과 | 90.5초 | 9 | first_reply_sec=10.5; saved=["agent", "attachments", "cases_run", "checks", "deployed_models", "errors", "finished", "first_reply_sec"]; fail_exit_code=1 |
| UI | 통과 | 229.1초 | 10 | screens=53; shots="C:\\Users\\psm55\\AppData\\Local\\Temp\\dds_e_xk79m81v\\ui_shots" |
| UId | 통과 | 157.0초 | 2 | screens=49 |

실행: 2026-10-31 20:15, Python 3.12.10, 서버 응답 500 0건, 서버 Traceback 1건, 전체 1209초.
<!-- 결과표 끝 -->
