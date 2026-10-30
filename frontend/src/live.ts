import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { getToken } from "./api";

/** 서버 실시간 이벤트(WebSocket /ws). 연결 하나를 앱 전체가 함께 쓰고, 끊기면 다시 붙는다. */
export interface LiveEvent {
  type: string; project_id?: number; session_id?: number; user_id?: number;
  data?: Record<string, unknown>; event?: Record<string, unknown>; [k: string]: unknown;
}
type Listener = (e: LiveEvent) => void;
const CASE_BATCH_MS = 400;
const listeners = new Set<Listener>();
let ws: WebSocket | null = null;
let retry = 1000;

function connect() {
  const t = getToken();
  if (!t || ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(t)}`);
  ws.onopen = () => { retry = 1000; };
  ws.onmessage = (m) => {
    try {
      const e = JSON.parse(m.data) as LiveEvent;
      listeners.forEach((l) => l(e));
    } catch { /* 형식이 다른 메시지는 무시 */ }
  };
  ws.onclose = () => {
    ws = null;
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
}
setInterval(() => { if (ws?.readyState === WebSocket.OPEN) ws.send("ping"); }, 25000);

/** 이벤트 구독. 콜백은 최신 것을 쓰므로 의존성을 따로 넘기지 않아도 된다. */
export function useLive(fn: Listener) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    connect();
    const l: Listener = (e) => ref.current(e);
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, []);
}

/** 서버 변경 이벤트 → React Query 캐시 무효화 (앱 루트에서 한 번 사용) */
export function useLiveInvalidation() {
  const qc = useQueryClient();
  // 케이스 이벤트는 수백 건이 몰려 올 수 있어 묶어서 한 번만 다시 읽는다
  const batch = useRef<{ pids: Set<number | undefined>; timer: ReturnType<typeof setTimeout> } | null>(null);
  useEffect(() => () => { if (batch.current) clearTimeout(batch.current.timer); }, []);
  const refreshStudies = (pid?: number) => {
    if (batch.current) { batch.current.pids.add(pid); return; }
    const pids = new Set([pid]);
    batch.current = {
      pids,
      timer: setTimeout(() => {
        batch.current = null;
        pids.forEach((p) => qc.invalidateQueries({ queryKey: ["studies", p] }));
        qc.invalidateQueries({ queryKey: ["study"] });
      }, CASE_BATCH_MS),
    };
  };
  useLive((e) => {
    const pid = e.project_id;
    switch (e.type) {
      case "project_updated":
        qc.invalidateQueries({ queryKey: ["projects"] });
        if (pid) qc.invalidateQueries({ queryKey: ["project", pid] });
        break;
      case "flow_updated":
        qc.invalidateQueries({ queryKey: ["flows", pid] });
        qc.invalidateQueries({ queryKey: ["projects"] });
        break;
      case "artifact_updated":
        qc.invalidateQueries({ queryKey: ["artifacts", pid] });
        qc.invalidateQueries({ queryKey: ["reports", pid] }); // 보고서 뒤에 생긴 결과 수
        break;
      case "report_updated": qc.invalidateQueries({ queryKey: ["reports", pid] }); break;
      case "study_updated":
      case "case_updated":
        refreshStudies(pid);
        break;
      case "model_updated": qc.invalidateQueries({ queryKey: ["models", pid] }); break;
      case "agent_status":
      case "agent_release":
        // 내 PC 진단(툴 찾기·WSL 확인)은 무거워 상태 변화마다 다시 돌리지 않는다 — 툴 화면의 '다시 확인'으로만
        qc.invalidateQueries({ queryKey: ["agent"], predicate: (q) => q.queryKey[1] !== "diagnose" });
        break;
    }
  });
}
