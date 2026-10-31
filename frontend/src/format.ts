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

/** 붙여 넣은 표의 한도. 서버의 paste_max_mb와 같다. 넘으면 올리기 전에 알린다 */
export const PASTE_MAX_BYTES = 50 * 1024 * 1024;
export const PASTE_TOO_BIG = "붙여 넣은 표가 너무 큽니다. 50MB까지 받습니다 → 필요한 행과 열만 골라 다시 복사해 주세요";

/** 붙여 넣은 글이 표인가: 탭으로 나뉜 칸이 두 개 이상인 줄이 대부분이고 두 줄 이상이다(엑셀 복사). 앞에 탭이 있는 코드는 칸이 하나라 걸리지 않는다 */
export function looksLikeTable(text: string): { rows: number; cols: number } | null {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return null;
  const cells = lines.map((l) => l.split("\t").filter((c) => c.trim()).length);
  if (cells.filter((n) => n >= 2).length / lines.length < 0.8) return null;
  return { rows: lines.length, cols: Math.max(...cells) };
}

export async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

/** 화면 어디서든 에이전트 채팅에 메시지를 보낸다 (예: 플로우 승인 후 "진행하세요") */
export const AGENT_SEND = "dds:agent-send";
export const sendToAgent = (text: string) => window.dispatchEvent(new CustomEvent(AGENT_SEND, { detail: text }));
/** 보내지 않고 입력창만 채운다(사용자가 이어서 쓰게) — 예: "Output 추가: ". askFile이 있으면 입력창 위에 파일을 달라는 안내를 띄운다 */
export const AGENT_PREFILL = "dds:agent-prefill";
export interface Prefill { text: string; askFile?: string }
export const prefillAgent = (text: string, askFile?: string) =>
  window.dispatchEvent(new CustomEvent<Prefill>(AGENT_PREFILL, { detail: { text, askFile } }));
