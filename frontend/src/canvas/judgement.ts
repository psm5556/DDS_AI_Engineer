import type { Flow, FlowNode, InsightNext, InsightSection } from "../types";

/** 판단을 어디에 보여 줄지 정하는 규칙. 화면 없이 시험한다(e2e/judgement_place.mjs).
 *  계산과 문장은 서버(study/insights.py)에 있고, 여기서는 놓는 자리와 양만 정한다. */

type StudyRef = { id: number; flow_id?: number | null };

/** 이 판단이 어느 단계의 결과를 읽은 것인가. 앞의 종류가 흐름에 없으면 다음 종류에 붙인다.
 *  AI가 DOE와 해석을 한 단계로 묶기도 하므로 설계 범위·이상한 값은 서로를 대신한다 */
const WANT: Record<string, string[]> = {
  coverage: ["doe", "solve"], quality: ["solve", "doe"],
  influence: ["train"], trust: ["train"], active: ["train"],
  convergence: ["optimize"], optimum: ["optimize"], pareto: ["optimize"],
};
/** 학습 단계가 여럿일 때 나눈다. 3D 필드 학습이나 능동학습 단계에 스칼라 예측의 판단을 붙이면 다른 일의 판단으로 읽힌다 */
const ACTIVE_STEP = /능동|active/i;
const OTHER_TRAIN_STEP = /능동|active|필드|형상|field/i;

function stepFor(key: string, nodes: FlowNode[]): string | null {
  for (const type of WANT[key] ?? []) {
    const same = nodes.filter((n) => n.type === type && n.status !== "skipped");
    if (!same.length) continue;
    if (type !== "train") return same[0].id;
    const isActive = (n: FlowNode) => ACTIVE_STEP.test(`${n.id} ${n.label}`);
    const isOther = (n: FlowNode) => OTHER_TRAIN_STEP.test(`${n.id} ${n.label}`);
    return (key === "active" ? same.find(isActive) : same.find((n) => !isOther(n)))?.id ?? same[0].id;
  }
  return null;
}

/** 지금 보여 줄 수 있는 판단만 남긴다. 아직 판단할 수 없는 항목은 숙제처럼 읽히므로 내지 않는다.
 *  출력 하나라도 판단했으면 그 출력만 남긴다. 그 안의 '아직 판단 못 함'도 보이지 않게 */
export function judged(s: InsightSection): InsightSection | null {
  if (s.pending || s.collapsed) return null;
  const variants = s.variants ?? [];
  const known = variants.filter((v) => v.state !== "unknown");
  if (s.state === "unknown") {
    const first = known[0];
    if (!first) return null;
    return { ...s, state: first.state, state_label: first.state_label, sentence: first.sentence, notes: first.notes, charts: first.charts, variants: known, lead: first.name };
  }
  if (!variants.length) return s;
  return { ...s, variants: known, lead: known.some((v) => v.name === s.lead) ? s.lead : known[0]?.name };
}

/** 어느 스터디의 판단을 이 흐름에서 보여 줄까.
 *  어느 흐름에도 없는 스터디(표로 만든 것 등)는 단계에 붙이지 않는다. 다른 일의 DOE 단계에 표의 판단이 붙으면 틀린 말이 된다.
 *  그런 스터디는 가장 최근 흐름의 '단계에 묶이지 않은' 자리에만 보여 준다(결과와 같은 규칙) */
export function studiesForFlow<T extends StudyRef>(studies: T[], flows: Flow[], flow: Flow | null | undefined): { onSteps: T[]; loose: T[] } {
  if (!flow) return { onSteps: [], loose: studies };
  const known = new Set(flows.map((f) => f.id));
  return {
    onSteps: studies.filter((s) => s.flow_id === flow.id),
    loose: flow.id === flows[0]?.id ? studies.filter((s) => s.flow_id == null || !known.has(s.flow_id)) : [],
  };
}

/** 단계별로 나눈다. 맞는 단계가 없으면 outside로 보낸다. 흐름 모양 때문에 판단이 사라지면 안 된다 */
export function placeSections(sections: InsightSection[], nodes: FlowNode[]): { byNode: Record<string, InsightSection[]>; outside: InsightSection[] } {
  const byNode: Record<string, InsightSection[]> = {};
  const outside: InsightSection[] = [];
  for (const raw of sections) {
    const s = judged(raw);
    if (!s) continue;
    const at = stepFor(s.key, nodes);
    if (at) (byNode[at] ??= []).push(s);
    else outside.push(s);
  }
  return { byNode, outside };
}

const FLOW_BUSY = new Set(["review", "approved", "in_progress"]);

/** 다음 할 일은 한 곳에 하나만. candidates는 먼저 볼 스터디부터 준다.
 *  흐름이 승인을 기다리거나 도는 동안에는 AI가 이어서 하므로 버튼을 내지 않는다. 같은 일을 두 번 시키게 된다.
 *  연결이 끊긴 것은 사람만 고칠 수 있어 언제나 알린다. 글자만 있는 '기다리세요'는 할 일이 아니다 */
export function pickNext<T extends { next: InsightNext | null | undefined }>(candidates: T[], flows: Flow[]): T | null {
  const busy = flows.some((f) => FLOW_BUSY.has(f.status));
  return candidates.find((c) => c.next?.key === "stuck") ?? (busy ? null : candidates.find((c) => !!c.next?.label && !!c.next?.request)) ?? null;
}
