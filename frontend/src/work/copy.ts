/** Project > Task 화면 문구를 모아 둔 곳. 낱말은 W 한 곳에서 가져오므로, 이름을 바꾸려면 여기만 고치면 된다.
 *  코드 이름(Work·works API·flow)과는 일부러 다르다: 흐름 노드 type 'task'와 겹치지 않게 코드는 Work로 둔다.
 *  서버가 보내는 문장은 server/app/work_service.py에 같은 낱말로 있다. server/tests/test_work_wording.py가 옛말을 막는다. */
export const W = { project: "Project", task: "Task", flow: "Task Flow" } as const;

/** 아직 없는 기능을 있는 척하지 않게 한 곳에서 켜고 끈다.
 *  taskFolder: Task별 PC 폴더(2단계)가 들어오면 켠다. endAi: 'AI 끝내기'(session.close, 8단계)가 들어오면 켠다. */
export const FEATURE = { taskFolder: false, endAi: false };

const { project: P, task: T, flow: F } = W;

export const COPY = {
  newProject: `새 ${P}`,
  newTask: `새 ${T}`,
  addTask: `+ 새 ${T}`,
  projectSearch: `${P} 검색`,
  // newTaskTip: 2단계가 들어오면 '폴더가 새로 생기고'를 넣는다. 지금은 새 Task도 Project 폴더에서 돌아서 없는 폴더를 말하지 않는다
  newProjectTip: `다른 부품이나 제품을 시작합니다. 폴더가 새로 생깁니다`,
  newTaskTip: `이 부품으로 새 일을 시작합니다. 결과는 이 ${T}에만 모이고 다른 ${T}는 그대로입니다`,
  moreTasks: (n: number) => `이전 ${T} ${n}개 더 보기`,
  fewerTasks: `접기`,
  menuProject: `${P} 메뉴`,
  menuTask: `${T} 메뉴`,
  renameTask: `이름 바꾸기`,
  renameTaskTitle: `${T} 이름 바꾸기`,
  renamed: `이름을 바꿨습니다.`,
  openTaskFolder: `${T} 폴더 열기`,
  endAi: `이 ${T}의 AI 끝내기`,
  remove: `삭제`,
  viewOnly: `보기만 가능`,
  // 첫 화면
  homeSend: `보내면 새 ${P}와 첫 ${T}가 만들어집니다. 폴더는 내 PC의 DDS_Workspace 아래에 생깁니다.`,
  homeAutoName: `이름 자동`,
  homeDefaultFolder: `기본 ${P} 폴더`,
  homeFolder: `${P} 폴더`,
  homeRecent: `최근 ${T} 이어서 하기`,
  // 새 Task 화면. 2단계가 들어오면 '보내면 Task와 폴더가 하나 생기고'로 바꾼다
  newTaskTitle: `이 부품으로 새 일을 시작합니다`,
  newTaskBody: `보내면 ${T}가 하나 생기고, 결과는 이 ${T}에만 모입니다. 다른 ${T}는 그대로입니다.`,
  // 입력창
  askHere: `이 ${T}에 요청하세요. 모델 파일은 끌어 놓으면 됩니다`,
  askNew: `할 일을 적으세요. 모델 파일은 끌어 놓으면 됩니다`,
  readOnly: (owner: string) => `${owner}님의 ${T}라 보기만 할 수 있습니다. 내 일은 왼쪽 '+ 새 ${T}'로 시작하세요`,
  // 빈 화면
  emptyOthers: `아직 기록이 없습니다`,
  emptyFlow: `아직 흐름이 없습니다. 해석을 돌릴 때 여기에 단계가 나타납니다`,
  emptyReport: `입력창에 "보고서 써 줘"라고 요청하세요`,
  emptyData: `표를 붙여 넣거나 해석을 돌리면 여기에 쌓입니다`,
  canvasOpen: `결과 열기 — ${F}·보고서·데이터 (Ctrl+J)`,
  canvasClose: `결과 닫기 (Ctrl+J)`,
  filesOfOther: (owner: string) => `이 ${T}의 파일은 ${owner}님 PC에 있습니다. 서버에 있는 결과와 첨부는 내려받을 수 있습니다.`,
  legacyFirst: `이제 일은 ${T} 단위로 나뉩니다. 지금까지의 기록은 '이전 기록' ${T}에 모았습니다. 새 일은 '+ 새 ${T}'로 시작하세요.`,
  legacyDivider: (who: string, when: string) => `이전 기록 · ${who} · ${when}`,
  tableInto: `이 ${T}에 넣습니다.`,
  tableIntoNew: `내 새 ${T}에 넣습니다.`,
  // 다른 Task 소식. 탭을 빼앗지 않고 알린다
  noticeDone: (name: string) => `'${name}' 해석이 끝났습니다`,
  noticeReview: (name: string) => `'${name}' 승인을 기다립니다`,
  noticeFailed: (name: string) => `'${name}' 진행에 실패했습니다`,
  noticeOpen: `열기`,
  // 삭제
  deleteTitle: (name: string) => `'${name}' 삭제`,
  deleteBody: (sims: number, links: number) => `기록, ${F}, 결과, 보고서${sims ? `, Simulator ${sims}개` : ""}가 서버에서 지워집니다.${links ? ` 공유 링크 ${links}개도 끊어집니다.` : ""}`,
  deleteKeep: `내 PC 폴더는 지우지 않습니다. 되돌릴 수 없습니다.`,
  deleted: `${T}를 삭제했습니다`,
  // 사이드바 줄
  made: (date: string) => `${date} 만듦`,
  cases: (n: number) => `케이스 ${n}건`,
};

/** 첫 요청에서 Task 이름을 짓는다. AI가 알맞게 바꾸기 전까지 쓴다(사람이 정한 이름이 아니다) */
export function autoTaskName(text: string, file?: string): string | null {
  const first = text.split(/[\n.!?]/)[0].trim();
  if (first) return first.length > 28 ? `${first.slice(0, 28)}…` : first;
  return file ? file.replace(/\.[^.]+$/, "") : null;
}

export const taskPath = (pid: number, wid?: number | "new" | null) => (wid ? `/projects/${pid}/w/${wid}` : `/projects/${pid}`);
