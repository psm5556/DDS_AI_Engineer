import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, FolderOpen, RefreshCw, Search, Wrench, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { get, post, put } from "../api";
import { ToolIcon, toolLabel } from "../components/ToolIcon";
import { when } from "../format";
import { useToast } from "../toast";
import type { AgentStatus } from "../types";

interface CatalogTool {
  id: string; name: string; vendor: string; category: string; skill: string; paths: string[]; optional?: string[];
  run: string[]; can: string[]; files?: Record<string, string[]>;
}
type Source = "picked" | "config" | "path" | "auto";
interface Exe { key: string; configured: string; found: string | null; optional: boolean; source?: Source | null; version?: string | null }
interface ToolState { id: string; status: "ready" | "partial" | "missing"; note: string | null; executables: Exe[] }
interface Diag { catalog?: ToolState[]; scan?: { scanned_at?: string | null; seconds?: number | null } }

const STATUS = {
  ready: { label: "바로 사용", cls: "ok", icon: CheckCircle2 },
  partial: { label: "일부만 찾음", cls: "warn", icon: CircleDashed },
  missing: { label: "찾지 못함", cls: "off", icon: null },
} as const;
const SOURCE: Record<Source, string> = { auto: "자동으로 찾음", path: "자동으로 찾음", picked: "직접 고름", config: "설정 파일" };
const DIAG = ["agent", "diagnose"];

/** CAD·CAE 툴: 에이전트가 다룰 수 있는 툴과 내 PC 설치 상태 — 분야별 앱 아이콘으로 한눈에.
 *  툴 경로는 PC Agent가 알아서 찾고(설치할 때·실행 중 설치 상태가 바뀔 때) 바뀌면 이 화면도 저절로 새로 고친다.
 *  못 찾은 툴만 눌러서 실행 파일을 고른다 — 설정 파일을 고칠 일은 없다. */
export default function ToolsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [mine, setMine] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { data: cat } = useQuery({ queryKey: ["tools", "catalog"], queryFn: () => get<{ categories: string[]; tools: CatalogTool[] }>("/api/tools/catalog"), staleTime: Infinity });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const diag = useQuery({
    queryKey: DIAG, enabled: !!agent?.connected, staleTime: 300_000, retry: false,
    queryFn: () => get<Diag>("/api/agent/diagnose"),
  });
  // PC Agent가 툴 설치·제거를 알아채 경로를 바꾸면(tools_rev) 다시 읽는다
  const rev = agent?.info?.tools_rev;
  const lastRev = useRef(rev);
  useEffect(() => {
    if (rev && lastRev.current && rev !== lastRev.current) qc.invalidateQueries({ queryKey: DIAG });
    lastRev.current = rev;
  }, [rev, qc]);
  const rescan = useMutation({
    mutationFn: () => get<Diag>("/api/agent/diagnose?rescan=true"),
    onSuccess: (d) => qc.setQueryData(DIAG, d),
    onError: (e: Error) => toast(e.message),
  });
  const busy = diag.isFetching || rescan.isPending;
  const state = useMemo(() => Object.fromEntries((diag.data?.catalog ?? []).map((t) => [t.id, t])), [diag.data]);
  const tools = cat?.tools ?? [];
  const count = (s: ToolState["status"]) => tools.filter((t) => state[t.id]?.status === s).length;
  const term = q.trim().toLowerCase();
  const shown = tools.filter((t) => (!mine || (state[t.id] && state[t.id].status !== "missing"))
    && (!term || [t.name, t.vendor, t.category, ...t.can].some((x) => x.toLowerCase().includes(term))));
  const cats = (cat?.categories ?? []).filter((c) => shown.some((t) => t.category === c));
  const sel = tools.find((t) => t.id === open);
  const scanned = diag.data?.scan?.scanned_at;

  return (
    <div className="page tools-page">
      <h1>CAD·CAE 툴</h1>
      <p className="page-sub">대화에서 툴 이름을 말하면 에이전트가 해당 스킬로 자동화합니다. 툴은 PC Agent가 내 PC에서 알아서 찾고, 설치·제거하면 저절로 반영합니다.</p>

      {/* 상태 한 줄: 무엇을 확인했는지와 '다시 확인'을 붙여 둔다 */}
      <div className="tools-status">
        {!agent?.connected ? (
          <span className="notice-inline warn">PC Agent가 연결되지 않아 내 PC 설치 상태를 볼 수 없습니다 · <Link to="/settings">설치·연결</Link></span>
        ) : busy ? (
          <span className="small muted"><span className="spinner" style={{ width: 12, height: 12 }} /> 내 PC에서 툴을 찾는 중… (설치된 앱·시작 메뉴·모든 드라이브)</span>
        ) : diag.error ? (
          <span className="notice-inline warn">{(diag.error as Error).message}</span>
        ) : diag.data ? (
          <>
            <span className="tools-count"><b>{count("ready")}</b> 바로 사용</span>
            {count("partial") > 0 && <span className="tools-count warn"><b>{count("partial")}</b> 일부만 찾음</span>}
            <span className="tools-count off"><b>{count("missing")}</b> 찾지 못함</span>
            <span className="small muted">· 지원 {tools.length}종{scanned ? ` · ${when(scanned.replace(" ", "T"))} 확인` : ""}</span>
          </>
        ) : null}
        {agent?.connected && !busy && (
          <button className="link-btn small tools-recheck" onClick={() => rescan.mutate()} title="지금 내 PC를 다시 훑어 툴을 찾습니다">
            <RefreshCw size={12} />다시 찾기</button>
        )}
      </div>

      {/* 검색 · 필터 한 줄 */}
      <div className="tools-filter">
        <label className="tools-search"><Search size={15} />
          <input type="search" placeholder="툴·분야·기능 검색 (예: Abaqus, 유동, 메쉬)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="툴 검색" /></label>
        <label className={`tools-mine ${diag.data ? "" : "off"}`} title={diag.data ? "" : "PC Agent가 연결되면 쓸 수 있습니다"}>
          <input type="checkbox" checked={mine} disabled={!diag.data} onChange={(e) => setMine(e.target.checked)} />내 PC에 있는 것만</label>
      </div>

      {/* 분야별 앱 아이콘: 바로 쓸 수 있는 툴은 컬러+체크, 없는 툴은 흐리게 — 한눈에 */}
      {diag.data && (
        <div className="tool-legend small muted">
          <span><i className="lg ok" />바로 사용</span><span><i className="lg warn" />일부만 찾음</span><span><i className="lg off" />찾지 못함(흐리게) — 설치돼 있다면 눌러서 실행 파일을 고르세요</span>
        </div>
      )}
      {/* 분야마다 작은 카드 — 카드를 나란히 놓아 전체 툴이 한 화면에 들어온다 */}
      <div className="tool-cats">
        {cats.map((c) => {
          const list = shown.filter((t) => t.category === c);
          return (
            // 카드 너비 = 툴 수에 비례(줄마다 남는 폭은 툴 수 비율로 나눠 채움) — 한 화면에 들어오게
            <section key={c} className="tool-cat" style={{ flex: `${list.length} 1 ${Math.max(150, list.length * 80 + 16)}px` }}>
              <h2>{c}<span className="cnt">{list.length}</span></h2>
              <div className="tool-apps">
                {list.map((t) => {
                  const st = state[t.id];
                  const S = st ? STATUS[st.status] : null;
                  return (
                    <button key={t.id} className={`tool-app ${open === t.id ? "on" : ""} ${st?.status ?? ""}`} onClick={() => setOpen(t.id)}
                      title={`${t.name} · ${t.vendor}${S ? ` — ${S.label}` : ""}`} aria-label={`${t.name}${S ? `, ${S.label}` : ""}`}>
                      <ToolIcon id={t.id} name={t.name} status={st?.status} size={48} />
                      <span className="tool-app-name">{toolLabel(t.id, t.name)}</span>
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
      {!!cat && !shown.length && <div className="empty-hero"><Wrench size={28} /><b>조건에 맞는 툴이 없습니다</b></div>}
      {sel && <ToolDrawer t={sel} st={state[sel.id]} connected={!!agent?.connected} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** 툴 자세히 (오른쪽 패널): 할 수 있는 일 · 내 PC의 실행 파일(자동으로 찾은 경로, 못 찾았으면 파일 고르기) */
function ToolDrawer({ t, st, connected, onClose }: { t: CatalogTool; st?: ToolState; connected: boolean; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose, t.id]);
  const done = (r: { path: string; warning?: string | null }, reset = false) => {
    if (!r.path && !reset) return; // 선택 창에서 취소
    toast(reset ? "자동으로 찾은 경로로 되돌렸습니다" : r.warning ? `저장했습니다 — ${r.warning}` : "저장했습니다");
    qc.invalidateQueries({ queryKey: DIAG });
  };
  // 내 PC(PC Agent)에 파일 선택 창을 띄워 실행 파일을 고른다
  const pick = useMutation({
    mutationFn: (key: string) => post<{ path: string; warning?: string | null }>(`/api/agent/tools/${key}/pick`),
    onSuccess: (r) => done(r), onError: (e: Error) => toast(e.message),
  });
  const reset = useMutation({
    mutationFn: (key: string) => put<{ path: string }>(`/api/agent/tools/${key}`, { path: null }),
    onSuccess: (r) => done(r, true), onError: (e: Error) => toast(e.message),
  });
  const S = st ? STATUS[st.status] : null;
  const exes: Exe[] = st?.executables ?? t.paths.map((k) => ({ key: k, configured: "", found: null, optional: !!t.optional?.includes(k) }));
  const working = pick.isPending || reset.isPending;

  return createPortal(
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`${t.name} 자세히`}>
        <header className="drawer-head">
          <ToolIcon id={t.id} name={t.name} status={st?.status} size={44} />
          <div className="grow"><h3>{t.name}</h3><span className="small muted">{t.vendor} · {t.category}</span></div>
          <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label="닫기" title="닫기 (Esc)"><X size={17} /></button>
        </header>
        <div className="drawer-body">
          <div className={`tool-st big ${S?.cls ?? "off"}`}>{S?.icon && <S.icon size={15} />}{S ? S.label : "아직 확인하지 않았습니다"}</div>
          {st?.note && <p className="small" style={{ color: "var(--warn)" }}>{st.note}</p>}

          <h4>할 수 있는 일</h4>
          <div className="tool-tags">{t.can.map((x) => <span key={x}>{x}</span>)}</div>

          <h4>내 PC의 실행 파일</h4>
          <ul className="tool-exes">
            {exes.map((e) => {
              const files = t.files?.[e.key]?.join(", ");
              return (
                <li key={e.key}>
                  {e.found ? <CheckCircle2 size={14} className="ok" /> : <i className="dot" />}
                  <div className="grow">
                    <span>{files || e.key}</span>{e.optional && <span className="small muted"> (선택)</span>}
                    <div className="small path">{e.found
                      ? <>{e.found}{e.source && <span className="muted"> · {SOURCE[e.source]}</span>}</>
                      : <span className="muted">찾지 못했습니다{st ? " — 설치돼 있다면 직접 골라 주세요" : ""}</span>}</div>
                    {connected && st && (
                      <div className="tool-exe-acts">
                        <button className={e.found ? "link-btn small" : "primary small"} disabled={working} onClick={() => pick.mutate(e.key)}>
                          <FolderOpen size={13} />{e.found ? "다른 파일 고르기" : "찾아보기…"}</button>
                        {e.source === "picked" && (
                          <button className="link-btn small" disabled={working} onClick={() => reset.mutate(e.key)}>자동으로 되돌리기</button>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {pick.isPending && <p className="small notice-inline">내 PC에 뜬 파일 선택 창에서 실행 파일을 고르세요. (작업 표시줄에 숨어 있을 수 있습니다)</p>}
          <p className="small muted drawer-foot">스킬 <code>{t.skill}</code> · 실행 키 {t.run.map((r) => <code key={r}>{r}</code>)}</p>
        </div>
      </aside>
    </>,
    document.body,
  );
}
