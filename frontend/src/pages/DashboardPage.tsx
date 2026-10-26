import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUp, Box, Cpu, FileText, Share2, Workflow } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { AgentBadge } from "../components/AgentBadge";
import { FLOW_STATUS, when } from "../format";
import { useToast } from "../toast";
import type { AgentStatus, Project } from "../types";

const STARTERS = [
  { icon: Box, t: "CAD로 만들기", p: "NX에서 브래킷 형상을 만들고, 두께·리브 높이를 파라미터로 바꿀 수 있게 정리해 줘" },
  { icon: Workflow, t: "해석 모델링·실행", p: "이 CAD 모델로 Abaqus 정적 해석 모델을 만들고(재료·하중·경계조건·메쉬) 해석해서 최대 응력을 알려 줘" },
  { icon: Cpu, t: "데이터 쌓고 예측 모델", p: "두께 2~6mm, 리브 높이 5~20mm로 DOE를 돌려 데이터를 쌓고, 질량·응력 예측 모델을 만들어 Simulator로 배포해 줘" },
  { icon: FileText, t: "결과 보고서", p: "지금까지의 해석 결과(표·그래프·컨투어)를 정리해서 보고서로 작성해 줘" },
  { icon: Share2, t: "최적 설계 찾기", p: "응력 250MPa 이하에서 질량이 최소가 되는 설계를 베이지안 최적화로 찾아 줘" },
];

/** 첫 화면: "무엇을 만들고 싶으세요?" — 입력하면 프로젝트를 만들고 바로 에이전트와 대화를 시작한다 */
export default function DashboardPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [text, setText] = useState("");
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const review = (projects ?? []).filter((p) => p.flow_status === "review");

  const start = useMutation({
    mutationFn: (prompt: string) => {
      const first = prompt.split(/[\n.!?]/)[0].trim();
      const name = first.length > 28 ? `${first.slice(0, 28)}…` : first || "새 작업";
      return post<Project>("/api/projects", { name, description: "" });
    },
    onSuccess: (p, prompt) => nav(`/projects/${p.id}/new`, { state: { prompt } }),
    onError: (e: Error) => toast(e.message ?? "시작하지 못했습니다", true),
  });
  const go = (t: string) => { if (t.trim() && !start.isPending) start.mutate(t.trim()); };

  return (
    <div className="home">
      <div className="main-topright"><AgentBadge /></div>
      <div className="home-hero">
        <h1>{user?.display_name}님, 무엇을 만들고 싶으세요?</h1>
        <p className="muted">말로 요청하면 에이전트가 CAD·CAE 툴을 다뤄 모델링부터 해석, 데이터, 예측 모델, 보고서까지 이어서 해 드립니다.</p>
        {agent && !agent.connected && (
          <div className="notice warn" style={{ textAlign: "left" }}><AlertTriangle size={16} />
            <span className="grow">에이전트와 CAD/CAE 툴은 내 PC에서 실행됩니다. 먼저 <b>PC Agent</b>를 설치해 주세요.</span>
            <Link to="/settings" className="btn small primary">설치하기</Link></div>
        )}
        <form className="composer big" onSubmit={(e) => { e.preventDefault(); go(text); }}>
          <textarea autoFocus value={text} rows={3} onChange={(e) => setText(e.target.value)} aria-label="에이전트에게 요청"
            placeholder="예: NX 브래킷 모델의 두께를 바꿔가며 Abaqus로 응력을 보고, 질량 최소 설계를 찾아 줘"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); go(text); } }} />
          <div className="composer-bar">
            <span className="small muted">새 프로젝트로 시작합니다 · Enter 보내기</span>
            <span className="grow" />
            <button type="submit" className="primary send" disabled={!text.trim() || start.isPending} aria-label="시작"><ArrowUp size={16} /></button>
          </div>
        </form>
        <div className="starters">
          {STARTERS.map(({ icon: Icon, t, p }) => (
            <button key={t} className="starter" onClick={() => setText(p)} title={p}><Icon size={15} />{t}</button>
          ))}
        </div>
      </div>

      {!!projects?.length && (
        <div className="home-recent">
          <div className="home-sec">{review.length > 0 ? `승인을 기다리는 작업 ${review.length}건 · ` : ""}최근 프로젝트</div>
          <div className="recent-list">
            {projects.slice(0, 8).map((p) => {
              const st = p.flow_status ? FLOW_STATUS[p.flow_status] : null;
              return (
                <Link key={p.id} to={`/projects/${p.id}`} className={`recent ${p.flow_status === "review" ? "todo" : ""}`}>
                  <b>{p.name}</b>
                  <span className="small muted">{st ? `${st.label} · ${p.flow_title}` : "대화를 이어서 하세요"}</span>
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
