import { useQuery } from "@tanstack/react-query";
import { BarChart3, Database, Gauge, PanelRightClose, PanelRightOpen, Radio, Workflow } from "lucide-react";
import { useEffect } from "react";
import { get } from "../api";
import { startPanelResize, type PanelLayout } from "../components/sideLayout";
import { useLive } from "../live";
import type { Artifact, Flow } from "../types";
import { useCanvas, type CanvasTab } from "./CanvasContext";
import { FlowView, ResultsView, SimulatorList, StudiesView } from "./views";

const TABS: [CanvasTab, string, typeof Workflow][] = [
  ["flow", "작업 흐름", Workflow], ["results", "결과", BarChart3], ["data", "데이터·모델", Database], ["simulator", "Simulator", Gauge],
];

/** 오른쪽 라이브 캔버스: 에이전트가 만드는 작업 흐름·결과·데이터·Simulator가 실시간으로 쌓인다 */
export function CanvasPanel({ pid, layout }: { pid: number; layout: PanelLayout }) {
  const c = useCanvas();
  const { data: flows } = useQuery({ queryKey: ["flows", pid], queryFn: () => get<Flow[]>(`/api/projects/${pid}/flows`) });
  const { data: arts } = useQuery({ queryKey: ["artifacts", pid], queryFn: () => get<Artifact[]>(`/api/projects/${pid}/artifacts`) });
  const { setCollapsed } = layout;

  // 대화 카드에서 "캔버스에서 보기"를 누르면 접혀 있어도 펼친다
  useEffect(() => { if (c.openRequest) setCollapsed(false); }, [c.openRequest, setCollapsed]);

  // 실시간 따라가기: 에이전트가 만든 것으로 자동 전환
  useLive((e) => {
    if (e.project_id !== pid) return;
    const id = Number((e.data as { id?: number } | undefined)?.id) || undefined;
    if (e.type === "flow_updated") c.autoShow("flow", id);
    else if (e.type === "artifact_updated" && !(e.data as { deleted?: boolean })?.deleted) c.autoShow("results", id);
    else if (e.type === "model_updated") c.autoShow("data");
  });

  const review = flows?.filter((f) => f.status === "review").length ?? 0;

  if (layout.collapsed) {
    return (
      <aside className="canvas rail" aria-label="라이브 캔버스 (접힘)">
        <button className="icon-btn" onClick={() => setCollapsed(false)} aria-label="캔버스 펼치기" title="캔버스 펼치기 (Ctrl+J)"><PanelRightOpen size={18} /></button>
        {TABS.map(([k, label, Icon]) => (
          <button key={k} className="icon-btn" title={label} aria-label={label} onClick={() => { c.setTab(k); setCollapsed(false); }}><Icon size={17} /></button>
        ))}
      </aside>
    );
  }

  return (
    <aside className="canvas" aria-label="라이브 캔버스">
      <div className="side-resizer cv-resizer" role="separator" aria-orientation="vertical" aria-label="캔버스 너비 조절"
        aria-valuenow={layout.width} aria-valuemin={layout.min} aria-valuemax={layout.max} tabIndex={0} title="끌어서 너비 조절 · 두 번 누르면 기본 너비"
        onPointerDown={(e) => startPanelResize(e, layout, "left")} onDoubleClick={layout.reset}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); layout.setWidth(layout.width + 16); }
          if (e.key === "ArrowRight") { e.preventDefault(); layout.setWidth(layout.width - 16); }
        }} />
      <header className="canvas-head">
        <nav className="canvas-tabs" role="tablist" aria-label="캔버스 화면">
          {TABS.map(([k, label, Icon]) => (
            <button key={k} role="tab" aria-selected={c.tab === k} className={c.tab === k ? "on" : ""} onClick={() => c.setTab(k)}>
              <Icon size={14} />{label}
              {k === "flow" && review > 0 && <small className="badge">{review}</small>}
              {k === "results" && !!arts?.length && <small>{arts.length}</small>}
            </button>
          ))}
        </nav>
        <button className={`icon-btn follow ${c.follow ? "on" : ""}`} onClick={() => c.setFollow(!c.follow)} aria-pressed={c.follow}
          title={c.follow ? "실시간 따라가기 켜짐 — 에이전트가 만든 결과로 자동 전환" : "실시간 따라가기 꺼짐"} aria-label="실시간 따라가기"><Radio size={15} /></button>
        <button className="icon-btn" onClick={() => setCollapsed(true)} title="캔버스 접기 (Ctrl+J)" aria-label="캔버스 접기"><PanelRightClose size={16} /></button>
      </header>
      <div className="canvas-body">
        {c.tab === "flow" ? <FlowView pid={pid} flows={flows} arts={arts ?? []} />
          : c.tab === "results" ? <ResultsView pid={pid} arts={arts ?? []} flows={flows ?? []} />
          : c.tab === "data" ? <StudiesView pid={pid} /> : <SimulatorList pid={pid} />}
      </div>
    </aside>
  );
}
