import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Box, Brain, CheckCircle2, Cpu, FileOutput, Grid3x3, Loader2, Rocket, Settings2, Target, Workflow, XCircle } from "lucide-react";
import { useMemo } from "react";
import { NODE_STATUS, NODE_TYPE } from "../format";
import type { Flow, FlowNode } from "../types";

const TYPE_ICON: Record<string, typeof Box> = {
  cad: Box, pre: Settings2, solve: Cpu, post: FileOutput, doe: Grid3x3, train: Brain, optimize: Target, deploy: Rocket, task: Workflow,
};

// React Flow의 data는 Record<string, unknown>이어야 하므로 type 별칭(교차 타입)으로 둔다
type StepData = FlowNode & { selected?: boolean; artifacts?: number } & Record<string, unknown>;

function StepNode({ data }: NodeProps<Node<StepData>>) {
  const Icon = TYPE_ICON[data.type] ?? Workflow;
  return (
    <div className={`flow-node st-${data.status} ${data.selected ? "sel" : ""}`}>
      <Handle type="target" position={Position.Left} />
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
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { step: StepNode };

/** 엣지로 단계(열)를 정해 왼쪽→오른쪽으로 자동 배치 (위상 정렬 깊이) */
function layout(flow: Flow): Record<string, { x: number; y: number }> {
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
  const rows: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  flow.nodes.forEach((n) => {
    const d = depth[n.id];
    const r = rows[d] ?? 0;
    rows[d] = r + 1;
    pos[n.id] = { x: d * 250, y: r * 130 };
  });
  return pos;
}

export function FlowCanvas({ flow, selected, onSelect, artifactCount }: {
  flow: Flow; selected: string | null; onSelect: (id: string | null) => void; artifactCount: Record<string, number>;
}) {
  const { nodes, edges } = useMemo(() => {
    const pos = layout(flow);
    const nodes: Node<StepData>[] = flow.nodes.map((n) => ({
      id: n.id, type: "step", position: pos[n.id], data: { ...n, selected: n.id === selected, artifacts: artifactCount[n.id] ?? 0 },
    }));
    const done = new Set(flow.nodes.filter((n) => n.status === "complete").map((n) => n.id));
    const active = new Set(flow.nodes.filter((n) => n.status === "in_progress").map((n) => n.id));
    const edges: Edge[] = flow.edges.map((e, i) => ({
      id: `e${i}`, source: e.source, target: e.target, label: e.label ?? undefined,
      animated: active.has(e.target), className: done.has(e.source) ? "done" : "",
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    }));
    return { nodes, edges };
  }, [flow, selected, artifactCount]);

  return (
    <div className="flow-canvas">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }} minZoom={0.3}
        nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}
        onNodeClick={(_, n) => onSelect(n.id === selected ? null : n.id)} onPaneClick={() => onSelect(null)}>
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
