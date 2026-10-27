import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Box, ChevronRight, Cpu, FolderSearch, Loader2, Rocket, Target, Workflow, Wrench } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get, post, put, uploadFile } from "../api";
import { useAuth } from "../auth";
import { Composer, type Outgoing } from "../chat/Composer";
import { AgentPicker, resolveAgent, usePreferredAgent } from "../components/AgentPicker";
import { folderName } from "../components/FolderModal";
import { FLOW_STATUS, when } from "../format";
import { useToast } from "../toast";
import type { AgentStatus, Attachment, Project } from "../types";

const STARTERS = [
  { icon: Rocket, t: "처음부터 끝까지", p: "브래킷 CAD를 만들고 해석 모델을 구성한 뒤 DOE로 데이터를 쌓아 형상·응력을 학습하고, 3D Simulator로 배포한 다음 보고서까지 작성해 줘" },
  { icon: Box, t: "CAD로 만들기", p: "NX에서 브래킷 형상을 만들고, 두께·리브 높이를 파라미터로 바꿀 수 있게 정리해 줘" },
  { icon: Workflow, t: "해석 모델링·실행", p: "이 CAD 모델로 Abaqus 정적 해석 모델을 만들고(재료·하중·경계조건·메쉬) 해석해서 최대 응력을 알려 줘" },
  { icon: Cpu, t: "DOE·예측 모델", p: "두께 2~6mm, 리브 높이 5~20mm로 DOE를 돌려 데이터를 쌓고, 질량·응력 예측 모델을 만들어 Simulator로 배포해 줘" },
  { icon: Target, t: "최적 설계 찾기", p: "응력 250MPa 이하에서 질량이 최소가 되는 설계를 베이지안 최적화로 찾아 줘" },
];

class FolderError extends Error {}

/** 첫 화면 = 새 프로젝트 시작: ① 이름 → ② 내 PC 작업 폴더 → ③ 요청.
 *  보내면 프로젝트를 만들고, 작업 폴더를 정한 뒤(에이전트가 첫 세션을 열기 전에) 대화로 넘어간다. */
export default function DashboardPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [seed, setSeed] = useState<{ text: string; n: number } | undefined>();
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [folderErr, setFolderErr] = useState("");
  const [picking, setPicking] = useState(false);
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
      const r = await post<{ path: string }>("/api/agent/pick-folder", { initial: folder || null, title: "프로젝트 작업 폴더 선택" });
      if (r.path) {
        setFolder(r.path);
        if (!name.trim()) setName(folderName(r.path)); // 이름을 비워 두었으면 폴더 이름으로
        focusComposer();
      }
    } catch (e) { setFolderErr((e as Error).message); } finally { setPicking(false); }
  };

  const start = useMutation({
    mutationFn: async (o: Outgoing) => {
      // ② 폴더를 먼저 확인한다 — 쓸 수 없는 폴더면 프로젝트를 만들지 않는다
      const path = folder.trim() ? (await post<{ path: string }>("/api/agent/check-folder", { path: folder.trim() })
        .catch((e: Error) => { throw new FolderError(e.message); })).path : null;
      // ① 이름: 적은 이름 → 없으면 요청 앞부분(에이전트가 곧 알맞게 바꾼다)
      const first = (o.text || o.pendingFiles[0]?.name || "").split(/[\n.!?]/)[0].trim();
      const auto = first.length > 28 ? `${first.slice(0, 28)}…` : first || "새 프로젝트";
      const p = await post<Project>("/api/projects", { name: name.trim() || auto, description: "" });
      if (path) {
        await put(`/api/projects/${p.id}/local/folder`, { path })
          .catch((e: Error) => toast(`작업 폴더를 정하지 못해 기본 위치로 시작합니다: ${e.message}`, true));
      }
      const ids: number[] = [];
      for (const f of o.pendingFiles) ids.push((await uploadFile<Attachment>(`/api/projects/${p.id}/attachments`, f)).id);
      return { p, ids, text: o.text };
    },
    // ③ 요청을 들고 대화로
    onSuccess: ({ p, ids, text }) => nav(`/projects/${p.id}/new`, { state: { prompt: text, attachmentIds: ids } }),
    onError: (e: Error) => {
      if (e instanceof FolderError) { setFolderErr(e.message); folderRef.current?.focus(); }
      else toast(e.message ?? "시작하지 못했습니다", true);
    },
  });

  return (
    <div className="home">
      <div className="home-hero">
        <h1>{user?.display_name}님, 새 프로젝트를 시작해 볼까요?</h1>
        <p className="muted">말로 요청하면 에이전트가 CAD·CAE 툴로 모델링부터 해석, 데이터, 예측 모델, 보고서까지 이어서 합니다.</p>
        {agent && !connected && (
          <div className="notice warn" style={{ textAlign: "left" }}><AlertTriangle size={16} />
            <span className="grow">에이전트와 CAD/CAE 툴은 내 PC에서 실행됩니다. 먼저 <b>PC Agent</b>를 설치해 주세요.</span>
            <Link to="/settings" className="btn small primary">설치하기</Link></div>
        )}

        <div className="np-card">
          <div className="np-row">
            <span className="np-n">1</span>
            <label className="np-label" htmlFor="np-name">프로젝트 이름</label>
            <input id="np-name" type="text" className="np-input" value={name} maxLength={200} autoFocus
              placeholder="비워 두면 요청 내용으로 정합니다" onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); folderRef.current?.focus(); } }} />
          </div>
          <div className="np-row">
            <span className="np-n">2</span>
            <label className="np-label" htmlFor="np-folder">내 PC 작업 폴더</label>
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
                  {folder.trim() ? "이 폴더에 에이전트 설정 파일과 작업 하위 폴더(inputs·runs·studies·reports)를 만듭니다. 기존 파일은 건드리지 않습니다."
                    : `기본 위치: ${agent?.info?.workspace ?? "내 PC의 DDS_Workspace"} 아래에 프로젝트 폴더를 만듭니다.`}</p>}
            </div>
          </div>
          <div className="np-row np-ask">
            <span className="np-n">3</span>
            <span className="np-label">무엇을 할까요?</span>
          </div>
          <Composer big suggestions={STARTERS.map((x) => x.p)} seed={seed} busy={start.isPending}
            onSend={(o) => start.mutateAsync(o).then(() => true, () => false)}
            leftBar={<AgentPicker value={resolveAgent(agent, preferred)} onChange={setPreferred} disabled={!connected} />}
            placeholder="예: NX 브래킷 모델의 두께를 바꿔가며 Abaqus로 응력을 보고, 질량 최소 설계를 찾아 줘" />
        </div>
        <div className="starters">
          {STARTERS.map(({ icon: Icon, t, p }) => (
            <button key={t} className="starter" onClick={() => setSeed({ text: p, n: Date.now() })} title={p}><Icon size={15} />{t}</button>
          ))}
        </div>
        <ToolsHint connected={connected} />
      </div>

      {!!projects?.length && (
        <div className="home-recent">
          <div className="home-sec">최근 프로젝트{review.length > 0 ? <span className="home-todo">승인 대기 {review.length}</span> : null}</div>
          <div className="recent-rows">
            {projects.slice(0, 6).map((p) => {
              const st = p.flow_status ? FLOW_STATUS[p.flow_status] : null;
              const run = p.flow_status === "in_progress" || p.flow_status === "approved";
              return (
                <Link key={p.id} to={`/projects/${p.id}`} className="recent-row">
                  <b>{p.name}</b>
                  {st && <span className={`chip ${st.chip}`}>{run && <Loader2 size={11} className="spin" />}{st.label}</span>}
                  <span className="small muted">{when(p.updated_at)}</span>
                </Link>
              );
            })}
          </div>
        </div>
      )}
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
