/** 에이전트 툴 호출·실행 확인을 사용자가 읽을 수 있는 말로 바꾼다 (원래 이름은 자세히 보기에 남긴다). */

const TOOL_LABEL: Record<string, string> = {
  "dds-flow.propose_flow": "작업 흐름 제안",
  "dds-flow.get_flow": "작업 흐름 확인",
  "dds-flow.update_node": "단계 진행 상황 표시",
  "dds-flow.publish_artifact": "결과 게시",
  "dds-flow.publish_image": "그림 게시",
  "dds-flow.set_project_info": "프로젝트 이름 정리",
  "dds-flow.make_report": "보고서 파일 저장",
  "dds-flow.get_report_context": "보고서 자료 읽기",
  "dds-flow.write_report": "보고서 작성",
  "dds-cae.case_contract": "스크립트 규약 확인",
  "dds-cae.list_tools": "내 PC CAD/CAE 툴 확인",
  "dds-cae.project_folder": "프로젝트 폴더 확인",
  "dds-cae.run_script": "스크립트 실행",
  "dds-study.list_adapters": "실행 방식 확인",
  "dds-study.create_study": "스터디 만들기",
  "dds-study.add_outputs": "Output 추가·추출",
  "dds-study.generate_doe": "DOE 설계점 만들기",
  "dds-study.run_cases": "케이스 해석 실행",
  "dds-study.get_study": "스터디 진행 확인",
  "dds-study.train_surrogate": "예측 모델 학습",
  "dds-study.suggest_next": "다음 설계점 제안",
  "dds-study.optimize": "최적화 반복",
  "dds-study.train_field_model": "형상·필드 학습",
  "dds-study.deploy_model": "Simulator 배포",
  "dds-study.predict": "예측 모델로 계산",
  "dds-live.live_status": "Live Link 연결 확인",
  "dds-live.live_launch": "CAD/CAE 툴 열기 (Live Link)",
  "dds-live.live_info": "열린 모델 정보 읽기",
  "dds-live.live_exec": "열린 모델에서 명령 실행",
  "dds-live.live_result": "Live Link 결과 확인",
  "dds-live.live_capture": "화면 캡처",
};

/** "dds-flow.update_node", "mcp__dds-flow__update_node", "dds_flow: update_node (MCP)" 같은 여러 표기를 하나로 */
function canonical(title: string): string | null {
  const m = title.match(/dds[-_](flow|cae|study|live)\W+(\w+)/i);
  return m ? `dds-${m[1].toLowerCase()}.${m[2]}` : null;
}

/** 툴 호출 제목 → [보여 줄 이름, 원래 이름(다르면)] */
export function toolLabel(title: string): [string, string | null] {
  const key = canonical(title);
  if (key && TOOL_LABEL[key]) return [TOOL_LABEL[key], title];
  return [title, null];
}

const ARG_LABEL: Record<string, string> = {
  study: "스터디", study_id: "스터디", cases: "케이스 수", n: "개수", tool: "툴", script: "스크립트", filename: "파일",
  command: "명령", cmd: "명령", path: "경로", file_path: "파일", cwd: "폴더", params: "설계변수", iterations: "반복 횟수",
  mode: "방식", model_id: "모델", timeout_sec: "제한 시간(초)", description: "설명",
};

/** 실행 확인 카드에 보일 입력값: 짧은 값만 가진 객체면 [이름, 값] 목록, 아니면 null(원문 그대로 보여 줌) */
export function readableArgs(raw: unknown): [string, string][] | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rows: [string, string][] = [];
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (v === null || v === undefined || v === "") continue;
    let s: string;
    if (typeof v === "object") {
      if (Array.isArray(v) || Object.values(v as object).some((x) => typeof x === "object")) return null;
      s = Object.entries(v as Record<string, unknown>).map(([a, b]) => `${a}=${b}`).join(", ");
    } else s = String(v);
    if (s.length > 160) return null;
    rows.push([ARG_LABEL[k] ?? k, s]);
  }
  return rows.length && rows.length <= 8 ? rows : null;
}
