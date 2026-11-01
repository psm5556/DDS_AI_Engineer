import { AlertTriangle, Database, FileText, FolderTree, SlidersHorizontal, Workflow, X } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { startPanelResize, type PanelLayout } from "../components/sideLayout";
import { useLive } from "../live";
import type { Artifact, Flow, Study, WorkDetail } from "../types";
import { COPY, taskPath, W } from "../work/copy";
import { useWorkList } from "../work/scope";
import { useCanvas, type CanvasTab } from "./CanvasContext";
import { LocalFiles } from "./LocalFiles";
import { ReportView } from "./ReportView";
import { SimulatorTab } from "./SimulatorTab";
import { FlowView, StudiesView } from "./views";

const TABS: [CanvasTab, string, typeof Workflow][] = [
  ["flow", W.flow, Workflow], ["report", "보고서", FileText], ["data", "데이터", Database], ["simulator", "Simulator", SlidersHorizontal], ["files", "파일", FolderTree],
];

/** 오른쪽 탭: 이 Task의 흐름(단계별 결과)·보고서·데이터·Simulator(모델 버전)·내 PC 파일이 실시간으로 쌓인다.
 *  에이전트가 새로 만든 것으로 탭이 자동으로 바뀐다(방금 직접 탭을 골랐으면 잠시 양보). 다른 Task의 소식은 탭을 빼앗지 않는다(WorkNotices가 알린다).
 *  닫으면 완전히 사라지고, 머리글의 버튼(Ctrl+J)으로 다시 연다. */
/** 데이터 탭 맨 위: 어디서 가져온 Task인지. 원본이 지워졌으면 이름만 남는다 */
function SourceLine({ pid, work }: { pid: number; work: WorkDetail | null }) {
  if (!work?.source_label) return null;
  return (
    <p className="src-line small muted">{work.source_kind === "takeover" ? COPY.sourceLineTakeover(work.source_label) : COPY.sourceLine(work.source_label)}
      {work.source_work_id && <>{" · "}<Link to={taskPath(pid, work.source_work_id)}>{COPY.sourceOpen}</Link></>}</p>
  );
}

export function CanvasPanel({ pid, wid, work, layout }: { pid: number; wid: number | null; work: WorkDetail | null; layout: PanelLayout }) {
  const c = useCanvas();
  const flows = useWorkList<Flow>("flows", pid, wid);
  const arts = useWorkList<Artifact>("artifacts", pid, wid);
  // 결과가 어느 탭에 있든 연습용 값을 실제 해석으로 읽지 않게 탭 위에 한 번만 알린다
  const studies = useWorkList<Study>("studies", pid, wid);
  const practice = !!studies?.some((s) => s.practice);
  const { setCollapsed } = layout;

  // 대화 카드에서 결과를 누르면 닫혀 있어도 연다
  useEffect(() => { if (c.openRequest) setCollapsed(false); }, [c.openRequest, setCollapsed]);

  // 실시간 따라가기: 에이전트가 만든 것으로 자동 전환
  useLive((e) => {
    // 이 Task의 소식만 탭을 옮긴다. 옆 Task에서 해석이 도는 중에 다른 Task를 보는 사람의 화면을 빼앗으면 안 된다
    if (e.project_id !== pid || e.work_id == null || e.work_id !== wid) return;
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
    <aside className="canvas" aria-label="결과">
      <div className="side-resizer cv-resizer" role="separator" aria-orientation="vertical" aria-label="결과 칸 너비 조절"
        aria-valuenow={layout.width} aria-valuemin={layout.min} aria-valuemax={layout.max} tabIndex={0} title="끌어서 너비 조절 · 두 번 누르면 기본 너비"
        onPointerDown={(e) => startPanelResize(e, layout, "left")} onDoubleClick={layout.reset}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); layout.setWidth(layout.width + 16); }
          if (e.key === "ArrowRight") { e.preventDefault(); layout.setWidth(layout.width - 16); }
        }} />
      <header className="canvas-head">
        <nav className="canvas-tabs" role="tablist" aria-label="결과 화면">
          {TABS.map(([k, label, Icon]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => c.setTab(k)}>
              <Icon size={14} />{label}
              {k === "flow" && review > 0 && <small className="badge">{review}</small>}
              {k === "report" && c.reportAsk && <span className="spinner tab-spin" aria-label="작성 중" />}
            </button>
          ))}
        </nav>
        <button className="icon-btn canvas-close" onClick={() => setCollapsed(true)} title={COPY.canvasClose} aria-label={COPY.canvasClose}><X size={17} /></button>
      </header>
      {/* Simulator 탭은 그 모델의 값이 연습용일 때 자기 자리에서 알린다. 같은 경고를 두 번 보이지 않는다 */}
      {practice && tab !== "simulator" && <div className="notice warn" role="status"><AlertTriangle size={15} /><span>이 {W.task}에는 <b>연습용 계산</b> 결과가 있습니다. 실제 해석이 아니므로 설계 판단에 쓰지 마세요.</span></div>}
      {/* 작업 흐름 탭: 흐름도는 고정하고 아래 단계별 결과만 스크롤한다(pinned) */}
      <div key={tab} className={`canvas-body ${tab === "flow" ? "pinned" : ""}`}>
        {tab === "flow" ? <FlowView pid={pid} flows={flows} arts={arts ?? []} />
          : tab === "report" ? <ReportView pid={pid} arts={arts ?? []} />
          : tab === "data" ? <><SourceLine pid={pid} work={work} /><StudiesView pid={pid} /></>
          : tab === "simulator" ? <SimulatorTab pid={pid} /> : <LocalFiles pid={pid} work={work} />}
      </div>
    </aside>
  );
}
