import type { FlowStatus, NodeStatus } from "./types";

/** 서버 시각을 '오늘 14:20' / '어제' / '10월 3일'로 */
export function when(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  const now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return `오늘 ${d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
  if (diff === 1) return "어제";
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}월 ${d.getDate()}일`;
  return `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}.`;
}

/** 서버와 같은 기준(로컬 시각, 시간대 없음)의 ISO 문자열 — 대화 타임라인 정렬용 */
export function localIso(d = new Date()): string {
  const z = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}.${z(d.getMilliseconds(), 3)}`;
}

export const FLOW_STATUS: Record<FlowStatus, { label: string; chip: string }> = {
  review: { label: "승인 대기", chip: "warn" },
  approved: { label: "승인됨", chip: "info" },
  in_progress: { label: "진행 중", chip: "info" },
  complete: { label: "완료", chip: "ok" },
  failed: { label: "실패", chip: "err" },
  rejected: { label: "반려", chip: "" },
};

export const NODE_STATUS: Record<NodeStatus, string> = {
  pending: "대기", in_progress: "진행 중", complete: "완료", failed: "실패", skipped: "건너뜀",
};

export const NODE_TYPE: Record<string, string> = {
  cad: "CAD", pre: "전처리", solve: "해석", post: "후처리", doe: "DOE", train: "학습",
  optimize: "최적화", deploy: "배포", task: "작업",
};

export const ROLE_LABEL: Record<string, string> = { master: "관리자", member: "멤버" };

export function fmt(v: unknown, digits = 4): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return v === null || v === undefined ? "" : String(v);
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
  return Number(v.toPrecision(digits)).toString();
}

export async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

/** 화면 어디서든 에이전트 채팅에 메시지를 보낸다 (예: 플로우 승인 후 "진행하세요") */
export const AGENT_SEND = "dds:agent-send";
export const sendToAgent = (text: string) => window.dispatchEvent(new CustomEvent(AGENT_SEND, { detail: text }));
/** 보내지 않고 입력창만 채운다(사용자가 이어서 쓰게) — 예: "Output 추가: " */
export const AGENT_PREFILL = "dds:agent-prefill";
export const prefillAgent = (text: string) => window.dispatchEvent(new CustomEvent(AGENT_PREFILL, { detail: text }));
