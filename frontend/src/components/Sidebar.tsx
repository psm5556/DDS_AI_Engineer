import { useQuery } from "@tanstack/react-query";
import {
  ChevronsUpDown, Loader2, LogOut, Menu, Moon, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Search, Settings, Shield, SlidersHorizontal, SquarePen, Sun, Users, Wrench, X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { Wordmark } from "./AgentGlyph";
import { get } from "../api";
import { useAuth } from "../auth";
import { FLOW_STATUS } from "../format";
import { useTheme } from "../theme";
import type { Project } from "../types";
import { AgentStatusRow } from "./AgentBadge";
import { ProjectMenu } from "./ProjectMenu";
import type { PanelLayout } from "./sideLayout";

/** 프로젝트 이름 앞 상태 표시 — 손이 가야 하는 것(승인 대기·진행 중·실패)만 보인다 */
function StatusMark({ p }: { p: Project }) {
  const s = p.flow_status;
  if (s === "in_progress" || s === "approved") return <Loader2 size={13} className="spin side-st run" aria-label="진행 중" />;
  if (s === "review") return <i className="side-st-dot review" title="승인 대기" aria-label="승인 대기" />;
  if (s === "failed") return <i className="side-st-dot failed" title="실패" aria-label="실패" />;
  return null;
}

/** 왼쪽 사이드바 (Claude·Codex 데스크톱 방식): 새 프로젝트 · 툴 · 검색 · 프로젝트(한 줄 평면 목록) · 아래 PC Agent·사용자.
 *  대화 전환은 프로젝트 화면 머리글에서 한다 — 사이드바는 한 단계만. */
export function Sidebar({ layout }: { layout: PanelLayout }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const { theme, toggle: toggleTheme } = useTheme();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false); // 모바일: 서랍 열림
  const [meOpen, setMeOpen] = useState(false);
  const meRef = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<{ p: Project; x: number; y: number } | null>(null);
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const themeLabel = theme === "dark" ? "밝은 화면" : "어두운 화면";
  const pid = Number(loc.pathname.match(/^\/projects\/(\d+)/)?.[1]) || null;

  useEffect(() => { setOpen(false); }, [loc.pathname]);
  useEffect(() => {
    if (!meOpen) return;
    const h = (e: MouseEvent) => { if (!meRef.current?.contains(e.target as Node)) setMeOpen(false); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setMeOpen(false); };
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [meOpen]);

  if (layout.collapsed) {
    return (
      <aside className="sidebar rail" aria-label="사이드바 (접힘)">
        <button className="icon-btn rail-btn" onClick={layout.toggle} aria-label="사이드바 펼치기" title="사이드바 펼치기 (Ctrl+B)"><PanelLeftOpen size={18} /></button>
        <Link className="icon-btn rail-btn" to="/" aria-label="새 프로젝트" title="새 프로젝트"><SquarePen size={18} /></Link>
        <Link className="icon-btn rail-btn" to="/simulators" aria-label="Simulator" title="Simulator"><SlidersHorizontal size={18} /></Link>
        <span className="grow" />
        <AgentStatusRow compact />
        <Link className="icon-btn rail-btn" to="/tools" aria-label="CAD·CAE 툴" title="CAD·CAE 툴"><Wrench size={18} /></Link>
        <Link className="icon-btn rail-btn" to="/settings" aria-label="설정" title="설정"><Settings size={18} /></Link>
      </aside>
    );
  }

  const term = q.trim().toLowerCase();
  const list = (projects ?? []).filter((p) => !term || p.name.toLowerCase().includes(term) || p.description.toLowerCase().includes(term));
  const favs = list.filter((p) => p.is_favorite);
  const mine = list.filter((p) => p.owner_id === user?.id && !p.is_favorite);
  const shared = list.filter((p) => p.owner_id !== user?.id && !p.is_favorite);
  const current = projects?.find((p) => p.id === pid);

  const group = (title: string, xs: Project[]) => xs.length > 0 && (
    <div className="side-group">
      <h4>{title}</h4>
      {xs.map((p) => {
        const st = p.flow_status ? FLOW_STATUS[p.flow_status].label : "";
        return (
          <div key={p.id} className={`side-item ${p.id === pid ? "active" : ""}`}
            onContextMenu={(e) => { e.preventDefault(); setMenu({ p, x: e.clientX, y: e.clientY }); }}>
            <NavLink to={`/projects/${p.id}`} className="side-link" title={[p.name, st].filter(Boolean).join(" · ")}>
              <span className="side-name">{p.name}</span>
              {p.members.length > 1 && <Users size={12} className="side-meta" aria-label={`멤버 ${p.members.length}명`} />}
              <StatusMark p={p} />
            </NavLink>
            <button className="side-more" aria-label={`${p.name} 메뉴`} aria-haspopup="menu" title="이름 바꾸기 · 즐겨찾기 · 멤버 · 폴더 열기 · 삭제"
              onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ p, x: r.left, y: r.bottom + 4 }); }}>
              <MoreHorizontal size={15} /></button>
          </div>
        );
      })}
    </div>
  );

  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="사이드바">
      {/* 모바일: 위쪽 막대(메뉴 · 현재 위치) — 누르면 서랍처럼 펼친다 */}
      <div className="side-mobilebar">
        <button className="icon-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={open ? "메뉴 닫기" : "메뉴 열기"}>
          {open ? <X size={18} /> : <Menu size={18} />}</button>
        <b>{current ? current.name : "DDS Conversa"}</b>
        <Link className="icon-btn" to="/" aria-label="새 프로젝트"><SquarePen size={17} /></Link>
      </div>
      <div className="side-panel">
        <div className="side-brand-row">
          <Link to="/" className="side-brand" aria-label="DDS Conversa 홈"><Wordmark /></Link>
          <button className="icon-btn side-fold" onClick={layout.toggle} aria-label="사이드바 접기" title="사이드바 접기 (Ctrl+B)"><PanelLeftClose size={17} /></button>
        </div>
        <nav className="side-nav-list" aria-label="바로 가기">
          <button className="side-nav" onClick={() => nav("/")}><SquarePen size={16} />새 프로젝트</button>
          <NavLink className="side-nav" to="/simulators" title="내 Simulator와 공유받은 Simulator"><SlidersHorizontal size={16} />Simulator</NavLink>
          <label className="side-nav side-find">
            <Search size={16} />
            <input type="search" placeholder="프로젝트 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="프로젝트 검색" />
          </label>
        </nav>
        <div className="side-body">
          {projects === undefined ? <p className="small muted side-empty">불러오는 중</p> : (
            <>
              {group("즐겨찾기", favs)}
              {group("내 프로젝트", mine)}
              {group("함께하는 프로젝트", shared)}
              {!list.length && <p className="small muted side-empty">{term ? "검색 결과가 없습니다." : "아직 프로젝트가 없습니다. 하고 싶은 일을 말해 보세요."}</p>}
            </>
          )}
        </div>
        {user && (
          <div className="side-foot" ref={meRef}>
            <AgentStatusRow />
            <button className="side-me-btn" onClick={() => setMeOpen((o) => !o)} aria-haspopup="menu" aria-expanded={meOpen}>
              <span className="avatar" aria-hidden="true">{user.display_name.slice(0, 1)}</span>
              <span className="who"><b>{user.display_name}</b><small>{user.is_admin ? "관리자" : user.username}</small></span>
              <ChevronsUpDown size={14} className="muted" />
            </button>
            {meOpen && (
              <div className="side-me-menu" role="menu" onClick={() => setMeOpen(false)}>
                <Link role="menuitem" to="/tools"><Wrench size={15} />CAD·CAE 툴</Link>
                <Link role="menuitem" to="/settings"><Settings size={15} />설정</Link>
                {user.is_admin && <Link role="menuitem" to="/admin"><Shield size={15} />관리</Link>}
                <button role="menuitem" onClick={toggleTheme}>{theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}{themeLabel}</button>
                <hr />
                <button role="menuitem" onClick={() => { logout(); nav("/login"); }}><LogOut size={15} />로그아웃</button>
              </div>
            )}
          </div>
        )}
      </div>
      {menu && <ProjectMenu p={menu.p} at={menu} onClose={() => setMenu(null)} />}
      <div className="side-resizer" role="separator" aria-orientation="vertical" aria-label="사이드바 너비 조절"
        aria-valuenow={layout.width} aria-valuemin={layout.min} aria-valuemax={layout.max} tabIndex={0}
        title="끌어서 너비 조절 · 두 번 누르면 기본 너비" onPointerDown={(e) => {
          e.preventDefault();
          const x0 = e.clientX, w0 = layout.width;
          document.body.classList.add("resizing");
          const move = (ev: PointerEvent) => layout.setWidth(w0 + ev.clientX - x0);
          const up = () => { document.body.classList.remove("resizing"); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }} onDoubleClick={layout.reset}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); layout.setWidth(layout.width - 16); }
          if (e.key === "ArrowRight") { e.preventDefault(); layout.setWidth(layout.width + 16); }
        }} />
      {open && <div className="side-scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
    </aside>
  );
}
