import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown, ChevronRight, Home, LogOut, MessageSquare, Moon, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Search,
  Settings, Shield, SquarePen, Star, Sun, Trash2, Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { del, get, put } from "../api";
import { useAuth } from "../auth";
import { FLOW_STATUS, when } from "../format";
import { useTheme } from "../theme";
import { useToast } from "../toast";
import type { ChatSession, Project } from "../types";
import { Confirm } from "./Modal";
import { ProjectMenu, RenameModal } from "./ProjectMenu";
import type { PanelLayout } from "./sideLayout";

/** 열려 있는 프로젝트의 대화 목록 */
function Sessions({ p, current }: { p: Project; current: number | null }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [more, setMore] = useState(false);
  const [renaming, setRenaming] = useState<ChatSession | null>(null);
  const [removing, setRemoving] = useState<ChatSession | null>(null);
  const { data } = useQuery({ queryKey: ["sessions", p.id], queryFn: () => get<ChatSession[]>(`/api/projects/${p.id}/sessions`) });
  const list = data ?? [];
  const canEdit = (s: ChatSession) => s.user_id === user?.id || p.user_role === "master" || !!user?.is_admin;
  const refresh = () => qc.invalidateQueries({ queryKey: ["sessions", p.id] });
  return (
    <ul className="side-sessions">
      <li><NavLink to={`/projects/${p.id}/new`} className="side-session new"><SquarePen size={12} />새 대화</NavLink></li>
      {(more ? list : list.slice(0, 8)).map((s) => (
        <li key={s.id} className="side-session-row">
          <Link to={`/projects/${p.id}/c/${s.id}`} className={`side-session ${s.id === current ? "here" : ""}`} title={s.title}>
            <MessageSquare size={12} /><span className="t">{s.title}</span>
            <span className="m">{s.user_id !== user?.id ? "멤버 · " : ""}{when(s.created_at)}</span>
          </Link>
          {canEdit(s) && (
            <span className="side-session-acts">
              <button className="icon-btn" title="이름 바꾸기" aria-label={`${s.title} 이름 바꾸기`} onClick={() => setRenaming(s)}><Pencil size={12} /></button>
              <button className="icon-btn danger" title="삭제" aria-label={`${s.title} 삭제`} onClick={() => setRemoving(s)}><Trash2 size={12} /></button>
            </span>
          )}
        </li>
      ))}
      {list.length > 8 && <li><button className="link-btn side-more-sessions" onClick={() => setMore(!more)}>{more ? "접기" : `대화 ${list.length - 8}개 더 보기`}</button></li>}
      {renaming && <RenameModal title="대화 이름 바꾸기" value={renaming.title} onClose={() => setRenaming(null)}
        onSave={(title) => put(`/api/sessions/${renaming.id}`, { title }).then(() => { refresh(); setRenaming(null); toast("이름을 바꿨습니다"); })
          .catch((e: Error) => toast(e.message, true))} />}
      {removing && <Confirm title="대화 삭제" danger confirmLabel="삭제" onClose={() => setRemoving(null)}
        message={<>‘{removing.title}’ 대화와 메시지가 삭제됩니다. 작업 흐름·결과·데이터는 프로젝트에 남습니다.</>}
        onConfirm={() => del(`/api/sessions/${removing.id}`).then(() => {
          refresh(); toast("대화를 삭제했습니다");
          if (removing.id === current) nav(`/projects/${p.id}/new`);
        }).catch((e: Error) => toast(e.message, true))} />}
    </ul>
  );
}

/** 왼쪽 사이드바: 새 작업 · 프로젝트(펼치면 대화 목록) · 사용자 */
export function Sidebar({ layout }: { layout: PanelLayout }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const { theme, toggle: toggleTheme } = useTheme();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [menu, setMenu] = useState<{ p: Project; x: number; y: number } | null>(null);
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const themeLabel = theme === "dark" ? "밝은 화면으로" : "어두운 화면으로";
  const pid = Number(loc.pathname.match(/^\/projects\/(\d+)/)?.[1]) || null;
  const sid = Number(loc.pathname.match(/\/c\/(\d+)/)?.[1]) || null;
  useEffect(() => { if (pid) setExpanded((x) => ({ ...x, [pid]: true })); }, [pid]);

  if (layout.collapsed) {
    return (
      <aside className="sidebar rail" aria-label="프로젝트 목록 (접힘)">
        <button className="icon-btn rail-btn" onClick={layout.toggle} aria-label="사이드바 펼치기" title="사이드바 펼치기 (Ctrl+B)"><PanelLeftOpen size={18} /></button>
        <Link className="icon-btn rail-btn primary" to="/" aria-label="새 작업" title="새 작업"><Plus size={18} /></Link>
        <Link className="icon-btn rail-btn" to="/" aria-label="첫 화면" title="첫 화면"><Home size={18} /></Link>
        <span className="grow" />
        <Link className="icon-btn rail-btn" to="/settings" aria-label="설정" title="설정 · PC Agent"><Settings size={18} /></Link>
        <button className="icon-btn rail-btn" onClick={toggleTheme} aria-label={themeLabel} title={themeLabel}>{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
        {user && <span className="avatar rail-me" title={user.display_name}>{user.display_name.slice(0, 1)}</span>}
      </aside>
    );
  }

  const term = q.trim().toLowerCase();
  const list = (projects ?? []).filter((p) => !term || p.name.toLowerCase().includes(term) || p.description.toLowerCase().includes(term));
  const favs = list.filter((p) => p.is_favorite);
  const mine = list.filter((p) => p.owner_id === user?.id && !p.is_favorite);
  const shared = list.filter((p) => p.owner_id !== user?.id && !p.is_favorite);
  const current = projects?.find((p) => p.id === pid);

  const group = (title: string, xs: Project[], empty?: string) => (xs.length > 0 || empty) && (
    <div className="side-group">
      <h4>{title} <span className="count">{xs.length}</span></h4>
      {xs.length ? xs.map((p) => {
        const st = p.flow_status ? FLOW_STATUS[p.flow_status] : null;
        const todo = p.flow_status === "review";
        const isOpen = !!expanded[p.id];
        return (
          <div key={p.id} className={`side-item ${p.id === pid ? "active" : ""}`}
            onContextMenu={(e) => { e.preventDefault(); setMenu({ p, x: e.clientX, y: e.clientY }); }}>
            <div className="side-row">
              <button className="side-caret" onClick={() => setExpanded({ ...expanded, [p.id]: !isOpen })} aria-expanded={isOpen}
                aria-label={`${p.name} 대화 ${isOpen ? "접기" : "펼치기"}`}>{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
              <i className={`side-dot ${todo ? "todo" : ""}`} aria-hidden="true" />
              <NavLink to={`/projects/${p.id}`} className="side-name" title={p.description || p.name}>{p.name}</NavLink>
              {p.is_favorite && <Star size={12} className="side-fav" fill="currentColor" aria-label="즐겨찾기" />}
              {p.members.length > 1 && <span className="mark" title="멤버 수 — 함께하는 프로젝트"><Users size={11} />{p.members.length}</span>}
              <button className="side-more" aria-label={`${p.name} 메뉴`} aria-haspopup="menu" title="이름 바꾸기 · 즐겨찾기 · 멤버·공유 · 폴더 열기 · 삭제"
                onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ p, x: r.left, y: r.bottom + 4 }); }}>
                <MoreHorizontal size={15} /></button>
            </div>
            {st && <div className={`side-next ${todo ? "mine" : ""}`}>{st.label} · {p.flow_title}</div>}
            {isOpen && <Sessions p={p} current={p.id === pid ? sid : null} />}
          </div>
        );
      }) : <p className="small muted" style={{ padding: "0 8px" }}>{empty}</p>}
    </div>
  );

  const startResize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = layout.width;
    document.body.classList.add("resizing");
    const move = (ev: PointerEvent) => layout.setWidth(w0 + ev.clientX - x0);
    const up = () => {
      document.body.classList.remove("resizing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="프로젝트 목록">
      <button className="side-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{current ? current.name : "프로젝트 목록"}</span><span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      <div className="side-brand-row">
        <Link to="/" className="side-brand" aria-label="DDS AI Engineer 홈"><span className="logo" aria-hidden="true">AI</span>DDS AI Engineer</Link>
        <button className="icon-btn side-fold" onClick={layout.toggle} aria-label="사이드바 접기" title="사이드바 접기 (Ctrl+B)"><PanelLeftClose size={17} /></button>
      </div>
      <div className="side-body">
        <button className="primary side-new" onClick={() => nav("/")}><Plus size={15} />새 작업</button>
        <div className="side-search">
          <Search size={14} />
          <input type="search" placeholder="프로젝트 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="프로젝트 검색" />
        </div>
        {projects === undefined ? <p className="small muted">불러오는 중</p> : (
          <>
            {group("즐겨찾기", favs)}
            {group("내 프로젝트", mine, list.length ? undefined : term ? "검색 결과가 없습니다." : "아직 없습니다. 첫 화면에서 하고 싶은 일을 말해 보세요.")}
            {group("함께하는 프로젝트", shared)}
          </>
        )}
      </div>
      {user && (
        <div className="side-me">
          <span className="avatar" aria-hidden="true">{user.display_name.slice(0, 1)}</span>
          <span className="who"><b>{user.display_name}</b><small>{user.username}{user.is_admin ? " · 관리자" : ""}</small></span>
          {user.is_admin && <Link className="icon-btn" to="/admin" aria-label="관리" title="관리 (사용자 · Agent 현황 · 배포)"><Shield size={15} /></Link>}
          <Link className="icon-btn" to="/settings" aria-label="설정" title="설정 · PC Agent"><Settings size={15} /></Link>
          <button className="icon-btn" onClick={toggleTheme} aria-label={themeLabel} title={themeLabel}>{theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}</button>
          <button className="icon-btn" title="로그아웃" aria-label="로그아웃" onClick={() => { logout(); nav("/login"); }}><LogOut size={15} /></button>
        </div>
      )}
      {menu && <ProjectMenu p={menu.p} at={menu} onClose={() => setMenu(null)} />}
      <div className="side-resizer" role="separator" aria-orientation="vertical" aria-label="사이드바 너비 조절"
        aria-valuenow={layout.width} aria-valuemin={layout.min} aria-valuemax={layout.max} tabIndex={0}
        title="끌어서 너비 조절 · 두 번 누르면 기본 너비" onPointerDown={startResize} onDoubleClick={layout.reset}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); layout.setWidth(layout.width - 16); }
          if (e.key === "ArrowRight") { e.preventDefault(); layout.setWidth(layout.width + 16); }
        }} />
    </aside>
  );
}
