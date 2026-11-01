import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, ChevronDown, ChevronRight, Cpu, FolderInput, FolderSearch, Loader2, Rocket, SlidersHorizontal, Table2, Target, Workflow, Wrench } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { get, post, put, uploadFile } from "../api";
import { useAuth } from "../auth";
import { TablePasteDialog } from "../canvas/TableImport";
import { Composer, type Outgoing } from "../chat/Composer";
import { AgentGlyph } from "../components/AgentGlyph";
import { AgentPicker, resolveAgent, usePreferredAgent } from "../components/AgentPicker";
import { folderName } from "../components/FolderModal";
import { FLOW_STATUS, PRACTICE_EXAMPLES, when } from "../format";
import { useToast } from "../toast";
import type { AgentStatus, Attachment, Project, WorkBrief } from "../types";
import { COPY, taskPath, W } from "../work/copy";
import { pickWork } from "../work/scope";

// 앞의 셋은 CAD·CAE 툴과 모델 파일 없이 끝까지 된다(연습용 계산). 마지막만 내 모델과 툴이 필요하다고 말하고 파일을 먼저 받는다
const STARTERS: { icon: typeof Rocket; t: string; p: string; askFile?: string }[] = [
  { icon: Rocket, t: "전체 과정 연습", p: PRACTICE_EXAMPLES.whole },
  { icon: Target, t: "세탁기 진동 줄이기 연습", p: PRACTICE_EXAMPLES.washer },
  { icon: Cpu, t: "두 목표 함께 보기 연습", p: PRACTICE_EXAMPLES.two },
  { icon: Workflow, t: "내 모델로 해석", p: "첨부한 모델로 응력 해석을 돌려 최대 응력을 알려 줘", askFile: "해석할 모델 파일을 첨부해 주세요. 내 PC에 그 CAD·CAE 툴이 있어야 합니다." },
];
/** 이미 해석을 마친 사람의 시작점. 어디서 시작하든 받아 준다는 것을 첫 화면에서 보여 준다 */
const FOLDER_PROMPT = "이 폴더에 이미 해석한 케이스들이 있어. 해석은 다시 돌리지 말고 결과를 모아 예측 모델을 만들어 Simulator로 배포해 줘. 폴더: ";
const TABLE_PROMPT = "붙여 넣은 표의 입력·출력으로 예측 모델을 만들어 Simulator로 배포하고, 정확도를 알려 줘";

class FolderError extends Error {}

/** 첫 화면 = 새 Project 시작: 요청 입력창이 가운데. Project 이름·폴더는 '옵션' 한 줄에 접어 둔다
 *  (비워 두면 이름은 요청 내용으로, 폴더는 기본 위치로 정해진다 — 단계를 줄이기 위해).
 *  보내면 Project를 만들고, 폴더를 정한 뒤(AI를 시작하기 전에) 첫 Task로 넘어간다. Task는 그 화면이 보낼 때 만든다. */
export default function DashboardPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  // 마켓의 "이렇게 말해 보세요"를 누르면 입력창을 채워 시작한다
  const incoming = (useLocation().state as { seed?: string } | null)?.seed;
  const [seed, setSeed] = useState<{ text: string; n: number; askFile?: string } | undefined>(incoming ? { text: incoming, n: Date.now() } : undefined);
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [folderErr, setFolderErr] = useState("");
  const [picking, setPicking] = useState(false);
  const [opts, setOpts] = useState(false);
  const [tableOpen, setTableOpen] = useState(false);
  const openSimulator = (pid: number, studyId: number, modelId: number, workId?: number | null) => nav(taskPath(pid, workId), { state: { canvasTab: "simulator", studyId, modelId } });
  const showOpts = opts || !!folderErr; // 폴더 오류가 나면 펼쳐서 보여 준다
  const folderRef = useRef<HTMLInputElement>(null);
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const [preferred, setPreferred] = usePreferredAgent();
  const review = (projects ?? []).filter((p) => p.flow_status === "review");
  const connected = !!agent?.connected;
  const focusComposer = () => (document.querySelector(".home .composer textarea") as HTMLTextAreaElement | null)?.focus();

  // 내 PC에 Windows 폴더 선택 창을 띄운다 (PC Agent 경유)
  const browse = async () => {
    setPicking(true); setFolderErr("");
    try {
      const r = await post<{ path: string }>("/api/agent/pick-folder", { initial: folder || null, title: `${W.project} 폴더 선택` });
      if (r.path) {
        setFolder(r.path);
        if (!name.trim()) setName(folderName(r.path)); // 이름을 비워 두었으면 폴더 이름으로
        focusComposer();
      }
    } catch (e) { setFolderErr((e as Error).message); } finally { setPicking(false); }
  };

  const start = useMutation({
    mutationFn: async (o: Outgoing) => {
      // ② 폴더를 먼저 확인한다 — 쓸 수 없는 폴더면 Project를 만들지 않는다
      const path = folder.trim() ? (await post<{ path: string }>("/api/agent/check-folder", { path: folder.trim() })
        .catch((e: Error) => { throw new FolderError(e.message); })).path : null;
      // ① 이름: 적은 이름 → 없으면 요청 앞부분(에이전트가 곧 알맞게 바꾼다)
      const first = (o.text || o.pendingFiles[0]?.name || "").split(/[\n.!?]/)[0].trim();
      const auto = first.length > 28 ? `${first.slice(0, 28)}…` : first || COPY.newProject;
      const p = await post<Project>("/api/projects", { name: name.trim() || auto, description: "" });
      if (path) {
        await put(`/api/projects/${p.id}/local/folder`, { path })
          .catch((e: Error) => toast(`폴더를 정하지 못해 기본 위치로 시작합니다: ${e.message}`, true));
      }
      const ids: number[] = [];
      for (const f of o.pendingFiles) ids.push((await uploadFile<Attachment>(`/api/projects/${p.id}/attachments`, f)).id);
      return { p, ids, text: o.text };
    },
    // ③ 요청을 들고 첫 Task 화면으로. Task는 거기서 보낼 때 만들어진다
    onSuccess: ({ p, ids, text }) => nav(`/projects/${p.id}/w/new`, { state: { prompt: text, attachmentIds: ids } }),
    onError: (e: Error) => {
      if (e instanceof FolderError) { setFolderErr(e.message); setTimeout(() => folderRef.current?.focus(), 0); }
      else toast(e.message ?? "시작하지 못했습니다", true);
    },
  });

  return (
    <div className="home">
      <div className="home-hero">
        <AgentGlyph size={36} />
        <h1><span className="line"><span>{user?.display_name}님,</span></span> <span className="line"><span>무엇을 맡길까요?</span></span></h1>
        <p className="muted">모델링부터 해석, 예측 모델, 보고서까지 말로 요청하면 이어서 합니다.</p>
        {agent && !connected && (
          <div className="notice warn" style={{ textAlign: "left" }}><AlertTriangle size={16} />
            <span className="grow">에이전트와 CAD/CAE 툴은 내 PC에서 실행됩니다. 먼저 <b>PC Agent</b>를 설치해 주세요. 표를 붙여 넣어 Simulator를 만드는 일은 설치 없이 됩니다.</span>
            <Link to="/settings" className="btn small primary">설치하기</Link></div>
        )}

        <div className="np-card">
          <Composer big autoFocus typing onSimulator={openSimulator} suggestions={[...STARTERS.map((x) => x.p), TABLE_PROMPT, FOLDER_PROMPT]} seed={seed} busy={start.isPending}
            onSend={(o) => start.mutateAsync(o).then(() => true, () => false)}
            leftBar={<AgentPicker value={resolveAgent(agent, preferred) ?? ""} onChange={setPreferred} disabled={!connected} />}
            placeholder="예: 내 브래킷 모델의 두께를 바꿔 가며 응력을 보고, 가장 가벼운 설계를 찾아 줘" />
          <p className="small muted np-send">{COPY.homeSend}</p>
          <div className={`np-opts ${showOpts ? "open" : ""}`}>
            <button type="button" className="np-opts-toggle" aria-expanded={showOpts} onClick={() => setOpts(!showOpts)}
              title={`${W.project} 이름과 폴더 — 비워 두면 자동으로 정합니다`}>
              <SlidersHorizontal size={13} />옵션
              <span className="np-opts-sum">{name.trim() ? `이름 '${name.trim()}'` : COPY.homeAutoName} · {folder.trim() ? folderName(folder.trim()) : COPY.homeDefaultFolder}</span>
              <ChevronDown size={13} className="np-opts-chev" />
            </button>
            {showOpts && (
              <div className="np-opts-body">
                <label className="np-row">
                  <span className="np-label">{W.project} 이름</span>
                  <input id="np-name" type="text" className="np-input" value={name} maxLength={200}
                    placeholder="비워 두면 요청 내용으로 정합니다" onChange={(e) => setName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); folderRef.current?.focus(); } }} />
                </label>
                <div className="np-row">
                  <label className="np-label" htmlFor="np-folder">{COPY.homeFolder}</label>
                  <div className="np-field">
                    <div className="np-folder">
                      <input id="np-folder" ref={folderRef} type="text" className="np-input mono" value={folder} disabled={!connected}
                        placeholder={connected ? "비워 두면 기본 위치에 새로 만듭니다" : "PC Agent가 연결되면 정할 수 있습니다"}
                        onChange={(e) => { setFolder(e.target.value); setFolderErr(""); }}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); focusComposer(); } }} />
                      <button type="button" onClick={() => void browse()} disabled={!connected || picking} title="내 PC에 폴더 선택 창을 띄웁니다">
                        <FolderSearch size={14} />{picking ? "PC에서 고르는 중…" : "찾아보기…"}</button>
                    </div>
                    {folderErr ? <p className="np-err">{folderErr}</p>
                      : <p className="np-hint">
                        {folder.trim() ? "이 폴더에 에이전트 설정 파일과 하위 폴더(inputs·runs·studies·reports)를 만듭니다. 기존 파일은 건드리지 않습니다."
                          : `기본 위치: ${agent?.info?.workspace ?? "내 PC의 DDS_Workspace"} 아래에 ${W.project} 폴더를 만듭니다.`}</p>}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="starters">
          {STARTERS.map(({ icon: Icon, t, p, askFile }) => (
            <button key={t} className="starter" onClick={() => setSeed({ text: p, n: Date.now(), askFile })} title={p}><Icon size={16} /><span>{t}</span></button>
          ))}
        </div>
        <div className="starters-data" aria-label="이미 가진 데이터로 시작">
          <span className="starters-data-label">이미 결과가 있다면</span>
          <button type="button" className="starter-data" title="엑셀에서 복사한 표를 붙여 넣으면 해석 없이 예측 모델을 학습해 Simulator로 배포합니다. AI와 PC Agent가 없어도 됩니다"
            onClick={() => setTableOpen(true)}><Table2 size={15} /><span><b>표로 바로 Simulator</b><small>엑셀에서 복사해 붙여 넣으면 바로 만듭니다</small></span></button>
          <button type="button" className="starter-data" title={FOLDER_PROMPT} onClick={() => setSeed({ text: FOLDER_PROMPT, n: Date.now() })}>
            <FolderInput size={15} /><span><b>해석 폴더로 Simulator</b><small>에이전트가 케이스 결과를 모아 만듭니다</small></span></button>
        </div>
        <ToolsHint connected={connected} />
      </div>

      {!!projects?.length && (
        <div className="home-recent">
          <div className="home-sec">{COPY.homeRecent}{review.length > 0 ? <span className="home-todo">승인 대기 {review.length}</span> : null}</div>
          <div className="recent-rows">
            {projects.slice(0, 6).map((p) => {
              const w: WorkBrief | null = pickWork(p.works);
              const status = w?.status ?? p.flow_status;
              const st = status ? FLOW_STATUS[status] : null;
              const run = status === "in_progress" || status === "approved";
              return (
                <Link key={p.id} to={taskPath(p.id, w?.id)} className="recent-row">
                  <b>{p.name}{w && <span className="recent-task"> · {w.name}</span>}</b>
                  {st && <span className={`chip ${st.chip}`}>{run && <Loader2 size={11} className="spin" />}{st.label}</span>}
                  <span className="small muted">{when(p.updated_at)}</span>
                  <ArrowRight size={14} className="go" aria-hidden="true" />
                </Link>
              );
            })}
          </div>
        </div>
      )}
      {tableOpen && <TablePasteDialog pid={null} onClose={() => setTableOpen(false)} onOpen={openSimulator} />}
    </div>
  );
}

/** 첫 화면 한 줄: 에이전트가 다룰 수 있는 툴 수와 내 PC에서 바로 쓸 수 있는 수 → 'CAD·CAE 툴' 화면 */
function ToolsHint({ connected }: { connected: boolean }) {
  const { data: cat } = useQuery({ queryKey: ["tools", "catalog"], queryFn: () => get<{ tools: { id: string }[] }>("/api/tools/catalog"), staleTime: Infinity });
  const { data: diag } = useQuery({
    queryKey: ["agent", "diagnose"], enabled: connected, staleTime: 300_000, retry: false,
    queryFn: () => get<{ catalog?: { status: string }[] }>("/api/agent/diagnose"),
  });
  if (!cat) return null;
  const ready = diag?.catalog?.filter((t) => t.status === "ready").length;
  return (
    <Link to="/tools" className="tools-hint">
      <Wrench size={13} />지원 CAD·CAE 툴 {cat.tools.length}종{ready != null ? ` · 내 PC에서 바로 사용 ${ready}종` : ""}<ChevronRight size={13} />
    </Link>
  );
}
