import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Copy, RefreshCw, Search, Wrench, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { get } from "../api";
import { ToolIcon, toolLabel } from "../components/ToolIcon";
import { copyText } from "../format";
import { useToast } from "../toast";
import type { AgentStatus } from "../types";

interface CatalogTool {
  id: string; name: string; vendor: string; category: string; skill: string; paths: string[]; optional?: string[];
  run: string[]; can: string[];
}
interface Exe { key: string; configured: string; found: string | null; optional: boolean; auto?: boolean; version?: string | null }
interface ToolState { id: string; status: "ready" | "partial" | "missing"; note: string | null; executables: Exe[] }
interface Diag { catalog?: ToolState[] }

const STATUS = {
  ready: { label: "바로 사용", cls: "ok", icon: CheckCircle2 },
  partial: { label: "일부만 확인됨", cls: "warn", icon: CircleDashed },
  missing: { label: "설치·경로 필요", cls: "off", icon: null },
} as const;

/** CAD·CAE 툴: 에이전트가 다룰 수 있는 툴과 내 PC 설치 상태 — 분야별 앱 아이콘으로 한눈에.
 *  한 줄 요약(+다시 확인) → 검색·필터 한 줄 → 분야별 앱 아이콘(상태는 색·표시로) → 누르면 오른쪽에 자세히 */
export default function ToolsPage() {
  const [mine, setMine] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { data: cat } = useQuery({ queryKey: ["tools", "catalog"], queryFn: () => get<{ categories: string[]; tools: CatalogTool[] }>("/api/tools/catalog"), staleTime: Infinity });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const diag = useQuery({
    queryKey: ["agent", "diagnose"], enabled: !!agent?.connected, staleTime: 300_000, retry: false,
    queryFn: () => get<Diag>("/api/agent/diagnose"),
  });
  const state = useMemo(() => Object.fromEntries((diag.data?.catalog ?? []).map((t) => [t.id, t])), [diag.data]);
  const tools = cat?.tools ?? [];
  const count = (s: ToolState["status"]) => tools.filter((t) => state[t.id]?.status === s).length;
  const term = q.trim().toLowerCase();
  const shown = tools.filter((t) => (!mine || (state[t.id] && state[t.id].status !== "missing"))
    && (!term || [t.name, t.vendor, t.category, ...t.can].some((x) => x.toLowerCase().includes(term))));
  const cats = (cat?.categories ?? []).filter((c) => shown.some((t) => t.category === c));
  const sel = tools.find((t) => t.id === open);

  return (
    <div className="page tools-page">
      <h1>CAD·CAE 툴</h1>
      <p className="page-sub">대화에서 툴 이름을 말하면 에이전트가 해당 스킬로 자동화합니다. 실행은 내 PC에 설치된 툴로 합니다.</p>

      {/* 상태 한 줄: 무엇을 확인했는지와 '다시 확인'을 붙여 둔다 */}
      <div className="tools-status">
        {!agent?.connected ? (
          <span className="notice-inline warn">PC Agent가 연결되지 않아 내 PC 설치 상태를 볼 수 없습니다 · <Link to="/settings">설치·연결</Link></span>
        ) : diag.isFetching ? (
          <span className="small muted"><span className="spinner" style={{ width: 12, height: 12 }} /> 내 PC에서 툴을 찾는 중…</span>
        ) : diag.error ? (
          <span className="notice-inline warn">{(diag.error as Error).message}</span>
        ) : diag.data ? (
          <>
            <span className="tools-count"><b>{count("ready")}</b> 바로 사용</span>
            {count("partial") > 0 && <span className="tools-count warn"><b>{count("partial")}</b> 일부만 확인됨</span>}
            <span className="tools-count off"><b>{count("missing")}</b> 설치·경로 필요</span>
            <span className="small muted">· 지원 {tools.length}종</span>
          </>
        ) : null}
        {agent?.connected && !diag.isFetching && (
          <button className="link-btn small tools-recheck" onClick={() => diag.refetch()}><RefreshCw size={12} />다시 확인</button>
        )}
      </div>
      {diag.data && count("ready") === 0 && (
        <p className="small muted tools-tip">툴은 기본 설치 위치에서 자동으로 찾습니다. 다른 위치에 설치했다면 툴을 눌러 경로 설정 방법을 확인하세요.</p>
      )}

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
          <span><i className="lg ok" />바로 사용</span><span><i className="lg warn" />일부만 확인됨</span><span><i className="lg off" />설치·경로 필요(흐리게)</span>
        </div>
      )}
      {/* 분야마다 작은 카드 — 카드를 나란히 놓아 전체 툴이 한 화면에 들어온다 */}
      <div className="tool-cats">
        {cats.map((c) => {
          const list = shown.filter((t) => t.category === c);
          return (
            <section key={c} className="tool-cat">
              <h2>{c}<span className="cnt">{list.length}</span></h2>
              <div className="tool-apps">
                {list.map((t) => {
                  const st = state[t.id];
                  const S = st ? STATUS[st.status] : null;
                  return (
                    <button key={t.id} className={`tool-app ${open === t.id ? "on" : ""} ${st?.status ?? ""}`} onClick={() => setOpen(t.id)}
                      title={`${t.name} · ${t.vendor}${S ? ` — ${S.label}` : ""}`} aria-label={`${t.name}${S ? `, ${S.label}` : ""}`}>
                      <ToolIcon id={t.id} name={t.name} status={st?.status} size={52} />
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
      {sel && <ToolDrawer t={sel} st={state[sel.id]} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** 툴 자세히 (오른쪽 패널): 할 수 있는 일 · 찾은 실행 파일 · 경로 설정 방법 */
function ToolDrawer({ t, st, onClose }: { t: CatalogTool; st?: ToolState; onClose: () => void }) {
  const toast = useToast();
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose, t.id]);
  const S = st ? STATUS[st.status] : null;
  const exes: Exe[] = st?.executables ?? t.paths.map((k) => ({ key: k, configured: "", found: null, optional: !!t.optional?.includes(k) }));
  const missing = exes.filter((e) => !e.found);
  const example = "[tools]\n" + missing.map((e) => `${e.key} = 'C:\\경로\\실행파일.exe'`).join("\n");

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
            {exes.map((e) => (
              <li key={e.key}>
                {e.found ? <CheckCircle2 size={14} className="ok" /> : <i className="dot" />}
                <div className="grow">
                  <code>{e.key}</code>{e.optional && <span className="small muted"> (선택)</span>}
                  <div className="small path">{e.found ? <>{e.found}{e.auto && <span className="muted"> · 자동으로 찾음</span>}</>
                    : <span className="muted">찾지 못함{e.configured ? ` — 설정값 ${e.configured}` : ""}</span>}</div>
                </div>
              </li>
            ))}
          </ul>

          {missing.length > 0 && (
            <>
              <h4>경로 설정</h4>
              <p className="small">기본 위치에 설치돼 있지 않으면 <code>%USERPROFILE%\.dds-agent\agent.toml</code>의 <code>[tools]</code>에 실행 파일 경로를 적고 PC Agent를 다시 시작하세요.</p>
              <pre className="code-block">{example}</pre>
              <button className="primary small" onClick={async () => toast((await copyText(example)) ? "설정 예시를 복사했습니다" : "복사하지 못했습니다")}>
                <Copy size={13} />설정 예시 복사</button>
            </>
          )}
          <p className="small muted drawer-foot">스킬 <code>{t.skill}</code> · 실행 키 {t.run.map((r) => <code key={r}>{r}</code>)}</p>
        </div>
      </aside>
    </>,
    document.body,
  );
}
