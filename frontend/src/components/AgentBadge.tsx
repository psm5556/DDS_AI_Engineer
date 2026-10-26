import { useQuery } from "@tanstack/react-query";
import { Laptop } from "lucide-react";
import { Link } from "react-router-dom";
import { get } from "../api";
import type { AgentStatus } from "../types";

/** 작업 영역 오른쪽 위: 내 PC Agent 연결 상태 (누르면 설정·설치) */
export function AgentBadge() {
  const { data } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"), refetchInterval: 30000 });
  if (!data) return null;
  const on = data.connected;
  return (
    <Link to="/settings" className={`agent-badge ${on ? "on" : ""}`}
      title={on ? `내 PC Agent ${data.info?.version ?? ""} · ${data.info?.hostname ?? ""} — 누르면 상태·설정` : "내 PC Agent가 연결되어 있지 않습니다 — 눌러서 설치"}>
      <i aria-hidden="true" /><Laptop size={13} />
      {on ? <>PC Agent 연결됨<small>{data.info?.hostname}</small></> : "PC Agent 연결 안 됨 · 설치"}
    </Link>
  );
}
