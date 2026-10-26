import { useQuery } from "@tanstack/react-query";
import { Laptop } from "lucide-react";
import { Link } from "react-router-dom";
import { get } from "../api";
import type { AgentStatus } from "../types";

/** 사이드바 아래: 내 PC Agent 연결 상태 한 줄 (누르면 설정·설치). compact = 접힌 사이드바용 아이콘 */
export function AgentStatusRow({ compact }: { compact?: boolean }) {
  const { data } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"), refetchInterval: 30000 });
  if (!data) return null;
  const on = data.connected;
  const title = on ? `내 PC Agent ${data.info?.version ?? ""} · ${data.info?.hostname ?? ""} — 누르면 상태·설정` : "내 PC Agent가 연결되어 있지 않습니다 — 눌러서 설치";
  if (compact) {
    return <Link to="/settings" className={`icon-btn rail-btn agent-dot-btn ${on ? "on" : ""}`} title={title} aria-label={title}><Laptop size={17} /><i aria-hidden="true" /></Link>;
  }
  return (
    <Link to="/settings" className={`agent-row ${on ? "on" : ""}`} title={title}>
      <i aria-hidden="true" />
      {on ? <>PC Agent 연결됨<small>{data.info?.hostname}</small></> : <>PC Agent 연결 안 됨<small>설치하기</small></>}
    </Link>
  );
}
