import { useQuery } from "@tanstack/react-query";
import { Database, FileText, FolderTree, SlidersHorizontal, Workflow, X } from "lucide-react";
import { useEffect } from "react";
import { get } from "../api";
import { startPanelResize, type PanelLayout } from "../components/sideLayout";
import { useLive } from "../live";
import type { Artifact, Flow } from "../types";
import { useCanvas, type CanvasTab } from "./CanvasContext";
import { LocalFiles } from "./LocalFiles";
import { ReportView } from "./ReportView";
import { SimulatorTab } from "./SimulatorTab";
import { FlowView, StudiesView } from "./views";

const TABS: [CanvasTab, string, typeof Workflow][] = [
  ["flow", "작업 흐름", Workflow], ["report", "보고서", FileText], ["data", "데이터", Database], ["simulator", "Simulator", SlidersHorizontal], ["files", "파일", FolderTree],
];

/** 오른쪽 캔버스: 에이전트가 만드는 작업 흐름(단계별 결과)·보고서·데이터·Simulator(모델 버전)·내 PC 파일이 실시간으로 쌓인다.
 *  에이전트가 새로 만든 것으로 탭이 자동으로 바뀐다(방금 직접 탭을 골랐으면 잠시 양보).
 *  닫으면 완전히 사라지고, 머리글의 캔버스 버튼(Ctrl+J)으로 다시 연다. */
export function CanvasPanel({ pid, layout }: { pid: number; layout: PanelLayout }) {
  const c = useCanvas();
  const { data: flows } = useQuery({ queryKey: ["flows", pid], queryFn: () => get<Flow[]>(`/api/projects/${pid}/flows`) });
  const { data: arts } = useQuery({ queryKey: ["artifacts", pid], queryFn: () => get<Artifact[]>(`/api/projects/${pid}/artifacts`) });
  const { setCollapsed } = layout;

  // 대화 카드에서 결과를 누르면 닫혀 있어도 연다
  useEffect(() => { if (c.openRequest) setCollapsed(false); }, [c.openRequest, setCollapsed]);

  // 실시간 따라가기: 에이전트가 만든 것으로 자동 전환
  useLive((e) => {
    if (e.project_id !== pid) return;
    const d = (e.data ?? {}) as { id?: number; flow_id?: number | null; deleted?: boolean };
    if (e.type === "flow_updated") c.autoShow("flow", { flowId: d.id });
    else if (e.type === "artifact_updated" && !d.deleted) c.autoShow("flow", { flowId: d.flow_id, artifactId: d.id });
    else if (e.type === "model_updated") c.autoShow("simulator");
    else if (e.type === "report_updated") { c.setReportAsk(null); c.autoShow("report"); }
    // 에이전트 차례가 끝났는데 보고서가 오지 않았으면(거절·오류) '작성 중' 표시를 거둔다
    else if (e.type === "agent_event" && (e.event as { kind?: string } | undefined)?.kind === "turn_ended") c.setReportAsk(null);
  });

  if (layout.collapsed) return null;
  const review = flows?.filter((f) => f.status === "review").length ?? 0;
  const tab = c.tab;

  return (
    <aside className="canvas" aria-label="캔버스">
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
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => c.setTab(k)}>
              <Icon size={14} />{label}
              {k === "flow" && review > 0 && <small className="badge">{review}</small>}
              {k === "report" && c.reportAsk && <span className="spinner tab-spin" aria-label="작성 중" />}
            </button>
          ))}
        </nav>
        <button className="icon-btn canvas-close" onClick={() => setCollapsed(true)} title="캔버스 닫기 (Ctrl+J)" aria-label="캔버스 닫기"><X size={17} /></button>
      </header>
      {/* 작업 흐름 탭: 흐름도는 고정하고 아래 단계별 결과만 스크롤한다(pinned) */}
      <div key={tab} className={`canvas-body ${tab === "flow" ? "pinned" : ""}`}>
        {tab === "flow" ? <FlowView pid={pid} flows={flows} arts={arts ?? []} />
          : tab === "report" ? <ReportView pid={pid} arts={arts ?? []} />
          : tab === "data" ? <StudiesView pid={pid} />
          : tab === "simulator" ? <SimulatorTab pid={pid} /> : <LocalFiles pid={pid} />}
      </div>
    </aside>
  );
}
