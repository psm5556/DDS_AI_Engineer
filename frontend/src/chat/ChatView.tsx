import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, BarChart3, Brain, Check, ChevronRight, CircleSlash, FileText, Image as ImageIcon, LineChart,
  FileUp, Loader2, PanelRight, ScatterChart, ShieldQuestion, Sparkles, Table2, Workflow, Wrench, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, get, post } from "../api";
import { useAuth } from "../auth";
import { useCanvas } from "../canvas/CanvasContext";
import { useFlowDecision } from "../canvas/views";
import { Markdown } from "../components/Markdown";
import { Composer } from "./Composer";
import { AgentPicker, resolveAgent, usePreferredAgent } from "../components/AgentPicker";
import { isQuietTool, readableArgs, toolLabel } from "./labels";
import { AGENT_SEND, FLOW_STATUS, NODE_STATUS, NODE_TYPE, localIso } from "../format";
import { useLive } from "../live";
import { useToast } from "../toast";
import type { AgentStatus, Artifact, ChatMessage, ChatSession, Flow, FlowNode, PermissionRequest } from "../types";

/** 가운데 대화: 사용자는 말로 지시하고, 에이전트의 작업 흐름 제안·실행 확인·결과가 대화 안의 카드로 이어진다.
 *  같은 내용은 오른쪽 라이브 캔버스에 크게 쌓인다. */

type Item =
  | { kind: "msg"; at: string; m: ChatMessage }
  | { kind: "tools"; at: string; items: ChatMessage[] }
  | { kind: "flow"; at: string; f: Flow }
  | { kind: "arts"; at: string; items: Artifact[] };

export const CHAT_SUGGEST = [
  "NX 브래킷 모델의 두께·리브 높이를 바꿔가며 Abaqus 응력·질량 DOE를 하고 예측 모델까지 만들어 줘",
  "Abaqus 모델에서 하중을 바꿔 해석하고 최대 응력과 컨투어 이미지를 보여 줘",
  "mock 솔버(beam)로 질량 최소화 최적화를 해보고 Simulator로 배포해 줘",
];

export function ChatView({ pid, sid, onSession, initialPrompt, onInitialSent }: {
  pid: number; sid: number | null; onSession: (id: number) => void;
  initialPrompt?: { text: string; attachmentIds: number[] } | null; onInitialSent?: () => void;
}) {
  const { user } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [preferred, setPreferred] = usePreferredAgent();
  const [busy, setBusy] = useState(false);
  const [perms, setPerms] = useState<PermissionRequest[]>([]);
  const bodyRef = useRef<HTMLDivElement>(null);
  const sentInitial = useRef(false);

  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"), refetchInterval: 30000 });
  const { data: sessions } = useQuery({ queryKey: ["sessions", pid], queryFn: () => get<ChatSession[]>(`/api/projects/${pid}/sessions`) });
  const session = sessions?.find((s) => s.id === sid) ?? null;
  const { data: msgs } = useQuery({ queryKey: ["messages", sid], enabled: !!sid, queryFn: () => get<ChatMessage[]>(`/api/sessions/${sid}/messages`) });
  const { data: flows } = useQuery({ queryKey: ["flows", pid], queryFn: () => get<Flow[]>(`/api/projects/${pid}/flows`) });
  const { data: arts } = useQuery({ queryKey: ["artifacts", pid], queryFn: () => get<Artifact[]>(`/api/projects/${pid}/artifacts`) });
  const readOnly = !!session && session.user_id !== user?.id;
  const chosen = resolveAgent(agent, preferred);
  const agentLabel = (name: string) => agent?.info?.agents?.find((a) => a.name === name)?.label ?? name;
  // 진행 중인 대화의 에이전트를 바꾸면 다음 요청부터 그 CLI로 이어 간다(이전 대화 내용은 서버가 넘긴다)
  const switchAgent = async (name: string) => {
    setPreferred(name);
    if (!session || readOnly) return;
    try {
      await api(`/api/sessions/${session.id}`, { method: "PUT", body: { agent: name } });
      qc.invalidateQueries({ queryKey: ["sessions", pid] });
      toast(`다음 요청부터 ${agentLabel(name)}(으)로 이어 갑니다`);
    } catch (e) { toast((e as Error).message, true); }
  };
  const disabled = !agent?.connected;

  useEffect(() => {
    if (!sid) { setBusy(false); setPerms([]); return; }
    get<{ busy: boolean; pending_permissions: PermissionRequest[] }>(`/api/sessions/${sid}/status`)
      .then((s) => { setBusy(s.busy); setPerms(s.pending_permissions); }).catch(() => {});
  }, [sid]);

  // 대화 + 이 대화 이후에 생긴 플로우·결과를 시간순으로 엮는다
  const timeline = useMemo<Item[]>(() => {
    if (!sid || !msgs) return [];
    const since = session?.created_at ?? msgs[0]?.created_at ?? "";
    const items: Item[] = [];
    for (const m of msgs) {
      const last = items[items.length - 1];
      if (m.role === "tool" && last?.kind === "tools") last.items.push(m);
      else if (m.role === "tool") items.push({ kind: "tools", at: m.created_at, items: [m] });
      else items.push({ kind: "msg", at: m.created_at, m });
    }
    (flows ?? []).filter((f) => f.created_at >= since).forEach((f) => items.push({ kind: "flow", at: f.created_at, f }));
    (arts ?? []).filter((a) => a.created_at >= since).forEach((a) => items.push({ kind: "arts", at: a.created_at, items: [a] }));
    items.sort((x, y) => (x.at < y.at ? -1 : x.at > y.at ? 1 : 0));
    // 연달아 게시된 결과는 한 묶음으로
    const merged: Item[] = [];
    for (const it of items) {
      const last = merged[merged.length - 1];
      if (it.kind === "arts" && last?.kind === "arts") last.items.push(...it.items);
      else merged.push(it.kind === "arts" ? { ...it, items: [...it.items] } : it);
    }
    return merged;
  }, [sid, msgs, flows, arts, session]);

  useEffect(() => { bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" }); }, [timeline.length, msgs, busy, perms]);

  useLive((e) => {
    if (e.type !== "agent_event" || e.session_id !== sid || !sid) {
      if (e.type === "agent_event" && e.project_id === pid) qc.invalidateQueries({ queryKey: ["sessions", pid] });
      return;
    }
    const ev = e.event as Record<string, unknown>;
    const key = ["messages", sid];
    const upd = (fn: (xs: ChatMessage[]) => ChatMessage[]) => qc.setQueryData<ChatMessage[]>(key, (xs) => fn(xs ?? []));
    const now = localIso();
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
          : [...xs, { id, session_id: sid, role: ev.role as ChatMessage["role"], content: ev.text as string, meta: null, created_at: now }]);
        break;
      }
      case "tool_call":
      case "tool_call_update": {
        const id = ev.message_id as number;
        const meta = ev.meta as ChatMessage["meta"];
        upd((xs) => xs.some((m) => m.id === id)
          ? xs.map((m) => (m.id === id ? { ...m, meta } : m))
          : [...xs, { id, session_id: sid, role: "tool", content: meta?.title ?? "", meta, created_at: now }]);
        break;
      }
      case "permission_request": setPerms((ps) => [...ps.filter((p) => p.request_id !== ev.request_id), ev as unknown as PermissionRequest]); break;
      case "permission_resolved": setPerms((ps) => ps.filter((p) => p.request_id !== ev.request_id)); break;
    }
  });

  const send = useCallback(async (text: string, attachmentIds: number[] = []) => {
    const t = text.trim();
    if (!t && !attachmentIds.length) return;
    try {
      let id = sid;
      if (!id || readOnly) {
        const s = await post<ChatSession>(`/api/projects/${pid}/sessions`, { agent: chosen });
        qc.invalidateQueries({ queryKey: ["sessions", pid] });
        id = s.id;
        onSession(s.id);
      }
      setBusy(true);
      qc.setQueryData<ChatMessage[]>(["messages", id], (xs) => [...(xs ?? []),
        { id: -Date.now(), session_id: id!, role: "user", content: t || "첨부한 파일을 확인해 주세요.", meta: null, created_at: localIso() }]);
      await post(`/api/sessions/${id}/prompt`, { text: t, attachment_ids: attachmentIds });
    } catch (e) {
      setBusy(false);
      toast((e as Error).message, true);
    }
  }, [pid, sid, readOnly, chosen, onSession, qc, toast]);

  // 첫 화면에서 입력한 요청(과 첨부)을 이어서 보낸다
  useEffect(() => {
    if (initialPrompt && agent?.connected && !sentInitial.current) {
      sentInitial.current = true;
      onInitialSent?.(); // 새 대화가 만들어지며 화면이 다시 그려져도 두 번 보내지 않게 먼저 비운다
      void send(initialPrompt.text, initialPrompt.attachmentIds);
    }
  }, [initialPrompt, agent?.connected, send, onInitialSent]);

  // 상황에 맞는 추천 프롬프트 (→ 키로 입력)
  const suggestions = useMemo(() => {
    const review = (flows ?? []).find((f) => f.status === "review");
    const done = (flows ?? []).some((f) => f.status === "complete");
    if (review) return [`작업 흐름 '${review.title}'에서 바꿀 점: `, "이 작업 흐름의 각 단계를 자세히 설명해 줘", "승인 전에 필요한 입력 파일과 툴 경로를 점검해 줘"];
    if (done) return ["결과를 해석해서 결론·권고가 담긴 보고서 요약을 작성해 줘", "능동학습으로 예측 모델 정확도를 더 높여 줘",
      "최적 설계점에서 실제 해석으로 확인(검증 해석)해 줘", "응력 분포를 3D로 보고 싶어 — 형상·필드 모델을 학습해 줘", "Simulator를 설계팀과 공유할 수 있게 배포해 줘"];
    if (timeline.length) return ["지금 진행 상황을 요약해 줘", "실패한 케이스 원인을 찾아서 고쳐 줘", "다음 단계로 진행해 줘"];
    return CHAT_SUGGEST;
  }, [flows, timeline.length]);

  // 캔버스 등 다른 곳에서 보낸 메시지(플로우 승인 등)
  useEffect(() => {
    const h = (e: Event) => { void send((e as CustomEvent<string>).detail); };
    window.addEventListener(AGENT_SEND, h);
    return () => window.removeEventListener(AGENT_SEND, h);
  }, [send]);

  // "작업하는 중…" 대신 지금 무엇을 하는지: 진행 중인 단계 → 실행 중인 툴 → 생각 중
  const activity = useMemo(() => {
    const f = (flows ?? []).find((x) => x.status === "in_progress" || x.status === "approved");
    if (f) {
      const steps = orderNodes(f);
      const i = steps.findIndex((n) => n.status === "in_progress");
      if (i >= 0) return `${steps[i].label} 진행 중 · ${i + 1}/${steps.length}단계`;
    }
    const t = [...(msgs ?? [])].reverse().find((m) => m.role === "tool");
    if (t && t.meta?.status !== "completed" && t.meta?.status !== "failed") return `${toolLabel(t.meta?.title || t.content)[0]} 중…`;
    return "생각하는 중…";
  }, [flows, msgs]);

  const cancel = () => { if (sid) post(`/api/sessions/${sid}/cancel`).catch((e: Error) => toast(e.message, true)); };
  const answer = async (p: PermissionRequest, optionId: string | null) => {
    try {
      await post(`/api/sessions/${sid}/permission`, { request_id: p.request_id, option_id: optionId });
      setPerms((ps) => ps.filter((x) => x.request_id !== p.request_id));
    } catch (e) { toast((e as Error).message, true); }
  };

  return (
    <section className="chat" aria-label="에이전트와 대화">
      <div className="chat-body" ref={bodyRef}>
        <div className="chat-col">
          {!agent?.connected ? (
            <div className="chat-empty"><AlertTriangle size={28} /><b>내 PC Agent가 연결되어 있지 않습니다</b>
              <p>에이전트(Gemini·Claude·Codex)와 CAD/CAE 툴은 내 PC에서 실행됩니다. 설치 파일 하나로 준비됩니다.</p>
              <Link className="btn primary" to="/settings">PC Agent 설치·연결</Link></div>
          ) : !sid || !timeline.length ? (
            <div className="chat-empty">
              <Sparkles size={28} /><b>무엇을 자동화할까요?</b>
              <p>CAD 변경, 해석 설정·실행, 결과 추출, DOE·예측 모델·최적화를 말로 요청하세요. 에이전트가 작업 흐름을 먼저 그려 보여 주고, 승인하면 실행합니다.</p>
              <div className="chat-suggest">{CHAT_SUGGEST.map((s) => <button key={s} onClick={() => void send(s)}>{s}</button>)}</div>
            </div>
          ) : timeline.map((it) => {
            if (it.kind === "flow") return <FlowCard key={`f${it.f.id}`} pid={pid} f={it.f} readOnly={readOnly} />;
            if (it.kind === "arts") return <ArtifactChips key={`a${it.items[0].id}`} items={it.items} />;
            if (it.kind === "tools") return it.items.length > 1 ? <ToolGroup key={`t${it.items[0].id}`} items={it.items} /> : <Message key={it.items[0].id} m={it.items[0]} />;
            return <Message key={it.m.id} m={it.m} />;
          })}
          {perms.map((p) => <PermissionCard key={p.request_id} p={p} readOnly={readOnly} onAnswer={(o) => void answer(p, o)} />)}
          {busy && !perms.length && <div className="chat-msg agent"><div className="bubble thinking"><Loader2 size={14} className="spin" />{activity}</div></div>}
        </div>
      </div>

      <div className="chat-composer">
        <div className="chat-col">
          <Composer projectId={pid} suggestions={suggestions} busy={busy && !readOnly} onStop={cancel} disabled={disabled}
            onSend={(o) => send(o.text, o.attachmentIds)}
            placeholder={readOnly ? "다른 멤버의 대화입니다. 보내면 내 새 대화로 시작합니다" : disabled ? "PC Agent 연결 후 사용할 수 있습니다"
              : "무엇이든 요청하세요"}
            leftBar={readOnly ? <span className="chip">{session ? agentLabel(session.agent) : ""} · 읽기 전용</span>
              : <AgentPicker value={session?.agent ?? chosen} onChange={(n) => void switchAgent(n)} disabled={disabled || busy} />} />
        </div>
      </div>
    </section>
  );
}

const KIND_ICON = { table: Table2, scatter: ScatterChart, line: LineChart, bar: BarChart3, parallel: LineChart, markdown: FileText, image: ImageIcon };

/** 대화 속 결과: 한 줄 목록(대화가 무거워지지 않게) — 누르면 오른쪽 캔버스에서 크게 연다 */
function ArtifactChips({ items }: { items: Artifact[] }) {
  const canvas = useCanvas();
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, 4);
  return (
    <div className="chat-arts">
      <div className="chat-arts-head"><BarChart3 size={13} />결과 {items.length}개</div>
      {shown.map((a) => {
        const Icon = KIND_ICON[a.kind] ?? FileText;
        return (
          <button key={a.id} className="chat-art-row chat-art-open" onClick={() => canvas.showArtifact(a.id)} title="캔버스에서 보기">
            <Icon size={14} className="kind" /><span className="chat-art-title">{a.title}</span><ChevronRight size={14} className="muted" />
          </button>
        );
      })}
      {items.length > 4 && <button className="link-btn chat-arts-more" onClick={() => setAll(!all)}>{all ? "접기" : `${items.length - 4}개 더 보기`}</button>}
    </div>
  );
}

/** 단계를 실행 순서대로(엣지 기준 위상 정렬, 같은 순위는 원래 순서) */
export function orderNodes(f: Flow): FlowNode[] {
  const indeg = new Map(f.nodes.map((n) => [n.id, 0]));
  f.edges.forEach((e) => indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1));
  const out: FlowNode[] = [];
  const left = [...f.nodes];
  while (left.length) {
    const i = Math.max(0, left.findIndex((n) => (indeg.get(n.id) ?? 0) === 0));
    const [n] = left.splice(i, 1);
    out.push(n);
    f.edges.filter((e) => e.source === n.id).forEach((e) => indeg.set(e.target, (indeg.get(e.target) ?? 1) - 1));
  }
  return out;
}

/** 대화 속 작업 흐름 카드: 실행 순서대로 단계 목록 + 진행률 + 승인/반려 (그래프는 캔버스에서) */
function FlowCard({ pid, f, readOnly }: { pid: number; f: Flow; readOnly: boolean }) {
  const canvas = useCanvas();
  const decide = useFlowDecision(pid);
  const [more, setMore] = useState(false);
  const st = FLOW_STATUS[f.status];
  const steps = orderNodes(f);
  const done = steps.filter((n) => n.status === "complete").length;
  const shown = more || steps.length <= 8 ? steps : steps.slice(0, 6);
  const review = f.status === "review";
  return (
    <div className={`chat-card flow-card st-${f.status}`}>
      <div className="chat-card-head">
        <Workflow size={16} className="brand" /><b>{f.title}</b><span className={`chip ${st.chip}`}>{st.label}</span>
        <span className="grow" />
        <button className="small ghost" onClick={() => canvas.showFlow(f.id)} title="오른쪽 캔버스에 흐름도로 보기"><PanelRight size={13} />흐름도</button>
      </div>
      {review && <p className="small">에이전트가 아래 순서로 진행하려고 합니다. 확인하고 승인해 주세요.</p>}
      <ol className="flow-steps">
        {shown.map((n, i) => (
          <li key={n.id} className={`fs-${n.status}`}>
            <span className="fs-ico" aria-label={NODE_STATUS[n.status]}>
              {n.status === "complete" ? <Check size={12} /> : n.status === "in_progress" ? <Loader2 size={12} className="spin" />
                : n.status === "failed" ? <X size={12} /> : i + 1}
            </span>
            <span className="fs-main">
              <span className="fs-label">{n.label}</span>
              {n.detail && <span className="fs-detail">{n.detail}</span>}
            </span>
            <span className="fs-tag">{NODE_TYPE[n.type] ?? n.type}{n.tool ? ` · ${n.tool}` : ""}</span>
          </li>
        ))}
      </ol>
      {steps.length > shown.length && <button className="link-btn small" onClick={() => setMore(true)}>나머지 {steps.length - shown.length}단계 더 보기</button>}
      {!review && f.status !== "rejected" && (
        <div className="flow-progress" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done}
          aria-label="진행률"><div style={{ width: `${(100 * done) / Math.max(1, steps.length)}%` }} /></div>
      )}
      <div className="row chat-card-foot">
        <span className="small muted">{review ? "고칠 점은 아래 입력창에 적어 보내면 다시 제안합니다" : `${steps.length}단계 중 ${done}단계 완료`}</span>
        <span className="grow" />
        {review && !readOnly && (
          <>
            <button className="small" onClick={() => decide.mutate({ flow: f, status: "rejected" })} disabled={decide.isPending}><X size={13} />반려</button>
            <button className="small primary" onClick={() => decide.mutate({ flow: f, status: "approved" })} disabled={decide.isPending}><Check size={13} />승인하고 실행</button>
          </>
        )}
      </div>
    </div>
  );
}

function ToolGroup({ items }: { items: ChatMessage[] }) {
  const [open, setOpen] = useState(false);
  const failed = items.filter((m) => m.meta?.status === "failed").length;
  const running = items.some((m) => m.meta?.status !== "completed" && m.meta?.status !== "failed");
  const visible = items.filter((m) => !isQuietTool(m.meta?.title || m.content));
  const last = visible[visible.length - 1] ?? items[items.length - 1];
  return (
    <div className="cv-toolgroup">
      <button className="cv-toolgroup-head" onClick={() => setOpen(!open)} aria-expanded={open} title="에이전트가 사용한 도구 기록">
        <Wrench size={12} />에이전트 작업 {items.length}건{failed ? ` · 실패 ${failed}` : ""}
        {running ? <Loader2 size={12} className="spin" /> : failed ? <X size={12} className="err" /> : <Check size={12} className="ok" />}
        <span className="small muted">{open ? "접기" : "펼치기"}</span>
      </button>
      {open ? items.map((m) => <Message key={m.id} m={m} />) : <Message m={last} />}
    </div>
  );
}

function Message({ m }: { m: ChatMessage }) {
  const [openTool, setOpenTool] = useState(false);
  if (m.role === "user") return (
    <div className="chat-msg user">
      <div className="bubble">{m.content}</div>
      {!!m.meta?.attachments?.length && (
        <div className="msg-files">{m.meta.attachments.map((a) => (
          <span key={a.id} className="file-chip" title={`내 PC 프로젝트 폴더 inputs/${a.filename}`}><FileUp size={12} /><span className="n">{a.filename}</span>
            <span className="s">{a.size > 1e6 ? `${(a.size / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(a.size / 1e3))} KB`}</span></span>
        ))}</div>
      )}
    </div>
  );
  if (m.role === "system") return <div className="chat-msg agent err"><div className="bubble">{m.content}</div></div>;
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
    const [label, raw] = toolLabel(m.meta?.title || m.content);
    const icon = st === "completed" ? <Check size={13} /> : st === "failed" ? <X size={13} /> : <Loader2 size={13} className="spin" />;
    return (
      <div className={`cv-tool ${st}`}>
        <button className="cv-tool-head" onClick={() => setOpenTool(!openTool)} aria-expanded={openTool}>
          <Wrench size={12} /><span className="t">{label}</span><span className="st">{icon}</span>
        </button>
        {openTool && (
          <div className="cv-tool-body">
            {raw && <div className="small muted mono">{raw}</div>}
            {m.meta?.raw_input != null && <pre>{JSON.stringify(m.meta.raw_input, null, 2)}</pre>}
            {m.meta?.output && <pre>{m.meta.output}</pre>}
          </div>
        )}
      </div>
    );
  }
  return <div className="chat-msg agent"><div className="bubble"><Markdown text={m.content} /></div></div>;
}

function PermissionCard({ p, readOnly, onAnswer }: { p: PermissionRequest; readOnly: boolean; onAnswer: (optionId: string | null) => void }) {
  const allow = p.options.filter((o) => o.kind.startsWith("allow"));
  const reject = p.options.filter((o) => o.kind.startsWith("reject"));
  const [label, raw] = toolLabel(p.title);
  const rows = readableArgs(p.raw_input);
  return (
    <div className="chat-card perm-card" role="group" aria-label={`실행 확인: ${label}`}>
      <div className="chat-card-head"><ShieldQuestion size={16} /><b>내 PC에서 실행해도 될까요?</b></div>
      <p className="perm-title">{label}{raw && <span className="small muted mono"> {raw}</span>}</p>
      {rows ? (
        <dl className="perm-args">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      ) : p.raw_input != null && <pre className="cv-perm-input">{JSON.stringify(p.raw_input, null, 2).slice(0, 1500)}</pre>}
      {!readOnly && <p className="small muted">허용하면 에이전트가 이 작업을 내 PC에서 실행합니다. 거부하면 멈추고 다른 방법을 묻습니다.</p>}
      {readOnly ? <p className="small muted">대화를 시작한 사람이 확인합니다.</p> : (
        <div className="row chat-card-foot">
          <span className="grow" />
          {reject.map((o) => <button key={o.optionId} className="small" onClick={() => onAnswer(o.optionId)}><CircleSlash size={13} />{o.name}</button>)}
          {!reject.length && <button className="small" onClick={() => onAnswer(null)}>취소</button>}
          {allow.map((o) => <button key={o.optionId} className={`small ${o.kind === "allow_once" ? "primary" : ""}`} onClick={() => onAnswer(o.optionId)}>{o.name}</button>)}
        </div>
      )}
    </div>
  );
}
