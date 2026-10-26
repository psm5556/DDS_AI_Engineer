import { useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { get } from "../api";
import { CanvasProvider } from "../canvas/CanvasContext";
import { CanvasPanel } from "../canvas/CanvasPanel";
import { MembersPanel } from "../canvas/views";
import { ChatView } from "../chat/ChatView";
import { AgentBadge } from "../components/AgentBadge";
import { Modal } from "../components/Modal";
import { useConversaLayout } from "../components/sideLayout";
import { ROLE_LABEL } from "../format";
import type { ChatSession, Project } from "../types";

/** 프로젝트 작업 공간: 가운데 대화(주인공) + 오른쪽 라이브 캔버스.
 *  주소: /projects/:pid (최근 대화) · /projects/:pid/c/:sid (특정 대화) · /projects/:pid/new (새 대화) */
export default function WorkspacePage() {
  const { pid: pidS, sid: sidS } = useParams();
  const pid = Number(pidS);
  const loc = useLocation();
  const nav = useNavigate();
  const canvasLayout = useConversaLayout();
  const [members, setMembers] = useState(false);
  const [initial, setInitial] = useState<string | null>((loc.state as { prompt?: string } | null)?.prompt ?? null);
  const isNew = loc.pathname.endsWith("/new");
  const { data: p, error } = useQuery({ queryKey: ["project", pid], queryFn: () => get<Project>(`/api/projects/${pid}`) });
  const { data: sessions } = useQuery({ queryKey: ["sessions", pid], queryFn: () => get<ChatSession[]>(`/api/projects/${pid}/sessions`) });

  // 대화를 고르지 않았으면 가장 최근 대화로
  useEffect(() => {
    if (!sidS && !isNew && sessions?.length) nav(`/projects/${pid}/c/${sessions[0].id}`, { replace: true });
  }, [sidS, isNew, sessions, pid, nav]);
  const onSession = useCallback((id: number) => nav(`/projects/${pid}/c/${id}`, { replace: true }), [nav, pid]);
  const sid = sidS ? Number(sidS) : null;

  if (error) return <div className="page"><div className="notice err">{(error as Error).message}</div></div>;
  if (!p) return <div className="page"><div className="busy"><span className="spinner" /> 불러오는 중</div></div>;
  const session = sessions?.find((s) => s.id === sid);

  return (
    <CanvasProvider>
      <div className={`workspace ${canvasLayout.collapsed ? "canvas-collapsed" : ""}`} style={{ "--canvas-w": `${canvasLayout.width}px` } as React.CSSProperties}>
        <div className="ws-main">
          <header className="ws-head">
            <div className="grow" style={{ minWidth: 0 }}>
              <h1 title={p.description}>{p.name}</h1>
              <div className="ws-sub">{session ? session.title : "새 대화"}{p.description ? ` · ${p.description}` : ""}</div>
            </div>
            <button className="small ghost" onClick={() => setMembers(true)} title="멤버·공유·프로젝트 정보">
              <Users size={14} />멤버 {p.members.length}</button>
            <AgentBadge />
          </header>
          <ChatView key={sid ?? "new"} pid={pid} sid={sid} onSession={onSession}
            initialPrompt={initial} onInitialSent={() => setInitial(null)} />
        </div>
        <CanvasPanel pid={pid} layout={canvasLayout} />
      </div>
      {members && (
        <Modal title={`멤버·설정 — ${p.name}`} wide onClose={() => setMembers(false)}>
          <p className="small muted" style={{ marginBottom: 12 }}>내 권한: {ROLE_LABEL[p.user_role ?? "member"]}</p>
          <MembersPanel p={p} />
        </Modal>
      )}
    </CanvasProvider>
  );
}
