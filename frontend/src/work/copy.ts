/** Project > Task 화면 문구를 모아 둔 곳. 낱말은 W 한 곳에서 가져오므로, 이름을 바꾸려면 여기만 고치면 된다.
 *  코드 이름(Work·works API·flow)과는 일부러 다르다: 흐름 노드 type 'task'와 겹치지 않게 코드는 Work로 둔다.
 *  서버가 보내는 문장은 server/app/work_service.py에 같은 낱말로 있다. server/tests/test_work_wording.py가 옛말을 막는다. */
export const W = { project: "Project", task: "Task", flow: "Task Flow" } as const;

/** 아직 없는 기능을 있는 척하지 않게 한 곳에서 켜고 끈다. */
export const FEATURE = { endAi: true };

const { project: P, task: T, flow: F } = W;

export const COPY = {
  newProject: `새 ${P}`,
  newTask: `새 ${T}`,
  addTask: `+ 새 ${T}`,
  projectSearch: `${P} 검색`,
  newProjectTip: `다른 부품이나 제품을 시작합니다. 폴더가 새로 생깁니다`,
  newTaskTip: `이 부품으로 새 일을 시작합니다. 폴더가 새로 생기고 다른 ${T}는 그대로입니다`,
  moreTasks: (n: number) => `이전 ${T} ${n}개 더 보기`,
  fewerTasks: `접기`,
  menuProject: `${P} 메뉴`,
  menuTask: `${T} 메뉴`,
  renameTask: `이름 바꾸기`,
  renameTaskTitle: `${T} 이름 바꾸기`,
  renamed: `이름을 바꿨습니다.`,
  openTaskFolder: `${T} 폴더 열기`,
  openProjectFolder: `${P} 폴더 열기`,
  endAi: `이 ${T}의 AI 끝내기`,
  endAiBody: `AI를 끝냅니다. 기록과 결과는 그대로이고, 다음 요청은 몇 분 걸릴 수 있습니다.`,
  endAiConfirm: `끝내기`,
  endAiDone: `AI를 끝냈습니다. 다음 요청을 보내면 다시 시작합니다.`,
  remove: `삭제`,
  viewOnly: `보기만 가능`,
  // 첫 화면
  homeSend: `보내면 새 ${P}와 첫 ${T}가 만들어집니다. 폴더는 내 PC의 DDS_Workspace 아래에 생깁니다.`,
  homeAutoName: `이름 자동`,
  homeDefaultFolder: `기본 ${P} 폴더`,
  homeFolder: `${P} 폴더`,
  homeRecent: `최근 ${T} 이어서 하기`,
  // 새 Task 화면
  newTaskTitle: `이 부품으로 새 일을 시작합니다`,
  newTaskBody: `보내면 ${T}와 폴더가 하나 생기고, 결과는 이 ${T}에만 모입니다. 다른 ${T}는 그대로입니다.`,
  // 입력창
  askHere: `이 ${T}에 요청하세요. 모델 파일은 끌어 놓으면 됩니다`,
  askNew: `할 일을 적으세요. 모델 파일은 끌어 놓으면 됩니다`,
  readOnly: (owner: string) => `${owner}님의 ${T}라 보기만 할 수 있습니다. 요청을 적어 보내면 내 PC로 복사해서 할 수 있습니다`,
  // 지난 Task처럼 시작
  sourceTitle: `지난 ${T}처럼 시작`,
  sourceHint: `고르지 않으면 처음부터 합니다`,
  sourceMore: `더 찾기`,
  sourceRowTip: `이 ${T}의 작업 순서·스크립트·설정을 복사해 시작합니다. 원래 ${T}는 그대로입니다`,
  sourceSteps: (n: number) => `${n}단계`,
  sourceNone: `찾은 ${T}가 없습니다`,
  sourcePicked: (name: string) => `'${name}'처럼 시작합니다`,
  sourceClear: `고르지 않기`,
  sourceBring: `가져오는 것: 작업 순서, 스크립트, 설계변수·범위, 목적·구속`,
  sourceLeave: `가져오지 않는 것: 결과, 모델 파일 → 새 모델 파일을 끌어 놓으세요`,
  sourceAuthor: (who: string) => `스크립트는 ${who}님이 만들었습니다. 해석 전에 승인 카드에서 다시 확인합니다.`,
  sourceSecret: `치수·경로·품번이 함께 옮겨질 수 있습니다. 승인 카드에서 범위와 경로를 확인하세요.`,
  sourceNoRecipe: `스크립트가 서버에 없어 작업 순서와 설정만 가져옵니다. AI가 스크립트를 새로 만듭니다.`,
  sourceLine: (name: string) => `시작점: '${name}'에서 가져옴`,
  sourceLineTakeover: (name: string) => `시작점: '${name}'을 복사함`,
  sourceOpen: `열기`,
  // 경로 카드: 한 문장, 주 버튼 하나, 글자 링크 하나. 지난 것이 그대로임을 말한다
  cardA: (file: string, source: string) => `새 모델 ${file}로 보입니다. 새 ${T}는 '${source}'의 작업 순서·스크립트·설정을 가져와 시작하고, 지금 ${T}는 그대로입니다.`,
  cardANoFile: (source: string) => `새 모델 이야기로 보입니다. 새 ${T}는 '${source}'의 작업 순서·스크립트·설정을 가져와 시작하고, 지금 ${T}는 그대로입니다.`,
  cardAStart: `새 ${T}로 시작`,
  cardB: `다른 부품으로 보입니다. 새 ${P}로 폴더와 결과를 따로 시작하고, 지금 ${P}는 그대로입니다.`,
  cardBStart: `새 ${P}로 시작`,
  cardD: (project: string) => `'${project}' ${P}가 이미 있습니다. 그 안에 새 ${T}로 시작하면 지난 스크립트·설정을 가져와 쓰고, 그 ${P}의 지난 것은 그대로입니다.`,
  cardDStart: `그 ${P}에 새 ${T}`,
  cardDKeep: `새 ${P}로 계속`,
  keepHere: `여기서 계속`,
  askModelFile: `새 모델 파일을 올리면 지난 ${T}와 달라진 점부터 알려 드립니다.`,
  startedFrom: (name: string) => `'${name}'에서 작업 순서·스크립트·설정을 가져왔습니다.`,
  undo: `되돌리기`,
  undone: (name: string | null) => name ? `새 ${T}를 되돌렸습니다. 요청은 '${name}' 입력창에 돌려놓았습니다.` : `새 ${T}를 되돌렸습니다. 요청은 입력창에 돌려놓았습니다.`,
  movedTo: (name: string) => `→ '${name}' ${T}에서 합니다`,
  // 내 PC로 복사해서 하기
  copyToPc: `내 PC로 복사해서 하기`,
  cardC: (owner: string) => `${owner}님의 ${T}라 보기만 할 수 있습니다. 이 요청은 내 이름의 새 ${T}로 내 PC에서 하고, 원래 ${T}는 그대로입니다.`,
  cardCancel: `취소`,
  copyCases: (n: number) => `끝난 케이스 ${n}건의 숫자 결과를 복사합니다. 다시 돌리지 않고 재학습할 수 있습니다.`,
  toolMissing: (label: string) => `이 PC에 없는 툴: ${label} →`,
  toolMissingLink: `CAD·CAE 툴 화면에서 경로를 지정하세요`,
  rawLeft: (n: number, owner: string) => `원본 해석 파일 ${n}건은 ${owner}님 PC에만 있어 받을 수 없습니다. 숫자·이미지·3D 필드는 볼 수 있습니다.`,
  copyAgentOff: `PC Agent가 꺼져 있습니다. 복사는 되고, 요청은 PC Agent가 연결되면 보냅니다.`,
  copied: (name: string) => `'${name}'로 복사했습니다. 원래 ${T}는 그대로입니다.`,
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
