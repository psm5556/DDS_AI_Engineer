import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, BarChart3, Brain, Check, ChevronRight, CircleSlash, FileText, Image as ImageIcon, LineChart,
  FileUp, Loader2, PanelRight, ScatterChart, ShieldQuestion, Table2, Workflow, Wrench, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, get, post } from "../api";
import { useCanvas } from "../canvas/CanvasContext";
import { useFlowDecision } from "../canvas/views";
import { AgentGlyph } from "../components/AgentGlyph";
import { Markdown } from "../components/Markdown";
import { scrollMode, useFreshKeys } from "../motion";
import { Composer, type Outgoing } from "./Composer";
import { clockOf, WaitingBubble, type TurnClock } from "./WaitingBubble";
import { AgentPicker, resolveAgent, usePreferredAgent } from "../components/AgentPicker";
import { clip, readableArgs, toolLabel } from "./labels";
import { Skeleton } from "../components/Skeleton";
import { AGENT_PREFILL, AGENT_SEND, FLOW_STATUS, NODE_STATUS, NODE_TYPE, PRACTICE_EXAMPLES, localIso, toolName, when, type Prefill } from "../format";
import { useLive, useServerDown } from "../live";
import { useToast } from "../toast";
import { orderNodes } from "../flow/order";
import type { AgentInstance, AgentStatus, Artifact, Attachment, ChatMessage, ChatSession, Flow, PermissionRequest, Project, RouteResult, SessionStatus, TurnStatus, WorkDetail, WorkSource } from "../types";
import { autoTaskName, COPY, taskPath, W } from "../work/copy";
import { RouteCard, routeCheck, TakeoverCard } from "../work/RouteCard";
import { useWorkList } from "../work/scope";
import { SourcePicker } from "../work/SourcePicker";

/** 가운데 기록: 사용자는 말로 지시하고, 에이전트의 Task Flow 제안·실행 확인·결과가 기록 안의 카드로 이어진다.
 *  같은 내용은 오른쪽 탭에 크게 쌓인다. 기록은 Task에 하나다. 이전 기록만 옛 대화 여러 개를 시간순으로 합쳐 보여 준다.
 *  wid가 없으면 새 Task 화면이다. 보낼 때 Task와 기록이 만들어진다. */

type Item =
  | { kind: "msg"; at: string; m: ChatMessage }
  | { kind: "divider"; at: string; chat: number; text: string }
  | { kind: "tools"; at: string; items: ChatMessage[] }
  | { kind: "flow"; at: string; f: Flow }
  | { kind: "arts"; at: string; items: Artifact[] };

// 누르면 바로 보낸다. 그래서 CAD·CAE 툴도 모델 파일도 없이 끝까지 되는 것만 둔다
export const CHAT_SUGGEST = [PRACTICE_EXAMPLES.washer, PRACTICE_EXAMPLES.whole, PRACTICE_EXAMPLES.two];

const SENDING_TURN: TurnStatus = {
  stage: "sending", stage_detail: null, started_at: null, elapsed_sec: 0, stage_elapsed_sec: 0, idle_sec: 0,
  typical_start_sec: null, link: "ok", cancelling: false, cancel_elapsed_sec: null, agent: null,
};
const STATUS_POLL_MS = 15000; // 진행 중에는 이벤트를 놓쳐도 스스로 맞춘다(중지 버튼에 갇히지 않게)
const RESTART_HOLD_MS = 90000; // 서버가 다시 시작되면 Agent가 붙기 전까지 턴을 모른다. 그동안 말풍선을 거두지 않고 기다리는 시간
const RESYNC_MS = 3000; // 놓친 것을 다시 읽는 간격. 글자가 쏟아지는 동안 기록을 너무 자주 읽지 않게
const PIECES_KEPT = 4000; // 답 하나에서 기억해 둘 글자 조각 수. 기록을 읽는 시간 동안 온 만큼이면 충분하다
const agentLabels = new Map<string, string>(); // Agent가 끊겨 목록이 비는 동안에도 'fake를 시작하는 중' 같은 날 이름을 보이지 않게

/** 기록을 읽는 사이 온 글자는 읽은 값에 덮여 사라진다. 서버가 준 위치(offset)로 조각을 다시 맞춘다: 이미 있는 글자는 겹치지 않고, 빠진 곳만 채운다 */
function fillPieces(xs: ChatMessage[], pieces: Map<number, { off: number; text: string }[]>): ChatMessage[] {
  let changed = false;
  const out = xs.map((m) => {
    const list = pieces.get(m.id);
    if (!list) return m;
    let c = m.content;
    for (const p of list) if (p.off <= c.length && p.off + p.text.length > c.length) c = c.slice(0, p.off) + p.text;
    if (c === m.content) return m;
    changed = true;
    return { ...m, content: c };
  });
  return changed ? out : xs;
}

export interface InitialPrompt {
  text: string; attachmentIds: number[]; fromWorkId?: number;
  /** 보내지 않고 입력창만 채운다(되돌리기, 새 모델 파일을 기다릴 때) */
  fillOnly?: boolean; askFile?: string;
}

export function ChatView({ pid, wid, work, firstTask, onWork, initialPrompt, onInitialSent }: {
  pid: number; wid: number | null; work: WorkDetail | null; firstTask?: boolean; onWork: (id: number) => void;
  initialPrompt?: InitialPrompt | null; onInitialSent?: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const canvas = useCanvas();
  const nav = useNavigate();
  const [preferred, setPreferred] = usePreferredAgent();
  // 보내는 곳: 이 Task 안 내 최근 기록. 표로 시작한 Task처럼 아직 없으면 보낼 때 만들고 여기에 적는다
  const [madeChat, setMadeChat] = useState<number | null>(null);
  const sid = work?.active_chat_id ?? madeChat;
  // 진행 중인 턴(없으면 null). 새 Task를 보내면 주소가 바뀌며 이 화면이 다시 만들어지므로, 보낸 쪽이 캐시에 남긴 것으로 이어 받는다
  const [clock, setClock] = useState<TurnClock | null>(() => (wid ? qc.getQueryData<TurnClock>(["turn", wid]) ?? null : null));
  const busy = !!clock;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const liveTick = useRef(0); // 상태를 읽는 사이에 이벤트가 오면 그 이벤트가 더 새롭다
  const sending = useRef(false);
  const newWork = useRef<WorkDetail | null>(null); // 새 Task의 첫 요청이 실패해도 빈 Task를 또 만들지 않는다
  const [perms, setPerms] = useState<PermissionRequest[]>([]);
  const [statusKnown, setStatusKnown] = useState(!sid || !!clock); // 진행 중인지 읽기 전에는 보내기를 열어 두지 않는다(409만 받는다)
  const [pendingFirst, setPendingFirst] = useState<string | null>(null); // 새 Task의 첫 요청: 답을 받아 주소가 바뀌기 전까지 여기서 보여 준다
  const [pollFailed, setPollFailed] = useState(false);
  const liveDown = useServerDown();
  const serverDown = liveDown || pollFailed;
  const stopWanted = useRef(false); // 서버가 턴을 받기 전에 누른 중지. 턴 id가 생기면 전달한다
  const holdUntil = useRef(0);
  const statusBusy = useRef(false);
  const resync = useRef<number | null>(null);
  useEffect(() => () => { if (resync.current !== null) clearTimeout(resync.current); }, []);
  const pieces = useRef(new Map<number, { off: number; text: string }[]>()); // 이 턴에 받은 글자 조각(메시지별)
  const bodyRef = useRef<HTMLDivElement>(null);
  const sentInitial = useRef(false);
  const [seed, setSeed] = useState<{ text: string; n: number; attachments?: Attachment[]; askFile?: string } | undefined>();
  const [source, setSource] = useState<WorkSource | null>(null); // 새 Task 화면에서 고른 '지난 Task처럼 시작'
  const homeSource = useRef(initialPrompt?.fromWorkId); // 첫 화면에서 골랐으면 처음 보내는 요청에 쓴다. initialPrompt는 보내면 비워진다
  const fromWork = useRef<number | undefined>(undefined);
  fromWork.current = source?.work_id ?? homeSource.current;
  // 남의 Task에 적은 요청: 보내지 않고 카드로 묻는다. 복사하면 새 Task에서 이 요청을 그대로 보낸다
  const [takeoverAsk, setTakeoverAsk] = useState<{ text: string; attachmentIds: number[] } | null>(null);
  const [copying, setCopying] = useState(false);
  // 경로 카드: 보내기 전에 규칙을 본다. 카드가 떠 있을 때 Enter를 한 번 더 누르면 글자 링크로 처리한다(실수로 폴더가 생기지 않게)
  const [route, setRoute] = useState<RouteResult | null>(null);
  const routeRef = useRef(route);
  routeRef.current = route;
  const kept = useRef<Set<string>>(new Set());
  const keepNext = useRef(false); // 다음 보내기는 '여기서 계속'을 고른 것이다
  const [submitNow, setSubmitNow] = useState(0);
  const peek = useRef<(() => Outgoing) | null>(null);
  const warmed = useRef(false);

  const { data: agent } = useQuery({
    queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"),
    // 연결 직후 hello가 오기 전에는 쓸 수 있는 에이전트를 모른다. 방송을 놓쳐도 입력창이 오래 막히지 않게 짧게 다시 읽는다
    refetchInterval: (q) => (q.state.data?.connected && !q.state.data.info?.agents?.length ? 2000 : 30000),
  });
  const session = work?.chats.find((s) => s.id === sid) ?? null;
  // refetchOnMount: 이 화면을 떠난 사이의 글자를 이벤트로는 받지 못했다. 캐시로 그리면 답 가운데가 빈 채로 이어 붙는다
  const { data: msgs, isError: msgsFailed } = useQuery({ queryKey: ["messages", wid], enabled: !!wid, refetchOnMount: "always", queryFn: () => get<ChatMessage[]>(`/api/works/${wid}/messages`) });
  const flows = useWorkList<Flow>("flows", pid, wid);
  const arts = useWorkList<Artifact>("artifacts", pid, wid);
  useEffect(() => { // 기록을 다 읽은 직후: 읽는 사이 온 글자를 바로 채운다(다음 글자가 오래 안 올 수 있다)
    if (!wid || !msgs) return;
    const filled = fillPieces(msgs, pieces.current);
    if (filled !== msgs) qc.setQueryData(["messages", wid], filled);
  }, [msgs, wid, qc]);
  const readOnly = !!work && !work.mine; // 남의 Task는 보기만 한다. 말없이 내 Task로 넘어가지 않는다
  const chosen = resolveAgent(agent, preferred);
  agent?.info?.agents?.forEach((a) => agentLabels.set(a.name, a.label));
  const agentLabel = (name: string) => agentLabels.get(name) ?? (name || "AI 도구");
  // 진행 중인 대화의 에이전트를 바꾸면 다음 요청부터 그 CLI로 이어 간다(이전 대화 내용은 서버가 넘긴다)
  const switchAgent = async (name: string) => {
    setPreferred(name);
    if (!session || readOnly) return;
    try {
      await api(`/api/sessions/${session.id}`, { method: "PUT", body: { agent: name } });
      qc.invalidateQueries({ queryKey: ["work", wid] });
      toast(`다음 요청부터 ${agentLabel(name)}(으)로 이어 갑니다`);
    } catch (e) { toast((e as Error).message, true); }
  };
  const agentKnown = !!agent?.connected && !!agent.info?.agents?.length; // 연결 직후 hello 전: 어떤 CLI를 쓸지 모르니 아직 보내지 않는다
  const disabled = !agentKnown;
  // 끊겼을 때 기록을 설치 안내로 덮지 않는다. 정말 설치한 적이 없을 때만 안내한다
  const offline = !!agent && !agent.connected;
  const { data: mine } = useQuery({ queryKey: ["agent", "mine"], enabled: offline, queryFn: () => get<AgentInstance[]>("/api/agent/instances/mine") });

  const refreshStatus = useCallback(() => {
    if (!sid) return;
    const tick = liveTick.current;
    statusBusy.current = true;
    get<SessionStatus>(`/api/sessions/${sid}/status`).then((s) => {
      setPollFailed(false);
      setStatusKnown(true);
      if (liveTick.current !== tick || sending.current) return;
      if (busyRef.current && !s.busy) {
        // 서버가 다시 시작되어 Agent가 아직 안 붙었다. 턴이 되살아나거나 '끊겼다'는 안내가 올 때까지 말풍선을 거두지 않는다
        if (s.link === "lost") {
          if (!holdUntil.current) holdUntil.current = Date.now() + RESTART_HOLD_MS;
          if (Date.now() < holdUntil.current) {
            setClock((c) => c && { at: c.at, turn: { ...c.turn, stage: "reconnecting", link: "lost" } });
            return;
          }
        }
        // 끝을 놓쳤다. 그사이 쌓인 글을 읽어 온다
        qc.invalidateQueries({ queryKey: ["messages", wid] });
      }
      holdUntil.current = 0;
      setClock(s.busy ? clockOf(s) : null);
      setPerms(s.pending_permissions);
    }).catch(() => { setStatusKnown(true); setPollFailed(true); }).finally(() => { statusBusy.current = false; });
  }, [sid, wid, qc]);

  useEffect(() => {
    if (wid) qc.removeQueries({ queryKey: ["turn", wid] });
    if (!sid) { if (!sending.current) { setClock(null); setPerms([]); } setStatusKnown(true); return; }
    refreshStatus(); // 새로고침·재마운트 뒤에도 진행 상황과 경과 시간을 서버에서 다시 받는다
  }, [sid, wid, qc, refreshStatus]);

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refreshStatus, STATUS_POLL_MS);
    return () => clearInterval(t);
  }, [busy, refreshStatus]);

  // 다시 연결되어 상태를 읽을 수 있게 되면 알림을 거둔다(진행 중이 아니면 위 주기 읽기가 돌지 않는다)
  useEffect(() => { if (pollFailed && !busy && !liveDown) setPollFailed(false); }, [pollFailed, busy, liveDown]);

  // 기록 + 이 Task의 흐름·결과를 시간순으로 엮는다. 탭과 기록이 같은 Task만 보므로 옆 Task의 흐름이 끼지 않는다
  const timeline = useMemo<Item[]>(() => {
    if (!wid || !msgs) return [];
    const items: Item[] = [];
    // 이전 기록은 옛 대화 여러 개를 합친 것이다. 누구의 대화에서 넘어왔는지 경계 줄로 보인다
    const speakers = new Map((work?.chats ?? []).map((c) => [c.id, c]));
    let chat: number | null = null;
    // 에이전트의 생각·툴 호출은 사람이 볼 필요가 적으므로 연달아 나온 것을 한 줄 묶음으로 접어 둔다
    for (const m of msgs) {
      if (work?.legacy && speakers.size > 1 && m.session_id > 0 && m.session_id !== chat) {
        chat = m.session_id;
        const who = speakers.get(m.session_id);
        items.push({ kind: "divider", at: m.created_at, chat: m.session_id, text: COPY.legacyDivider(who?.display_name ?? "", when(who?.created_at ?? m.created_at)) });
      }
      const last = items[items.length - 1];
      const quiet = m.role === "tool" || m.role === "thought";
      if (quiet && last?.kind === "tools") last.items.push(m);
      else if (quiet) items.push({ kind: "tools", at: m.created_at, items: [m] });
      else items.push({ kind: "msg", at: m.created_at, m });
    }
    (flows ?? []).forEach((f) => items.push({ kind: "flow", at: f.created_at, f }));
    (arts ?? []).forEach((a) => items.push({ kind: "arts", at: a.created_at, items: [a] }));
    items.sort((x, y) => (x.at < y.at ? -1 : x.at > y.at ? 1 : 0));
    // 연달아 게시된 결과는 한 묶음으로
    const merged: Item[] = [];
    for (const it of items) {
      const last = merged[merged.length - 1];
      if (it.kind === "arts" && last?.kind === "arts") last.items.push(...it.items);
      else merged.push(it.kind === "arts" ? { ...it, items: [...it.items] } : it);
    }
    return merged;
  }, [wid, msgs, flows, arts, work]);

  // 스크롤은 바닥 근처에 있을 때만 따라간다(위로 올려 읽는 사람을 끌어내리지 않는다).
  // 처음 열 때와 글자가 흘러나올 때는 즉시, 새 항목이 생길 때만 부드럽게.
  const atBottom = useRef(true);
  const autoUntil = useRef(0); // 부드러운 스크롤 중에는 scroll 이벤트로 '바닥에서 벗어남'을 판단하지 않는다
  const painted = useRef(0);
  const onScroll = () => {
    const el = bodyRef.current;
    if (el && performance.now() > autoUntil.current) atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  // 메시지가 늘지 않아도 내용이 자란다(말풍선의 안내·다시 시작 버튼). 그 버튼이 입력창 아래에 숨으면 멈춘 스크립트에서 빠져나올 길이 안 보인다
  const colRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = bodyRef.current;
    const col = colRef.current;
    if (!el || !col || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (atBottom.current && performance.now() > autoUntil.current) el.scrollTop = el.scrollHeight; });
    ro.observe(col);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const el = bodyRef.current;
    const count = timeline.length + perms.length;
    if (el && atBottom.current && count) {
      if (painted.current && count > painted.current) {
        autoUntil.current = performance.now() + 500;
        el.scrollTo({ top: el.scrollHeight, behavior: scrollMode() });
        // 부드럽게 내려가는 사이 말풍선이 자라면 목적지가 짧아진다. 끝난 뒤 바닥을 다시 맞춘다
        setTimeout(() => { if (atBottom.current) el.scrollTop = el.scrollHeight; }, 520);
      } else if (performance.now() > autoUntil.current) el.scrollTop = el.scrollHeight;
    }
    painted.current = count;
  }, [timeline.length, msgs, busy, perms]);

  // 새로 생긴 항목만 등장 움직임을 준다. 메시지·흐름·결과는 따로 오므로 셋 다 온 뒤를 기준으로 삼는다
  const fresh = useFreshKeys(!!wid && !!msgs && !!flows && !!arts);

  useLive((e) => {
    // 브라우저 연결이나 PC Agent 연결이 바뀌면 그사이 놓친 것을 서버에서 다시 받는다
    if ((e.type === "live_reconnected" || e.type === "agent_status") && sid) {
      refreshStatus();
      if (e.type === "live_reconnected") qc.invalidateQueries({ queryKey: ["messages", wid] });
      return;
    }
    if (e.type === "live_reconnected") setPollFailed(false);
    const key = ["messages", wid];
    const resyncSoon = () => {
      if (resync.current === null) resync.current = window.setTimeout(() => { resync.current = null; qc.invalidateQueries({ queryKey: key }); }, RESYNC_MS);
    };
    if (e.type !== "agent_event" || e.session_id !== sid || !sid) {
      // 이전 기록은 다른 멤버의 대화도 같은 기록이다. 글자를 이어 붙이지 않고 잠시 뒤 다시 읽는다
      if (e.type === "agent_event" && wid && e.work_id === wid) resyncSoon();
      return;
    }
    const ev = e.event as Record<string, unknown>;
    const upd = (fn: (xs: ChatMessage[]) => ChatMessage[]) => qc.setQueryData<ChatMessage[]>(key, (xs) => fn(xs ?? []));
    const now = localIso();
    // 상태를 담은 이벤트만 "읽는 중인 상태보다 새롭다"고 표시한다. 글자 조각까지 세면 답이 흐르는 동안 상태를 영영 받지 못해
    // 새로고침 뒤 말풍선·중지 버튼 없이 입력창만 열린다
    if (["turn_started", "turn_stage", "turn_ended", "permission_request", "permission_resolved"].includes(ev.kind as string)) {
      liveTick.current += 1;
      holdUntil.current = 0;
    }
    // 글자나 도구 소식이 왔으니 '답을 기다리는 중'의 시계를 처음부터 다시 잰다. 말풍선이 없으면 상태를 놓친 것이니 다시 읽는다
    const heard = () => {
      setClock((c) => c && { at: c.at, turn: { ...c.turn, idle_sec: -(Date.now() - c.at) / 1000 } });
      if (!busyRef.current && !statusBusy.current) refreshStatus();
    };
    switch (ev.kind) {
      case "turn_started":
        setClock(clockOf(ev as unknown as TurnStatus));
        qc.invalidateQueries({ queryKey: key });
        break;
      case "turn_stage": setClock(clockOf(ev as unknown as TurnStatus)); break;
      case "turn_ended":
        setClock(null); setPerms([]);
        pieces.current.clear();
        qc.invalidateQueries({ queryKey: key });
        break;
      case "chunk": {
        heard();
        const id = ev.message_id as number;
        const piece = { off: (ev.offset as number | undefined) ?? 0, text: ev.text as string };
        const list = pieces.current.get(id) ?? [];
        list.push(piece);
        if (list.length > PIECES_KEPT) list.shift();
        pieces.current.set(id, list);
        upd((xs) => fillPieces(xs.some((m) => m.id === id) ? xs
          : [...xs, { id, session_id: sid, role: ev.role as ChatMessage["role"], content: "", meta: null, created_at: now }], pieces.current));
        // 앞부분이 없다(잠깐 끊겨 이벤트를 놓쳤다). 읽는 중이면 그 답이 채운다
        const got = qc.getQueryData<ChatMessage[]>(key)?.find((m) => m.id === id);
        if (got && got.content.length < piece.off && !qc.isFetching({ queryKey: key })) resyncSoon();
        break;
      }
      case "tool_call":
      case "tool_call_update": {
        heard();
        // 읽는 중인 기록에 덮여 도구 줄이 사라지지 않게 그 읽기를 새로 시작한다(도구 소식은 글자처럼 쏟아지지 않는다)
        if (qc.isFetching({ queryKey: key })) qc.invalidateQueries({ queryKey: key });
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

  const send = useCallback(async (text: string, attachmentIds: number[] = [], fileName?: string, routeKept = false) => {
    const t = text.trim();
    if (!t && !attachmentIds.length) return true;
    const tempId = -Date.now();
    let id = sid;
    let target = wid;
    let fresh = false;
    sending.current = true;
    stopWanted.current = false;
    try {
      if (readOnly) { // 어느 입구로 왔든(입력창, 탭의 요청 버튼) 남의 Task에 보내지 않고 카드로 묻는다
        setTakeoverAsk({ text: t, attachmentIds });
        return false;
      }
      if (!target || !id) {
        if (!chosen) throw new Error("쓸 수 있는 AI 에이전트를 찾지 못했습니다 → PC Agent 연결을 확인하거나 설정에서 AI 도구를 설치하세요.");
        if (!target) setPendingFirst(t || "첨부한 파일을 확인해 주세요.");
        setClock(clockOf(SENDING_TURN));
        if (!target) {
          // 첫 화면에서 온 첫 Task는 '첫 Task'로 두고, 그 뒤는 요청 앞부분으로 부른다. 보낼 때 비로소 만들어진다
          const name = firstTask ? null : autoTaskName(t, fileName);
          const made = newWork.current ?? await post<WorkDetail>(`/api/projects/${pid}/works`, { name, name_auto: true, agent: chosen, ...(fromWork.current ? { from_work_id: fromWork.current } : {}) });
          newWork.current = made;
          qc.setQueryData(["work", made.id], made); // 주소가 바뀔 때 뼈대 화면이 끼지 않게
          target = made.id;
          id = made.active_chat_id;
          fresh = true;
          qc.invalidateQueries({ queryKey: ["projects"] });
          qc.invalidateQueries({ queryKey: ["project", pid] });
        } else {
          id = (await post<ChatSession>(`/api/works/${target}/chat`, { agent: chosen })).id;
          setMadeChat(id);
          qc.invalidateQueries({ queryKey: ["work", target] });
        }
      }
      if (!stopWanted.current) setClock(clockOf(SENDING_TURN)); // 새 Task를 만드는 사이 중지를 눌렀다면 '중지하는 중'을 덮지 않는다
      atBottom.current = true; // 내가 보낸 말은 위로 올려 읽던 중이어도 따라간다
      qc.setQueryData<ChatMessage[]>(["messages", target], (xs) => [...(xs ?? []),
        { id: tempId, session_id: id!, role: "user", content: t || "첨부한 파일을 확인해 주세요.", meta: null, created_at: localIso() }]);
      // 서버가 CLI를 기다리지 않고 바로 답한다. 새 Task는 답을 받은 뒤에 주소를 바꾼다:
      // 새 화면이 읽는 상태에 이 턴이 있고, 실패하면 적은 요청과 첨부가 그대로 남은 입력창으로 돌아올 수 있다
      await post(`/api/sessions/${id}/prompt`, { text: t, attachment_ids: attachmentIds, ...(routeKept ? { route_kept: true } : {}) });
      sending.current = false;
      if (stopWanted.current) { // 서버가 받기 전에 누른 중지를 이제 전달한다
        stopWanted.current = false;
        post(`/api/sessions/${id}/cancel`).catch((e: Error) => toast(e.message, true));
      }
      qc.invalidateQueries({ queryKey: ["messages", target] });
      if (fresh) {
        newWork.current = null;
        qc.setQueryData(["turn", target], clockOf(SENDING_TURN));
        onWork(target!);
      } else refreshStatus();
      return true;
    } catch (e) {
      // 보내지 못했으면 화면에 미리 붙인 내 메시지를 거두고, 입력창에 요청을 되돌린다(false)
      setClock(null);
      setPendingFirst(null);
      stopWanted.current = false;
      if (target) qc.setQueryData<ChatMessage[]>(["messages", target], (xs) => (xs ?? []).filter((m) => m.id !== tempId));
      toast((e as Error).message, true);
      sending.current = false;
      refreshStatus(); // 진행 중이라 거절된 경우(409) 진행 표시와 중지 버튼을 되돌린다
      return false;
    } finally {
      sending.current = false;
    }
  }, [pid, sid, wid, work, firstTask, readOnly, chosen, onWork, qc, toast, refreshStatus]);

  // 첫 화면에서 입력한 요청(과 첨부)을 이어서 보낸다
  useEffect(() => {
    if (initialPrompt && (agentKnown || initialPrompt.fillOnly) && !sentInitial.current) {
      sentInitial.current = true;
      onInitialSent?.(); // 새 Task가 만들어지며 화면이 다시 그려져도 두 번 보내지 않게 먼저 비운다
      const { text, attachmentIds, fillOnly, askFile } = initialPrompt;
      if (fillOnly) {
        void (attachmentIds.length ? get<Attachment[]>(`/api/projects/${pid}/attachments?all_works=true`).catch(() => [] as Attachment[]) : Promise.resolve([] as Attachment[]))
          .then((all) => setSeed((s) => ({ text, n: (s?.n ?? 0) + 1, askFile, attachments: all.filter((a) => attachmentIds.includes(a.id)) })));
        return;
      }
      void send(text, attachmentIds).then(async (ok) => {
        if (ok) return;
        // 입력창에 쓴 적 없는 요청이라 Composer가 되돌려 줄 수 없다. 첫 화면에서 쓴 요청과 첨부를 잃지 않게 여기서 채운다
        const all = attachmentIds.length ? await get<Attachment[]>(`/api/projects/${pid}/attachments?all_works=true`).catch(() => [] as Attachment[]) : [];
        setSeed((s) => ({ text, n: (s?.n ?? 0) + 1, attachments: all.filter((a) => attachmentIds.includes(a.id)) }));
      });
    }
  }, [initialPrompt, agentKnown, send, onInitialSent, pid]);

  /** 입력창의 보내기: 남의 Task는 send가 카드로 묻는다. 내 Task와 새 Task 화면은 규칙을 먼저 본다 */
  const submit = async (o: Outgoing): Promise<boolean> => {
    if (readOnly) return send(o.text, o.attachmentIds, o.fileName);
    const keep = keepNext.current || !!routeRef.current; // 글자 링크를 눌렀거나, 카드가 떠 있는데 Enter를 또 눌렀다
    keepNext.current = false;
    if (keep) {
      o.files.forEach((f) => kept.current.add(f.sha256));
      setRoute(null);
      return send(o.text, o.attachmentIds, o.fileName, true);
    }
    const r = await routeCheck({ work_id: wid, project_id: pid, text: o.text, files: o.files, kept: [...kept.current] });
    if (r.card === "A" || r.card === "B") { setRoute(r); return false; }
    return send(o.text, o.attachmentIds, o.fileName);
  };
  /** 파일을 놓는 즉시 묻는다. 지금 Task에 결과가 있고 내용이 다른 모델 파일일 때만 카드가 뜬다 */
  const onFile = (file: { name: string; sha256: string }) => {
    if (!wid || readOnly) return;
    void routeCheck({ work_id: wid, text: "", files: [file], kept: [...kept.current] }).then((r) => { if (r.card === "A") setRoute((now) => now ?? r); });
  };
  const keepHere = () => {
    const o = peek.current?.();
    o?.files.forEach((f) => kept.current.add(f.sha256));
    if (o?.text) { keepNext.current = true; setSubmitNow((n) => n + 1); } else setRoute(null); // 파일만 놓았으면 요청을 적을 때까지 기다린다
  };
  /** 카드 A: 지금 Task의 작업 순서·스크립트·설정을 가져온 새 Task. 지금 Task는 그대로다 */
  const startNewTask = async (o: { text: string; attachmentIds: number[]; fileName?: string; file?: string | null }, from: number) => {
    if (!o.file) { // 파일이 없으면 AI를 띄우지 않는다. 새 Task 화면에서 파일을 받는다
      nav(`/projects/${pid}/w/new`, { state: { prompt: o.text, attachmentIds: o.attachmentIds, fromWorkId: from, fillOnly: true, askFile: COPY.askModelFile } });
      return;
    }
    setCopying(true);
    try {
      const made = await post<WorkDetail>(`/api/projects/${pid}/works`, { name: autoTaskName("", o.file), name_auto: true, agent: chosen, from_work_id: from });
      qc.setQueryData(["work", made.id], made);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project", pid] });
      nav(taskPath(pid, made.id), { state: { prompt: o.text, attachmentIds: o.attachmentIds } });
    } catch (e) { toast((e as Error).message, true); setCopying(false); }
  };
  /** 카드 B: 요청과 첨부를 새 Project로 옮긴다. 지금 Project는 그대로다 */
  const startNewProject = async (o: { text: string; attachmentIds: number[]; fileName?: string }) => {
    setCopying(true);
    try {
      const first = (o.text || o.fileName || "").split(/[\n.!?]/)[0].trim();
      const made = await post<{ id: number }>("/api/projects", { name: (first.length > 28 ? `${first.slice(0, 28)}…` : first) || COPY.newProject, description: "" });
      if (o.attachmentIds.length) await post(`/api/projects/${made.id}/attachments/adopt`, { ids: o.attachmentIds });
      qc.invalidateQueries({ queryKey: ["projects"] });
      nav(`/projects/${made.id}/w/new`, { state: { prompt: o.text, attachmentIds: o.attachmentIds } });
    } catch (e) { toast((e as Error).message, true); setCopying(false); }
  };
  const primary = () => {
    const o = peek.current?.() ?? { text: "", attachmentIds: [], fileName: undefined, files: [], pendingFiles: [] };
    if (route?.card === "B") void startNewProject(o);
    else void startNewTask({ ...o, file: route?.file ?? null }, route?.source_work?.id ?? wid!);
  };
  const undo = async () => {
    if (!wid) return;
    try {
      const r = await post<{ source_work_id: number | null; text: string; attachment_ids: number[] }>(`/api/works/${wid}/undo`);
      qc.removeQueries({ queryKey: ["work", wid] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project", pid] });
      toast(COPY.undone(work?.source_label ?? null));
      nav(r.source_work_id ? taskPath(pid, r.source_work_id) : `/projects/${pid}/w/new`, { replace: true, state: { prompt: r.text, attachmentIds: r.attachment_ids, fillOnly: true } });
    } catch (e) { toast((e as Error).message, true); }
  };
  const prefillKeep = () => { void send(COPY.keepHere, [], undefined, true); };
  // 이 Task에서 시작한 새 Task. 옛 기록 끝에서 어디로 갔는지 보인다
  const { data: projectNow } = useQuery({ queryKey: ["project", pid], queryFn: () => get<Project>(`/api/projects/${pid}`) });
  const nextTasks = (projectNow?.works ?? []).filter((w) => wid && w.source_work_id === wid && w.mine && w.source_kind === "same_again");

  /** 내 PC로 복사해서 하기: 내 이름의 새 Task를 만들고, 적어 둔 요청은 그 Task에서 보낸다. 원래 Task는 그대로다 */
  const copyToPc = async () => {
    if (!wid) return;
    setCopying(true);
    try {
      const made = await post<WorkDetail>(`/api/works/${wid}/takeover`, chosen ? { agent: chosen } : {});
      qc.setQueryData(["work", made.id], made);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project", pid] });
      toast(COPY.copied(made.name));
      nav(taskPath(pid, made.id), { state: { prompt: takeoverAsk?.text ?? "", attachmentIds: takeoverAsk?.attachmentIds ?? [] } });
    } catch (e) { toast((e as Error).message, true); setCopying(false); }
  };

  // 상황에 맞는 추천 프롬프트 (→ 키로 입력)
  const suggestions = useMemo(() => {
    const review = (flows ?? []).find((f) => f.status === "review");
    const done = (flows ?? []).some((f) => f.status === "complete");
    if (review) return [`${W.flow} '${review.title}'에서 바꿀 점: `, `이 ${W.flow}의 각 단계를 자세히 설명해 줘`, "승인 전에 필요한 입력 파일과 툴 경로를 점검해 줘"];
    if (done) return ["최신 결과로 보고서를 다시 작성해 줘", "Output 추가: ", "능동학습으로 예측 모델 정확도를 더 높여 줘",
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
  // 보내지 않고 입력창만 채우기(예: 데이터 탭의 'Output 추가')
  useEffect(() => {
    const h = (e: Event) => setSeed((s) => ({ ...(e as CustomEvent<Prefill>).detail, n: (s?.n ?? 0) + 1 }));
    window.addEventListener(AGENT_PREFILL, h);
    return () => window.removeEventListener(AGENT_PREFILL, h);
  }, []);

  // 지금 진행 중인 흐름 단계(프롬프트를 보낸 뒤의 말풍선에만 쓴다)
  const flowActivity = useMemo(() => {
    const f = (flows ?? []).find((x) => x.status === "in_progress" || x.status === "approved");
    if (!f) return null;
    const steps = orderNodes(f);
    const i = steps.findIndex((n) => n.status === "in_progress");
    return i >= 0 ? `${steps[i].label} 진행 중 · ${i + 1}/${steps.length}단계` : null;
  }, [flows]);
  const toolTitle = clock?.turn.stage === "tool" ? clock.turn.stage_detail : null;

  const last = msgs?.[msgs.length - 1];
  const streamId = busy && last?.role === "agent" ? last.id : null; // 지금 글자가 흘러나오는 답

  const cancel = () => {
    if (clock?.turn.stage === "sending") { // 아직 서버에 턴이 없다. 받는 대로 중지한다
      stopWanted.current = true;
      setClock((c) => c && { at: c.at, turn: { ...c.turn, cancelling: true, cancel_elapsed_sec: 0 } });
      return;
    }
    if (sid) post(`/api/sessions/${sid}/cancel`).catch((e: Error) => toast(e.message, true));
  };
  const restart = () => { if (sid) post(`/api/sessions/${sid}/force-restart`).then(refreshStatus).catch((e: Error) => toast(e.message, true)); };
  const answer = async (p: PermissionRequest, optionId: string | null) => {
    try {
      await post(`/api/sessions/${sid}/permission`, { request_id: p.request_id, option_id: optionId });
      setPerms((ps) => ps.filter((x) => x.request_id !== p.request_id));
    } catch (e) { toast((e as Error).message, true); }
  };

  return (
    <section className="chat" aria-label="이 Task의 기록">
      <div className="chat-body" ref={bodyRef} onScroll={onScroll}>
        <div className="chat-col" ref={colRef}>
          {work?.source_kind === "same_again" && work.source_label && (
            <p className="tl-source small muted">{COPY.startedFrom(work.source_label)}{work.can_undo && <> · <button className="link-btn" onClick={() => void undo()}>{COPY.undo}</button></>}</p>
          )}
          {serverDown ? (
            <div className="notice warn chat-offline" role="status"><AlertTriangle size={15} />
              <span>웹앱 서버에 연결할 수 없습니다. 다시 연결을 시도하고 있습니다. 내용은 그대로이고, 연결되면 이어서 보여 줍니다.</span>
            </div>
          ) : offline && (!!timeline.length || mine?.length !== 0) && (
            <div className="notice warn chat-offline" role="status"><AlertTriangle size={15} />
              <span>PC Agent가 연결되어 있지 않습니다. 기록은 그대로 볼 수 있고, 다시 연결되면 이어서 요청할 수 있습니다.
                {" "}<Link to="/settings">연결 상태 보기</Link></span>
            </div>
          )}
          {offline && !timeline.length && mine?.length === 0 ? (
            <div className="chat-empty"><AlertTriangle size={28} /><b>내 PC Agent가 설치되어 있지 않습니다</b>
              <p>에이전트(Gemini·Claude·Codex)와 CAD/CAE 툴은 내 PC에서 실행됩니다. 설치 파일 하나로 준비됩니다.</p>
              <p>입력·출력 표가 있으면 PC Agent 없이도 오른쪽 데이터 탭에서 표를 붙여 넣어 바로 Simulator를 만들 수 있습니다.</p>
              <Link className="btn primary" to="/settings">PC Agent 설치·연결</Link></div>
          ) : !wid && pendingFirst ? (
            <div className="tl-item enter"><Message m={{ id: 0, session_id: 0, role: "user", content: pendingFirst, meta: null, created_at: "" }} /></div>
          ) : wid && !msgs ? (
            // 아직 읽지 못한 것과 비어 있는 것은 다르다. 빈 화면을 보이면 요청이 사라진 줄 안다
            msgsFailed ? <div className="notice err" role="alert"><AlertTriangle size={15} /><span>기록을 불러오지 못했습니다 → 잠시 뒤 새로고침해 주세요.</span></div>
              : <Skeleton rows={4} />
          ) : !wid ? (
            <div className="chat-empty">
              <AgentGlyph size={32} /><b>{COPY.newTaskTitle}</b>
              <p>{COPY.newTaskBody}</p>
              {!source && !disabled && !busy && statusKnown && <div className="chat-suggest">{CHAT_SUGGEST.map((s) => <button key={s} onClick={() => void send(s)}>{s}</button>)}</div>}
            </div>
          ) : !timeline.length && work?.can_undo ? (
            <div className="chat-empty"><AgentGlyph size={32} /><b>{COPY.askHere}</b></div>
          ) : !timeline.length && readOnly ? (
            <div className="chat-empty"><AgentGlyph size={32} /><b>{COPY.emptyOthers}</b></div>
          ) : !timeline.length ? (
            <div className="chat-empty">
              <AgentGlyph size={32} /><b>무엇을 자동화할까요?</b>
              <p>CAD 변경, 해석, 결과 추출, 예측 모델·최적화를 말로 요청하세요. 엑셀 표를 여기에 붙여 넣거나 해석 결과 폴더를 알려 주어 시작해도 됩니다.</p>
              <p>해석을 새로 돌리거나 내 모델을 바꾸는 일은 {W.flow}를 먼저 보여 주고 승인을 받습니다. 조회·후처리, 가진 데이터로 예측 모델·Simulator 만들기, 보고서는 바로 합니다.</p>
              {!disabled && !busy && statusKnown && !readOnly && <div className="chat-suggest">{CHAT_SUGGEST.map((s) => <button key={s} onClick={() => void send(s)}>{s}</button>)}</div>}
            </div>
          ) : timeline.map((it, i) => {
            if (it.kind === "msg" && it.m.role === "route") return <div key={`m${it.m.id}`} className="tl-item"><RouteInHistory m={it.m} last={i === timeline.length - 1} readOnly={readOnly} busy={copying} onPick={(from) => {
              const a = it.m.meta?.attachments ?? [];
              const o = { text: it.m.meta?.request ?? "", attachmentIds: a.map((x) => x.id), fileName: a[0]?.filename };
              if (it.m.meta?.card === "B") void startNewProject(o);
              else void startNewTask({ ...o, file: a[0]?.filename ?? null }, from ?? wid!);
            }} onKeep={() => prefillKeep()} /></div>;
            if (it.kind === "divider") return <div key={`d${it.chat}-${it.at}`} className="tl-divider" role="separator">{it.text}</div>;
            const key = it.kind === "flow" ? `f${it.f.id}` : it.kind === "arts" ? `a${it.items[0].id}` : it.kind === "tools" ? `t${it.items[0].id}` : `m${it.m.id}`;
            // 내 메시지는 임시 id로 먼저 붙고 서버 id로 바뀐다 — 바뀔 때 다시 등장하지 않게 임시일 때만 움직인다
            const enter = fresh(key) && !(it.kind === "msg" && it.m.role === "user" && it.m.id > 0);
            return (
              <div key={key} className={`tl-item ${enter ? "enter" : ""}`}>
                {it.kind === "flow" ? <FlowCard pid={pid} sid={sid} f={it.f} readOnly={readOnly} connected={!!agent?.connected} />
                  : it.kind === "arts" ? <ArtifactChips items={it.items} />
                  : it.kind === "tools" ? <ToolGroup items={it.items} />
                  : <Message m={it.m} streaming={it.m.id === streamId} />}
              </div>
            );
          })}
          {nextTasks.map((w) => <p key={w.id} className="tl-source small muted">{COPY.movedTo(w.name)} · <Link to={taskPath(pid, w.id)}>{COPY.sourceOpen}</Link></p>)}
          {perms.map((p) => <PermissionCard key={p.request_id} p={p} readOnly={readOnly} onAnswer={(o) => void answer(p, o)} />)}
          {clock && !perms.length && (
            <WaitingBubble clock={clock} agentLabel={agentLabel(clock.turn.agent ?? session?.agent ?? chosen ?? "")} flowActivity={flowActivity}
              toolActivity={toolTitle ? `${clip(toolLabel(toolTitle)[0])} 중` : null} onRestart={restart} readOnly={readOnly} serverDown={serverDown} />
          )}
        </div>
      </div>

      <div className="chat-composer">
        <div className="chat-col">
          {!wid && !pendingFirst && <SourcePicker pid={pid} picked={source} onPick={(s) => { if (!s) homeSource.current = undefined; setSource(s); }} preselect={homeSource.current} />}
          {route && (route.card === "A" || route.card === "B") && (
            <RouteCard text={route.card === "B" ? COPY.cardB : route.file ? COPY.cardA(route.file, route.source_work?.name ?? "") : COPY.cardANoFile(route.source_work?.name ?? "")}
              primary={route.card === "B" ? COPY.cardBStart : COPY.cardAStart} link={COPY.keepHere} busy={copying} onPrimary={primary} onLink={keepHere} />
          )}
          {readOnly && takeoverAsk && wid && <TakeoverCard wid={wid} owner={work?.owner_name ?? ""} busy={copying} onPrimary={() => void copyToPc()} onLink={() => setTakeoverAsk(null)} />}
          <Composer projectId={pid} workId={wid} workMine={!!work?.mine} suggestions={readOnly ? [] : suggestions} busy={busy && !readOnly} stopping={!!clock?.turn.cancelling} onStop={cancel}
            onSimulator={(p, s, m, w) => { if (w != null && w !== wid) nav(taskPath(p, w), { state: { canvasTab: "simulator", studyId: s, modelId: m } }); else canvas.openSimulator(s, m); }}
            disabled={readOnly ? false : disabled || !statusKnown} seed={seed}
            onSend={submit} onFile={onFile} submitNow={submitNow} peek={peek}
            onTyping={() => { if (wid && !readOnly && !warmed.current) { warmed.current = true; post(`/api/works/${wid}/warm`).catch(() => undefined); } }}
            placeholder={readOnly ? COPY.readOnly(work?.owner_name ?? "") : busy ? "진행 중입니다. 끝나면 이어서 요청할 수 있습니다"
              : !statusKnown ? "기록을 불러오는 중입니다" : agent?.connected && !agentKnown ? "PC Agent가 준비되는 중입니다"
              : disabled ? "PC Agent 연결 후 요청할 수 있습니다. 표는 지금 붙여 넣어 Simulator를 만들 수 있습니다" : wid ? COPY.askHere : COPY.askNew}
            leftBar={readOnly ? <button type="button" className="small" disabled={copying} onClick={() => setTakeoverAsk({ text: "", attachmentIds: [] })}>{COPY.copyToPc}</button>
              : <AgentPicker value={session?.agent ?? chosen ?? ""} onChange={(n) => void switchAgent(n)} disabled={disabled || busy} />} />
        </div>
      </div>
    </section>
  );
}

/** AI가 첫 단계에서 알아본 경로 카드. 기록에 남는다. 그 뒤에 다른 말이 오갔으면 글만 남기고 버튼은 거둔다 */
function RouteInHistory({ m, last, readOnly, busy, onPick, onKeep }: { m: ChatMessage; last: boolean; readOnly: boolean; busy: boolean; onPick: (from?: number) => void; onKeep: () => void }) {
  const file = m.meta?.attachments?.[0]?.filename;
  const b = m.meta?.card === "B";
  const source = m.meta?.source_work;
  const text = b ? COPY.cardB : file ? COPY.cardA(file, source?.name ?? "") : COPY.cardANoFile(source?.name ?? "");
  if (!last || readOnly) return <p className="small muted">{text}</p>;
  return <RouteCard text={text} lines={m.content ? <p className="route-lines small muted">{m.content}</p> : undefined} primary={b ? COPY.cardBStart : COPY.cardAStart} link={COPY.keepHere} busy={busy}
    onPrimary={() => onPick(source?.id)} onLink={onKeep} />;
}

const KIND_ICON = { table: Table2, scatter: ScatterChart, line: LineChart, bar: BarChart3, parallel: LineChart, markdown: FileText, image: ImageIcon };

/** 기록 속 결과: 한 줄 목록(기록이 무거워지지 않게) — 누르면 오른쪽 탭에서 크게 연다 */
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
          <button key={a.id} className="chat-art-row chat-art-open" onClick={() => canvas.showArtifact(a.id, a.flow_id)} title={`${W.flow} 탭의 그 단계에서 보기`}>
            <Icon size={14} className="kind" /><span className="chat-art-title">{a.title}</span><ChevronRight size={14} className="muted" />
          </button>
        );
      })}
      {items.length > 4 && <button className="link-btn chat-arts-more" onClick={() => setAll(!all)}>{all ? "접기" : `${items.length - 4}개 더 보기`}</button>}
    </div>
  );
}

/** 기록 속 흐름 카드: 실행 순서대로 단계 목록 + 진행률 + 승인/반려 (그래프는 캔버스에서) */
function FlowCard({ pid, sid, f, readOnly, connected }: { pid: number; sid: number | null; f: Flow; readOnly: boolean; connected: boolean }) {
  const canvas = useCanvas();
  const decide = useFlowDecision(pid, sid, connected);
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
        <button className="small ghost" onClick={() => canvas.showFlow(f.id)} title="오른쪽 탭에 흐름도로 보기"><PanelRight size={13} />흐름도</button>
      </div>
      {review && <p className="small">에이전트가 아래 순서로 진행하려고 합니다. 확인하고 승인해 주세요.</p>}
      <ol className={`flow-steps ${f.status === "in_progress" ? "live" : ""}`}>
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
            <span className="fs-tag">{NODE_TYPE[n.type] ?? n.type}{n.tool ? ` · ${toolName(n.tool)}` : ""}</span>
          </li>
        ))}
      </ol>
      {steps.length > shown.length && <button className="link-btn small" onClick={() => setMore(true)}>나머지 {steps.length - shown.length}단계 더 보기</button>}
      {!review && f.status !== "rejected" && (
        <div className="flow-progress" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done}
          aria-label="진행률"><div style={{ width: `${(100 * done) / Math.max(1, steps.length)}%` }} /></div>
      )}
      <div className="row chat-card-foot">
        <span className="small muted">{review ? "승인하면 묻지 않고 끝까지 진행합니다. 고칠 점은 아래 입력창에 적어 보내면 다시 제안합니다"
          : f.status === "approved" && f.start_queued ? (connected ? "승인했습니다. 지금 답이 끝나면 바로 시작합니다" : "승인했습니다. PC Agent가 연결되면 바로 시작합니다")
          : f.status === "stopped" ? `${steps.length}단계 중 ${done}단계에서 멈췄습니다. 이어 가려면 입력창에 '이어서 진행해 줘'라고 보내세요`
          : `${steps.length}단계 중 ${done}단계 완료`}</span>
        <span className="grow" />
        {review && !readOnly && (
          <>
            <button className="small" onClick={() => decide.mutate({ flow: f, status: "rejected" })} disabled={decide.isPending}><X size={13} />반려</button>
            <button className="small primary" onClick={() => decide.mutate({ flow: f, status: "approved" })} disabled={decide.isPending}><Check size={13} />승인하고 실행</button>
          </>
        )}
        {f.status === "approved" && f.start_queued && !readOnly && (
          <button className="small" onClick={() => decide.mutate({ flow: f, status: "review" })} disabled={decide.isPending}><X size={13} />승인 취소</button>
        )}
      </div>
    </div>
  );
}

function ToolGroup({ items }: { items: ChatMessage[] }) {
  const [open, setOpen] = useState(false);
  const tools = items.filter((m) => m.role === "tool");
  const failed = tools.filter((m) => m.meta?.status === "failed").length;
  const running = tools.find((m) => m.meta?.status !== "completed" && m.meta?.status !== "failed");
  // 눈앞에서 끝났을 때만 체크가 튀어오른다(기록을 열 때는 그대로)
  const wasRunning = useRef(false);
  const justDone = useRef(false);
  if (running) justDone.current = false;
  else if (wasRunning.current) justDone.current = true;
  wasRunning.current = !!running;
  if (!tools.length) return null; // 생각만 있는 묶음은 보이지 않는다(진행 중에는 아래 '생각하는 중…'이 보인다)
  const label = running ? `${clip(toolLabel(running.meta?.title || running.content)[0])} 중…`
    : tools.length === 1 ? clip(toolLabel(tools[0].meta?.title || tools[0].content)[0]) : `도구 사용 ${tools.length}건`;
  return (
    <div className="cv-toolgroup">
      <button className="cv-toolgroup-head" onClick={() => setOpen(!open)} aria-expanded={open} title="에이전트가 한 일(툴 사용·생각) 기록">
        {running ? <Loader2 size={12} className="spin" /> : failed ? <X size={12} className="err" /> : <Check size={12} className={`ok ${justDone.current ? "pop-in" : ""}`} />}
        <span className="cv-toolgroup-label">{label}{failed ? ` · 실패 ${failed}` : ""}</span>
        <span className="small muted">{open ? "접기" : "자세히"}</span>
      </button>
      {open && items.map((m) => <Message key={m.id} m={m} />)}
    </div>
  );
}

function Message({ m, streaming }: { m: ChatMessage; streaming?: boolean }) {
  const [openTool, setOpenTool] = useState(false);
  // 서버가 이어서 하라고 보낸 말. 사용자가 한 말처럼 보이면 안 된다
  if (m.role === "user" && m.meta?.auto === "continue") return <div className="chat-msg agent"><span className="small muted">{m.content}</span></div>;
  if (m.role === "user") return (
    <div className="chat-msg user">
      <div className="bubble">{m.content}</div>
      {!!m.meta?.attachments?.length && (
        <div className="msg-files">{m.meta.attachments.map((a) => (
          <span key={a.id} className="file-chip" title={`내 PC 폴더 inputs/${a.filename}`}><FileUp size={12} /><span className="n">{a.filename}</span>
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
  return <div className="chat-msg agent"><div className={`bubble ${streaming ? "streaming" : ""}`}><Markdown text={m.content} /></div></div>;
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
      {readOnly ? <p className="small muted">이 {W.task}의 주인이 확인합니다.</p> : (
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
