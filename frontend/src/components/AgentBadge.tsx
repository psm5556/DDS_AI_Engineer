import { useQuery } from "@tanstack/react-query";
import { Laptop } from "lucide-react";
import { Link } from "react-router-dom";
import { get } from "../api";
import { useServerDown } from "../live";
import type { AgentStatus } from "../types";

/** 사이드바 아래: 내 PC Agent 연결 상태 한 줄 (누르면 설정·설치). compact = 접힌 사이드바용 아이콘 */
export function AgentStatusRow({ compact }: { compact?: boolean }) {
  const { data } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"), refetchInterval: 30000 });
  const serverDown = useServerDown();
  if (!data) return null;
  const on = data.connected && !serverDown; // 서버에 닿지 않으면 마지막에 읽은 '연결됨'을 믿을 수 없다
  const title = serverDown ? "웹앱 서버에 연결할 수 없어 PC Agent 상태를 알 수 없습니다. 다시 연결을 시도하고 있습니다" : on ? `내 PC Agent ${data.info?.version ?? ""} · ${data.info?.hostname ?? ""} — 누르면 상태·설정` : "내 PC Agent가 연결되어 있지 않습니다 — 눌러서 설치";
  if (compact) {
    return <Link to="/settings" className={`icon-btn rail-btn agent-dot-btn ${on ? "on" : ""}`} title={title} aria-label={title}><Laptop size={17} /><i aria-hidden="true" /></Link>;
  }
  return (
    <Link to="/settings" className={`agent-row ${on ? "on" : ""}`} title={title}>
      <i aria-hidden="true" />
      {serverDown ? <>서버 연결 끊김<small>다시 연결하는 중</small></>
        : on ? <>PC Agent 연결됨<small>{data.info?.hostname}</small></> : <>PC Agent 연결 안 됨<small>설치하기</small></>}
    </Link>
  );
}
