import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

/** 대화(가운데)와 라이브 캔버스(오른쪽)가 함께 쓰는 상태.
 *  - 대화의 카드에서 "캔버스에서 보기"를 누르면 해당 탭·항목으로 이동
 *  - '실시간 따라가기'가 켜져 있으면 에이전트가 만든 결과로 캔버스가 자동 전환된다 */
export type CanvasTab = "flow" | "results" | "data" | "simulator";

interface Ctx {
  tab: CanvasTab;
  setTab: (t: CanvasTab) => void;
  flowId: number | null;
  setFlowId: (id: number | null) => void;
  focusArtifactId: number | null;
  showArtifact: (id: number) => void;
  showFlow: (id: number) => void;
  follow: boolean;
  setFollow: (v: boolean) => void;
  /** 자동 전환(실시간 따라가기): 사용자가 방금 직접 탭을 골랐으면 잠시 양보한다 */
  autoShow: (t: CanvasTab, id?: number) => void;
  openRequest: number;
}
const CanvasCtx = createContext<Ctx | null>(null);

export function CanvasProvider({ children }: { children: ReactNode }) {
  const [tab, setTabState] = useState<CanvasTab>("flow");
  const [flowId, setFlowId] = useState<number | null>(null);
  const [focusArtifactId, setFocus] = useState<number | null>(null);
  const [follow, setFollow] = useState(true);
  const [openRequest, setOpenRequest] = useState(0);
  const manualAt = useRef(0);

  const setTab = useCallback((t: CanvasTab) => { manualAt.current = Date.now(); setTabState(t); }, []);
  const showArtifact = useCallback((id: number) => {
    setTabState("results"); setFocus(id); setOpenRequest((n) => n + 1); manualAt.current = Date.now();
  }, []);
  const showFlow = useCallback((id: number) => {
    setTabState("flow"); setFlowId(id); setOpenRequest((n) => n + 1); manualAt.current = Date.now();
  }, []);
  const autoShow = useCallback((t: CanvasTab, id?: number) => {
    if (!follow || Date.now() - manualAt.current < 8000) return;
    setTabState(t);
    if (t === "flow" && id) setFlowId(id);
    if (t === "results" && id) setFocus(id);
  }, [follow]);

  const value = useMemo(() => ({
    tab, setTab, flowId, setFlowId, focusArtifactId, showArtifact, showFlow, follow, setFollow, autoShow, openRequest,
  }), [tab, setTab, flowId, focusArtifactId, showArtifact, showFlow, follow, autoShow, openRequest]);
  return <CanvasCtx.Provider value={value}>{children}</CanvasCtx.Provider>;
}

export function useCanvas(): Ctx {
  const c = useContext(CanvasCtx);
  if (!c) throw new Error("CanvasProvider 밖에서 사용했습니다");
  return c;
}
