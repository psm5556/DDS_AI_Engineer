import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Box, Cpu, Plus, Share2, Sparkles, Workflow } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";
import { useAuth } from "../auth";
import { NewProjectModal } from "../components/NewProjectModal";
import { FLOW_STATUS, when } from "../format";
import type { AgentStatus, Project } from "../types";

const JOURNEY = [
  { icon: Box, t: "CAD로 만들기", d: "NX 형상 생성·파라미터 변경" },
  { icon: Workflow, t: "해석 모델링·실행", d: "Abaqus · LS-DYNA · Ansys · STAR-CCM+ …" },
  { icon: Cpu, t: "데이터 쌓고 예측", d: "DOE → 예측 모델 → 최적화" },
  { icon: Share2, t: "공유", d: "Simulator로 누구나 실시간 예측" },
];

/** 첫 화면: 인사 + 할 일 + 프로젝트 카드 */
export default function DashboardPage() {
  const { user } = useAuth();
  const [creating, setCreating] = useState(false);
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const review = (projects ?? []).filter((p) => p.flow_status === "review");
  const running = (projects ?? []).filter((p) => p.flow_status === "in_progress" || p.flow_status === "approved");

  return (
    <div className="page">
      <div className="home-head">
        <div className="grow">
          <h1>안녕하세요, {user?.display_name}님</h1>
          <div className="todo-pills">
            <span>승인 대기<b>{review.length}</b></span>
            <span>진행 중<b>{running.length}</b></span>
            <span>프로젝트<b>{projects?.length ?? 0}</b></span>
          </div>
        </div>
      </div>

      {agent && !agent.connected && (
        <div className="notice warn" style={{ marginTop: 20 }}>
          <Sparkles size={16} />
          <span className="grow">에이전트와 CAD/CAE 툴은 내 PC에서 실행됩니다. 먼저 <b>PC Agent</b>를 설치해 주세요 (설치 파일 하나로 끝납니다).</span>
          <Link to="/settings" className="btn small primary">설치하기</Link>
        </div>
      )}

      <div className="journey">
        {JOURNEY.map(({ icon: Icon, t, d }, i) => (
          <div key={t} className="journey-step"><span className="n">{i + 1}</span><Icon size={18} /><b>{t}</b><span>{d}</span></div>
        ))}
      </div>

      <div className="home-sec">프로젝트</div>
      <div className="doe-cards">
        {(projects ?? []).map((p) => {
          const st = p.flow_status ? FLOW_STATUS[p.flow_status] : null;
          return (
            <Link key={p.id} to={`/projects/${p.id}`} className="doe-card">
              <div className="t"><b>{p.name}</b>{st && <span className={`chip ${st.chip}`}>{st.label}</span>}</div>
              <div className="who">{p.description || "설명 없음"}</div>
              <div className="who" style={{ marginTop: "auto" }}>멤버 {p.members.length}명 · 수정 {when(p.updated_at)}</div>
              <div className={`next ${p.flow_status === "review" ? "mine" : ""}`}>
                {p.flow_status === "review" && <i className="dot" />}
                {st ? `${st.label} · ${p.flow_title}` : "에이전트에게 첫 작업을 요청하세요"}<ArrowRight size={14} />
              </div>
            </Link>
          );
        })}
        <button className="doe-card blank" onClick={() => setCreating(true)}>
          <b><Plus size={16} />새 프로젝트</b><span>만들고 오른쪽 에이전트에게 하고 싶은 일을 말하세요</span>
        </button>
      </div>
      {creating && <NewProjectModal onClose={() => setCreating(false)} />}
    </div>
  );
}
