import { useCallback, useEffect, useState } from "react";

/** 사이드바(왼쪽 프로젝트 목록, 오른쪽 에이전트 채팅)의 너비(드래그로 조절)와 접힘 상태.
 *  사람마다 편한 너비가 달라 브라우저에 기억해 둔다(실패해도 기본값으로 동작). */
export interface PanelSpec { key: string; min: number; max: number; width: number; collapsed: boolean; shortcut: string }
export const LEFT: PanelSpec = { key: "dds.sidebar", min: 200, max: 480, width: 264, collapsed: false, shortcut: "b" };
export const RIGHT: PanelSpec = { key: "dds.canvas.layout", min: 360, max: 1100, width: 560, collapsed: false, shortcut: "j" };
// 예전 이름 (왼쪽 사이드바)
export const SIDE_MIN = LEFT.min;
export const SIDE_MAX = LEFT.max;
export const SIDE_DEFAULT = LEFT.width;

function load(spec: PanelSpec): { width: number; collapsed: boolean } {
  try {
    const v = JSON.parse(localStorage.getItem(spec.key) ?? "{}") as { width?: number; collapsed?: boolean };
    const w = Number(v.width);
    return {
      width: Number.isFinite(w) ? Math.min(spec.max, Math.max(spec.min, w)) : spec.width,
      collapsed: typeof v.collapsed === "boolean" ? v.collapsed : spec.collapsed,
    };
  } catch {
    return { width: spec.width, collapsed: spec.collapsed };
  }
}

export function usePanelLayout(spec: PanelSpec) {
  const [state, setState] = useState(() => load(spec));
  useEffect(() => {
    try { localStorage.setItem(spec.key, JSON.stringify(state)); } catch { /* 저장 못 해도 화면은 그대로 */ }
  }, [state, spec.key]);
  const setWidth = useCallback((w: number) => setState((s) => ({ ...s, width: Math.round(Math.min(spec.max, Math.max(spec.min, w))) })), [spec.max, spec.min]);
  const toggle = useCallback(() => setState((s) => ({ ...s, collapsed: !s.collapsed })), []);
  const setCollapsed = useCallback((collapsed: boolean) => setState((s) => ({ ...s, collapsed })), []);
  const reset = useCallback(() => setState((s) => ({ ...s, width: spec.width })), [spec.width]);
  // 단축키 (Ctrl/⌘ + 글자): 접기/펼치기
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === spec.shortcut) { e.preventDefault(); toggle(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [toggle, spec.shortcut]);
  return { ...state, setWidth, toggle, setCollapsed, reset, min: spec.min, max: spec.max };
}
export type PanelLayout = ReturnType<typeof usePanelLayout>;

/** 왼쪽 프로젝트 목록 (Ctrl+B) */
export const useSideLayout = () => usePanelLayout(LEFT);
/** 오른쪽 에이전트 채팅 (Ctrl+J) */
export const useConversaLayout = () => usePanelLayout(RIGHT);

/** 사이드바 가장자리를 끌어 너비를 바꾼다. side: 끄는 가장자리가 패널의 오른쪽(left 패널)인지 왼쪽(right 패널)인지 */
export function startPanelResize(e: React.PointerEvent, layout: PanelLayout, edge: "right" | "left") {
  e.preventDefault();
  const x0 = e.clientX, w0 = layout.width;
  document.body.classList.add("resizing");
  const move = (ev: PointerEvent) => layout.setWidth(edge === "right" ? w0 + ev.clientX - x0 : w0 - (ev.clientX - x0));
  const up = () => {
    document.body.classList.remove("resizing");
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}
