import { useQuery } from "@tanstack/react-query";
import {
  ChevronsUpDown, Inbox, Layers, Loader2, LogOut, Menu, Moon, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Search, Send, Settings, Shield, SlidersHorizontal, SquarePen, Star, Sun, Users, Wrench, X,
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

type ProjectView = "all" | "favorite" | "sent" | "received";
const VIEW_KEY = "dds.projectView";
/** 프로젝트 보기. 관리자는 모든 프로젝트를 받지만, 멤버가 아닌 프로젝트는 공유받은 것이 아니다.
 *  그런 프로젝트도 관리자가 즐겨찾기하면 즐겨찾기에, 관리자가 만들고 빠진 프로젝트에 다른 멤버가 있으면 내가 공유한 것에 보인다 */
const VIEWS: { id: ProjectView; label: string; icon: typeof Layers; empty: string; has: (p: Project, me: number | undefined) => boolean }[] = [
  { id: "all", label: "전체", icon: Layers, empty: "아직 프로젝트가 없습니다. 하고 싶은 일을 말해 보세요.", has: () => true },
  { id: "favorite", label: "즐겨찾기", icon: Star, empty: "즐겨찾기한 프로젝트가 없습니다. 프로젝트 메뉴에서 고르면 여기에 모입니다.", has: (p) => p.is_favorite },
  { id: "sent", label: "내가 공유한 것", icon: Send, empty: "공유한 프로젝트가 없습니다. 프로젝트 메뉴의 멤버에서 동료를 더하면 여기에 보입니다.",
    has: (p, me) => p.owner_id === me && p.members.some((m) => m.user_id !== me) },
  { id: "received", label: "공유 받은 것", icon: Inbox, empty: "공유 받은 프로젝트가 없습니다. 동료가 멤버로 더하면 여기에 보입니다.",
    has: (p, me) => p.owner_id !== me && p.members.some((m) => m.user_id === me) },
];

function savedProjectView(): ProjectView {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return VIEWS.find((x) => x.id === v)?.id ?? "all";
  } catch { return "all"; }
}

/** 왼쪽 사이드바 (Claude·Codex 데스크톱 방식): Simulator · 구분선 · 새 프로젝트 · 프로젝트 검색 · 프로젝트 보기 아이콘(전체·즐겨찾기·내가 공유한 것·공유 받은 것)
 *  · 고른 보기의 평면 목록(최근 작업 순) · 아래 PC Agent·사용자. 접으면 같은 순서로 아이콘만 남는다.
 *  고른 보기 밖이라도 지금 연 프로젝트는 따로 한 줄 보인다. 방금 만든 프로젝트가 사라진 것처럼 보이지 않게.
 *  대화 전환은 프로젝트 화면 머리글에서 한다 — 사이드바는 한 단계만. */
export function Sidebar({ layout }: { layout: PanelLayout }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const { theme, toggle: toggleTheme } = useTheme();
  const [q, setQ] = useState("");
  const [view, setView] = useState<ProjectView>(savedProjectView);
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
        <NavLink className="icon-btn rail-btn" to="/simulators" aria-label="Simulator" title="Simulator"><SlidersHorizontal size={18} /></NavLink>
        <hr className="rail-sep" />
        <Link className="icon-btn rail-btn" to="/" aria-label="새 프로젝트" title="새 프로젝트"><SquarePen size={18} /></Link>
        <span className="grow" />
        <AgentStatusRow compact />
        <Link className="icon-btn rail-btn" to="/tools" aria-label="CAD·CAE 툴" title="CAD·CAE 툴"><Wrench size={18} /></Link>
        <Link className="icon-btn rail-btn" to="/settings" aria-label="설정" title="설정"><Settings size={18} /></Link>
      </aside>
    );
  }

  const term = q.trim().toLowerCase();
  const found = (projects ?? []).filter((p) => !term || p.name.toLowerCase().includes(term) || p.description.toLowerCase().includes(term));
  const chosen = VIEWS.find((v) => v.id === view) ?? VIEWS[0];
  const list = found.filter((p) => chosen.has(p, user?.id)); // 서버가 최근 작업 순으로 준다
  const current = projects?.find((p) => p.id === pid);
  const pinned = current && found.includes(current) && !chosen.has(current, user?.id) ? current : null;
  // 보기 밖 프로젝트의 승인 대기·실패는 목록에 안 보이므로 전체 아이콘에 표시한다. 손이 가야 하는 것을 놓치지 않게.
  // 관리자라서 보이는 남의 프로젝트는 내 일이 아니므로 세지 않는다. 그러지 않으면 점이 늘 켜져 뜻이 없어진다
  const hidden = (projects ?? []).filter((p) => p !== pinned && !chosen.has(p, user?.id) && p.members.some((m) => m.user_id === user?.id));
  const alert = hidden.some((p) => p.flow_status === "review") ? "review" : hidden.some((p) => p.flow_status === "failed") ? "failed" : null;
  const chooseView = (v: ProjectView) => {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* 이번 화면에서만 기억한다 */ }
  };
  const item = (p: Project) => {
    const st = p.flow_status ? FLOW_STATUS[p.flow_status].label : "";
    return (
      <div key={p.id} className={`side-item ${p.id === pid ? "active" : ""}`}
        onContextMenu={(e) => { e.preventDefault(); setMenu({ p, x: e.clientX, y: e.clientY }); }}>
        <NavLink to={`/projects/${p.id}`} className="side-link" title={[p.name, st].filter(Boolean).join(" · ")}>
          <span className="side-name">{p.name}</span>
          {/* 즐겨찾기 보기에서는 모두 즐겨찾기이므로 표시하지 않는다 */}
          {p.is_favorite && view !== "favorite" && <Star size={12} className="side-fav" fill="currentColor" aria-label="즐겨찾기" />}
          {p.members.length > 1 && <Users size={12} className="side-meta" aria-label={`멤버 ${p.members.length}명`} />}
          <StatusMark p={p} />
        </NavLink>
        <button className="side-more" aria-label={`${p.name} 메뉴`} aria-haspopup="menu" title="이름 바꾸기 · 즐겨찾기 · 멤버 · 폴더 열기 · 삭제"
          onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ p, x: r.left, y: r.bottom + 4 }); }}>
          <MoreHorizontal size={15} /></button>
      </div>
    );
  };

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
          <NavLink className="side-nav" to="/simulators" title="내 Simulator와 공유받은 Simulator"><SlidersHorizontal size={16} />Simulator</NavLink>
          <hr className="side-sep" />
          <button className="side-nav" onClick={() => nav("/")}><SquarePen size={16} />새 프로젝트</button>
          <label className="side-nav side-find">
            <Search size={16} />
            <input type="search" placeholder="프로젝트 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="프로젝트 검색" />
          </label>
        </nav>
        <div className="side-views" role="group" aria-label="프로젝트 보기">
          {VIEWS.map((v) => {
            const n = found.filter((p) => v.has(p, user?.id)).length;
            const dot = v.id === "all" && alert;
            const tip = `${v.label} ${n}개${dot ? ` · ${alert === "review" ? "승인 대기" : "실패"} 있음` : ""}`;
            // 이름은 글자 풍선으로 보인다. 브라우저 툴팁(title)과 달리 키보드로 왔을 때도 보인다
            return (
              <button key={v.id} className={view === v.id ? "on" : ""} onClick={() => chooseView(v.id)} aria-pressed={view === v.id}
                aria-label={tip} data-tip={tip} data-view={v.id}>
                <v.icon size={15} />{n > 0 && <small>{n}</small>}
                {dot && <i className={`side-st-dot ${alert}`} aria-hidden="true" />}
              </button>
            );
          })}
        </div>
        <div className="side-body">
          {projects === undefined ? <p className="small muted side-empty">불러오는 중</p> : (
            <>
              {pinned && <div className="side-group side-pinned"><h4>지금 연 프로젝트</h4>{item(pinned)}</div>}
              <div className="side-group">
                <h4>{chosen.label}</h4>
                {list.map(item)}
                {!list.length && <p className="small muted side-empty">{term ? `'${chosen.label}'에서 찾지 못했습니다.` : chosen.empty}</p>}
              </div>
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
