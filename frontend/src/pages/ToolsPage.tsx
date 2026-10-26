import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, CircleDashed, Copy, RefreshCw, Wrench, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";
import { copyText } from "../format";
import { useToast } from "../toast";
import type { AgentStatus } from "../types";

interface CatalogTool {
  id: string; name: string; vendor: string; category: string; skill: string; paths: string[]; optional?: string[];
  run: string[]; can: string[];
}
interface ToolState {
  id: string; status: "ready" | "partial" | "missing"; note: string | null;
  executables: { key: string; configured: string; found: string | null; optional: boolean; auto?: boolean; version?: string | null }[];
}
interface Diag { catalog?: ToolState[] }

const STATUS = {
  ready: { label: "바로 사용", chip: "ok", icon: CheckCircle2 },
  partial: { label: "일부만 확인됨", chip: "warn", icon: CircleDashed },
  missing: { label: "설치·경로 필요", chip: "", icon: XCircle },
} as const;

/** 에이전트가 다룰 수 있는 CAD·CAE 툴과 내 PC 설치 상태 — 한 화면에서 확인 */
export default function ToolsPage() {
  const toast = useToast();
  const [only, setOnly] = useState<"all" | "mine">("all");
  const [cat, setCat] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const { data: cat0 } = useQuery({ queryKey: ["tools", "catalog"], queryFn: () => get<{ categories: string[]; tools: CatalogTool[] }>("/api/tools/catalog"), staleTime: Infinity });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const diag = useQuery({
    queryKey: ["agent", "diagnose"], enabled: !!agent?.connected, staleTime: 300_000, retry: false,
    queryFn: () => get<Diag>("/api/agent/diagnose"),
  });
  const state = useMemo(() => Object.fromEntries((diag.data?.catalog ?? []).map((t) => [t.id, t])), [diag.data]);
  const tools = cat0?.tools ?? [];
  const ready = tools.filter((t) => state[t.id]?.status === "ready").length;
  const shown = tools.filter((t) => (!cat || t.category === cat) && (only === "all" || (state[t.id] && state[t.id].status !== "missing")));
  const cats = (cat0?.categories ?? []).filter((c) => shown.some((t) => t.category === c));

  return (
    <div className="page">
      <div className="proj-head"><div className="grow"><h1>CAD·CAE 툴</h1>
        <div className="proj-sub">에이전트가 다룰 수 있는 툴과 내 PC 설치 상태입니다. 대화에서 툴 이름을 말하면 해당 스킬로 자동화합니다.</div></div></div>

      <div className="tools-summary">
        {!agent?.connected ? (
          <span className="notice-inline warn">PC Agent가 연결되지 않아 내 PC 설치 상태를 확인할 수 없습니다 · <Link to="/settings">설치·연결</Link></span>
        ) : diag.isFetching ? (
          <span className="small muted"><span className="spinner" style={{ width: 12, height: 12 }} /> 내 PC에서 툴을 찾는 중…</span>
        ) : diag.error ? (
          <span className="notice-inline warn">{(diag.error as Error).message}</span>
        ) : (
          <span><b className="big-num">{ready}</b> <span className="muted">/ {tools.length}개 툴을 내 PC에서 바로 쓸 수 있습니다</span></span>
        )}
        <span className="grow" />
        <span className="seg" role="radiogroup" aria-label="보기">
          <button role="radio" aria-checked={only === "all"} className={only === "all" ? "on" : ""} onClick={() => setOnly("all")}>지원하는 툴 전체</button>
          <button role="radio" aria-checked={only === "mine"} className={only === "mine" ? "on" : ""} onClick={() => setOnly("mine")} disabled={!diag.data}>내 PC에 있는 툴</button>
        </span>
        <button className="icon-btn" title="내 PC 다시 확인" aria-label="내 PC 다시 확인" disabled={!agent?.connected || diag.isFetching}
          onClick={() => diag.refetch()}><RefreshCw size={15} className={diag.isFetching ? "spin" : ""} /></button>
      </div>
      <div className="tools-cats" role="group" aria-label="분야">
        <button className={`chip-btn ${!cat ? "on" : ""}`} onClick={() => setCat(null)}>전체 분야</button>
        {(cat0?.categories ?? []).map((c) => <button key={c} className={`chip-btn ${cat === c ? "on" : ""}`} onClick={() => setCat(cat === c ? null : c)}>{c}</button>)}
      </div>

      {cats.map((c) => (
        <section key={c} className="tools-sec">
          <h2>{c}</h2>
          <div className="tools-grid">
            {shown.filter((t) => t.category === c).map((t) => {
              const st = state[t.id];
              const S = st ? STATUS[st.status] : null;
              const isOpen = open === t.id;
              return (
                <div key={t.id} className={`tool-card ${st?.status ?? "unknown"}`}>
                  <div className="tool-head">
                    <div className="grow"><b>{t.name}</b><div className="small muted">{t.vendor}</div></div>
                    {S ? <span className={`chip ${S.chip}`}><S.icon size={12} />{S.label}</span> : <span className="chip">확인 전</span>}
                  </div>
                  <div className="tool-can">{t.can.map((x) => <span key={x}>{x}</span>)}</div>
                  {st?.note && <p className="small" style={{ color: "var(--warn)" }}>{st.note}</p>}
                  <button className="link-btn small tool-more" onClick={() => setOpen(isOpen ? null : t.id)} aria-expanded={isOpen}>
                    {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{st?.status === "ready" ? "찾은 경로 보기" : "설정 방법"}</button>
                  {isOpen && (
                    <div className="tool-detail">
                      {(st?.executables ?? t.paths.map((k) => ({ key: k, configured: "", found: null, optional: !!t.optional?.includes(k) }))).map((e) => (
                        <div key={e.key} className="tool-exe">
                          <code>{e.key}</code>
                          {e.found ? <span className="small">{e.found}{"auto" in e && e.auto ? <span className="chip" style={{ marginLeft: 6 }}>자동으로 찾음</span> : null}</span>
                            : <span className="small muted">{e.optional ? "(선택) " : ""}찾지 못함{e.configured ? ` — 설정값 ${e.configured}` : ""}</span>}
                        </div>
                      ))}
                      {st?.status !== "ready" && (
                        <div className="tool-howto">
                          <span className="small muted">기본 위치에 설치돼 있지 않으면 <code>%USERPROFILE%\.dds-agent\agent.toml</code>에 경로를 적고 PC Agent를 다시 시작하세요.</span>
                          <button className="small" onClick={async () => {
                            const text = "[tools]\n" + t.paths.map((k) => `${k} = 'C:\\경로\\실행파일.exe'`).join("\n");
                            toast((await copyText(text)) ? "agent.toml 예시를 복사했습니다" : "복사하지 못했습니다");
                          }}><Copy size={12} />설정 예시 복사</button>
                        </div>
                      )}
                      <div className="small muted">스킬 <code>{t.skill}</code> · 실행 키 {t.run.map((r) => <code key={r} style={{ marginRight: 4 }}>{r}</code>)}</div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}
      {!!cat0 && !shown.length && <div className="empty-hero"><Wrench size={28} /><b>조건에 맞는 툴이 없습니다</b></div>}
    </div>
  );
}
