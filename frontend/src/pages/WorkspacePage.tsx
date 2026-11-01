import { useQuery } from "@tanstack/react-query";
import { ChevronDown, PanelRight, Users, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ApiError, get } from "../api";
import { CanvasProvider, useCanvas, type CanvasArrival, type CanvasTab } from "../canvas/CanvasContext";
import { CanvasPanel } from "../canvas/CanvasPanel";
import { TableImportHost } from "../canvas/TableImport";
import { MembersPanel } from "../canvas/views";
import { ChatView, type InitialPrompt } from "../chat/ChatView";
import { Skeleton } from "../components/Skeleton";
import { Modal } from "../components/Modal";
import { ProjectMenu } from "../components/ProjectMenu";
import { useConversaLayout } from "../components/sideLayout";
import type { ChatSession, Project, WorkDetail } from "../types";
import { COPY, taskPath } from "../work/copy";
import { pickWork } from "../work/scope";
import { hasWorkMenu, WorkMenu } from "../work/WorkMenu";

const LEGACY_SEEN = "dds.legacyNoticeSeen";

/** Project 화면: 가운데 Task의 기록(주인공) + 오른쪽 탭(켜고 끄기). 탭은 모두 이 Task의 것만 보여 준다.
 *  주소: /projects/:pid (내 최근 Task로 넘김) · /projects/:pid/w/:wid · /projects/:pid/w/new (새 Task, 보낼 때 만들어진다)
 *  옛 주소 /projects/:pid/c/:sid는 그 Task로, /projects/:pid/new는 /w/new로 넘긴다. */
export default function WorkspacePage() {
  const { pid: pidS, wid: widS, sid: sidS } = useParams();
  const pid = Number(pidS);
  const loc = useLocation();
  const nav = useNavigate();
  const wide = useConversaLayout();
  // 좁은 화면(모바일)에서는 캔버스를 전체 화면 시트로 — 저장된 데스크톱 상태와 따로, 처음엔 닫힌 채로
  const narrow = useNarrow();
  const [sheet, setSheet] = useState(false);
  const canvasLayout = narrow
    ? { ...wide, collapsed: !sheet, setCollapsed: (v: boolean) => setSheet(!v), toggle: () => setSheet((o) => !o) }
    : wide;
  const [members, setMembers] = useState(false);
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const [workMenuAt, setWorkMenuAt] = useState<{ x: number; y: number } | null>(null);
  const isNew = widS === "new";
  const oldNew = !widS && !sidS && loc.pathname.endsWith("/new");
  const wid = widS && !isNew ? Number(widS) : null;
  // 첫 화면에서 넘어온 요청(과 첨부)
  const [initial, setInitial] = useState<InitialPrompt | null>(() => promptFrom(loc.state));
  // 이 화면 안에서 다른 Task로 넘어올 때도(복사, 경로 카드, 되돌리기) 요청을 받는다. 그리기 전에 맞춰 새 Task 화면이 고른 시작점을 놓치지 않게 한다
  const seenKey = useRef(loc.key);
  if (seenKey.current !== loc.key) {
    seenKey.current = loc.key;
    const next = promptFrom(loc.state);
    if (next) setInitial(next);
  }
  useEffect(() => { // 새로고침해도 같은 요청을 또 보내지 않게 읽자마자 지운다
    if ((!wid && !isNew) || !promptFrom(loc.state)) return;
    const { prompt: _p, attachmentIds: _a, fromWorkId: _f, fillOnly: _o, askFile: _k, ...rest } = loc.state as Record<string, unknown>;
    nav(loc.pathname + loc.search, { replace: true, state: Object.keys(rest).length ? rest : null });
  }, [loc.state, loc.pathname, loc.search, nav, wid, isNew]);
  // '표로 바로 Simulator'나 넓은 Simulator 화면에서 넘어오면 그 Simulator 탭부터 연다.
  // 주소 이력의 state는 새로고침에도 남으므로 읽자마자 지운다. 평범한 주소로 다시 와도 탭을 강제하지 않게
  const [arrival, setArrival] = useState(() => arrivalFrom(loc.state));
  useEffect(() => {
    if (!wid && !isNew) return; // 아직 넘기는 중이다. 도착한 화면이 읽어야 한다
    const a = arrivalFrom(loc.state);
    if (!a) return;
    setArrival(a);
    const { canvasTab: _t, studyId: _s, modelId: _m, ...rest } = loc.state as Record<string, unknown>;
    nav(loc.pathname + loc.search, { replace: true, state: Object.keys(rest).length ? rest : null });
  }, [loc.state, loc.pathname, loc.search, nav, wid, isNew]);

  const { data: p, error } = useQuery({ queryKey: ["project", pid], queryFn: () => get<Project>(`/api/projects/${pid}`) });
  const { data: work, error: workError } = useQuery({
    queryKey: ["work", wid], enabled: wid != null, retry: false,
    queryFn: () => get<WorkDetail>(`/api/works/${wid}`),
  });
  const lost = workError instanceof ApiError && workError.status === 404; // 지워진 Task의 주소다

  // 주소 넘기기: 프로젝트 줄 → 내 최근 Task, 옛 대화 주소 → 그 Task. 표나 Simulator에서 온 도착 정보를 잃지 않게 state를 그대로 넘긴다
  useEffect(() => {
    if (oldNew) { nav(`/projects/${pid}/w/new`, { replace: true, state: loc.state }); return; }
    if (sidS) {
      get<ChatSession[]>(`/api/projects/${pid}/sessions`).then((rows) => {
        const hit = rows.find((s) => s.id === Number(sidS));
        nav(taskPath(pid, hit?.work_id ?? null), { replace: true, state: loc.state });
      }).catch(() => nav(taskPath(pid), { replace: true }));
      return;
    }
    if (!widS && p) {
      const target = pickWork(p.works);
      nav(target ? taskPath(pid, target.id) : `/projects/${pid}/w/new`, { replace: true, state: loc.state });
    }
    if (lost) nav(taskPath(pid), { replace: true });
    if (work && work.project_id !== pid) nav(taskPath(work.project_id, work.id), { replace: true });
  }, [oldNew, sidS, widS, p, pid, nav, loc.state, lost, work]);

  const onWork = useCallback((id: number) => nav(taskPath(pid, id), { replace: true }), [nav, pid]);

  if (error) return <div className="page"><div className="notice err">{(error as Error).message}</div></div>;
  if (workError && !lost) return <div className="page"><div className="notice err">{(workError as Error).message}</div></div>;
  if (!p || (!isNew && !work)) return <Skeleton page rows={5} />;
  const canvasOn = !canvasLayout.collapsed;
  const menu = !!work && hasWorkMenu(work);
  const others = !!work && !work.mine;

  return (
    <CanvasProvider initial={arrival} workId={wid} readOnly={others}>
      <ApplyArrival arrival={arrival} />
      <TableImportHost pid={pid} wid={wid} mine={!!work?.mine} />
      <div className={`workspace ${canvasOn ? "" : "canvas-collapsed"}`} style={{ "--canvas-w": `${canvasLayout.width}px` } as React.CSSProperties}>
        <div className="ws-main">
          <header className="ws-head">
            <button className="ws-crumb ws-project" title={COPY.menuProject}
              onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenuAt({ x: r.left, y: r.bottom + 4 }); }}>
              <span className="t">{p.name}</span><ChevronDown size={14} />
            </button>
            <span className="ws-sep" aria-hidden="true">/</span>
            {!work ? <span className="ws-crumb ws-task static"><span className="t">{COPY.newTask}</span></span> : (
              <>
                {menu ? (
                  <button className="ws-crumb ws-task" title={COPY.menuTask}
                    onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setWorkMenuAt({ x: r.left, y: r.bottom + 4 }); }}>
                    <span className="t">{work.name}</span><ChevronDown size={14} />
                  </button>
                ) : <span className="ws-crumb ws-task static"><span className="t">{work.name}</span></span>}
                {others && <span className="ws-owner small muted">{work.owner_name} · {COPY.viewOnly}</span>}
              </>
            )}
            <span className="grow" />
            <button className="ws-share" onClick={() => setMembers(true)} title={`멤버 ${p.members.length}명 — 함께 볼 사람 추가`} aria-label="멤버·공유">
              <Users size={15} />공유{p.members.length > 1 && <small>{p.members.length}</small>}</button>
            <button className={`icon-btn ws-icon ${canvasOn ? "on" : ""}`} onClick={canvasLayout.toggle} aria-pressed={canvasOn}
              title={canvasOn ? COPY.canvasClose : COPY.canvasOpen} aria-label={canvasOn ? COPY.canvasClose : COPY.canvasOpen}><PanelRight size={17} /></button>
          </header>
          {work?.legacy && <LegacyNotice />}
          <ChatView key={wid ?? "new"} pid={pid} wid={wid} work={work ?? null} firstTask={!p.works?.length} onWork={onWork}
            initialPrompt={initial} onInitialSent={() => setInitial(null)} />
        </div>
        <CanvasPanel pid={pid} wid={wid} work={work ?? null} layout={canvasLayout} />
      </div>
      {menuAt && <ProjectMenu p={p} at={menuAt} onClose={() => setMenuAt(null)} />}
      {workMenuAt && work && <WorkMenu work={work} at={workMenuAt} onClose={() => setWorkMenuAt(null)} />}
      {members && (
        <Modal title={`멤버·공유 — ${p.name}`} onClose={() => setMembers(false)}>
          <MembersPanel p={p} />
        </Modal>
      )}
    </CanvasProvider>
  );
}

/** 이전 기록을 처음 볼 때 한 번만 알린다 */
function LegacyNotice() {
  const [seen, setSeen] = useState(() => { try { return !!localStorage.getItem(LEGACY_SEEN); } catch { return true; } });
  if (seen) return null;
  const close = () => { setSeen(true); try { localStorage.setItem(LEGACY_SEEN, "1"); } catch { /* 이번 화면에서만 기억한다 */ } };
  return (
    <div className="notice info ws-legacy" role="status"><span className="grow">{COPY.legacyFirst}</span>
      <button className="icon-btn" aria-label="닫기" title="닫기" onClick={close}><X size={14} /></button></div>
  );
}

function promptFrom(state: unknown): InitialPrompt | null {
  const st = state as { prompt?: string; attachmentIds?: number[]; fromWorkId?: number; fillOnly?: boolean; askFile?: string } | null;
  return st?.prompt || st?.attachmentIds?.length || st?.fromWorkId
    ? { text: st.prompt ?? "", attachmentIds: st.attachmentIds ?? [], fromWorkId: st.fromWorkId, fillOnly: st.fillOnly, askFile: st.askFile } : null;
}

function arrivalFrom(state: unknown): CanvasArrival | null {
  const st = state as { canvasTab?: CanvasTab; studyId?: number; modelId?: number } | null;
  return st?.canvasTab ? { tab: st.canvasTab, studyId: st.studyId, modelId: st.modelId } : null;
}

/** 이 화면이 이미 떠 있을 때 도착한 이동(처음 뜰 때는 CanvasProvider의 initial이 맡는다) */
function ApplyArrival({ arrival }: { arrival: CanvasArrival | null }) {
  const { openSimulator } = useCanvas();
  const first = useRef(arrival);
  useEffect(() => {
    if (arrival && arrival !== first.current && arrival.tab === "simulator" && arrival.studyId) openSimulator(arrival.studyId, arrival.modelId);
  }, [arrival, openSimulator]);
  return null;
}

function useNarrow() {
  const q = "(max-width: 900px)";
  const [v, setV] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const h = () => setV(m.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, []);
  return v;
}
