import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Box, Brain, CheckCircle2, Cpu, FileOutput, Grid3x3, Loader2, Rocket, Settings2, Target, Workflow, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { NODE_STATUS, NODE_TYPE } from "../format";
import type { Flow, FlowNode } from "../types";

const TYPE_ICON: Record<string, typeof Box> = {
  cad: Box, pre: Settings2, solve: Cpu, post: FileOutput, doe: Grid3x3, train: Brain, optimize: Target, deploy: Rocket, task: Workflow,
};

// React Flow의 data는 Record<string, unknown>이어야 하므로 type 별칭(교차 타입)으로 둔다
type StepData = FlowNode & { selected?: boolean; artifacts?: number; rtl?: boolean } & Record<string, unknown>;

function StepNode({ data }: NodeProps<Node<StepData>>) {
  const Icon = TYPE_ICON[data.type] ?? Workflow;
  return (
    <div className={`flow-node st-${data.status} ${data.selected ? "sel" : ""}`}>
      <Handle type="target" position={data.rtl ? Position.Right : Position.Left} />
      <div className="fn-top">
        <span className="fn-icon"><Icon size={14} /></span>
        <span className="fn-type">{NODE_TYPE[data.type] ?? data.type}{data.tool ? ` · ${data.tool}` : ""}</span>
        <span className="fn-st" title={NODE_STATUS[data.status]}>
          {data.status === "complete" ? <CheckCircle2 size={14} /> : data.status === "failed" ? <XCircle size={14} />
            : data.status === "in_progress" ? <Loader2 size={14} className="spin" /> : <i className="dot" />}
        </span>
      </div>
      <b className="fn-label">{data.label}</b>
      {data.detail && <span className="fn-detail">{data.detail}</span>}
      {!!data.artifacts && <span className="fn-arts">결과 {data.artifacts}</span>}
      <Handle type="source" position={data.rtl ? Position.Left : Position.Right} />
    </div>
  );
}
const nodeTypes = { step: StepNode };

const COL_W = 250, ROW_H = 118, ROW_GAP = 46;

/** 엣지로 단계 깊이(위상 정렬)를 정하고, 컨테이너 폭에 맞춰 perRow개씩 줄을 바꿔 뱀처럼(→ ← →) 배치한다.
 *  같은 깊이의 병렬 단계는 세로로 쌓는다. 좁은 캔버스에서도 노드가 작아지지 않게 하기 위함. */
function layout(flow: Flow, perRow: number): { pos: Record<string, { x: number; y: number }>; rtl: Record<string, boolean> } {
  const depth: Record<string, number> = {};
  const incoming: Record<string, string[]> = {};
  flow.nodes.forEach((n) => { incoming[n.id] = []; });
  flow.edges.forEach((e) => incoming[e.target]?.push(e.source));
  const visit = (id: string, seen = new Set<string>()): number => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const d = Math.max(-1, ...incoming[id].map((s) => visit(s, seen))) + 1;
    depth[id] = d;
    return d;
  };
  flow.nodes.forEach((n) => visit(n.id));
  const levels: string[][] = [];
  flow.nodes.forEach((n) => { (levels[depth[n.id]] ??= []).push(n.id); });
  const pos: Record<string, { x: number; y: number }> = {};
  const rtl: Record<string, boolean> = {};
  let y = 0;
  for (let r = 0; r * perRow < levels.length; r++) {
    const row = levels.slice(r * perRow, (r + 1) * perRow);
    const back = r % 2 === 1;
    row.forEach((ids, i) => {
      const col = back ? perRow - 1 - i : i;
      (ids ?? []).forEach((id, k) => { pos[id] = { x: col * COL_W, y: y + k * ROW_H }; rtl[id] = back; });
    });
    y += Math.max(...row.map((ids) => (ids ?? []).length)) * ROW_H + ROW_GAP;
  }
  return { pos, rtl };
}

export function FlowCanvas({ flow, selected, onSelect, artifactCount, mini }: {
  flow: Flow; selected: string | null; onSelect: (id: string | null) => void; artifactCount: Record<string, number>; mini?: boolean;
}) {
  // 컨테이너 폭에 맞춰 한 줄에 놓을 단계 수
  const box = useRef<HTMLDivElement>(null);
  const [perRow, setPerRow] = useState(4);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setPerRow(Math.max(2, Math.floor((e.contentRect.width - 30) / (COL_W - 10)))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const { nodes, edges } = useMemo(() => {
    const { pos, rtl } = layout(flow, perRow);
    const nodes: Node<StepData>[] = flow.nodes.map((n) => ({
      id: n.id, type: "step", position: pos[n.id],
      data: { ...n, selected: n.id === selected, artifacts: artifactCount[n.id] ?? 0, rtl: rtl[n.id] },
    }));
    const done = new Set(flow.nodes.filter((n) => n.status === "complete").map((n) => n.id));
    const active = new Set(flow.nodes.filter((n) => n.status === "in_progress").map((n) => n.id));
    const edges: Edge[] = flow.edges.map((e, i) => ({
      id: `e${i}`, source: e.source, target: e.target, label: e.label ?? undefined, type: "smoothstep",
      animated: active.has(e.target), className: done.has(e.source) ? "done" : "",
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    }));
    return { nodes, edges };
  }, [flow, selected, artifactCount, perRow]);

  return (
    <div ref={box} className={`flow-canvas ${mini ? "mini" : ""}`}>
      {/* mini: 대화 카드 안 미리보기 — 확대·이동 없이 전체를 맞춰 보여 준다 */}
      <ReactFlow key={perRow} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: mini ? 0.08 : 0.2, maxZoom: 1.1 }} minZoom={0.2}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable={!mini} proOptions={{ hideAttribution: true }}
        panOnDrag={!mini} zoomOnScroll={!mini} zoomOnPinch={!mini} zoomOnDoubleClick={!mini} preventScrolling={!mini}
        onNodeClick={(_, n) => onSelect(n.id === selected ? null : n.id)} onPaneClick={() => { if (!mini) onSelect(null); }}>
        {!mini && <Background gap={20} size={1} />}
        {!mini && <Controls showInteractive={false} />}
      </ReactFlow>
    </div>
  );
}
