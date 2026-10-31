import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { get } from "../api";
import { baseLayout, chartColors, Plot, plotConfig } from "../components/Plot";
import { fmt, sendToAgent } from "../format";
import { useTheme } from "../theme";
import type { InsightChart, InsightPick, InsightSection, InsightVariant, StudyInsightsData } from "../types";

/** 스터디 판단 도우미: 전문 지식이 없어도 첫 줄만 읽고 판단하도록 질문 → 답(상태 말) → 한 문장 → 할 일 순서로 보여 준다.
 *  그림은 눌러서 펼칠 때만 그린다. 계산과 문장은 모두 서버(study/insights.py)에 있어 대화·보고서와 숫자가 같다. */
const CHIP: Record<string, string> = { good: "ok", fair: "warn", caution: "err", unknown: "" };
const usePlot = () => { const { theme } = useTheme(); return useMemo(() => chartColors(), [theme]); }; // eslint-disable-line react-hooks/exhaustive-deps
/** 표로 불러온 값은 '해석값'이 아니다. 그림의 글자도 서버 문장과 같은 낱말을 쓴다 */
const Word = createContext("해석");
/** 값의 출처가 숫자를 읽는 법을 바꾸는 항목에만 적는다. 입력 위치만 보여 주는 항목에 '해석으로 확인한 값'이라고 쓰면 틀린 말이다 */
const SHOW_BASIS = new Set(["influence", "trust", "optimum", "pareto", "active"]);

/** Simulator의 '자세히'가 데이터 탭을 열면서 어느 항목을 펼칠지 남긴다. 탭이 바뀌어야 이 화면이 그려지므로 값으로 넘긴다 */
let focusKey: string | null = null;
export function askInsightFocus(key: string) { focusKey = key; }

function Chart({ data, layout, height = 260 }: { data: Record<string, unknown>[]; layout?: Record<string, unknown>; height?: number }) {
  const t = usePlot();
  return (
    <Plot data={data} config={plotConfig} style={{ width: "100%" }} useResizeHandler
      layout={{ ...baseLayout, height, autosize: true, showlegend: data.length > 1, legend: { orientation: "h", y: -0.28 }, font: { ...baseLayout.font, color: t.text }, ...layout }} />
  );
}
const axis = (t: ReturnType<typeof chartColors>, title?: string, extra?: Record<string, unknown>) =>
  ({ title: { text: title ?? "", font: { size: 11 } }, gridcolor: t.grid, zerolinecolor: t.grid, color: t.text, automargin: true, ...extra });
const titled = (label: string, unit?: string) => (unit ? `${label} [${unit}]` : label);
/** 변수 범위보다 조금 넓게 그린다. 범위 끝의 점과 번호가 잘리지 않게 */
const padded = (v: { lower: number; upper: number }) => { const p = (v.upper - v.lower) * 0.05; return [v.lower - p, v.upper + p]; };
/** 표의 숫자: 유효숫자 3자리. 지수 표기(1.00e-3)는 읽기 어렵다 */
const plain = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? Number(v.toPrecision(3)).toLocaleString("en-US", { maximumFractionDigits: 8 }) : fmt(v));

/** 한 번 더 눌러야 보이는 그림. 접혀 있으면 그리지 않는다 */
function Fold({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="insight-fold">
      <button className="small ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{label}</button>
      {open && children}
    </div>
  );
}

/* ---------- 변수쌍 ---------- */
type Pt = { id: number; x: number[] };
interface VarRow { name: string; label: string; unit?: string; lower: number; upper: number }
interface PairsData { variables: VarRow[]; groups: { base: Pt[]; opt: Pt[]; al: Pt[]; failed: Pt[]; waiting: Pt[] }; thinned?: boolean }
const GROUP_LABEL: Record<string, string> = { base: "처음 계획한 점", opt: "최적화로 더한 점", al: "정확도를 높이려고 더한 점", failed: "실패", waiting: "아직 해석 안 함" };
const groupColors = (t: ReturnType<typeof chartColors>): Record<string, string> => ({ base: t.mean, opt: t.brand, al: t.series[2], failed: t.err, waiting: t.muted });

function PairsChart({ c }: { c: PairsData }) {
  const t = usePlot();
  const color = groupColors(t);
  const vars = c.variables;
  const present = (Object.keys(GROUP_LABEL) as (keyof PairsData["groups"])[]).filter((g) => c.groups[g].length);
  const [pair, setPair] = useState<[number, number]>([0, Math.min(1, vars.length - 1)]);
  if (vars.length <= 5) {
    const data = present.map((g) => ({
      type: "splom", name: GROUP_LABEL[g], showupperhalf: false, diagonal: { visible: false },
      dimensions: vars.map((v, i) => ({ label: titled(v.label, v.unit), values: c.groups[g].map((p) => p.x[i]) })),
      text: c.groups[g].map((p) => `케이스 ${p.id}`),
      marker: { color: color[g], size: 5, opacity: 0.85, symbol: g === "failed" ? "x" : "circle" },
    }));
    const n = vars.length - 1;
    const layoutAxes = Object.fromEntries(Array.from({ length: vars.length }, (_, i) => [
      [`xaxis${i ? i + 1 : ""}`, { gridcolor: t.grid, zerolinecolor: t.grid, color: t.text, tickfont: { size: 9 } }],
      [`yaxis${i ? i + 1 : ""}`, { gridcolor: t.grid, zerolinecolor: t.grid, color: t.text, tickfont: { size: 9 } }],
    ]).flat());
    return <Chart data={data} height={Math.max(260, n * 130 + 40)} layout={{ margin: { l: 56, r: 12, t: 12, b: 56 }, ...layoutAxes }} />;
  }
  const [a, b] = pair;
  return (
    <>
      <div className="insight-row small">
        <label>가로 <select value={a} aria-label="가로 변수" onChange={(e) => setPair([Number(e.target.value), b])}>{vars.map((v, i) => <option key={v.name} value={i}>{v.label}</option>)}</select></label>
        <label>세로 <select value={b} aria-label="세로 변수" onChange={(e) => setPair([a, Number(e.target.value)])}>{vars.map((v, i) => <option key={v.name} value={i}>{v.label}</option>)}</select></label>
      </div>
      <Chart data={present.map((g) => ({ type: "scatter", mode: "markers", name: GROUP_LABEL[g], x: c.groups[g].map((p) => p.x[a]), y: c.groups[g].map((p) => p.x[b]),
        text: c.groups[g].map((p) => `케이스 ${p.id}`), marker: { color: color[g], size: 7, opacity: 0.85, symbol: g === "failed" ? "x" : "circle" } }))}
        layout={{ xaxis: axis(t, titled(vars[a].label, vars[a].unit), { range: padded(vars[a]) }), yaxis: axis(t, titled(vars[b].label, vars[b].unit), { range: padded(vars[b]) }) }} />
    </>
  );
}

/** 변수별 줄무늬: 범위를 0~100%로 맞춰 어느 구간이 비었는지 한눈에 본다 */
function StripChart({ c }: { c: PairsData }) {
  const t = usePlot();
  const color = groupColors(t);
  const present = (Object.keys(GROUP_LABEL) as (keyof PairsData["groups"])[]).filter((g) => c.groups[g].length);
  const labels = c.variables.map((v) => v.label);
  const pos = (i: number, x: number) => ((x - c.variables[i].lower) / (c.variables[i].upper - c.variables[i].lower || 1)) * 100;
  const data = present.map((g) => ({
    type: "scatter", mode: "markers", name: GROUP_LABEL[g], hoverinfo: "text",
    x: c.groups[g].flatMap((p) => p.x.map((v, i) => pos(i, v))), y: c.groups[g].flatMap((p) => p.x.map((_, i) => labels[i])),
    text: c.groups[g].flatMap((p) => p.x.map((v, i) => `${labels[i]} ${fmt(v)}${c.variables[i].unit ? ` ${c.variables[i].unit}` : ""} · 케이스 ${p.id}`)),
    marker: { color: color[g], size: 10, opacity: 0.6, symbol: "line-ns", line: { width: 1.5, color: color[g] } },
  }));
  // 축 이름과 겹치지 않게 범례를 더 아래에 둔다
  return <Chart data={data} height={Math.max(120, labels.length * 34 + 60) + 44} layout={{ showlegend: data.length > 1, legend: { orientation: "h", y: -0.5 }, xaxis: axis(t, "범위 안 위치 (왼쪽 끝 = 하한, 오른쪽 끝 = 상한)", { range: [-2, 102], ticksuffix: "%" }), yaxis: axis(t, "", { type: "category", autorange: "reversed" }) }} />;
}

/* ---------- 결과 분포 ---------- */
function DotsChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const word = useContext(Word);
  const outputs = c.outputs as Record<string, { label: string; unit?: string; points: [number, number][] }>;
  const names = Object.keys(outputs);
  const [cur, setCur] = useState(names[0]);
  const o = outputs[cur] ?? outputs[names[0]];
  const flagged = new Set<number>(c.outliers ?? []);
  const ok = o.points.filter((p) => !flagged.has(p[0]));
  const bad = o.points.filter((p) => flagged.has(p[0]));
  return (
    <>
      {names.length > 1 && <span className="seg">{names.map((n) => <button key={n} className={n === cur ? "on" : ""} onClick={() => setCur(n)}>{outputs[n].label}</button>)}</span>}
      <Chart height={210} data={[
        { type: "scatter", mode: "markers", name: `${word}값`, x: ok.map((p) => p[0]), y: ok.map((p) => p[1]), marker: { color: t.mean, size: 6, opacity: 0.8 } },
        ...(bad.length ? [{ type: "scatter", mode: "markers", name: "많이 다른 값", x: bad.map((p) => p[0]), y: bad.map((p) => p[1]), marker: { color: t.err, size: 9 } }] : []),
      ]} layout={{ xaxis: axis(t, "케이스 번호"), yaxis: axis(t, titled(o.label, o.unit)) }} />
    </>
  );
}

/* ---------- 영향도 ---------- */
function InfluenceChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const bars = c.bars as { name: string; label: string; share: number }[];
  const weak = !!c.weak;
  const [pick, setPick] = useState<string>(bars[0]?.name);
  const e = (c.effects as Record<string, { x: number[]; mean: number[]; sd: number[]; unit?: string; seen?: [number, number]; caption?: string }>)[pick];
  const label = bars.find((b) => b.name === pick)?.label ?? pick;
  const pct = (v: number) => `${Math.max(0, Math.min(100, v * 100))}%`;
  // 해석이 없는 구간은 예측으로 이어 그린 것이다. 그림에서도 나눠 보여 준다
  const xs = e?.x ?? [];
  const unseen = e?.seen ? [[xs[0], e.seen[0]], [e.seen[1], xs[xs.length - 1]]].filter(([a, b]) => b - a > (xs[xs.length - 1] - xs[0]) * 0.1) : [];
  return (
    <>
      {weak && <p className="small muted">예측이 약해 믿기 어려운 순위입니다. 참고로만 보세요.</p>}
      <ul className={`insight-bars ${weak ? "weak" : ""}`}>{bars.map((b) => (
        <li key={b.name}><button className={`insight-bar ${b.name === pick ? "on" : ""}`} onClick={() => setPick(b.name)} aria-pressed={b.name === pick}>
          <span>{b.label}</span>
          <span className="insight-track"><span className="insight-fill" style={{ width: pct(b.share) }} /></span>
          <span className="r">{weak ? "" : `${Math.round(b.share * 100)}%`}</span>
        </button></li>))}</ul>
      {e && (
        <>
          <p className="small muted">{e.caption ?? label}</p>
          <Chart height={220} data={[
            { type: "scatter", mode: "lines", x: e.x, y: e.mean.map((m, i) => m + 2 * e.sd[i]), line: { width: 0 }, hoverinfo: "skip", showlegend: false },
            { type: "scatter", mode: "lines", name: "AI 예측이 확신하지 못하는 폭", x: e.x, y: e.mean.map((m, i) => m - 2 * e.sd[i]), fill: "tonexty", fillcolor: t.band, line: { width: 0 }, hoverinfo: "skip" },
            { type: "scatter", mode: "lines", name: "예측", x: e.x, y: e.mean, line: { color: t.mean, width: 2 } },
          ]} layout={{ xaxis: axis(t, titled(label, e.unit)), yaxis: axis(t, `${titled(c.output, c.unit)} (예측, 다른 변수 평균)`),
            shapes: unseen.map(([a, b]) => ({ type: "rect", xref: "x", yref: "paper", x0: a, x1: b, y0: 0, y1: 1, fillcolor: t.grid, opacity: 0.35, line: { width: 0 }, layer: "below" })),
            annotations: unseen.map(([a, b]) => ({ x: (a + b) / 2, y: 1, yref: "paper", text: "해석 없음", showarrow: false, font: { size: 10, color: t.text } })) }} />
        </>
      )}
    </>
  );
}

/* ---------- 예측 정확도 ---------- */
function PvaChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const word = useContext(Word);
  const pts = c.points as [number | null, number, number, boolean?][];
  const all = pts.flatMap((p) => [p[1], p[2]]);
  const lo = Math.min(...all), hi = Math.max(...all);
  const worst = (c.worst ?? []) as { case_id: number | null; actual: number; predicted: number; error: number }[];
  const shown = pts.filter((p) => p[3] !== false);
  const left = pts.filter((p) => p[3] === false);
  return (
    <>
      <Chart height={240} data={[
        { type: "scatter", mode: "markers", name: "학습에 안 쓰고 맞춰 본 값", x: shown.map((p) => p[1]), y: shown.map((p) => p[2]), text: shown.map((p) => (p[0] ? `케이스 ${p[0]}` : "")), marker: { color: t.mean, size: 6, opacity: 0.8 } },
        ...(left.length ? [{ type: "scatter", mode: "markers", name: "최적화로 더한 점 (정확도 계산에서 뺌)", x: left.map((p) => p[1]), y: left.map((p) => p[2]), text: left.map((p) => (p[0] ? `케이스 ${p[0]}` : "")), marker: { color: t.muted, size: 5, opacity: 0.6 } }] : []),
        { type: "scatter", mode: "lines", name: "완전히 맞는 선", x: [lo, hi], y: [lo, hi], line: { color: t.muted, dash: "dash", width: 1 }, hoverinfo: "skip" },
      ]} layout={{ xaxis: axis(t, `${word}값${c.unit ? ` [${c.unit}]` : ""}`), yaxis: axis(t, `예측값${c.unit ? ` [${c.unit}]` : ""}`) }} />
      {worst.length > 0 && (
        <div className="table-wrap insight-table"><table>
          <thead><tr><th>가장 많이 틀린 케이스</th><th className="r">{word}값</th><th className="r">예측</th><th className="r">차이</th></tr></thead>
          <tbody>{worst.map((w, i) => <tr key={i}><td>{w.case_id ? `케이스 ${w.case_id}` : "-"}</td><td className="r">{plain(w.actual)}</td><td className="r">{plain(w.predicted)}</td><td className="r">{plain(w.error)}</td></tr>)}</tbody>
        </table></div>
      )}
    </>
  );
}

function PriorTable({ c }: { c: InsightChart }) {
  const word = useContext(Word);
  const rows = c.rows as { case_id: number; actual: number; predicted: number; std: number }[];
  return (
    <div className="table-wrap insight-table"><table>
      <thead><tr><th>최근 케이스</th><th className="r">돌리기 전 예측(AI가 말한 범위)</th><th className="r">{word}값</th><th className="r">차이</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.case_id}><td>케이스 {r.case_id}</td><td className="r">{plain(r.predicted)} ± {plain(2 * r.std)}</td><td className="r">{plain(r.actual)}</td><td className="r">{plain(Math.abs(r.actual - r.predicted))}</td></tr>)}</tbody>
    </table></div>
  );
}

/* ---------- 수렴 ---------- */
const startLine = (t: ReturnType<typeof chartColors>, from?: number | null) => ({
  shapes: from ? [{ type: "line", x0: from - 0.5, x1: from - 0.5, yref: "paper", y0: 0, y1: 1, line: { color: t.muted, width: 1, dash: "dot" } }] : [],
  annotations: from ? [{ x: from - 0.5, y: 1, yref: "paper", text: "여기부터 최적화", showarrow: false, xanchor: "left", font: { size: 10, color: t.text } }] : [],
});

function CurveChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const word = useContext(Word);
  const idx = c.index as number[];
  const pick = (ok: boolean) => idx.filter((_: number, i: number) => (c.feasible as boolean[])[i] === ok);
  const vals = (ok: boolean) => (c.values as number[]).filter((_, i) => (c.feasible as boolean[])[i] === ok);
  return (
    <Chart data={[
      { type: "scatter", mode: "markers", name: `케이스별 ${word}값`, x: pick(true), y: vals(true), marker: { color: t.text, size: 5, opacity: 0.7 } },
      ...(pick(false).length ? [{ type: "scatter", mode: "markers", name: "제약을 어긴 값(최고 값에서 뺌)", x: pick(false), y: vals(false), marker: { color: t.err, size: 6, symbol: "x", opacity: 0.7 } }] : []),
      { type: "scatter", mode: "lines", name: "지금까지 가장 좋은 값", x: idx, y: c.best, line: { color: t.brand, width: 2, shape: "hv" } },
    ]} layout={{ xaxis: axis(t, `${word} 순서`), yaxis: axis(t, titled(c.output, c.unit)), ...startLine(t, c.bo_from) }} />
  );
}

function HvChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const word = useContext(Word);
  // 처음 해석에서 크게 뛴 값이 축을 차지하면 최적화 구간이 평평해 보여 위 문장과 어긋난다. 최적화 구간을 확대해 그린다
  const from = c.bo_from as number | null;
  return <Chart height={220} data={[{ type: "scatter", mode: "lines", name: "종합 점수", x: c.index, y: c.hv, line: { color: t.brand, width: 2, shape: "hv" } }]}
    layout={{ showlegend: false, xaxis: axis(t, `${word} 순서`, from ? { range: [Math.max(0.5, from - 2), (c.index as number[]).length + 0.5] } : undefined),
      yaxis: axis(t, c.axis, c.zoom ? { range: c.zoom } : undefined), ...startLine(t, from) }} />;
}

/* ---------- 후보 표 ---------- */
function CandidatesTable({ c }: { c: InsightChart }) {
  const vars = c.variables as { name: string; label: string; unit?: string }[];
  const rows = c.rows as { kind: string; case_id: number | null; params: Record<string, number>; value: number; margin: string | null }[];
  const showMargin = rows.some((r) => r.margin);
  return (
    <div className="table-wrap insight-table"><table>
      <thead><tr><th>구분</th>{vars.map((v) => <th key={v.name} className="r">{titled(v.label, v.unit)}</th>)}<th className="r">{titled(c.output, c.unit)}</th>{showMargin && <th>제약 여유</th>}</tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i}><td className="nowrap">{r.kind === "predicted" ? <span className="chip info">예측</span> : <>케이스 {r.case_id}</>}</td>
          {vars.map((v) => <td key={v.name} className="r">{plain(r.params[v.name])}</td>)}
          <td className="r">{plain(r.value)}</td>{showMargin && <td>{r.margin ?? ""}</td>}</tr>))}</tbody>
    </table></div>
  );
}

/** 좋은 후보의 실제 값. 그림의 점만으로는 어떤 설계인지 알 수 없다 */
function FrontTable({ c }: { c: InsightChart }) {
  const goals = c.goals as { name: string; label: string; unit?: string }[];
  const vars = c.variables as { name: string; label: string; unit?: string }[];
  const rows = c.rows as { case_id: number; goals: Record<string, number>; params: Record<string, number> }[];
  const word = useContext(Word);
  return (
    <>
      <div className="table-wrap insight-table"><table>
        <thead><tr><th>좋은 후보 ({word === "해석" ? "해석 확인" : "데이터 값"})</th>{goals.map((g) => <th key={g.name} className="r">{titled(g.label, g.unit)}</th>)}{vars.map((v) => <th key={v.name} className="r">{titled(v.label, v.unit)}</th>)}</tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.case_id}><td className="nowrap">케이스 {r.case_id}{r.case_id === c.knee && <> <span className="chip info">균형 제안</span></>}</td>
            {goals.map((g) => <td key={g.name} className="r">{plain(r.goals[g.name])}</td>)}
            {vars.map((v) => <td key={v.name} className="r">{plain(r.params[v.name])}</td>)}</tr>))}</tbody>
      </table></div>
      {(c.total as number) > rows.length && <p className="small muted">후보 {c.total}개 중 고르게 {rows.length}개만 보여 줍니다.</p>}
    </>
  );
}

/* ---------- 두 목표 ---------- */
function ParetoChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const word = useContext(Word);
  const all = c.all as [number, number, number, boolean][];
  const front = c.front as [number, number, number][];
  const pred = c.predicted as [number, number][];
  const knee = c.knee as { case_id: number; a: number; b: number } | null;
  const arrow = (g: string) => (g === "min" ? "작을수록 좋음" : "클수록 좋음");
  // 계단선은 앞면을 첫 값 순서로 잇는다. 목표 방향에 따라 꺾이는 쪽이 달라지므로 뒤쪽 값 기준으로 이어 그린다
  const sorted = [...front].sort((p, q) => p[1] - q[1]);
  // 전체 점에 맞추면 먼 점 때문에 후보가 한 줄로 눌린다. 후보 쪽을 확대하고 더블 클릭으로 전체를 보게 한다
  const near = (vals: number[]) => {
    if (!vals.length) return undefined;
    const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo || Math.abs(hi) || 1) * 0.25;
    return { range: [lo - pad, hi + pad] };
  };
  const xr = near([...front.map((p) => p[1]), ...pred.map((p) => p[0])]);
  const yr = near([...front.map((p) => p[2]), ...pred.map((p) => p[1])]);
  const outside = all.filter((p) => (xr && (p[1] < xr.range[0] || p[1] > xr.range[1])) || (yr && (p[2] < yr.range[0] || p[2] > yr.range[1]))).length;
  return (
    <>
    <Chart height={300} data={[
      { type: "scatter", mode: "markers", name: word === "해석" ? "해석한 설계" : "데이터의 설계", x: all.map((p) => p[1]), y: all.map((p) => p[2]), text: all.map((p) => `케이스 ${p[0]}${p[3] ? "" : " · 제약 위반 또는 판단 불가"}`), marker: { color: t.text, size: 5, opacity: 0.45 } },
      { type: "scatter", mode: "lines+markers", name: word === "해석" ? "좋은 후보 (해석 확인)" : "좋은 후보 (데이터 값)", x: sorted.map((p) => p[1]), y: sorted.map((p) => p[2]), text: sorted.map((p) => `케이스 ${p[0]}`), line: { color: t.brand, width: 2, shape: c.a.goal === "min" ? "hv" : "vh" }, marker: { color: t.brand, size: 8 } },
      ...(pred.length ? [{ type: "scatter", mode: "markers", name: "예측 후보 (아직 해석 안 함)", x: pred.map((p) => p[0]), y: pred.map((p) => p[1]), marker: { color: "rgba(0,0,0,0)", size: 7, line: { color: t.mean, width: 1.2 } } }] : []),
      ...(knee ? [{ type: "scatter", mode: "markers", name: "균형 설계 (제안)", x: [knee.a], y: [knee.b], text: [`케이스 ${knee.case_id}`], marker: { color: t.mean, size: 13, symbol: "diamond", line: { color: t.ink, width: 1 } } }] : []),
    ]} layout={{ xaxis: axis(t, `${titled(c.a.label, c.a.unit)} (${arrow(c.a.goal)})`, xr), yaxis: axis(t, `${titled(c.b.label, c.b.unit)} (${arrow(c.b.goal)})`, yr), legend: { orientation: "h", y: -0.32 } }} />
    {outside > 0 && <p className="small muted">좋은 후보 쪽을 확대했습니다. {word === "해석" ? "해석한 설계" : "데이터의 설계"} {outside}개는 그림 밖에 있고, 그림을 더블 클릭하면 모두 보입니다.</p>}
    </>
  );
}

/* ---------- 능동학습 ---------- */
function LearningChart({ c }: { c: InsightChart }) {
  const t = usePlot();
  const curve = c.curve as { m: number; rmse: number; r2: number | null }[];
  return (
    <Chart height={220} data={[{ type: "scatter", mode: "lines+markers", name: c.output, x: curve.map((q) => q.m), y: curve.map((q) => q.rmse), line: { color: t.brand, width: 2 }, marker: { color: t.brand, size: 7 } }]}
      layout={{ showlegend: false, xaxis: axis(t, "정확도를 높이려고 더 돌린 해석 건수", { dtick: curve.length > 1 ? undefined : 1 }), yaxis: axis(t, `예측 평균 오차${c.unit ? ` [${c.unit}]` : ""}`, { rangemode: "tozero" }) }} />
  );
}

function AlPoints({ c }: { c: InsightChart }) {
  const t = usePlot();
  const vars = c.variables as VarRow[];
  const [a, setA] = useState(0);
  const [b, setB] = useState(Math.min(1, vars.length - 1));
  const added = c.added as number[][];
  const base = c.base as number[][];
  if (vars.length < 2) return null;
  return (
    <Fold label="점이 간 곳 보기">
      <p className="small muted">번호 순서대로 AI가 고른 곳입니다. 빈 곳을 채웠다면 잘 고른 것입니다.</p>
      {vars.length > 2 && <div className="insight-row small">
        <label>가로 <select value={a} aria-label="가로 변수" onChange={(e) => setA(Number(e.target.value))}>{vars.map((v, i) => <option key={i} value={i}>{v.label}</option>)}</select></label>
        <label>세로 <select value={b} aria-label="세로 변수" onChange={(e) => setB(Number(e.target.value))}>{vars.map((v, i) => <option key={i} value={i}>{v.label}</option>)}</select></label>
      </div>}
      <Chart height={240} data={[
        { type: "scatter", mode: "markers", name: "처음 계획한 점", x: base.map((p) => p[a]), y: base.map((p) => p[b]), marker: { color: t.text, size: 5, opacity: 0.6 } },
        { type: "scatter", mode: "markers+text", name: "더 돌린 점 (순서)", x: added.map((p) => p[a]), y: added.map((p) => p[b]), text: added.map((_, i) => String(i + 1)), textposition: "top center", textfont: { size: 10, color: t.text }, cliponaxis: false,
          marker: { color: t.series[2], size: 9 } },
      ]} layout={{ xaxis: axis(t, titled(vars[a].label, vars[a].unit), { range: padded(vars[a]) }), yaxis: axis(t, titled(vars[b].label, vars[b].unit), { range: padded(vars[b]) }) }} />
    </Fold>
  );
}

function ChartView({ c }: { c: InsightChart }) {
  switch (c.kind) {
    case "pairs": {
      const d = c as unknown as PairsData;
      return <>
        <StripChart c={d} />
        {d.variables.length > 1 && <Fold label="변수를 둘씩 짝지어 보기"><PairsChart c={d} /></Fold>}
        {c.thinned && <p className="small muted">점이 많아 일부만 그렸습니다.</p>}
      </>;
    }
    case "dots": return <DotsChart c={c} />;
    case "influence": return <InfluenceChart c={c} />;
    case "pva": return <PvaChart c={c} />;
    case "prior": return <PriorTable c={c} />;
    case "curve": return <CurveChart c={c} />;
    case "hv": return <HvChart c={c} />;
    case "candidates": return <CandidatesTable c={c} />;
    case "pareto": return <ParetoChart c={c} />;
    case "front_table": return <FrontTable c={c} />;
    case "learning": return <LearningChart c={c} />;
    case "al_points": return <AlPoints c={c} />;
    default: return null;
  }
}

/** 다른 출력과 함께 보기: 고른 두 출력의 후보를 서버가 계산한다.
 *  sameAs가 있으면 그 짝의 판정은 이미 위 첫 줄에 있으므로 되풀이하지 않고, 목표가 정해져 있으니 방향도 묻지 않는다 */
function PairPicker({ id, first, choices, sameAs }: { id: number; first: InsightPick; choices: InsightPick[]; sameAs?: string }) {
  const [b, setB] = useState(choices[0]?.name ?? "");
  const [goal, setGoal] = useState<"min" | "max">(choices[0]?.goal ?? "min");
  const { data, error } = useQuery({
    queryKey: ["study", id, "insights", "pair", b, goal],
    queryFn: () => get<InsightSection>(`/api/studies/${id}/insights/pareto?a=${encodeURIComponent(first.name)}&a_goal=${first.goal}&b=${encodeURIComponent(b)}&b_goal=${goal}`),
    enabled: !!b,
  });
  const repeat = !!sameAs && b === sameAs;
  const charts = (data?.charts ?? []).filter((c) => !(sameAs && c.kind === "front_table"));
  return (
    <>
      <div className="insight-row small">
        <span>함께 볼 결과:</span>
        <select value={b} aria-label="함께 볼 결과" onChange={(e) => { setB(e.target.value); setGoal(choices.find((o) => o.name === e.target.value)?.goal ?? "min"); }}>{choices.map((o) => <option key={o.name} value={o.name}>{o.label}</option>)}</select>
        {!sameAs && <span className="seg"><button className={goal === "min" ? "on" : ""} onClick={() => setGoal("min")}>작을수록 좋음</button><button className={goal === "max" ? "on" : ""} onClick={() => setGoal("max")}>클수록 좋음</button></span>}
      </div>
      {error ? <p className="small" style={{ color: "var(--err-ink)" }}>{(error as Error).message}</p> : data && (
        <>
          {!repeat && <p className="small"><span className={`chip ${CHIP[data.state] ?? ""}`}>{data.state_label}</span> {data.sentence}</p>}
          {!repeat && <Notes notes={data.notes} />}
          {charts.map((c, i) => <ChartView key={i} c={c} />)}
          <p className="small insight-how"><b>읽는 법</b> {data.how_to_read}</p>
          {data.next && !repeat && <div><button className="small" onClick={() => sendToAgent(data.next!.request)}>{data.next.label}</button></div>}
        </>
      )}
    </>
  );
}

function Notes({ notes }: { notes: string[] }) {
  return notes.length ? <ul className="insight-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null;
}

function Section({ id, s, open, toggle }: { id: number; s: InsightSection; open: boolean; toggle: () => void }) {
  const variants = s.variants ?? [];
  const [pick, setPick] = useState<string | null>(null);
  const v: InsightVariant | undefined = variants.find((q) => q.name === pick);
  const shown = { state: v?.state ?? s.state, label: v?.state_label ?? s.state_label, sentence: v?.sentence ?? s.sentence, notes: v?.notes ?? s.notes, charts: v?.charts ?? s.charts };
  const next = v ? v.next : s.next;
  const nextAbove = v ? v.next_shown_above : s.next_shown_above;
  const pending = !!s.pending;
  const low = !!s.collapsed || pending;
  const expandable = !pending && (shown.charts.length > 0 || shown.notes.length > 0 || variants.length > 1 || !!s.pair_choices);
  // 세 목표 이상: 그림은 고른 짝을 그리는 아래 선택기가 맡는다. 같은 그림을 두 번 그리지 않는다
  const charts = s.pair_default ? shown.charts.filter((c) => c.kind !== "pareto") : shown.charts;
  return (
    <section id={`insight-${s.key}`} className={`insight-sec ${low ? "low" : ""}`} aria-label={s.title}>
      <button className="insight-head" aria-expanded={open && expandable} disabled={!expandable} onClick={toggle}>
        {expandable ? (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : <span />}
        <span className="insight-q">{s.title}<small>({s.term})</small></span>
        <span className={`chip ${pending ? "info" : CHIP[shown.state] ?? ""}`}>{shown.label}</span>
        <span className="insight-say">{shown.sentence}</span>
      </button>
      {open && expandable && (
        <div className="insight-body">
          {variants.length > 1 && (
            <span className="seg" role="group" aria-label="출력 고르기">{variants.map((q, i) => (
              <button key={q.name} className={(pick ?? s.lead ?? variants[0].name) === q.name || (!pick && !s.lead && i === 0) ? "on" : ""} onClick={() => setPick(q.name)}>{q.label}</button>))}</span>
          )}
          <Notes notes={shown.notes} />
          {charts.map((c, i) => <ChartView key={`${pick}-${c.kind}-${i}`} c={c} />)}
          {s.pair_first && s.pair_choices && <PairPicker id={id} first={s.pair_first} choices={s.pair_choices} sameAs={s.pair_default} />}
          {s.how_to_read && !s.pair_first && <p className="insight-how"><b>읽는 법</b> {s.how_to_read}</p>}
          {SHOW_BASIS.has(s.key) && <p className="small muted">값의 출처: {s.basis_note ?? s.basis_label}</p>}
          {s.bench && <p className="insight-bench">{s.bench}</p>}
          {next && (nextAbove
            ? <p className="small muted">할 일은 위쪽 버튼에 있습니다.</p>
            : <div><button className="small" onClick={() => sendToAgent(next.request)}>{next.label}</button></div>)}
        </div>
      )}
    </section>
  );
}

export function StudyInsights({ id }: { id: number }) {
  const { data, error } = useQuery({
    queryKey: ["study", id, "insights"], queryFn: () => get<StudyInsightsData>(`/api/studies/${id}/insights`),
    refetchInterval: (q) => (q.state.data?.status === "computing" ? 2500 : false),
  });
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const reveal = (key: string) => {
    setOpen((o) => ({ ...o, [key]: true }));
    window.setTimeout(() => document.getElementById(`insight-${key}`)?.scrollIntoView({ block: "start" }), 50);
  };
  const ready = !!data;
  useEffect(() => {
    if (ready && focusKey) { reveal(focusKey); focusKey = null; }
  }, [ready]);
  if (error) return <section className="block"><p className="small" style={{ color: "var(--err-ink)" }}>{(error as Error).message} → 잠시 뒤 다시 열어 보세요</p></section>;
  if (!data) return null;
  const f = data.overview.facts;
  const nx = data.overview.next;
  const word = data.overview.word ?? "해석";
  const mix = [f.base ? `처음 계획 ${f.base}` : "", f.bo ? `최적화 ${f.bo}` : "", f.al ? `정확도 높이기 ${f.al}` : ""].filter(Boolean);
  const done = `${word === "해석" ? `해석 ${f.complete}건 완료` : `데이터 ${f.complete}건`}${mix.length > 1 ? `(${mix.join(" + ")})` : ""}`;
  const parts = [done, f.failed ? `실패 ${f.failed}건` : "", f.waiting ? `대기 ${f.waiting}건` : "", f.run_text ? `해석 시간 합계 ${f.run_text}` : ""].filter(Boolean);
  const watch = data.overview.watch ?? [];
  return (
    <section className="block insights" aria-label="판단 도우미">
      <div className="insight-summary">
        <div className="insight-row">
          <h3 className="grow">지금 어디까지 왔나요?</h3>
          {data.status === "computing" && <span className="chip info"><span className="spinner" style={{ width: 10, height: 10 }} />새 결과로 계산 중</span>}
        </div>
        <span className="insight-facts">{parts.join(" · ")}</span>
        {data.error && <span className="small" style={{ color: "var(--warn-ink)" }}>{data.error} 잠시 뒤 다시 계산합니다.</span>}
        {watch.length > 0 && (
          <div className="insight-watch small"><span>바로 확정하기 전에 볼 것:</span>
            {watch.map((w, i) => <span key={w.key}>{i > 0 && " · "}<button className="trust-more" onClick={() => reveal(w.key)}>{w.text}</button></span>)}
          </div>
        )}
        {nx && (
          <div className="insight-next">
            <span className="grow"><b>다음 할 일</b> {nx.text}</span>
            {nx.label && nx.request && <button className="small primary" onClick={() => sendToAgent(nx.request)}>{nx.label}</button>}
          </div>
        )}
      </div>
      <Word.Provider value={word}>
        {data.sections.map((s) => <Section key={s.key} id={id} s={s} open={!!open[s.key]} toggle={() => setOpen((o) => ({ ...o, [s.key]: !o[s.key] }))} />)}
      </Word.Provider>
    </section>
  );
}
