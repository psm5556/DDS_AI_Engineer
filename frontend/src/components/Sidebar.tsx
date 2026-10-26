import { useQuery } from "@tanstack/react-query";
import { Home, LogOut, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings, Shield, Sun, Users } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { get } from "../api";
import { useAuth } from "../auth";
import { FLOW_STATUS } from "../format";
import { useTheme } from "../theme";
import type { Project } from "../types";
import { NewProjectModal } from "./NewProjectModal";
import type { PanelLayout } from "./sideLayout";

/** 왼쪽 사이드바: 프로젝트 목록(검색), 내 PC Agent 상태, 사용자·테마·설정 */
export function Sidebar({ layout }: { layout: PanelLayout }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const { theme, toggle: toggleTheme } = useTheme();
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(false);
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const themeLabel = theme === "dark" ? "밝은 화면으로" : "어두운 화면으로";
  const pid = Number(loc.pathname.match(/^\/projects\/(\d+)/)?.[1]) || null;

  if (layout.collapsed) {
    return (
      <aside className="sidebar rail" aria-label="프로젝트 목록 (접힘)">
        <button className="icon-btn rail-btn" onClick={layout.toggle} aria-label="사이드바 펼치기" title="사이드바 펼치기 (Ctrl+B)"><PanelLeftOpen size={18} /></button>
        <Link className="icon-btn rail-btn" to="/" aria-label="첫 화면" title="첫 화면"><Home size={18} /></Link>
        <button className="icon-btn rail-btn primary" onClick={() => setCreating(true)} aria-label="새 프로젝트" title="새 프로젝트"><Plus size={18} /></button>
        <span className="grow" />
        <Link className="icon-btn rail-btn" to="/settings" aria-label="설정" title="설정 · PC Agent"><Settings size={18} /></Link>
        <button className="icon-btn rail-btn" onClick={toggleTheme} aria-label={themeLabel} title={themeLabel}>{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
        {user && <span className="avatar rail-me" title={user.display_name}>{user.display_name.slice(0, 1)}</span>}
        {creating && <NewProjectModal onClose={() => setCreating(false)} />}
      </aside>
    );
  }

  const term = q.trim().toLowerCase();
  const list = (projects ?? []).filter((p) => !term || p.name.toLowerCase().includes(term) || p.description.toLowerCase().includes(term));
  const mine = list.filter((p) => p.owner_id === user?.id);
  const shared = list.filter((p) => p.owner_id !== user?.id);
  const current = projects?.find((p) => p.id === pid);

  const group = (title: string, xs: Project[], empty?: string) => (xs.length > 0 || empty) && (
    <div className="side-group">
      <h4>{title} <span className="count">{xs.length}</span></h4>
      {xs.length ? xs.map((p) => {
        const st = p.flow_status ? FLOW_STATUS[p.flow_status] : null;
        const todo = p.flow_status === "review";
        return (
          <div key={p.id} className={`side-item ${p.id === pid ? "active" : ""}`}>
            <div className="side-row">
              <i className={`side-dot ${todo ? "todo" : ""}`} aria-hidden="true" style={{ marginLeft: 8 }} />
              <NavLink to={`/projects/${p.id}`} className="side-name" title={p.description || p.name}>{p.name}</NavLink>
            </div>
            {p.members.length > 1 && (
              <div className="side-marks" style={{ marginLeft: 25 }}><span className="mark" title="멤버 수"><Users size={11} />멤버 {p.members.length}</span></div>
            )}
            <div className={`side-next ${todo ? "mine" : ""}`} style={{ marginLeft: 25 }}>
              {st ? `${st.label} · ${p.flow_title}` : "에이전트에게 작업을 요청하세요"}
            </div>
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
        <button className="primary side-new" onClick={() => setCreating(true)}><Plus size={15} />새 프로젝트</button>
        <div className="side-search">
          <Search size={14} />
          <input type="search" placeholder="프로젝트 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="프로젝트 검색" />
        </div>
        {projects === undefined ? <p className="small muted">불러오는 중</p> : (
          <>
            {group("내 프로젝트", mine, list.length ? undefined : term ? "검색 결과가 없습니다." : "아직 없습니다. 위 버튼으로 시작하세요.")}
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
      <div className="side-resizer" role="separator" aria-orientation="vertical" aria-label="사이드바 너비 조절"
        aria-valuenow={layout.width} aria-valuemin={layout.min} aria-valuemax={layout.max} tabIndex={0}
        title="끌어서 너비 조절 · 두 번 누르면 기본 너비" onPointerDown={startResize} onDoubleClick={layout.reset}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); layout.setWidth(layout.width - 16); }
          if (e.key === "ArrowRight") { e.preventDefault(); layout.setWidth(layout.width + 16); }
        }} />
      {creating && <NewProjectModal onClose={() => setCreating(false)} />}
    </aside>
  );
}
