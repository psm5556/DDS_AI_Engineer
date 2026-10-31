import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Copy, FolderOpen, Link2, RotateCcw, Share2, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, del, get, post } from "../api";
import { Field3DPanel } from "../components/Field3DPanel";
import { TrustLine, useTrustLine } from "../components/TrustLine";
import { Modal } from "../components/Modal";
import { ModelTrust } from "../components/ModelTrust";
import { baseLayout, chartColors, Plot, plotConfig } from "../components/Plot";
import { Skeleton } from "../components/Skeleton";
import { copyText, fmt, when } from "../format";
import { useTheme } from "../theme";
import { useToast } from "../toast";
import type { DataSource, Objective, SurrogateModel, Variable } from "../types";

interface Spec {
  model: SurrogateModel; variables: Variable[]; objectives: Objective[]; study_name: string;
  project_id?: number; project_name?: string; shared_by?: string; note?: string; current_model_id?: number | null;
  data_source?: Partial<DataSource> | null;
}
interface Pred { mean: Record<string, number>; std: Record<string, number> }
interface Batch { mean: Record<string, number[]>; std: Record<string, number[]> }
interface Share { id: number; token: string; note: string; enabled: boolean; uses: number; expires_at: string | null; created_at: string; warning?: string | null }
const SWEEP_N = 41;
const CLOSED = [403, 404, 410]; // 권한이 끊김·지워짐·공유 기간 끝. 다시 눌러도 되지 않는다
const MANY_VARS = 6; // 이보다 많으면 민감도 변수는 목록으로 고르고 예측 결과를 슬라이더 위에 둔다. 표로 시작하면 입력이 수십 개일 수 있다

/** 이 버전이 쓰는 3D 모델 목록(되돌리면 3D도 그때 것으로 돌아간다) */
export const memberFieldList = (modelId: number) => (s: Spec) => `/api/studies/${s.model.study_id}/field-models?model_id=${modelId}`;

/** 넓은 화면의 Simulator (프로젝트 멤버용). 평소에는 프로젝트의 Simulator 탭에서 대화와 함께 쓴다 */
export default function SimulatorPage() {
  const mid = Number(useParams().mid);
  return <SimulatorView base={`/api/models/${mid}`} specPath={`/api/models/${mid}/spec`} modelId={mid}
    fieldList={memberFieldList(mid)} fieldBase={(id) => `/api/field-models/${id}`} />;
}

/** 공유 링크로 연 Simulator (프로젝트 멤버가 아니어도 로그인한 사내 사용자면 사용) */
export function SharedSimulatorPage() {
  const token = useParams().token!;
  return <SimulatorView base={`/api/shares/${token}`} specPath={`/api/shares/${token}`}
    fieldList={() => `/api/shares/${token}/field-models`} fieldBase={(id) => `/api/shares/${token}/field-models/${id}`} />;
}

/** 해석 없이 설계변수를 바꿔 결과를 실시간 예측 (± 불확실도) + 민감도 곡선. embedded면 캔버스 탭 안에 넣는다(제목·버전은 탭이 보여 준다) */
export function SimulatorView({ base, specPath, modelId, fieldList, fieldBase, embedded, onMore }: {
  base: string; specPath: string; modelId?: number; fieldList: (s: Spec) => string; fieldBase: (id: number) => string; embedded?: boolean; onMore?: () => void;
}) {
  const [sharing, setSharing] = useState(false);
  const { data: spec, error } = useQuery({ queryKey: ["sim-spec", specPath], queryFn: () => get<Spec>(specPath) });
  const mid0 = useMemo(() => Object.fromEntries((spec?.variables ?? []).map((v) => [v.name, (v.lower + v.upper) / 2])), [spec]);
  const [x, setX] = useState<Record<string, number>>({});
  const [pred, setPred] = useState<Pred | null>(null);
  const [sweepVar, setSweepVar] = useState<string | null>(null);
  const [sweep, setSweep] = useState<Batch | null>(null);
  const [stale, setStale] = useState(false); // 입력을 바꿨는데 새 예측이 아직 오지 않음(값을 흐리게)
  const [failure, setFailure] = useState<{ reason: string; closed: boolean } | null>(null); // 옛 숫자를 그대로 두면 영향이 없는 줄 알고 판단한다
  const closed = !!failure?.closed;
  const seq = useRef(0);
  const trust = useTrustLine(`${base}/trust-line`);
  useTheme(); // 테마를 바꾸면 민감도 그래프 색도 다시 그린다
  useEffect(() => { if (spec) { setX(mid0); setSweepVar(spec.variables[0]?.name ?? null); } }, [spec, mid0]);
  useEffect(() => { setFailure(null); }, [base]); // 탭에서 다른 버전으로 바꾸면 지난 버전의 오류를 들고 가지 않는다

  // 입력이 바뀌면 바로 예측 (짧게 모아서). 늦게 도착한 옛 응답이 새 값을 덮지 않게 순서 번호를 본다
  useEffect(() => {
    if (!spec || closed || !Object.keys(x).length) return;
    const n = ++seq.current;
    setStale(true);
    const fail = (e: unknown) => {
      if (n !== seq.current) return;
      setPred(null);
      setSweep(null);
      setStale(false);
      setFailure({ reason: e instanceof Error ? e.message : String(e), closed: e instanceof ApiError && CLOSED.includes(e.status) });
    };
    const t = setTimeout(() => {
      post<Pred>(`${base}/predict`, { params: x })
        .then((r) => { if (n === seq.current) { setPred(r); setStale(false); setFailure(null); } })
        .catch(fail);
      const v = spec.variables.find((q) => q.name === sweepVar);
      if (v) {
        const pts = Array.from({ length: SWEEP_N }, (_, i) => ({ ...x, [v.name]: v.lower + ((v.upper - v.lower) * i) / (SWEEP_N - 1) }));
        post<Batch>(`${base}/predict_batch`, { points: pts }).then((r) => { if (n === seq.current) setSweep(r); }).catch(fail);
      }
    }, 120);
    return () => clearTimeout(t);
  }, [x, spec, base, sweepVar, closed]);

  if (error) {
    const msg = <div className="notice err">{(error as Error).message}</div>;
    return embedded ? msg : <div className="page">{msg}<p className="small" style={{ marginTop: 12 }}><Link to="/simulators">내 Simulator 목록으로</Link></p></div>;
  }
  if (!spec) return <Skeleton page={!embedded} rows={5} />;
  const outs = spec.model.outputs;
  const v = spec.variables.find((q) => q.name === sweepVar);
  const c = chartColors(); // 디자인 토큰 색(테마가 바뀌면 다시 그릴 때 새 값)
  const grid = c.grid, txt = c.text;
  const many = spec.variables.length > MANY_VARS;
  const outside = spec.variables.filter((q) => x[q.name] !== undefined && (x[q.name] < q.lower || x[q.name] > q.upper));
  const results = (
    <section className="block sim-results">
      <div className="block-head"><h3><Sparkles size={15} />예측 결과</h3><span className="small muted">{trust?.outputs && Object.values(trust.outputs).some((q) => q.p80 != null) ? `± ${spec.data_source && spec.data_source.kind !== "solver" ? "지난 데이터로" : "지난 해석으로"} 맞춰 본 오차(10번 중 8번)` : "± 예측 불확실도"}</span></div>
      <ul className={`sim-out ${stale ? "stale" : ""}`}>
        {outs.map((o) => {
          const obj = spec.objectives.find((q) => q.name === o);
          // 값 옆의 ±는 실제로 맞춰 본 오차다. AI가 스스로 짐작한 폭은 훨씬 좁게 나오기도 해 믿을 숫자로 보이지 않게 툴팁에 둔다
          const t = trust?.outputs?.[o];
          const guess = pred ? `AI가 스스로 짐작한 폭입니다: ± ${fmt(2 * pred.std[o], 2)}` : undefined;
          return (
            <li key={o}>
              <span className="n" title={o}>{t && t.label !== o ? `${t.label} (${o})` : o}{obj && <small>{obj.goal === "min" ? "최소화 목표" : "최대화 목표"}</small>}</span>
              <b className={!pred && failure ? "none" : undefined}>{pred ? fmt(pred.mean[o], 4) : failure ? "없음" : "…"}</b>
              <small className="u" title={guess}>{pred ? (t?.p80 != null ? `± ${fmt(t.p80, 2)}${t.unit ? ` ${t.unit}` : ""}` : `± ${fmt(2 * pred.std[o], 2)}`) : ""}</small>
            </li>
          );
        })}
      </ul>
      {/* 숫자 칸에는 범위 밖 값을 적을 수 있다. 외삽한 값을 믿지 않게 알린다 */}
      {outside.length > 0 && <div className="notice warn sim-outside" role="status"><AlertTriangle size={14} /><span>이 버전을 학습한 범위 밖입니다({outside.slice(0, 3).map((q) => q.name).join(", ")}). 예측을 믿기 어렵습니다 → 범위 안으로 되돌리거나 그 범위의 데이터를 더해 다시 학습하세요</span></div>}
    </section>
  );

  return (
    <div className={embedded ? "sim-page sim-embed" : "page sim-page"}>
      {!embedded && <>
        {/* 이동은 경로 글자가 아니라 버튼으로: 눌러서 어디로 가는지 한눈에 보이게 한다 */}
        <nav className="sim-nav" aria-label="이동">
          <Link className="btn small" to="/simulators"><ArrowLeft size={14} />Simulator 목록</Link>
          {spec.project_id && <Link className="btn small" to={`/projects/${spec.project_id}`} state={{ canvasTab: "simulator", studyId: spec.model.study_id, modelId: spec.model.id }}
            title="이 Simulator를 만든 프로젝트를 열고, 지금 보는 버전의 Simulator 탭을 보여 줍니다"><FolderOpen size={14} />프로젝트 열기: {spec.project_name || "프로젝트"}</Link>}
        </nav>
        <div className="sim-head">
          <div className="grow"><h1>{spec.study_name} Simulator</h1>
            <p className="page-sub">{spec.shared_by ? `${spec.shared_by}님이 공유${spec.note ? ` · ${spec.note}` : ""} · ` : ""}
              해석 없이 설계변수를 바꿔 바로 예측합니다 · v{spec.model.version} · 학습 {spec.model.n_train}건
              {spec.current_model_id && spec.current_model_id !== spec.model.id ? " · 현재 배포된 버전이 아닙니다" : ""}</p></div>
          {modelId && spec.current_model_id && <button onClick={() => setSharing(true)}><Share2 size={14} />공유</button>}
        </div>
      </>}

      <ModelTrust model={spec.model} source={spec.data_source} />

      {failure && (
        <div className={`notice ${closed ? "err" : "warn"} sim-failed`} role="alert"><AlertTriangle size={14} />
          <span>{closed
            ? `이 Simulator를 더 쓸 수 없어 예측값을 지웠습니다. ${failure.reason} → ${spec.shared_by ? `공유한 ${spec.shared_by}님에게 문의하세요.` : "프로젝트 관리자에게 문의하세요."}`
            : `예측하지 못해 값을 지웠습니다. ${failure.reason} → 입력을 바꾸거나 잠시 뒤 다시 시도하세요.`}</span></div>
      )}

      <TrustLine url={`${base}/trust-line`} onMore={onMore} moreTo={!onMore && spec.project_id ? `/projects/${spec.project_id}` : undefined} />

      {/* 왼쪽: 입력(설계변수)과 출력(예측값)을 붙여 둔다 · 오른쪽: 3D 형상·필드와 민감도 */}
      <div className="sim2">
        <div className={`sim-side ${many ? "many" : ""}`}>
          {/* 변수가 많으면 슬라이더 아래의 결과는 화면 밖에 있다. 위에 붙여 값을 바꾸며 함께 본다 */}
          {many && results}
          <section className="block sim-inputs">
            <div className="block-head"><h3>설계변수</h3><span className="grow" />
              <button className="small ghost" disabled={closed} onClick={() => setX(mid0)} title="모든 변수를 범위 가운데로"><RotateCcw size={13} />가운데로</button></div>
            {spec.variables.map((q) => (
              <div key={q.name} className="sim-var">
                <div className="sim-var-top"><span title={q.name}>{q.note ? `${q.note.replace(/\s*\[[^\]]*\]\s*/g, "") || q.name} (${q.name})` : q.name}{q.unit && <small className="sim-var-unit"> [{q.unit}]</small>}</span>
                  <input type="number" inputMode="decimal" value={x[q.name] !== undefined ? Number(x[q.name].toPrecision(5)) : ""}
                    min={q.lower} max={q.upper} disabled={closed} onChange={(e) => setX({ ...x, [q.name]: Number(e.target.value) })} aria-label={`${q.name} 값`} /></div>
                <input type="range" min={q.lower} max={q.upper} step={(q.upper - q.lower) / 200} value={x[q.name] ?? q.lower} disabled={closed}
                  onChange={(e) => setX({ ...x, [q.name]: Number(e.target.value) })} aria-label={q.name} />
                <div className="sim-var-range small muted"><span>{fmt(q.lower, 4)}</span><span>{fmt(q.upper, 4)}</span></div>
              </div>
            ))}
          </section>
          {!many && results}
        </div>

        <div className="sim-main">
          {!closed && <Field3DPanel listUrl={fieldList(spec)} baseUrl={fieldBase} params={x} />}
          <section className="block">
            <div className="block-head"><h3>민감도</h3><span className="small muted">한 변수만 바꿀 때의 변화 · 주홍 점이 지금 설계</span><span className="grow" />
              {many ? <select className="sim-sweep" value={sweepVar ?? ""} aria-label="민감도를 볼 변수" onChange={(e) => setSweepVar(e.target.value)}>{spec.variables.map((q) => <option key={q.name} value={q.name}>{q.name}</option>)}</select>
                : spec.variables.length > 1 && <span className="seg">{spec.variables.map((q) => <button key={q.name} className={sweepVar === q.name ? "on" : ""} title={q.name} onClick={() => setSweepVar(q.name)}>{q.name}</button>)}</span>}</div>
            <div className="sim-plots">
              {sweep && v && outs.map((o) => {
                const xs = Array.from({ length: SWEEP_N }, (_, i) => v.lower + ((v.upper - v.lower) * i) / (SWEEP_N - 1));
                const m = sweep.mean[o], s = sweep.std[o];
                return (
                  <Plot key={o} config={plotConfig} style={{ width: "100%" }} useResizeHandler
                    data={[
                      { x: [...xs, ...xs.slice().reverse()], y: [...m.map((a, i) => a + 2 * s[i]), ...m.map((a, i) => a - 2 * s[i]).reverse()],
                        fill: "toself", fillcolor: "rgba(100,116,139,0.15)", line: { width: 0 }, hoverinfo: "skip", showlegend: false, type: "scatter" },
                      { x: xs, y: m, type: "scatter", mode: "lines", line: { color: c.mean, width: 2 }, name: o },
                      { x: [x[v.name]], y: [pred?.mean[o]], type: "scatter", mode: "markers", marker: { color: c.brand, size: 9 }, name: "현재" },
                    ]}
                    layout={{ ...baseLayout, height: 220, autosize: true, showlegend: false, font: { ...baseLayout.font, color: txt },
                      xaxis: { title: { text: v.name }, gridcolor: grid, color: txt }, yaxis: { title: { text: o }, gridcolor: grid, color: txt } }} />
                );
              })}
            </div>
          </section>
        </div>
      </div>
      {sharing && spec.current_model_id && <ShareModal modelId={spec.current_model_id} onClose={() => setSharing(false)} />}
    </div>
  );
}

/** 공유 링크 만들기·복사·중지 */
export function ShareModal({ modelId, onClose }: { modelId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [days, setDays] = useState<string>("90");
  const [warning, setWarning] = useState<string | null>(null);
  const key = ["shares", modelId];
  const { data: shares } = useQuery({ queryKey: key, queryFn: () => get<Share[]>(`/api/models/${modelId}/shares`) });
  const url = (t: string) => `${location.origin}/s/${t}`;
  const create = useMutation({
    mutationFn: () => post<Share>(`/api/models/${modelId}/shares`, { note, expires_days: days ? Number(days) : null }),
    onSuccess: async (s) => { qc.invalidateQueries({ queryKey: key }); setNote(""); setWarning(s.warning ?? null); toast((await copyText(url(s.token))) ? "링크를 만들고 복사했습니다" : "링크를 만들었습니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  const stop = useMutation({
    mutationFn: (t: string) => del(`/api/shares/${t}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: key }); toast("공유를 중지했습니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <Modal title="Simulator 공유" wide onClose={onClose}>
      <p className="small muted" style={{ marginBottom: 12 }}>링크를 받은 사내 사용자는 로그인만 하면 프로젝트 멤버가 아니어도 이 Simulator로 예측할 수 있습니다. 처음 열면 그 사람의 Simulator 목록에 들어옵니다. 링크는 항상 현재 버전을 보여 주고, 프로젝트의 대화·데이터·모델 파일은 보이지 않습니다.</p>
      {warning && <div className="notice warn share-warning" role="status"><span>{warning}</span></div>}
      <div className="row" style={{ marginBottom: 16 }}>
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} placeholder="메모 (예: 설계팀 공유)" style={{ flex: 1 }} aria-label="공유 메모" />
        <select value={days} onChange={(e) => setDays(e.target.value)} style={{ width: "auto" }} aria-label="공유 기간">
          <option value="30">30일</option><option value="90">90일</option><option value="365">1년</option><option value="">기간 없음</option>
        </select>
        <button className="primary" onClick={() => create.mutate()} disabled={create.isPending}><Link2 size={14} />링크 만들기</button>
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>메모</th><th>만든 날</th><th>기간</th><th className="r">사용</th><th>상태</th><th /></tr></thead>
        <tbody>{(shares ?? []).map((s) => (
          <tr key={s.id}>
            <td>{s.note || "-"}</td><td>{when(s.created_at)}</td><td>{s.expires_at ? when(s.expires_at) + "까지" : "없음"}</td>
            <td className="r">{s.uses}</td>
            <td>{s.enabled ? <span className="chip ok">공유 중</span> : <span className="chip">중지됨</span>}</td>
            <td className="r">{s.enabled && <>
              <button className="icon-btn" title="링크 복사" aria-label="링크 복사" onClick={async () => toast((await copyText(url(s.token))) ? "링크를 복사했습니다" : "복사하지 못했습니다")}><Copy size={13} /></button>
              <button className="icon-btn danger" title="공유 중지" aria-label="공유 중지" onClick={() => stop.mutate(s.token)}><Trash2 size={13} /></button>
            </>}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </Modal>
  );
}
