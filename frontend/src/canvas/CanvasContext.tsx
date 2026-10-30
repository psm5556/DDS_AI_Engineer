import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

/** 대화(가운데)와 라이브 캔버스(오른쪽)가 함께 쓰는 상태.
 *  - 대화의 카드에서 결과를 누르면 작업 흐름 탭의 그 단계 결과로 이동
 *  - '실시간 따라가기'가 켜져 있으면 에이전트가 만든 결과로 캔버스가 자동 전환된다 */
export type CanvasTab = "flow" | "report" | "data" | "simulator" | "files";

interface Focus { id: number; n: number }
export interface ReportAsk { at: number; after: number }

interface Ctx {
  tab: CanvasTab;
  setTab: (t: CanvasTab) => void;
  flowId: number | null;
  setFlowId: (id: number | null) => void;
  /** 작업 흐름 탭에서 스크롤할 결과(n은 같은 결과를 다시 눌러도 다시 이동하게) */
  focus: Focus | null;
  showArtifact: (id: number, flowId: number | null) => void;
  showFlow: (id: number) => void;
  follow: boolean;
  setFollow: (v: boolean) => void;
  /** 자동 전환(실시간 따라가기): 사용자가 방금 직접 탭을 골랐으면 잠시 양보한다 */
  autoShow: (t: CanvasTab, o?: { flowId?: number | null; artifactId?: number }) => void;
  openRequest: number;
  /** 'AI 보고서 작성'을 누른 때(시각, 그때 최신 판 id) — 새 판이 오거나 에이전트 차례가 끝날 때까지 '작성 중'을 보여 준다 */
  reportAsk: ReportAsk | null;
  setReportAsk: (v: ReportAsk | null) => void;
}
const CanvasCtx = createContext<Ctx | null>(null);

export function CanvasProvider({ children }: { children: ReactNode }) {
  const [tab, setTabState] = useState<CanvasTab>("flow");
  const [flowId, setFlowId] = useState<number | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [follow, setFollow] = useState(true);
  const [openRequest, setOpenRequest] = useState(0);
  const [reportAsk, setReportAsk] = useState<ReportAsk | null>(null);
  const manualAt = useRef(0);

  const setTab = useCallback((t: CanvasTab) => { manualAt.current = Date.now(); setTabState(t); }, []);
  const showArtifact = useCallback((id: number, fid: number | null) => {
    setTabState("flow"); setFlowId(fid); setFocus((f) => ({ id, n: (f?.n ?? 0) + 1 }));
    setOpenRequest((n) => n + 1); manualAt.current = Date.now();
  }, []);
  const showFlow = useCallback((id: number) => {
    setTabState("flow"); setFlowId(id); setOpenRequest((n) => n + 1); manualAt.current = Date.now();
  }, []);
  const autoShow = useCallback((t: CanvasTab, o?: { flowId?: number | null; artifactId?: number }) => {
    if (!follow || Date.now() - manualAt.current < 8000) return;
    setTabState(t);
    if (o?.flowId) setFlowId(o.flowId);
    if (o?.artifactId) setFocus((f) => ({ id: o.artifactId!, n: (f?.n ?? 0) + 1 }));
  }, [follow]);

  const value = useMemo(() => ({
    tab, setTab, flowId, setFlowId, focus, showArtifact, showFlow, follow, setFollow, autoShow, openRequest, reportAsk, setReportAsk,
  }), [tab, setTab, flowId, focus, showArtifact, showFlow, follow, autoShow, openRequest, reportAsk]);
  return <CanvasCtx.Provider value={value}>{children}</CanvasCtx.Provider>;
}

export function useCanvas(): Ctx {
  const c = useContext(CanvasCtx);
  if (!c) throw new Error("CanvasProvider 밖에서 사용했습니다");
  return c;
}
