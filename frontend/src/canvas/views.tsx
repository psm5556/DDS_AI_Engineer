import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Gauge, Play, Plus, Trash2, Workflow, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, put } from "../api";
import { ArtifactCard } from "../artifacts/ArtifactView";
import { useAuth } from "../auth";
import { Confirm } from "../components/Modal";
import { FlowCanvas } from "../flow/FlowCanvas";
import { FLOW_STATUS, fmt, NODE_STATUS, NODE_TYPE, ROLE_LABEL, sendToAgent, when } from "../format";
import { useCanvas } from "./CanvasContext";
import { useToast } from "../toast";
import type { Artifact, Case, Flow, Project, StudyStatus, Study, SurrogateModel, User } from "../types";

/** 라이브 캔버스에 들어가는 화면들: 작업 흐름 · 결과 · 데이터·모델 · Simulator 목록 · 멤버.
 *  대화(가운데)에서 에이전트가 일하면 해당 화면이 실시간으로 갱신된다. */

/* ---------- 작업 흐름 ---------- */
/** 플로우 승인/반려. 승인하면 대화 중인 에이전트에게 "진행하세요"를 보낸다. */
export function useFlowDecision(pid: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (v: { flow: Flow; status: "approved" | "rejected" }) =>
      put<Flow>(`/api/flows/${v.flow.id}/status`, { status: v.status, version: v.flow.version }),
    onSuccess: (f) => {
      qc.invalidateQueries({ queryKey: ["flows", pid] });
      if (f.status === "approved") {
        toast("승인했습니다. 에이전트가 실행을 시작합니다");
        sendToAgent(`플로우 #${f.id} '${f.title}'을(를) 승인했습니다. 계획대로 진행하세요.`);
      } else {
        toast("반려했습니다");
        sendToAgent(`플로우 #${f.id} '${f.title}'을(를) 반려했습니다. 무엇을 바꾸면 좋을지 물어보고 다시 제안해 주세요.`);
      }
    },
    onError: (e: Error) => toast(e.message ?? "변경하지 못했습니다", true),
  });
}

export function FlowView({ pid, flows, arts }: { pid: number; flows?: Flow[]; arts: Artifact[] }) {
  const { flowId: fid, setFlowId: setFid } = useCanvas();
  const [sel, setSel] = useState<string | null>(null);
  const flow = flows?.find((f) => f.id === fid) ?? flows?.[0];
  useEffect(() => { setSel(null); }, [flow?.id]);
  const decide = useFlowDecision(pid);

  if (!flows) return <div className="busy"><span className="spinner" /></div>;
  if (!flow) {
    return (
      <div className="empty-hero">
        <Workflow size={30} />
        <b>아직 작업 흐름이 없습니다</b>
        <p>대화창에 하고 싶은 일을 말하면, 에이전트가 CAD → 해석 → 결과 추출 → DOE·예측 모델까지 작업 흐름을 먼저 그려 여기에 보여 줍니다.</p>
      </div>
    );
  }

  const count: Record<string, number> = {};
  arts.filter((a) => a.flow_id === flow.id && a.node_id).forEach((a) => { count[a.node_id!] = (count[a.node_id!] ?? 0) + 1; });
  const node = flow.nodes.find((n) => n.id === sel);
  const shown = arts.filter((a) => a.flow_id === flow.id && (!sel || a.node_id === sel));
  const st = FLOW_STATUS[flow.status];
  const done = flow.nodes.filter((n) => n.status === "complete").length;

  return (
    <div className="flow-tab">
      <div className="flow-bar">
        {flows.length > 1 && (
          <select value={flow.id} onChange={(e) => setFid(Number(e.target.value))} aria-label="작업 흐름 선택" style={{ width: "100%" }}>
            {flows.map((f) => <option key={f.id} value={f.id}>#{f.id} {f.title} ({FLOW_STATUS[f.status].label})</option>)}
          </select>
        )}
        <div className="grow">
          <b className="flow-title">{flow.title}</b> <span className={`chip ${st.chip}`}>{st.label}</span>
          <div className="small muted">{flow.goal || "목표 미기재"} · 단계 {done}/{flow.nodes.length} 완료 · {when(flow.updated_at)}</div>
        </div>
        {flow.status === "review" && (
          <>
            <button className="ghost" onClick={() => decide.mutate({ flow, status: "rejected" })} disabled={decide.isPending}><X size={14} />반려</button>
            <button className="primary big" onClick={() => decide.mutate({ flow, status: "approved" })} disabled={decide.isPending}><Check size={15} />승인하고 실행</button>
          </>
        )}
      </div>
      <div className="bar" aria-label="진행률"><i style={{ width: `${(done / Math.max(1, flow.nodes.length)) * 100}%` }} /></div>
      <FlowCanvas flow={flow} selected={sel} onSelect={setSel} artifactCount={count} />
      {node && (
        <div className="panel node-detail">
          <div className="panel-head"><h3>{node.label}</h3>
            <span className="chip">{NODE_TYPE[node.type] ?? node.type}{node.tool ? ` · ${node.tool}` : ""}</span>
            <span className={`chip ${node.status === "complete" ? "ok" : node.status === "failed" ? "err" : node.status === "in_progress" ? "info" : ""}`}>{NODE_STATUS[node.status]}</span></div>
          {node.detail && <p className="small">{node.detail}</p>}
          {flow.status !== "review" && (
            <button className="small" onClick={() => sendToAgent(`플로우 #${flow.id}의 '${node.label}' 단계에 대해 설명하고, 결과나 문제를 알려줘`)}>
              이 단계에 대해 에이전트에게 묻기</button>
          )}
        </div>
      )}
      <div className="home-sec" style={{ marginTop: 24 }}>{node ? `'${node.label}' 단계 결과` : "이 흐름의 결과"} <span className="count">{shown.length}</span></div>
      <div className="art-grid" style={{ marginTop: 20 }}>
        {shown.map((a) => <ArtifactCard key={a.id} a={a} />)}
        {!shown.length && <p className="small muted">아직 게시된 결과가 없습니다.</p>}
      </div>
    </div>
  );
}

/* ---------- 결과 ---------- */
export function ResultsView({ pid, arts, flows }: { pid: number; arts: Artifact[]; flows: Flow[] }) {
  const [flowId, setFlowId] = useState<number | "all">("all");
  const { focusArtifactId } = useCanvas();
  // 대화에서 "캔버스에서 보기"로 고른 결과로 스크롤
  useEffect(() => {
    if (!focusArtifactId) return;
    setFlowId("all");
    setTimeout(() => document.getElementById(`art-${focusArtifactId}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  }, [focusArtifactId]);
  const [rm, setRm] = useState<Artifact | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const shown = arts.filter((a) => flowId === "all" || a.flow_id === flowId);
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/artifacts/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["artifacts", pid] }); toast("삭제했습니다"); },
    onError: (e: Error) => toast(e.message ?? "삭제하지 못했습니다", true),
  });
  return (
    <>
      <div className="row" style={{ marginBottom: 16 }}>
        <select value={flowId} onChange={(e) => setFlowId(e.target.value === "all" ? "all" : Number(e.target.value))} style={{ width: "auto", maxWidth: 260 }} aria-label="작업 흐름별 보기">
          <option value="all">전체 {arts.length}</option>
          {flows.map((f) => <option key={f.id} value={f.id}>#{f.id} {f.title}</option>)}
        </select>
        <span className="grow" />
        <button className="small" onClick={() => sendToAgent("지금까지의 결과(표·그래프·이미지)를 정리해서 보고서(markdown 아티팩트)로 작성해 줘")}>보고서 작성 요청</button>
      </div>
      {!shown.length ? <p className="small muted">아직 게시된 결과가 없습니다. 에이전트가 작업하면서 표·그래프·컨투어 이미지를 여기에 올립니다.</p> : (
        <div className="art-grid">{shown.map((a) => <ArtifactCard key={a.id} a={a} onDelete={() => setRm(a)} highlight={a.id === focusArtifactId} />)}</div>
      )}
      {rm && <Confirm title="결과 삭제" message={`'${rm.title}'을(를) 삭제할까요?`} confirmLabel="삭제" danger
        onConfirm={() => remove.mutate(rm.id)} onClose={() => setRm(null)} />}
    </>
  );
}

/* ---------- 데이터·모델 ---------- */
export function StudiesView({ pid }: { pid: number }) {
  const { data: studies } = useQuery({ queryKey: ["studies", pid], queryFn: () => get<Study[]>(`/api/projects/${pid}/studies`) });
  const [sid, setSid] = useState<number | null>(null);
  const cur = sid ?? studies?.[0]?.id ?? null;
  if (!studies) return <div className="busy"><span className="spinner" /></div>;
  if (!studies.length) {
    return <div className="empty-hero"><Gauge size={30} /><b>아직 스터디가 없습니다</b>
      <p>에이전트가 DOE를 시작하면 케이스 데이터·예측 모델이 여기에 쌓입니다.</p></div>;
  }
  return (
    <>
      <div className="row" style={{ marginBottom: 16 }}>
        <span className="seg">{studies.map((s) => <button key={s.id} className={cur === s.id ? "on" : ""} onClick={() => setSid(s.id)}>{s.name}</button>)}</span>
      </div>
      {cur && <StudyDetail pid={pid} id={cur} />}
    </>
  );
}

const CASE_STATUS: Record<string, [string, string]> = {
  pending: ["대기", ""], queued: ["대기열", "info"], running: ["실행 중", "info"], complete: ["완료", "ok"], failed: ["실패", "err"], dry_run: ["스크립트만", "warn"],
};

function StudyDetail({ pid, id }: { pid: number; id: number }) {
  const { data: st } = useQuery({ queryKey: ["study", id], queryFn: () => get<StudyStatus>(`/api/studies/${id}`) });
  const { data: cases } = useQuery({ queryKey: ["study", id, "cases"], queryFn: () => get<Case[]>(`/api/studies/${id}/cases`) });
  const { data: models } = useQuery({ queryKey: ["models", pid], queryFn: () => get<SurrogateModel[]>(`/api/projects/${pid}/models`) });
  if (!st) return <div className="busy"><span className="spinner" /></div>;
  const s = st.study;
  const vars = s.variables.map((v) => v.name);
  const outs = Array.from(new Set((cases ?? []).flatMap((c) => Object.keys(c.results))));
  const obj = s.objectives[0];
  const total = Object.values(st.counts).reduce((a, b) => a + b, 0);
  return (
    <div className="stack">
      <section className="block">
        <div className="hero">
          <div>
            <div className="lbl">{obj ? `현재 최적 (${obj.name} ${obj.goal === "min" ? "최소" : "최대"})` : "스터디"}</div>
            {st.best ? (
              <div className="cond-big">
                {vars.map((v) => <div key={v}><span>{v}</span><b>{fmt(st.best!.params[v], 4)}</b></div>)}
                {obj && <div><span>{obj.name}</span><b style={{ color: "var(--brand)" }}>{fmt(st.best.results[obj.name], 4)}</b></div>}
              </div>
            ) : <p className="small muted" style={{ marginTop: 10 }}>완료된 케이스가 아직 없습니다.</p>}
            <div className="row" style={{ marginTop: 16 }}>
              {Object.entries(st.counts).map(([k, n]) => <span key={k} className={`chip ${CASE_STATUS[k]?.[1] ?? ""}`}>{CASE_STATUS[k]?.[0] ?? k} {n}</span>)}
              {st.running && <span className="chip info"><span className="spinner" style={{ width: 10, height: 10 }} />실행 중</span>}
            </div>
          </div>
          <div className="gauge"><div className="ring" style={{ "--p": total ? Math.round(((st.counts.complete ?? 0) / total) * 100) : 0 } as React.CSSProperties}>
            <div><b>{st.counts.complete ?? 0}</b><span>/ {total} 완료</span></div></div>
            <p>어댑터: {s.adapter}{s.adapter_config?.tool ? ` · ${String(s.adapter_config.tool)}` : ""}</p></div>
        </div>
      </section>

      <section className="block">
        <div className="block-head"><h3>예측 모델</h3><span className="cnt">{models?.filter((m) => m.study_id === id).length ?? 0}개</span></div>
        <div className="table-wrap"><table>
          <thead><tr><th>모델</th><th className="r">학습 수</th><th>정확도 (R²)</th><th>상태</th><th /></tr></thead>
          <tbody>{(models ?? []).filter((m) => m.study_id === id).slice(0, 8).map((m) => (
            <tr key={m.id}>
              <td>{m.name}</td><td className="r">{m.n_train}</td>
              <td>{Object.entries(m.metrics).map(([k, v]) => <span key={k} className={`chip ${v.r2 >= 0.9 ? "ok" : v.r2 >= 0.7 ? "warn" : "err"}`} style={{ marginRight: 4 }}>{k} {v.r2.toFixed(3)}</span>)}</td>
              <td>{m.deployed ? <span className="chip ok">배포됨</span> : <span className="chip">-</span>}</td>
              <td className="r">{m.deployed && <Link className="btn small" to={`/simulator/${m.id}`}><Play size={12} />Simulator</Link>}</td>
            </tr>))}</tbody>
        </table></div>
      </section>

      <section className="block">
        <div className="block-head"><h3>케이스</h3><span className="cnt">{cases?.length ?? 0}건</span></div>
        <div className="table-wrap case-table"><table>
          <thead><tr><th>ID</th><th>출처</th><th>상태</th>{vars.map((v) => <th key={v} className="r">{v}</th>)}{outs.map((o) => <th key={o} className="r">{o}</th>)}</tr></thead>
          <tbody>{(cases ?? []).map((c) => (
            <tr key={c.id} className={st.best?.id === c.id ? "best-row" : ""} title={c.error ?? undefined}>
              <td>{c.id}</td><td>{({ doe: "DOE", bo: "최적화", al: "능동학습", manual: "직접" } as Record<string, string>)[c.source] ?? c.source}</td>
              <td className="status-cell"><span className={`st ${c.status === "complete" ? "done" : c.status === "failed" ? "fail" : ""}`}><i />{CASE_STATUS[c.status]?.[0] ?? c.status}</span></td>
              {vars.map((v) => <td key={v} className="r">{fmt(c.params[v])}</td>)}
              {outs.map((o) => <td key={o} className="r">{fmt(c.results[o])}</td>)}
            </tr>))}</tbody>
        </table></div>
      </section>
    </div>
  );
}

/* ---------- Simulator 목록 ---------- */
export function SimulatorList({ pid }: { pid: number }) {
  const { data: models } = useQuery({ queryKey: ["models", pid], queryFn: () => get<SurrogateModel[]>(`/api/projects/${pid}/models`) });
  const deployed = (models ?? []).filter((m) => m.deployed);
  if (!deployed.length) {
    return <div className="empty-hero"><Gauge size={30} /><b>배포된 Simulator가 없습니다</b>
      <p>예측 모델을 배포하면 해석 없이 설계변수를 바꿔 가며 결과를 실시간으로 예측할 수 있습니다.</p>
      <button onClick={() => sendToAgent("가장 정확한 예측 모델을 Simulator로 배포해 줘")}>에이전트에게 배포 요청</button></div>;
  }
  return (
    <div className="doe-cards" style={{ marginTop: 0 }}>
      {deployed.map((m) => (
        <Link key={m.id} to={`/simulator/${m.id}`} className="doe-card">
          <div className="t"><b>{m.name}</b><span className="chip ok">배포됨</span></div>
          <div className="who">출력: {m.outputs.join(", ")} · 학습 {m.n_train}건</div>
          <div className="row" style={{ marginTop: "auto" }}>{Object.entries(m.metrics).map(([k, v]) => <span key={k} className="chip">{k} R² {v.r2.toFixed(3)}</span>)}</div>
          <div className="next mine"><Play size={13} />Simulator 열기</div>
        </Link>
      ))}
    </div>
  );
}

/* ---------- 멤버·설정 ---------- */
export function MembersPanel({ p }: { p: Project }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const isMaster = p.user_role === "master";
  const [add, setAdd] = useState<number | "">("");
  const [name, setName] = useState(p.name);
  const [desc, setDesc] = useState(p.description);
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/api/users"), enabled: isMaster });
  const candidates = useMemo(() => (users ?? []).filter((u) => u.is_active && !p.members.some((m) => m.user_id === u.id)), [users, p.members]);
  const done = () => { qc.invalidateQueries({ queryKey: ["project", p.id] }); qc.invalidateQueries({ queryKey: ["projects"] }); };
  const err = (e: Error) => toast(e.message ?? "처리하지 못했습니다", true);
  const upsert = useMutation({ mutationFn: (v: { uid: number; role: string }) => put(`/api/projects/${p.id}/members/${v.uid}`, { role: v.role }), onSuccess: () => { done(); toast("저장했습니다"); setAdd(""); }, onError: err });
  const remove = useMutation({ mutationFn: (uid: number) => del(`/api/projects/${p.id}/members/${uid}`), onSuccess: () => { done(); toast("제거했습니다"); }, onError: err });
  const save = useMutation({ mutationFn: () => put(`/api/projects/${p.id}`, { name, description: desc, version: p.version }), onSuccess: () => { done(); toast("저장했습니다"); }, onError: err });

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head"><h3>멤버</h3><span className="small muted">멤버는 작업 흐름·결과·대화를 함께 보고, 자기 PC Agent로 작업을 요청할 수 있습니다</span></div>
        <div className="table-wrap"><table>
          <thead><tr><th>이름</th><th>아이디</th><th>권한</th><th /></tr></thead>
          <tbody>{p.members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name}{m.user_id === user?.id ? " (나)" : ""}</td><td>{m.username}</td>
              <td>{isMaster ? (
                <select value={m.role} style={{ width: "auto", minHeight: 30 }} onChange={(e) => upsert.mutate({ uid: m.user_id, role: e.target.value })}>
                  <option value="master">관리자</option><option value="member">멤버</option></select>
              ) : ROLE_LABEL[m.role]}</td>
              <td className="r">{isMaster && <button className="icon-btn danger" title="제거" aria-label={`${m.display_name} 제거`} onClick={() => remove.mutate(m.user_id)}><Trash2 size={13} /></button>}</td>
            </tr>))}</tbody>
        </table></div>
        {isMaster && (
          <div className="row" style={{ marginTop: 12 }}>
            <select value={add} onChange={(e) => setAdd(e.target.value ? Number(e.target.value) : "")} style={{ width: 260 }} aria-label="추가할 사용자">
              <option value="">추가할 사용자 선택</option>
              {candidates.map((u) => <option key={u.id} value={u.id}>{u.display_name} ({u.username})</option>)}
            </select>
            <button disabled={add === ""} onClick={() => add !== "" && upsert.mutate({ uid: add, role: "member" })}><Plus size={14} />멤버 추가</button>
          </div>
        )}
      </section>
      {isMaster && (
        <section className="panel">
          <div className="panel-head"><h3>프로젝트 정보</h3></div>
          <div className="stack">
            <label className="field"><span className="lbl">이름</span><input type="text" value={name} onChange={(e) => setName(e.target.value)} /></label>
            <label className="field"><span className="lbl">설명</span><textarea value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
            <div className="row end"><button className="primary" disabled={save.isPending} onClick={() => save.mutate()}>저장</button></div>
          </div>
        </section>
      )}
    </div>
  );
}
