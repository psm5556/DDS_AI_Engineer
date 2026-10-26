import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, MessageSquare, PanelRight, Pencil, SquarePen, Trash2, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { del, get, put } from "../api";
import { useAuth } from "../auth";
import { CanvasProvider } from "../canvas/CanvasContext";
import { CanvasPanel } from "../canvas/CanvasPanel";
import { MembersPanel } from "../canvas/views";
import { ChatView } from "../chat/ChatView";
import { Confirm, Modal } from "../components/Modal";
import { ProjectMenu, RenameModal } from "../components/ProjectMenu";
import { useConversaLayout } from "../components/sideLayout";
import { ROLE_LABEL, when } from "../format";
import { useToast } from "../toast";
import type { ChatSession, Project } from "../types";

/** 프로젝트 작업 공간: 가운데 대화(주인공) + 오른쪽 캔버스(켜고 끄기).
 *  주소: /projects/:pid (최근 대화) · /projects/:pid/c/:sid (특정 대화) · /projects/:pid/new (새 대화) */
export default function WorkspacePage() {
  const { pid: pidS, sid: sidS } = useParams();
  const pid = Number(pidS);
  const loc = useLocation();
  const nav = useNavigate();
  const wide = useConversaLayout();
  // 좁은 화면(모바일)에서는 캔버스를 전체 화면 시트로 — 저장된 데스크톱 상태와 따로, 처음엔 닫힌 채로
  const narrow = useNarrow();
  const [sheet, setSheet] = useState(false);
  const canvasLayout = narrow
    ? { ...wide, collapsed: !sheet, setCollapsed: (v: boolean) => setSheet(!v), toggle: () => setSheet((o) => !o) }
    : wide;
  const [members, setMembers] = useState(false);
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  // 첫 화면에서 넘어온 요청(과 첨부)
  const [initial, setInitial] = useState<{ text: string; attachmentIds: number[] } | null>(() => {
    const st = loc.state as { prompt?: string; attachmentIds?: number[] } | null;
    return st?.prompt || st?.attachmentIds?.length ? { text: st.prompt ?? "", attachmentIds: st.attachmentIds ?? [] } : null;
  });
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
  const canvasOn = !canvasLayout.collapsed;

  return (
    <CanvasProvider>
      <div className={`workspace ${canvasOn ? "" : "canvas-collapsed"}`} style={{ "--canvas-w": `${canvasLayout.width}px` } as React.CSSProperties}>
        <div className="ws-main">
          <header className="ws-head">
            <button className="ws-crumb ws-project" title="프로젝트 메뉴 — 이름 바꾸기 · 즐겨찾기 · 멤버 · 폴더 열기 · 삭제"
              onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenuAt({ x: r.left, y: r.bottom + 4 }); }}>
              <span className="t">{p.name}</span><ChevronDown size={14} />
            </button>
            <span className="ws-sep" aria-hidden="true">/</span>
            <ConversationSwitcher p={p} sessions={sessions ?? []} current={sid} />
            <span className="grow" />
            <button className="icon-btn ws-icon" onClick={() => setMembers(true)} title={`멤버 ${p.members.length}명 · 공유`} aria-label="멤버·공유">
              <Users size={17} />{p.members.length > 1 && <small>{p.members.length}</small>}</button>
            <button className={`icon-btn ws-icon ${canvasOn ? "on" : ""}`} onClick={canvasLayout.toggle} aria-pressed={canvasOn}
              title={canvasOn ? "캔버스 닫기 (Ctrl+J)" : "캔버스 열기 — 작업 흐름·결과·데이터 (Ctrl+J)"} aria-label="캔버스 열기/닫기"><PanelRight size={17} /></button>
          </header>
          <ChatView key={sid ?? "new"} pid={pid} sid={sid} onSession={onSession}
            initialPrompt={initial} onInitialSent={() => setInitial(null)} />
        </div>
        <CanvasPanel pid={pid} layout={canvasLayout} />
      </div>
      {menuAt && <ProjectMenu p={p} at={menuAt} onClose={() => setMenuAt(null)} />}
      {members && (
        <Modal title={`멤버·공유 — ${p.name}`} wide onClose={() => setMembers(false)}>
          <p className="small muted" style={{ marginBottom: 12 }}>내 권한: {ROLE_LABEL[p.user_role ?? "member"]}</p>
          <MembersPanel p={p} />
        </Modal>
      )}
    </CanvasProvider>
  );
}

function useNarrow() {
  const q = "(max-width: 900px)";
  const [v, setV] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const h = () => setV(m.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, []);
  return v;
}

/** 머리글의 대화 전환: 현재 대화 이름 ▾ → 새 대화 · 대화 목록(이름 바꾸기·삭제) */
function ConversationSwitcher({ p, sessions, current }: { p: Project; sessions: ChatSession[]; current: number | null }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState<ChatSession | null>(null);
  const [removing, setRemoving] = useState<ChatSession | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [open]);
  const cur = sessions.find((s) => s.id === current);
  const canEdit = (s: ChatSession) => s.user_id === user?.id || p.user_role === "master" || !!user?.is_admin;
  const refresh = () => qc.invalidateQueries({ queryKey: ["sessions", p.id] });
  const go = (to: string) => { setOpen(false); nav(to); };

  return (
    <div className="ws-conv" ref={ref}>
      <button className="ws-crumb" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} title="대화 바꾸기 · 새 대화">
        <span className="t">{cur ? cur.title : "새 대화"}</span>
        {sessions.length > 1 && <small className="muted">{sessions.length}</small>}
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="ws-conv-menu" role="menu">
          <button role="menuitem" className="ws-conv-new" onClick={() => go(`/projects/${p.id}/new`)}><SquarePen size={14} />새 대화</button>
          {sessions.length > 0 && <div className="ws-conv-sep">대화 {sessions.length}</div>}
          <div className="ws-conv-list">
            {sessions.map((s) => (
              <div key={s.id} className={`ws-conv-item ${s.id === current ? "on" : ""}`}>
                <button role="menuitem" className="ws-conv-open" onClick={() => go(`/projects/${p.id}/c/${s.id}`)} title={s.title}>
                  {s.id === current ? <Check size={14} /> : <MessageSquare size={14} />}
                  <span className="t">{s.title}</span>
                  <span className="m">{s.user_id !== user?.id ? "멤버 · " : ""}{when(s.created_at)}</span>
                </button>
                {canEdit(s) && (
                  <span className="ws-conv-acts">
                    <button className="icon-btn" title="이름 바꾸기" aria-label={`${s.title} 이름 바꾸기`} onClick={() => { setOpen(false); setRenaming(s); }}><Pencil size={13} /></button>
                    <button className="icon-btn danger" title="삭제" aria-label={`${s.title} 삭제`} onClick={() => { setOpen(false); setRemoving(s); }}><Trash2 size={13} /></button>
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {renaming && <RenameModal title="대화 이름 바꾸기" value={renaming.title} onClose={() => setRenaming(null)}
        onSave={(title) => put(`/api/sessions/${renaming.id}`, { title }).then(() => { refresh(); setRenaming(null); toast("이름을 바꿨습니다"); })
          .catch((e: Error) => toast(e.message, true))} />}
      {removing && <Confirm title="대화 삭제" danger confirmLabel="삭제" onClose={() => setRemoving(null)}
        message={<>‘{removing.title}’ 대화와 메시지가 삭제됩니다. 작업 흐름·결과·데이터는 프로젝트에 남습니다.</>}
        onConfirm={() => del(`/api/sessions/${removing.id}`).then(() => {
          refresh(); toast("대화를 삭제했습니다");
          if (removing.id === current) nav(`/projects/${p.id}/new`);
        }).catch((e: Error) => toast(e.message, true))} />}
    </div>
  );
}
