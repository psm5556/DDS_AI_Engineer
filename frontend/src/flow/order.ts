import type { Flow, FlowNode } from "../types";

/** 단계를 실행 순서대로(엣지 기준 위상 정렬, 같은 순위는 원래 순서).
 *  이 순서가 흐름도·단계별 결과·대화 카드·보고서에 붙는 'N단계' 번호다(서버 report.order_nodes와 같은 규칙). */
export function orderNodes(f: Pick<Flow, "nodes" | "edges">): FlowNode[] {
  const indeg = new Map(f.nodes.map((n) => [n.id, 0]));
  f.edges.forEach((e) => indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1));
  const out: FlowNode[] = [];
  const left = [...f.nodes];
  while (left.length) {
    const i = Math.max(0, left.findIndex((n) => (indeg.get(n.id) ?? 0) === 0));
    const [n] = left.splice(i, 1);
    out.push(n);
    f.edges.filter((e) => e.source === n.id).forEach((e) => indeg.set(e.target, (indeg.get(e.target) ?? 1) - 1));
  }
  return out;
}

/** 노드 id → 단계 번호(1부터) */
export function stepNumbers(f: Pick<Flow, "nodes" | "edges">): Record<string, number> {
  return Object.fromEntries(orderNodes(f).map((n, i) => [n.id, i + 1]));
}
