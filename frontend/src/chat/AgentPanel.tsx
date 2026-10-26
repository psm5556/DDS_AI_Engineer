import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowUp, Bot, Brain, Check, CircleSlash, History, Loader2, PanelRightClose, ShieldQuestion, Sparkles, Square,
  SquarePen, Wrench, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { Markdown } from "../components/Markdown";
import { startPanelResize, type PanelLayout } from "../components/sideLayout";
import { AGENT_SEND, when } from "../format";
import { useLive } from "../live";
import { useToast } from "../toast";
import type { AgentStatus, ChatMessage, ChatSession, PermissionRequest } from "../types";

/** 오른쪽 에이전트 채팅. 사용자가 고른 AI CLI(Gemini/Claude/Codex)가 내 PC Agent에서 돌고,
 *  대화·툴 호출·권한 요청이 실시간으로 이어진다. 대화는 프로젝트 멤버가 함께 볼 수 있고, 요청은 대화를 만든 사람만 보낸다. */

const curKey = (pid: number) => `dds.chat.current.${pid}`;
const loadCur = (pid: number) => { try { return Number(localStorage.getItem(curKey(pid))) || null; } catch { return null; } };
const saveCur = (pid: number, id: number | null) => {
  try { if (id) localStorage.setItem(curKey(pid), String(id)); else localStorage.removeItem(curKey(pid)); } catch { /* 무시 */ }
};

const SUGGEST = [
  "NX 브래킷 모델의 두께·리브 높이를 바꿔가며 Abaqus 응력을 보는 DOE를 구성해 줘",
  "mock 솔버(beam)로 질량 최소화 최적화를 해보고 Simulator로 배포해 줘",
  "Abaqus 모델에서 하중을 바꿔 해석하고 최대 응력과 컨투어 이미지를 보여 줘",
];

export function AgentPanel({ layout }: { layout: PanelLayout }) {
  const loc = useLocation();
  const { user } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const pid = useMemo(() => Number(loc.pathname.match(/^\/projects\/(\d+)/)?.[1]) || null, [loc.pathname]);
  const open = !layout.collapsed;
  const [sid, setSid] = useState<number | null>(null);
  const [view, setView] = useState<"chat" | "list">("chat");
  const [input, setInput] = useState("");
  const [agentChoice, setAgentChoice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [perms, setPerms] = useState<PermissionRequest[]>([]);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"), refetchInterval: 30000 });
  const { data: sessions } = useQuery({
    queryKey: ["sessions", pid], enabled: !!pid,
    queryFn: () => get<ChatSession[]>(`/api/projects/${pid}/sessions`),
  });
  const session = sessions?.find((s) => s.id === sid) ?? null;
  const { data: msgs } = useQuery({
    queryKey: ["messages", sid], enabled: !!sid,
    queryFn: () => get<ChatMessage[]>(`/api/sessions/${sid}/messages`),
  });
  const readOnly = !!session && session.user_id !== user?.id;
  const clis = (agent?.info?.agents ?? []).filter((a) => a.installed);
  const chosen = agentChoice ?? agent?.info?.default_agent ?? clis[0]?.name ?? "gemini";

  // 프로젝트가 바뀌면 그 프로젝트에서 마지막으로 보던 대화
  useEffect(() => {
    setSid(pid ? loadCur(pid) : null);
    setView("chat");
    setPerms([]);
  }, [pid]);
  // 대화를 열면 진행 중 여부와 대기 중인 권한 요청을 서버에서 받아 온다
  useEffect(() => {
    if (!sid) { setBusy(false); setPerms([]); return; }
    get<{ busy: boolean; pending_permissions: PermissionRequest[] }>(`/api/sessions/${sid}/status`)
      .then((s) => { setBusy(s.busy); setPerms(s.pending_permissions); }).catch(() => {});
  }, [sid]);
  useEffect(() => { bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" }); }, [msgs, busy, perms]);
  useEffect(() => { if (open && view === "chat") setTimeout(() => inputRef.current?.focus(), 50); }, [open, view, sid]);

  // 실시간 이벤트 → 메시지 캐시 갱신
  useLive((e) => {
    if (e.type !== "agent_event" || !e.session_id) return;
    if (e.session_id !== sid) {
      if (e.project_id === pid) qc.invalidateQueries({ queryKey: ["sessions", pid] });
      return;
    }
    const ev = e.event as Record<string, unknown>;
    const key = ["messages", sid];
    const upd = (fn: (xs: ChatMessage[]) => ChatMessage[]) => qc.setQueryData<ChatMessage[]>(key, (xs) => fn(xs ?? []));
    switch (ev.kind) {
      case "turn_started": setBusy(true); qc.invalidateQueries({ queryKey: key }); break;
      case "turn_ended":
        setBusy(false); setPerms([]);
        qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["sessions", pid] });
        break;
      case "chunk": {
        const id = ev.message_id as number;
        upd((xs) => xs.some((m) => m.id === id)
          ? xs.map((m) => (m.id === id ? { ...m, content: m.content + (ev.text as string) } : m))
          : [...xs, { id, session_id: sid, role: ev.role as ChatMessage["role"], content: ev.text as string, meta: null, created_at: new Date().toISOString() }]);
        break;
      }
      case "tool_call":
      case "tool_call_update": {
        const id = ev.message_id as number;
        const meta = ev.meta as ChatMessage["meta"];
        upd((xs) => xs.some((m) => m.id === id)
          ? xs.map((m) => (m.id === id ? { ...m, meta } : m))
          : [...xs, { id, session_id: sid, role: "tool", content: meta?.title ?? "", meta, created_at: new Date().toISOString() }]);
        break;
      }
      case "permission_request": setPerms((ps) => [...ps.filter((p) => p.request_id !== ev.request_id), ev as unknown as PermissionRequest]); break;
      case "permission_resolved": setPerms((ps) => ps.filter((p) => p.request_id !== ev.request_id)); break;
    }
  });

  const pick = (id: number | null) => { setSid(id); if (pid) saveCur(pid, id); setView("chat"); };

  const send = useCallback(async (text: string) => {
    const t = text.trim();
    if (!t || !pid) return;
    try {
      let id = sid;
      if (!id || readOnly) {
        const s = await post<ChatSession>(`/api/projects/${pid}/sessions`, { agent: chosen });
        qc.invalidateQueries({ queryKey: ["sessions", pid] });
        id = s.id;
        pick(s.id);
      }
      setInput("");
      setBusy(true);
      qc.setQueryData<ChatMessage[]>(["messages", id], (xs) => [...(xs ?? []), { id: -Date.now(), session_id: id!, role: "user", content: t, meta: null, created_at: new Date().toISOString() }]);
      await post(`/api/sessions/${id}/prompt`, { text: t });
    } catch (e) {
      setBusy(false);
      toast((e as Error).message, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, sid, readOnly, chosen]);

  // 다른 화면(플로우 승인 등)에서 보낸 메시지
  useEffect(() => {
    const h = (e: Event) => { layout.setCollapsed(false); void send((e as CustomEvent<string>).detail); };
    window.addEventListener(AGENT_SEND, h);
    return () => window.removeEventListener(AGENT_SEND, h);
  }, [send, layout]);

  const cancel = () => { if (sid) post(`/api/sessions/${sid}/cancel`).catch((e: Error) => toast(e.message, true)); };
  const answer = async (p: PermissionRequest, optionId: string | null) => {
    try {
      await post(`/api/sessions/${sid}/permission`, { request_id: p.request_id, option_id: optionId });
      setPerms((ps) => ps.filter((x) => x.request_id !== p.request_id));
    } catch (e) { toast((e as Error).message, true); }
  };

  if (!open) {
    return (
      <aside className="conversa rail" aria-label="에이전트 (접힘)">
        <button className="icon-btn cv-rail-btn" onClick={() => layout.setCollapsed(false)} aria-label="에이전트 펼치기" title="에이전트 펼치기 (Ctrl+J)"><Sparkles size={18} /></button>
        <button className="cv-rail-label" onClick={() => layout.setCollapsed(false)} tabIndex={-1} aria-hidden="true">CAE 에이전트</button>
      </aside>
    );
  }

  const agentLabel = (name: string) => agent?.info?.agents?.find((a) => a.name === name)?.label ?? name;
  const disabled = !pid || !agent?.connected;

  return (
    <aside className="conversa" role="complementary" aria-label="CAE 에이전트">
      <div className="side-resizer cv-resizer" role="separator" aria-orientation="vertical" aria-label="에이전트 창 너비 조절"
        aria-valuenow={layout.width} aria-valuemin={layout.min} aria-valuemax={layout.max} tabIndex={0} title="끌어서 너비 조절 · 두 번 누르면 기본 너비"
        onPointerDown={(e) => startPanelResize(e, layout, "left")} onDoubleClick={layout.reset}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); layout.setWidth(layout.width + 16); }
          if (e.key === "ArrowRight") { e.preventDefault(); layout.setWidth(layout.width - 16); }
        }} />
      <header className="cv-head">
        <span className="cv-logo"><Sparkles size={15} /></span>
        <div className="grow"><b>CAE 에이전트</b>
          <small>{agent?.connected ? `${session ? agentLabel(session.agent) : agentLabel(chosen)} · ${agent.info?.hostname ?? ""}` : "PC Agent 연결 안 됨"}</small></div>
        {pid && <button className={`icon-btn ${view === "list" ? "on" : ""}`} title="대화 목록" aria-label="대화 목록" aria-pressed={view === "list"}
          onClick={() => setView((v) => (v === "list" ? "chat" : "list"))}><History size={16} /></button>}
        {pid && <button className="icon-btn" title="새 대화" aria-label="새 대화" onClick={() => pick(null)}><SquarePen size={15} /></button>}
        <button className="icon-btn" title="접기 (Ctrl+J)" aria-label="에이전트 접기" onClick={() => layout.setCollapsed(true)}><PanelRightClose size={16} /></button>
      </header>

      {view === "list" && pid ? (
        <div className="cv-list" role="region" aria-label="대화 목록">
          <div className="cv-list-top"><button className="small" onClick={() => pick(null)}><SquarePen size={14} />새 대화</button></div>
          {!sessions?.length ? <div className="cv-list-empty">아직 대화가 없습니다.</div> : (
            <ul>{sessions.map((s) => (
              <li key={s.id} className={`cv-conv ${s.id === sid ? "active" : ""}`}>
                <button className="cv-conv-open" onClick={() => pick(s.id)}>
                  <span className="t">{s.title}</span>
                  <span className="m">{when(s.created_at)} · {agentLabel(s.agent)}{s.user_id !== user?.id ? " · 다른 멤버" : ""}</span>
                </button>
              </li>))}
            </ul>
          )}
        </div>
      ) : (
        <>
          {session && <div className="cv-convbar" title={session.title}><span>{session.title}{readOnly ? " · 읽기 전용(다른 멤버의 대화)" : ""}</span></div>}
          <div className="cv-body" ref={bodyRef}>
            {!pid ? (
              <div className="cv-empty"><Bot size={26} /><b>프로젝트를 열어 주세요</b>
                <p>에이전트는 프로젝트 안에서 일합니다. 왼쪽에서 프로젝트를 고르거나 새로 만드세요.</p></div>
            ) : !agent?.connected ? (
              <div className="cv-empty"><AlertTriangle size={26} /><b>내 PC Agent가 연결되어 있지 않습니다</b>
                <p>에이전트(Gemini·Claude·Codex)와 CAD/CAE 툴은 내 PC에서 실행됩니다.</p>
                <Link className="btn primary" to="/settings">PC Agent 설치·연결</Link></div>
            ) : !msgs?.length ? (
              <div className="cv-empty">
                <Sparkles size={26} /><b>무엇을 자동화할까요?</b>
                <p>CAD 변경, 해석 설정·실행, 결과 추출, DOE·예측 모델·최적화를 말로 요청하세요. 에이전트가 먼저 작업 흐름을 그려 보여 주고, 승인하면 실행합니다.</p>
                <div className="cv-suggest">{SUGGEST.map((s) => <button key={s} className="small" onClick={() => void send(s)}>{s}</button>)}</div>
              </div>
            ) : groupTools(msgs).map((g) => Array.isArray(g)
              ? <ToolGroup key={g[0].id} items={g} /> : <Message key={g.id} m={g} />)}
            {perms.map((p) => <PermissionCard key={p.request_id} p={p} readOnly={readOnly} onAnswer={(o) => void answer(p, o)} />)}
            {busy && !perms.length && <div className="cv-msg assistant"><div className="bubble thinking"><Loader2 size={14} className="spin" />작업하는 중…</div></div>}
          </div>

          {!session && pid && agent?.connected && clis.length > 1 && (
            <div className="cv-agent-pick" role="radiogroup" aria-label="사용할 AI 에이전트">
              <span className="small muted">에이전트</span>
              <span className="seg">{clis.map((a) => (
                <button key={a.name} role="radio" aria-checked={chosen === a.name} className={chosen === a.name ? "on" : ""} onClick={() => setAgentChoice(a.name)}>{a.label}</button>
              ))}</span>
            </div>
          )}
          <form className="cv-input" onSubmit={(e) => { e.preventDefault(); void send(input); }}>
            <textarea ref={inputRef} value={input} rows={1} disabled={disabled || readOnly}
              placeholder={!pid ? "프로젝트를 열면 요청할 수 있습니다" : readOnly ? "다른 멤버의 대화입니다. 새 대화를 시작하세요"
                : disabled ? "PC Agent 연결 후 사용할 수 있습니다" : "예: 이 모델의 두께를 2~6mm로 바꿔가며 응력·질량 DOE 해줘"}
              aria-label="에이전트에게 요청" onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input); } }} />
            {busy && !readOnly ? (
              <button type="button" className="cv-send" onClick={cancel} aria-label="중지" title="중지"><Square size={14} /></button>
            ) : (
              <button type="submit" className="primary cv-send" disabled={disabled || readOnly || !input.trim()} aria-label="보내기"><ArrowUp size={16} /></button>
            )}
          </form>
        </>
      )}
      <p className="cv-foot">내 PC에서 실행 · 실행 전 확인 · 대화는 프로젝트 멤버와 공유</p>
    </aside>
  );
}

/** 연달아 실행된 툴 호출은 하나로 묶는다 (마지막 것만 보이고, 펼치면 전체) */
function groupTools(msgs: ChatMessage[]): (ChatMessage | ChatMessage[])[] {
  const out: (ChatMessage | ChatMessage[])[] = [];
  for (const m of msgs) {
    const last = out[out.length - 1];
    if (m.role === "tool" && Array.isArray(last)) last.push(m);
    else if (m.role === "tool") out.push([m]);
    else out.push(m);
  }
  return out.map((g) => (Array.isArray(g) && g.length === 1 ? g[0] : g));
}

function ToolGroup({ items }: { items: ChatMessage[] }) {
  const [open, setOpen] = useState(false);
  const failed = items.filter((m) => m.meta?.status === "failed").length;
  const running = items.some((m) => m.meta?.status !== "completed" && m.meta?.status !== "failed");
  const last = items[items.length - 1];
  return (
    <div className="cv-toolgroup">
      <button className="cv-toolgroup-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Wrench size={12} />툴 {items.length}개 실행{failed ? ` · 실패 ${failed}` : ""}
        {running ? <Loader2 size={12} className="spin" /> : failed ? <X size={12} className="err" /> : <Check size={12} className="ok" />}
        <span className="grow" /><span className="small muted">{open ? "접기" : "펼치기"}</span>
      </button>
      {open ? items.map((m) => <Message key={m.id} m={m} />) : <Message m={last} />}
    </div>
  );
}

function Message({ m }: { m: ChatMessage }) {
  const [openTool, setOpenTool] = useState(false);
  if (m.role === "user") return <div className="cv-msg user"><div className="bubble">{m.content}</div></div>;
  if (m.role === "system") return <div className="cv-msg assistant err"><div className="bubble">{m.content}</div></div>;
  if (m.role === "thought") {
    return (
      <details className="cv-thought">
        <summary><Brain size={12} />생각</summary>
        <Markdown text={m.content} />
      </details>
    );
  }
  if (m.role === "tool") {
    const st = m.meta?.status ?? "pending";
    const icon = st === "completed" ? <Check size={13} /> : st === "failed" ? <X size={13} /> : <Loader2 size={13} className="spin" />;
    return (
      <div className={`cv-tool ${st}`}>
        <button className="cv-tool-head" onClick={() => setOpenTool(!openTool)} aria-expanded={openTool}>
          <Wrench size={12} /><span className="t">{m.meta?.title || m.content}</span><span className="st">{icon}</span>
        </button>
        {openTool && (
          <div className="cv-tool-body">
            {m.meta?.raw_input != null && <pre>{JSON.stringify(m.meta.raw_input, null, 2)}</pre>}
            {m.meta?.output && <pre>{m.meta.output}</pre>}
          </div>
        )}
      </div>
    );
  }
  return <div className="cv-msg assistant"><div className="bubble"><Markdown text={m.content} /></div></div>;
}

function PermissionCard({ p, readOnly, onAnswer }: { p: PermissionRequest; readOnly: boolean; onAnswer: (optionId: string | null) => void }) {
  const allow = p.options.filter((o) => o.kind.startsWith("allow"));
  const reject = p.options.filter((o) => o.kind.startsWith("reject"));
  return (
    <div className="cv-card" role="group" aria-label={`실행 확인: ${p.title}`}>
      <div className="cv-card-title"><ShieldQuestion size={15} />실행해도 될까요?</div>
      <p className="small" style={{ marginTop: 6 }}>{p.title}</p>
      {p.raw_input != null && <pre className="cv-perm-input">{JSON.stringify(p.raw_input, null, 2).slice(0, 1500)}</pre>}
      {readOnly ? <p className="small muted">대화를 시작한 사람이 확인합니다.</p> : (
        <div className="row cv-actions">
          {reject.map((o) => <button key={o.optionId} className="small" onClick={() => onAnswer(o.optionId)}><CircleSlash size={13} />{o.name}</button>)}
          {!reject.length && <button className="small" onClick={() => onAnswer(null)}>취소</button>}
          {allow.map((o) => <button key={o.optionId} className={`small ${o.kind === "allow_once" ? "primary" : ""}`} onClick={() => onAnswer(o.optionId)}>{o.name}</button>)}
        </div>
      )}
    </div>
  );
}
