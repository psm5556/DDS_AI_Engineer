import { useQuery } from "@tanstack/react-query";
import { RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, post } from "../api";
import { baseLayout, Plot, plotConfig } from "../components/Plot";
import { fmt } from "../format";
import { useTheme } from "../theme";
import type { Objective, SurrogateModel, Variable } from "../types";

interface Spec { model: SurrogateModel; variables: Variable[]; objectives: Objective[]; study_name: string }
interface Pred { mean: Record<string, number>; std: Record<string, number> }
interface Batch { mean: Record<string, number[]>; std: Record<string, number[]> }
const SWEEP_N = 41;

/** 배포된 예측 모델: 해석 없이 설계변수를 바꿔 결과를 실시간 예측 (± 불확실도) */
export default function SimulatorPage() {
  const mid = Number(useParams().mid);
  const { theme } = useTheme();
  const { data: spec, error } = useQuery({ queryKey: ["model-spec", mid], queryFn: () => get<Spec>(`/api/models/${mid}/spec`) });
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
      post<Pred>(`/api/models/${mid}/predict`, { params: x }).then(setPred).catch(() => {});
      const v = spec.variables.find((q) => q.name === sweepVar);
      if (v) {
        const pts = Array.from({ length: SWEEP_N }, (_, i) => ({ ...x, [v.name]: v.lower + ((v.upper - v.lower) * i) / (SWEEP_N - 1) }));
        post<Batch>(`/api/models/${mid}/predict_batch`, { points: pts }).then(setSweep).catch(() => {});
      }
    }, 120);
    return () => clearTimeout(t);
  }, [x, spec, mid, sweepVar]);

  if (error) return <div className="page"><div className="notice err">{(error as Error).message}</div></div>;
  if (!spec) return <div className="page"><div className="busy"><span className="spinner" /> 불러오는 중</div></div>;
  const outs = spec.model.outputs;
  const v = spec.variables.find((q) => q.name === sweepVar);
  const grid = theme === "dark" ? "#2a2a30" : "#ececef";
  const txt = theme === "dark" ? "#a1a1aa" : "#71717a";

  return (
    <div className="page">
      <div className="crumb"><Link to="/">프로젝트</Link> / Simulator</div>
      <div className="proj-head"><div className="grow"><h1>{spec.study_name} Simulator</h1>
        <div className="proj-sub">{spec.model.name} · 학습 {spec.model.n_train}건 · 해석 없이 즉시 예측합니다 (± 는 예측 불확실도)</div></div></div>

      <div className="sim" style={{ marginTop: 22 }}>
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

        <section className="block">
          <div className="lbl"><Sparkles size={13} />예측 결과</div>
          <div className="cond-big">
            {outs.map((o) => (
              <div key={o}><span>{o}{spec.objectives.find((q) => q.name === o) ? ` (${spec.objectives.find((q) => q.name === o)!.goal === "min" ? "최소화" : "최대화"} 목표)` : ""}</span>
                <b>{pred ? fmt(pred.mean[o], 4) : "…"}</b>
                {pred && <small>± {fmt(2 * pred.std[o], 2)}</small>}</div>
            ))}
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
    </div>
  );
}
