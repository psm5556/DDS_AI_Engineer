import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Database, Download, FolderOpen, Loader2, Plus, RotateCcw, Square, Table2, Trash2 } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { del, download, get, post, put } from "../api";
import { ArtifactCard } from "../artifacts/ArtifactView";
import { useAuth } from "../auth";
import { AgentGlyph } from "../components/AgentGlyph";
import { Confirm } from "../components/Modal";
import { Skeleton } from "../components/Skeleton";
import { dataSourceText } from "../components/ModelTrust";
import { FlowCanvas } from "../flow/FlowCanvas";
import { copyText, FLOW_STATUS, fmt, NODE_STATUS, NODE_TYPE, prefillAgent, ROLE_LABEL, sendToAgent, when } from "../format";
import { orderNodes } from "../flow/order";
import { flash, scrollMode } from "../motion";
import { useCanvas } from "./CanvasContext";
import { StudyInsights } from "./StudyInsights";
import { relFromRunDir, useOpenLocal } from "./LocalFiles";
import { TableAddRows } from "./TableImport";
import { useToast } from "../toast";
import type { AgentStatus, Artifact, Case, Flow, Project, StudyStatus, Study, TrainingDataset, User } from "../types";

/** 캔버스에 들어가는 화면들: 작업 흐름(단계별 결과) · 데이터(입력·출력, 학습 데이터, 케이스) · 멤버. 보고서는 ReportView, Simulator는 SimulatorTab.
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
        sendToAgent(`작업 흐름 '${f.title}'(#${f.id})을 승인했습니다. 계획대로 진행해 주세요.`);
      } else {
        toast("반려했습니다");
        sendToAgent(`작업 흐름 '${f.title}'(#${f.id})을 반려했습니다. 무엇을 바꾸면 좋을지 물어보고 다시 제안해 주세요.`);
      }
    },
    onError: (e: Error) => toast(e.message ?? "변경하지 못했습니다", true),
  });
}

/** 작업 흐름 탭: 위에는 흐름도, 아래에는 단계 순서대로 그 단계의 결과(표·그래프·이미지)를 모두 펼친다.
 *  흐름도의 단계를 누르면 그 단계 결과로 스크롤한다. 흐름도·결과·대화 카드·보고서가 같은 'N단계' 번호를 쓴다. */
export function FlowView({ pid, flows, arts }: { pid: number; flows?: Flow[]; arts: Artifact[] }) {
  const { flowId: fid, setFlowId: setFid, focus } = useCanvas();
  const [sel, setSel] = useState<string | null>(null);
  const [rm, setRm] = useState<Artifact | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/artifacts/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["artifacts", pid] }); toast("삭제했습니다"); },
    onError: (e: Error) => toast(e.message ?? "삭제하지 못했습니다", true),
  });
  const flow = flows?.find((f) => f.id === fid) ?? flows?.[0];
  useEffect(() => { setSel(null); }, [flow?.id]);
  // 대화에서 결과를 누르면(또는 에이전트가 새 결과를 올리면) 그 결과로 스크롤
  useEffect(() => {
    if (!focus) return;
    const t = setTimeout(() => {
      const el = document.getElementById(`art-${focus.id}`);
      el?.scrollIntoView({ behavior: scrollMode(), block: "start" });
      flash(el); // 같은 결과를 다시 눌러도 다시 강조된다
    }, 120);
    return () => clearTimeout(t);
  }, [focus]);
  const goStep = (id: string | null) => {
    setSel(id);
    if (id) setTimeout(() => document.getElementById(`step-${id}`)?.scrollIntoView({ behavior: scrollMode(), block: "start" }), 40);
  };

  if (!flows) return <Skeleton rows={5} />;
  const removeDialog = rm && <Confirm title="결과 삭제" message={`'${rm.title}'을(를) 삭제할까요?`} confirmLabel="삭제" danger
    onConfirm={() => remove.mutate(rm.id)} onClose={() => setRm(null)} />;
  if (!flow) {
    // 표에서 바로 만든 Simulator나 보고서는 작업 흐름 없이 생긴다. 그 결과가 빈 화면 뒤에 숨으면 안 된다
    const loose = [...arts].sort((a, b) => a.id - b.id);
    return (
      <div className="flow-tab flow-none">
        <div className="empty-hero">
          <AgentGlyph size={32} />
          <b>{loose.length ? "작업 흐름 없이 만든 결과입니다" : "아직 작업 흐름이 없습니다"}</b>
          <p>해석을 새로 돌리거나 내 모델을 바꾸는 일은 에이전트가 작업 흐름을 먼저 그려 여기에 보여 주고, 승인하면 실행합니다. 조회·후처리, 가진 데이터로 예측 모델·Simulator 만들기, 보고서는 작업 흐름 없이 바로 합니다.</p>
        </div>
        {loose.length > 0 && <div className="step-list" aria-label="결과"><div className="art-grid">{loose.map((a) => <ArtifactCard key={a.id} a={a} onDelete={() => setRm(a)} />)}</div></div>}
        {removeDialog}
      </div>
    );
  }

  const steps = orderNodes(flow);
  const known = new Set(flow.nodes.map((n) => n.id));
  const mine = [...arts].filter((a) => a.flow_id === flow.id).sort((a, b) => a.id - b.id);
  const byNode: Record<string, Artifact[]> = {};
  mine.forEach((a) => { if (a.node_id && known.has(a.node_id)) (byNode[a.node_id] ??= []).push(a); });
  // 단계에 붙지 않은 결과: 이 흐름의 것 + 어느 흐름에도 없는 것(가장 최근 흐름에서만 보여 준다)
  const outside = [...mine.filter((a) => !a.node_id || !known.has(a.node_id)),
    ...(flow.id === flows[0]?.id ? arts.filter((a) => a.flow_id == null).sort((a, b) => a.id - b.id) : [])];
  const count = Object.fromEntries(Object.entries(byNode).map(([k, v]) => [k, v.length]));
  const st = FLOW_STATUS[flow.status];
  const done = flow.nodes.filter((n) => n.status === "complete").length;
  const card = (a: Artifact) => <ArtifactCard key={a.id} a={a} onDelete={() => setRm(a)} />;

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
        {flow.status === "review" && <span className="notice-inline warn">대화의 카드에서 승인하거나 반려하세요</span>}
        {flow.status !== "review" && flow.status !== "rejected" && (
          <button className="small" title="입력창에 요청을 채웁니다. 새 모델 파일을 첨부해 보내면 옛 결과는 그대로 두고 새 스터디로 진행합니다"
            onClick={() => {
              if (!agent?.connected) { toast("PC Agent가 연결되어 있어야 다시 진행할 수 있습니다 → 설정에서 연결 상태를 확인하세요", true); return; }
              prefillAgent(`지난 작업 흐름 '${flow.title}'(#${flow.id})을 새 모델로 다시 진행해 줘. 옛 스터디·모델·Simulator는 그대로 두고, 새 모델에서 달라진 점(부품·면 이름, 변수 범위, 단위)을 먼저 알려 줘. 바뀐 점: `,
                "새 모델 파일을 첨부해 주세요. 파일이 내 PC에 이미 있으면 경로를 요청에 적어도 됩니다.");
            }}><RotateCcw size={13} />이 흐름을 새 모델로 다시</button>
        )}
      </div>
      <div className="pbar" aria-label="진행률"><i style={{ width: `${(done / Math.max(1, flow.nodes.length)) * 100}%` }} /></div>
      <FlowCanvas flow={flow} selected={sel} onSelect={goStep} artifactCount={count} compact />
      <p className="small muted flow-hint">단계를 누르면 그 단계의 결과로 이동합니다 · 결과 {mine.length + outside.length}개</p>

      {/* 흐름도는 고정, 결과만 이 안에서 스크롤 */}
      <div className="step-list" aria-label="단계별 결과">
        {steps.map((n, i) => {
          const list = byNode[n.id] ?? [];
          return (
            <section key={n.id} id={`step-${n.id}`} className={`step-sec st-${n.status} ${sel === n.id ? "sel" : ""}`} aria-label={`${i + 1}단계 ${n.label}`}>
              <header className="step-head">
                <span className="step-no">{i + 1}단계</span>
                <b className="step-label">{n.label}</b>
                <span className="chip">{NODE_TYPE[n.type] ?? n.type}{n.tool ? ` · ${n.tool}` : ""}</span>
                <span className={`chip ${n.status === "complete" ? "ok" : n.status === "failed" ? "err" : n.status === "in_progress" ? "info" : ""}`}>{NODE_STATUS[n.status]}</span>
                <span className="grow" />
                {n.status === "failed" && flow.status !== "review" && (
                  <button className="small ghost" onClick={() => sendToAgent(`${i + 1}단계 '${n.label}'이(가) 실패했습니다. 원인을 찾아 고쳐 주세요`)}>원인 찾기</button>
                )}
              </header>
              {n.detail && <p className="small muted step-detail">{n.detail}</p>}
              {list.length ? <div className="art-grid">{list.map(card)}</div>
                : <p className="small muted step-empty">{n.status === "pending" ? "아직 시작하지 않았습니다" : n.status === "in_progress" ? "진행 중입니다. 결과가 나오면 여기에 쌓입니다" : "이 단계에서 게시된 결과가 없습니다"}</p>}
            </section>
          );
        })}
        {outside.length > 0 && (
          <section className="step-sec" id="step-outside" aria-label="단계 밖 결과">
            <header className="step-head"><span className="step-no muted">기타</span><b className="step-label">단계에 묶이지 않은 결과</b></header>
            <div className="art-grid">{outside.map(card)}</div>
          </section>
        )}
      </div>
      {removeDialog}
    </div>
  );
}

/* ---------- 데이터 ---------- */
const TABLE_START_HINT = "엑셀에서 복사한 표를 붙여 넣으면 해석 없이 예측 모델을 학습해 Simulator로 배포합니다. AI와 PC Agent가 없어도 됩니다";

export function StudiesView({ pid }: { pid: number }) {
  const c = useCanvas();
  const [adding, setAdding] = useState(false);
  const { data: studies } = useQuery({ queryKey: ["studies", pid], queryFn: () => get<Study[]>(`/api/projects/${pid}/studies`) });
  const cur = studies?.find((s) => s.id === c.studyId)?.id ?? studies?.[0]?.id ?? null;
  if (!studies) return <Skeleton rows={5} />;
  if (!studies.length) {
    return <div className="empty-hero"><AgentGlyph size={32} /><b>아직 데이터가 없습니다</b>
      <p>에이전트가 해석을 돌리거나 가진 결과를 등록하면 케이스가 여기에 쌓입니다. 엑셀 표가 있으면 붙여 넣어 지금 바로 Simulator를 만들 수 있습니다.</p>
      <button className="primary" title={TABLE_START_HINT} onClick={() => c.setTableOpen(true)}><Table2 size={14} />표로 바로 Simulator</button></div>;
  }
  return (
    <>
      <div className="row study-bar">
        {studies.length > 1 && <span className="seg">{studies.map((s) => <button key={s.id} className={cur === s.id ? "on" : ""} onClick={() => c.setStudyId(s.id)}>{s.name}</button>)}</span>}
        <span className="grow" />
        {cur && <button className="small" title="이 스터디에 표의 행이나 출력 열을 더합니다. AI와 PC Agent가 없어도 됩니다" onClick={() => setAdding(true)}><Plus size={13} />행 추가</button>}
        <button className="small" title={TABLE_START_HINT} onClick={() => c.setTableOpen(true)}><Table2 size={13} />표로 바로 Simulator</button>
      </div>
      {cur && <StudyDetail pid={pid} id={cur} />}
      {adding && cur && studies.find((x) => x.id === cur) && <TableAddRows pid={pid} study={studies.find((x) => x.id === cur)!} onClose={() => setAdding(false)} />}
    </>
  );
}

const CASE_STATUS: Record<string, [string, string]> = {
  pending: ["대기", ""], queued: ["대기열", "info"], running: ["실행 중", "info"], complete: ["완료", "ok"], failed: ["실패", "err"], dry_run: ["스크립트만", "warn"], cancelled: ["중지됨", "warn"],
};

/** 출력 순서: 목적 → 제약 → 추가한 출력 → 그 밖에 케이스 결과에 있는 키(서버 output_names와 같은 규칙) */
function outputNames(s: Study, cases: Case[]): string[] {
  const names = [...s.objectives.map((o) => o.name), ...(s.constraints ?? []).map((c) => c.name), ...(s.outputs ?? []).map((o) => o.name)];
  const rest = Array.from(new Set(cases.flatMap((c) => Object.keys(c.results ?? {})))).sort();
  return Array.from(new Set([...names, ...rest]));
}

const bytes = (n?: number) => (!n ? "" : n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

/** 케이스 해석 데이터(case.h5) 칸: 서버에 있으면 받기, PC에만 있으면 위치를 알려 준다 */
function ArchiveCell({ c, name }: { c: Case; name: string }) {
  const toast = useToast();
  const a = c.archive;
  if (!a) return <span className="muted">-</span>;
  if (a.ok === false) return <span className="h5-chip err" title={a.error ?? "묶지 못했습니다"}>오류</span>;
  const what = [a.field_names?.length ? `필드 ${a.field_names.join(", ")}` : "", a.frames ? `프레임 ${a.frames}` : "",
    a.history ? `이력 ${a.history}` : "", a.raw ? `PC 원본 ${a.raw}개(${bytes(a.raw_size)})` : ""].filter(Boolean).join(" · ");
  return a.stored === "server" ? (
    <button className="h5-chip" title={`case.h5 받기 — ${what || "메타·결과·로그"}`}
      onClick={() => download(`/api/cases/${c.id}/archive/file`, `${name}_C${String(c.id).padStart(4, "0")}.h5`).catch((e: Error) => toast(e.message, true))}>
      {bytes(a.size)}</button>
  ) : <span className="h5-chip pc" title={`PC에만 있습니다(크기가 커서 서버에 올리지 않음${a.upload_error ? " 또는 올리기 실패" : ""}) — ${a.pc_path ?? ""}
${what}`}>PC · {bytes(a.size)}</span>;
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** 케이스 표 한 줄. 케이스가 수백 건이어도 바뀐 줄만 다시 그린다(React Query가 바뀌지 않은 케이스 객체를 그대로 둔다) */
const CaseRow = memo(function CaseRow({ c, vars, outs, best, extracting, pulse, name, pid, onDelete }: {
  c: Case; vars: string[]; outs: string[]; best: boolean; extracting: boolean; pulse: boolean; name: string; pid: number; onDelete?: (c: Case) => void;
}) {
  const { user } = useAuth();
  const toast = useToast();
  const open = useOpenLocal(pid);
  const imported = c.source === "import";
  const rel = imported ? null : relFromRunDir(c.run_dir); // 불러온 케이스의 폴더는 작업 폴더 밖 원본이라 웹앱에서 열 수 없다
  const mine = c.run_by === user?.id;
  const state = c.status === "complete" ? "done" : c.status === "failed" ? "fail" : c.status === "running" ? `run ${pulse ? "pulse" : ""}` : "";
  return (
    <tr className={best ? "best-row" : ""} title={c.error ?? undefined}>
      <td>{c.id}</td><td className="nowrap" title={c.origin?.source ? `${c.origin.source}${c.origin.row ? ` ${c.origin.row}행` : ""}${c.origin.label ? ` · ${c.origin.label}` : ""}` : undefined}>{({ doe: "DOE", bo: "최적화", al: "능동학습", manual: "직접", import: "불러옴" } as Record<string, string>)[c.source] ?? c.source}</td>
      <td className="status-cell"><span className={`st ${state}`}><i />{CASE_STATUS[c.status]?.[0] ?? c.status}</span></td>
      {vars.map((v) => <td key={v} className="r col-in">{fmt(c.params[v])}</td>)}
      {outs.map((o) => (
        <td key={o} className="r col-out">{o in (c.results ?? {}) ? fmt(c.results[o])
          : extracting ? <Loader2 size={11} className="spin muted" aria-label="추출 중" /> : ""}</td>
      ))}
      <td className="nowrap"><ArchiveCell c={c} name={name} /></td>
      <td className="r nowrap">{rel ? (
        <button className="icon-btn" disabled={!mine} aria-label={`케이스 ${c.id} 폴더 열기`}
          title={mine ? `내 PC에서 케이스 폴더 열기 — ${rel}` : "다른 멤버의 PC에서 실행된 케이스입니다"}
          onClick={() => open.mutate({ path: rel })}><FolderOpen size={13} /></button>
      ) : imported && c.run_dir ? (
        <button className="icon-btn" aria-label={`케이스 ${c.id} 원본 폴더 경로 복사`} title={`원본 폴더 경로 복사 — ${c.run_dir}`}
          onClick={async () => toast((await copyText(c.run_dir!)) ? "경로를 복사했습니다" : "복사하지 못했습니다", false)}><Copy size={13} /></button>
      ) : null}{(imported || c.origin) && onDelete && <button className="icon-btn danger" aria-label={`케이스 ${c.id} ${imported ? "지우기" : "값 비우기"}`} title={imported ? "잘못 넣은 행을 지웁니다. 배포 중인 Simulator는 다시 학습할 때까지 그대로입니다" : "표로 채운 값을 비우고 이 DOE 점을 대기로 돌립니다"} onClick={() => onDelete(c)}><Trash2 size={13} /></button>}</td>
    </tr>
  );
}, (a, b) => a.c === b.c && a.best === b.best && a.extracting === b.extracting && a.pulse === b.pulse && a.name === b.name && a.pid === b.pid
  && sameList(a.vars, b.vars) && sameList(a.outs, b.outs));

const DATASET_STATUS: Record<TrainingDataset["status"], [string, string]> = { building: ["만드는 중", "info"], ready: ["준비됨", "ok"], failed: ["실패", "err"] };

/** 학습 데이터: 몇 건을 어떻게 나눴는지, 빠진 케이스와 입력은 왜 빠졌는지를 숨기지 않고 보여 준다 */
function DatasetsBlock({ s, done }: { s: Study; done: number }) {
  const { data } = useQuery({
    queryKey: ["study", s.id, "datasets"], queryFn: () => get<TrainingDataset[]>(`/api/studies/${s.id}/datasets`),
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === "building") ? 2000 : false),
  });
  if (!data || (!data.length && done < 4)) return null;
  return (
    <section className="block">
      <div className="block-head"><h3>학습 데이터</h3><span className="cnt">{data.length}개</span></div>
      {!data.length ? (
        <div className="row"><span className="small muted grow">신경망 형상 학습에 쓸 데이터가 아직 없습니다. 해석은 다시 하지 않습니다.</span>
          <button className="small" onClick={() => sendToAgent(`스터디 '${s.name}'(#${s.id})의 완료된 케이스로 학습 데이터셋을 만들어 줘`)}>만들기 요청</button></div>
      ) : (
        <div className="model-list">{data.slice(0, 3).map((d) => (
          <div key={d.id} className="model-row">
            <div className="grow">
              <b>{d.name}</b>
              <div className="model-r2">
                <span className={`chip ${DATASET_STATUS[d.status][1]}`}>{DATASET_STATUS[d.status][0]}</span>
                {d.status === "building" && <span className="small muted">남은 케이스 {d.pending.length}건</span>}
                {d.status === "ready" && <span className="small muted">샘플 {d.n_samples}건(학습 {d.splits.train ?? 0} · 검증 {d.splits.val ?? 0} · 시험 {d.splits.test ?? 0}) · {bytes(d.size)}</span>}
                {d.status === "ready" && !d.units_known && <span className="chip warn" title="스터디 adapter_config.units가 없어 단위를 맞추지 못했습니다. 다른 스터디와 섞으면 안 됩니다">단위 모름</span>}
              </div>
              {d.status === "ready" && <p className="small muted">출력 {d.targets.join(", ")} · 입력 형상{d.inputs.length ? `, ${d.inputs.join(", ")}` : ""}</p>}
              {d.error && <p className="small" style={{ color: "var(--err-ink)" }}>{d.error}</p>}
              {d.inputs_dropped.length > 0 && <p className="small" style={{ color: "var(--warn-ink)" }}>빠진 입력: {d.inputs_dropped.map((x) => `${x.name} — ${x.reason}`).join(" · ")}</p>}
              {d.excluded.length > 0 && (
                <details className="small"><summary style={{ color: "var(--warn-ink)", cursor: "pointer" }}>제외된 케이스 {d.excluded.length}건</summary>
                  <ul>{Array.from(new Set(d.excluded.map((x) => x.reason))).map((r) => {
                    const ids = d.excluded.filter((x) => x.reason === r).map((x) => x.case_id);
                    return <li key={r}>{r} — 케이스 {ids.slice(0, 12).join(", ")}{ids.length > 12 ? ` 외 ${ids.length - 12}건` : ""}</li>;
                  })}</ul></details>
              )}
            </div>
          </div>))}</div>
      )}
    </section>
  );
}

const CASE_ROWS_STEP = 200;

function StudyDetail({ pid, id }: { pid: number; id: number }) {
  const { data: st } = useQuery({ queryKey: ["study", id], queryFn: () => get<StudyStatus>(`/api/studies/${id}`) });
  const { data: cases } = useQuery({ queryKey: ["study", id, "cases"], queryFn: () => get<Case[]>(`/api/studies/${id}/cases`) });
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [askStop, setAskStop] = useState(false);
  const [shownRows, setShownRows] = useState(CASE_ROWS_STEP); // 불러온 표는 수천 행일 수 있다. 표를 한 번에 다 그리면 화면이 멈춘다
  // 해석 창이 사용자 화면에 없으므로 웹앱에서 멈출 수 있어야 한다
  const stop = useMutation({
    mutationFn: () => post<{ stopping: number; others: number; released?: number; note?: string }>(`/api/studies/${id}/stop`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["study", id] });
      const head = r.note || `${r.stopping}건을 중지합니다`;
      toast(r.others ? `${head} 다른 멤버의 PC에서 도는 ${r.others}건은 그대로 둡니다` : head);
    },
    onError: (e: Error) => toast(e.message, true),
  });
  const [askDelete, setAskDelete] = useState<Case | null>(null);
  const removeCase = useMutation({
    mutationFn: (c: Case) => del(`/api/studies/${id}/cases/${c.id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["study", id] }); qc.invalidateQueries({ queryKey: ["artifacts", pid] }); toast("행을 지웠습니다. 다시 학습해 배포하면 Simulator에 반영됩니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  // 최적값이 바뀌었을 때만 잠깐 강조한다(화면을 처음 열 때는 그대로)
  const bestBox = useRef<HTMLDivElement>(null);
  const bestId = st?.best?.id;
  const prevBest = useRef(bestId);
  useEffect(() => {
    if (prevBest.current != null && bestId != null && bestId !== prevBest.current) flash(bestBox.current);
    if (bestId != null) prevBest.current = bestId;
  }, [bestId]);
  if (!st) return <Skeleton rows={6} />;
  const s = st.study;
  const vars = s.variables.map((v) => v.name);
  const outs = outputNames(s, cases ?? []);
  const obj = s.objectives[0];
  const cons = (s.constraints ?? []).map((c) => [c.lower != null ? `${c.lower} ≤ ` : "", c.name, c.upper != null ? ` ≤ ${c.upper}` : ""].join("")).join(", ");
  const total = Object.values(st.counts).reduce((a, b) => a + b, 0);
  const done = (cases ?? []).filter((c) => c.status === "complete");
  const extracting = new Set(st.extracting ?? []);
  const myRunning = (cases ?? []).filter((c) => c.run_by === user?.id && c.status === "running").length;
  const myQueued = (cases ?? []).filter((c) => c.run_by === user?.id && c.status === "queued").length;
  const myExtracting = (cases ?? []).filter((c) => c.run_by === user?.id && extracting.has(c.id)).length;
  const role = (o: string) => {
    const ob = s.objectives.find((x) => x.name === o);
    if (ob) return ob.goal === "min" ? "목적 · 최소" : "목적 · 최대";
    const c = (s.constraints ?? []).find((x) => x.name === o);
    if (c) return `제약 · ${[c.lower != null ? `≥ ${c.lower}` : "", c.upper != null ? `≤ ${c.upper}` : ""].filter(Boolean).join(" ")}`;
    return "";
  };
  const note = (o: string) => s.outputs?.find((x) => x.name === o)?.note ?? "";
  const planned = (cases ?? []).some((c) => c.source === "doe"); // 밖에서 해석하려고 받아 갈 DOE 점이 있을 때만
  const source = dataSourceText(st.data_source);
  const toolName = s.adapter === "data" ? (source || "불러온 데이터") : `${s.adapter}${s.adapter_config?.tool ? `/${String(s.adapter_config.tool)}` : ""}`;
  const fixed = Object.entries(s.import_info?.fixed ?? {});
  return (
    <div className="stack">
      <section className="block">
        <div className="lbl">{obj ? `현재 최적${s.objectives.length > 1 ? "(첫 번째 목표 기준)" : ""} · ${obj.name} ${obj.goal === "min" ? "최소" : "최대"}${cons ? ` · ${cons}` : ""}` : s.name}</div>
        {st.best ? (
          <div className="cond-big" ref={bestBox}>
            {vars.map((v) => <div key={v}><span>{v}</span><b>{fmt(st.best!.params[v], 4)}</b></div>)}
            {obj && <div><span>{obj.name}</span><b style={{ color: "var(--brand)" }}>{fmt(st.best.results[obj.name], 4)}</b></div>}
          </div>
        ) : <p className="small muted" style={{ marginTop: 10 }}>{st.counts.complete && !obj ? "목적(최소·최대로 할 값)을 정하지 않아 최적 케이스를 고르지 않았습니다. 대화로 목적을 알려 주면 찾아 드립니다."
          : st.counts.complete && cons ? "제약을 만족하는 케이스가 아직 없습니다." : "완료된 케이스가 아직 없습니다."}</p>}
        <div className="study-progress">
          <div className="pbar" aria-label="케이스 진행률"><i style={{ width: `${total ? ((st.counts.complete ?? 0) / total) * 100 : 0}%` }} /></div>
          <span className="small muted">
            케이스 {st.counts.complete ?? 0}/{total} 완료
            {Object.entries(st.counts).filter(([k, n]) => k !== "complete" && n).map(([k, n]) => ` · ${CASE_STATUS[k]?.[0] ?? k} ${n}`).join("")}
            {` · ${toolName}`}
          </span>
          {st.running && <span className="chip info"><span className="spinner" style={{ width: 10, height: 10 }} />실행 중</span>}
          {extracting.size > 0 && <span className="chip info"><span className="spinner" style={{ width: 10, height: 10 }} />Output 추출 중 {extracting.size}건</span>}
          {myRunning + myQueued + myExtracting > 0 && (
            <button className="small" disabled={stop.isPending} onClick={() => setAskStop(true)}><Square size={12} />중지</button>
          )}
        </div>
        {!!st.stalled && <div className="notice warn" role="status"><span>{myRunning + myQueued > 0
          ? `내 PC와 연결이 끊겨 케이스 ${st.stalled}건이 실행 중으로 남아 있습니다. 잠깐 끊긴 것이면 해석은 계속 돌고, 다시 연결되면 결과가 들어옵니다 → 기다리지 않으려면 '중지'를 누르세요.`
          : `${st.stalled_by?.join(", ")}님의 PC와 연결이 끊겨 케이스 ${st.stalled}건이 실행 중으로 남아 있습니다. 잠깐 끊긴 것이면 해석은 계속 돌고, 다시 연결되면 결과가 들어옵니다. 중지는 실행한 사람만 할 수 있습니다. 결과는 그대로 볼 수 있습니다.`}</span></div>}
        {st.pc_notice && st.pc_notice.by === user?.id && <div className="notice warn" role="status"><span>{st.pc_notice.text}</span></div>}
        {askStop && <Confirm title="해석 중지" danger confirmLabel="중지"
          message={[myRunning ? `실행 중인 해석 ${myRunning}건을 끝냅니다. 끝낸 해석의 결과는 남지 않습니다.` : "",
            myQueued ? `대기 중인 ${myQueued}건은 시작하지 않습니다.` : "",
            myExtracting ? `Output 추출 ${myExtracting}건을 멈춥니다.` : "",
            st.stalled ? "연결이 끊긴 PC의 해석은 다시 연결될 때 끝냅니다. 이전 결과가 있으면 그대로 둡니다." : "",
            "다시 돌리려면 대화로 요청하세요."].filter(Boolean).join(" ")}
          onConfirm={() => stop.mutate()} onClose={() => setAskStop(false)} />}
        {askDelete && <Confirm title={askDelete.source === "import" ? "행 지우기" : "값 비우기"} danger confirmLabel={askDelete.source === "import" ? "지우기" : "비우기"}
          message={askDelete.source === "import" ? `케이스 ${askDelete.id}을(를) 지웁니다. 되돌릴 수 없습니다. 배포 중인 Simulator는 다시 학습해 배포할 때까지 그대로입니다.`
            : `케이스 ${askDelete.id}에 표로 채운 값을 비우고 대기로 돌립니다. 되돌릴 수 없습니다. 배포 중인 Simulator는 다시 학습해 배포할 때까지 그대로입니다.`}
          onConfirm={() => removeCase.mutate(askDelete)} onClose={() => setAskDelete(null)} />}
        {(() => {
          const saved = (cases ?? []).filter((c) => c.archive && c.archive.ok !== false);
          if (!saved.length) return null;
          const onServer = saved.filter((c) => c.archive?.stored === "server");
          return (
            <p className="small muted h5-line" title="케이스마다 결과(메타·필드 전 프레임·이력·로그)를 HDF5로 압축해 담은 파일 — docs/cae-data-hdf5.md">
              <Database size={12} />해석 데이터(HDF5) {saved.length}/{cases?.length ?? 0}건 저장 · 서버 {onServer.length}건 {bytes(onServer.reduce((t, c) => t + (c.archive?.size ?? 0), 0))}
              {saved.length > onServer.length && ` · PC에만 ${saved.length - onServer.length}건`}
            </p>
          );
        })()}
      </section>

      <StudyInsights id={id} />

      {/* 입력(설계 변수)과 출력(해석에서 뽑는 값)을 나눠 보여 준다. Output은 대화로 언제든 늘릴 수 있다 */}
      <section className="block io-block" aria-label="입력과 출력">
        <div className="io-col">
          <div className="io-head"><span className="io-tag in">입력 · Input</span><span className="cnt">{vars.length}개</span></div>
          <ul className="io-list">{s.variables.map((v) => (
            <li key={v.name}><b>{v.name}</b><span className="io-meta">{fmt(v.lower)} ~ {fmt(v.upper)}{v.unit ? ` ${v.unit}` : ""}</span></li>))}</ul>
          {fixed.length > 0 && <p className="small muted io-fixed" title="모든 행의 값이 같아 변수로 쓰지 않은 열입니다">고정값: {fixed.map(([k, v]) => `${k}=${String(v)}`).join(", ")}</p>}
        </div>
        <div className="io-col">
          <div className="io-head">
            <span className="io-tag out">출력 · Output</span><span className="cnt">{outs.length}개</span><span className="grow" />
            <button className="small ghost" title="입력창에 요청을 채웁니다. 이름(예: 바닥 전달력)을 적어 보내면 이미 해석한 케이스에서도 뽑습니다"
              onClick={() => prefillAgent(`'${s.name}' 스터디에 Output 추가: `)}><Plus size={13} />Output 추가</button>
          </div>
          <ul className="io-list">{outs.map((o) => {
            const n = done.filter((c) => o in (c.results ?? {})).length;
            return (
              <li key={o}><b>{o}</b><span className="io-meta">{[role(o), note(o)].filter(Boolean).join(" · ")}</span>
                <span className={`io-cov ${done.length && n < done.length ? "part" : ""}`} title="값이 있는 완료 케이스 수">{n}/{done.length}</span></li>
            );
          })}{!outs.length && <li className="muted small">완료된 케이스가 생기면 결과 항목이 여기에 보입니다</li>}</ul>
        </div>
      </section>

      {/* 신경망 형상 학습 데이터는 해석 필드가 있어야 만든다. 표만 불러온 스터디에는 만들 것이 없다 */}
      {(s.adapter !== "data" || done.some((c) => c.fields?.length || c.archive)) && <DatasetsBlock s={s} done={done.length} />}

      <section className="block">
        <div className="block-head"><h3>케이스</h3><span className="cnt">{cases?.length ?? 0}건</span><span className="grow" />
          {planned && (
            <button className="small" title="실험계획(DOE) 점을 CSV로 받습니다. 대기 중인 점이 있으면 그 점만 받습니다. 밖에서 해석한 뒤 출력 칸을 채워 대화로 넘기면 그 점이 채워집니다"
              onClick={() => download(`/api/studies/${id}/doe.csv`, `${s.name}_DOE.csv`).catch((e: Error) => toast(e.message, true))}><Download size={13} />DOE 계획표 받기(CSV)</button>
          )}
        </div>
        <div className="table-wrap case-table"><table>
          <thead>
            <tr className="io-group">
              <th colSpan={3} />
              {vars.length > 0 && <th colSpan={vars.length} className="col-in grp">입력</th>}
              {outs.length > 0 && <th colSpan={outs.length} className="col-out grp">출력</th>}
              <th colSpan={2} />
            </tr>
            <tr><th>ID</th><th>출처</th><th>상태</th>{vars.map((v) => <th key={v} className="r col-in">{v}</th>)}{outs.map((o) => <th key={o} className="r col-out">{o}</th>)}
              <th title="해석 데이터(case.h5, HDF5)">HDF5</th><th /></tr>
          </thead>
          <tbody>{(cases ?? []).slice(0, shownRows).map((c) => (
            <CaseRow key={c.id} c={c} vars={vars} outs={outs} best={st.best?.id === c.id} extracting={extracting.has(c.id)} onDelete={setAskDelete}
              pulse={(st.counts.running ?? 0) <= 20} name={s.name} pid={pid} />))}</tbody>
        </table></div>
        {(cases?.length ?? 0) > shownRows && (
          <div className="row case-more"><span className="small muted grow">{cases!.length.toLocaleString("ko-KR")}건 중 앞 {shownRows.toLocaleString("ko-KR")}건을 보여 줍니다</span>
            <button className="small" onClick={() => setShownRows((n) => n + CASE_ROWS_STEP * 5)}>{(CASE_ROWS_STEP * 5).toLocaleString("ko-KR")}건 더 보기</button></div>
        )}
      </section>
    </div>
  );
}

/* ---------- 멤버·공유 ---------- */
/** 프로젝트 멤버(=공유 대상) 관리. 이름·설명은 프로젝트 메뉴의 '이름·설명 바꾸기'에서 바꾼다 */
export function MembersPanel({ p }: { p: Project }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const isMaster = p.user_role === "master";
  const [add, setAdd] = useState<number | "">("");
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/api/users"), enabled: isMaster });
  const candidates = useMemo(() => (users ?? []).filter((u) => u.is_active && !p.members.some((m) => m.user_id === u.id)), [users, p.members]);
  const done = () => { qc.invalidateQueries({ queryKey: ["project", p.id] }); qc.invalidateQueries({ queryKey: ["projects"] }); };
  const err = (e: Error) => toast(e.message ?? "처리하지 못했습니다", true);
  const upsert = useMutation({ mutationFn: (v: { uid: number; role: string }) => put(`/api/projects/${p.id}/members/${v.uid}`, { role: v.role }), onSuccess: () => { done(); toast("저장했습니다"); setAdd(""); }, onError: err });
  const remove = useMutation({ mutationFn: (uid: number) => del(`/api/projects/${p.id}/members/${uid}`), onSuccess: () => { done(); toast("제거했습니다"); }, onError: err });

  return (
    <div className="members">
      <p className="small muted">멤버는 작업 흐름·결과·대화를 함께 보고, 자기 PC Agent로 작업을 요청할 수 있습니다 · 내 권한: {ROLE_LABEL[p.user_role ?? "member"]}</p>
      <p className="small muted">서버에 있는 결과·모델·Simulator는 내 PC가 꺼져 있어도 멤버가 이어서 씁니다. 해석 원본 파일(odb·d3plot 등)은 해석한 사람의 PC에만 있어, 그 파일이 필요한 일은 그 사람의 PC가 켜져 있어야 합니다.</p>
      {isMaster && (
        <div className="row members-add">
          <select value={add} onChange={(e) => setAdd(e.target.value ? Number(e.target.value) : "")} aria-label="추가할 사용자">
            <option value="">{candidates.length ? "추가할 사용자 선택" : "추가할 수 있는 사용자가 없습니다"}</option>
            {candidates.map((u) => <option key={u.id} value={u.id}>{u.display_name} ({u.username})</option>)}
          </select>
          <button className="primary" disabled={add === ""} onClick={() => add !== "" && upsert.mutate({ uid: add, role: "member" })}><Plus size={14} />추가</button>
        </div>
      )}
      <ul className="member-list">
        {p.members.map((m) => (
          <li key={m.user_id}>
            <span className="avatar" aria-hidden="true">{m.display_name.slice(0, 1)}</span>
            <span className="grow who"><b>{m.display_name}{m.user_id === user?.id ? " (나)" : ""}</b><small>{m.username}</small></span>
            {isMaster ? (
              <select value={m.role} onChange={(e) => upsert.mutate({ uid: m.user_id, role: e.target.value })} aria-label={`${m.display_name} 권한`}>
                <option value="master">관리자</option><option value="member">멤버</option></select>
            ) : <span className="small muted">{ROLE_LABEL[m.role]}</span>}
            {isMaster && m.user_id !== user?.id
              ? <button className="icon-btn danger" title="제거" aria-label={`${m.display_name} 제거`} onClick={() => remove.mutate(m.user_id)}><Trash2 size={14} /></button>
              : <span className="icon-gap" />}
          </li>))}
      </ul>
    </div>
  );
}
