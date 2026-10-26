import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Link2, RotateCcw, Share2, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { del, get, post } from "../api";
import { Field3DPanel } from "../components/Field3DPanel";
import { Modal } from "../components/Modal";
import { baseLayout, Plot, plotConfig } from "../components/Plot";
import { copyText, fmt, when } from "../format";
import { useTheme } from "../theme";
import { useToast } from "../toast";
import type { Objective, SurrogateModel, Variable } from "../types";

interface Spec { model: SurrogateModel; variables: Variable[]; objectives: Objective[]; study_name: string; shared_by?: string; note?: string }
interface Pred { mean: Record<string, number>; std: Record<string, number> }
interface Batch { mean: Record<string, number[]>; std: Record<string, number[]> }
interface Share { id: number; token: string; note: string; enabled: boolean; uses: number; expires_at: string | null; created_at: string }
const SWEEP_N = 41;

/** 배포된 예측 모델 화면 (프로젝트 멤버용) */
export default function SimulatorPage() {
  const mid = Number(useParams().mid);
  return <SimulatorView base={`/api/models/${mid}`} specPath={`/api/models/${mid}/spec`} modelId={mid}
    fieldList={(s) => `/api/studies/${s.model.study_id}/field-models`} fieldBase={(id) => `/api/field-models/${id}`} />;
}

/** 공유 링크로 연 Simulator (프로젝트 멤버가 아니어도 로그인한 사내 사용자면 사용) */
export function SharedSimulatorPage() {
  const token = useParams().token!;
  return <SimulatorView base={`/api/shares/${token}`} specPath={`/api/shares/${token}`}
    fieldList={() => `/api/shares/${token}/field-models`} fieldBase={(id) => `/api/shares/${token}/field-models/${id}`} />;
}

/** 해석 없이 설계변수를 바꿔 결과를 실시간 예측 (± 불확실도) + 민감도 곡선 */
function SimulatorView({ base, specPath, modelId, fieldList, fieldBase }: {
  base: string; specPath: string; modelId?: number; fieldList: (s: Spec) => string; fieldBase: (id: number) => string;
}) {
  const { theme } = useTheme();
  const [sharing, setSharing] = useState(false);
  const { data: spec, error } = useQuery({ queryKey: ["sim-spec", specPath], queryFn: () => get<Spec>(specPath) });
  const mid0 = useMemo(() => Object.fromEntries((spec?.variables ?? []).map((v) => [v.name, (v.lower + v.upper) / 2])), [spec]);
  const [x, setX] = useState<Record<string, number>>({});
  const [pred, setPred] = useState<Pred | null>(null);
  const [sweepVar, setSweepVar] = useState<string | null>(null);
  const [sweep, setSweep] = useState<Batch | null>(null);
  useEffect(() => { if (spec) { setX(mid0); setSweepVar(spec.variables[0]?.name ?? null); } }, [spec, mid0]);

  // 입력이 바뀌면 바로 예측 (짧게 모아서)
  useEffect(() => {
    if (!spec || !Object.keys(x).length) return;
    const t = setTimeout(() => {
      post<Pred>(`${base}/predict`, { params: x }).then(setPred).catch(() => {});
      const v = spec.variables.find((q) => q.name === sweepVar);
      if (v) {
        const pts = Array.from({ length: SWEEP_N }, (_, i) => ({ ...x, [v.name]: v.lower + ((v.upper - v.lower) * i) / (SWEEP_N - 1) }));
        post<Batch>(`${base}/predict_batch`, { points: pts }).then(setSweep).catch(() => {});
      }
    }, 120);
    return () => clearTimeout(t);
  }, [x, spec, base, sweepVar]);

  if (error) return <div className="page"><div className="notice err">{(error as Error).message}</div></div>;
  if (!spec) return <div className="page"><div className="busy"><span className="spinner" /> 불러오는 중</div></div>;
  const outs = spec.model.outputs;
  const v = spec.variables.find((q) => q.name === sweepVar);
  const grid = theme === "dark" ? "#2a2a30" : "#ececef";
  const txt = theme === "dark" ? "#a1a1aa" : "#71717a";

  return (
    <div className="page">
      <div className="crumb"><Link to="/">홈</Link> / Simulator{spec.shared_by ? " · 공유받음" : ""}</div>
      <div className="proj-head">
        <div className="grow"><h1>{spec.study_name} Simulator</h1>
          <div className="proj-sub">{spec.shared_by ? `${spec.shared_by}님이 공유${spec.note ? ` · ${spec.note}` : ""} · ` : ""}
            {spec.model.name} · 학습 {spec.model.n_train}건 · 해석 없이 즉시 예측합니다 (± 는 예측 불확실도)</div></div>
        {modelId && <button onClick={() => setSharing(true)}><Share2 size={14} />공유</button>}
      </div>

      <div className="sim" style={{ marginTop: 22 }}>
        <div className="sim-left">
        <section className="block sim-inputs">
          <div className="block-head"><h3>설계변수</h3><span className="grow" />
            <button className="small ghost" onClick={() => setX(mid0)}><RotateCcw size={13} />가운데로</button></div>
          {spec.variables.map((q) => (
            <div key={q.name} className="slider-row">
              <span title={`${q.lower} ~ ${q.upper}`}>{q.name}</span>
              <input type="range" min={q.lower} max={q.upper} step={(q.upper - q.lower) / 200} value={x[q.name] ?? q.lower}
                onChange={(e) => setX({ ...x, [q.name]: Number(e.target.value) })} aria-label={q.name} />
              <input type="number" inputMode="decimal" value={x[q.name] !== undefined ? Number(x[q.name].toPrecision(5)) : ""}
                min={q.lower} max={q.upper} onChange={(e) => setX({ ...x, [q.name]: Number(e.target.value) })} aria-label={`${q.name} 값`} />
            </div>
          ))}
        </section>
          <Field3DPanel listUrl={fieldList(spec)} baseUrl={fieldBase} params={x} />
        </div>

        <section className="block">
          <div className="lbl"><Sparkles size={13} />예측 결과</div>
          <div className="cond-big">
            {outs.map((o) => {
              const obj = spec.objectives.find((q) => q.name === o);
              return (
                <div key={o}><span>{o}{obj ? ` (${obj.goal === "min" ? "최소화" : "최대화"} 목표)` : ""}</span>
                  <b>{pred ? fmt(pred.mean[o], 4) : "…"}</b>
                  {pred && <small>± {fmt(2 * pred.std[o], 2)}</small>}</div>
              );
            })}
          </div>
          <div className="row" style={{ marginTop: 20 }}>
            <span className="small muted">민감도</span>
            <span className="seg">{spec.variables.map((q) => <button key={q.name} className={sweepVar === q.name ? "on" : ""} onClick={() => setSweepVar(q.name)}>{q.name}</button>)}</span>
          </div>
          {sweep && v && outs.map((o) => {
            const xs = Array.from({ length: SWEEP_N }, (_, i) => v.lower + ((v.upper - v.lower) * i) / (SWEEP_N - 1));
            const m = sweep.mean[o], s = sweep.std[o];
            return (
              <Plot key={o} config={plotConfig} style={{ width: "100%" }} useResizeHandler
                data={[
                  { x: [...xs, ...xs.slice().reverse()], y: [...m.map((a, i) => a + 2 * s[i]), ...m.map((a, i) => a - 2 * s[i]).reverse()],
                    fill: "toself", fillcolor: "rgba(100,116,139,0.15)", line: { width: 0 }, hoverinfo: "skip", showlegend: false, type: "scatter" },
                  { x: xs, y: m, type: "scatter", mode: "lines", line: { color: "#2563eb", width: 2 }, name: o },
                  { x: [x[v.name]], y: [pred?.mean[o]], type: "scatter", mode: "markers", marker: { color: "#dc2626", size: 9 }, name: "현재" },
                ]}
                layout={{ ...baseLayout, height: 220, autosize: true, showlegend: false, font: { ...baseLayout.font, color: txt },
                  xaxis: { title: { text: v.name }, gridcolor: grid, color: txt }, yaxis: { title: { text: o }, gridcolor: grid, color: txt } }} />
            );
          })}
        </section>
      </div>
      {sharing && modelId && <ShareModal modelId={modelId} onClose={() => setSharing(false)} />}
    </div>
  );
}

/** 공유 링크 만들기·복사·중지 */
function ShareModal({ modelId, onClose }: { modelId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [days, setDays] = useState<string>("90");
  const key = ["shares", modelId];
  const { data: shares } = useQuery({ queryKey: key, queryFn: () => get<Share[]>(`/api/models/${modelId}/shares`) });
  const url = (t: string) => `${location.origin}/s/${t}`;
  const create = useMutation({
    mutationFn: () => post<Share>(`/api/models/${modelId}/shares`, { note, expires_days: days ? Number(days) : null }),
    onSuccess: async (s) => { qc.invalidateQueries({ queryKey: key }); setNote(""); toast((await copyText(url(s.token))) ? "링크를 만들고 복사했습니다" : "링크를 만들었습니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  const stop = useMutation({
    mutationFn: (t: string) => del(`/api/shares/${t}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: key }); toast("공유를 중지했습니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <Modal title="Simulator 공유" wide onClose={onClose}>
      <p className="small muted" style={{ marginBottom: 12 }}>링크를 받은 사내 사용자는 로그인만 하면 프로젝트 멤버가 아니어도 이 Simulator로 예측할 수 있습니다. 프로젝트의 대화·데이터·모델 파일은 보이지 않습니다.</p>
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
