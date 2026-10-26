import { Copy, Download, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { blobUrl } from "../api";
import { Markdown } from "../components/Markdown";
import { baseLayout, Plot, plotConfig } from "../components/Plot";
import { copyText, fmt, when } from "../format";
import { useTheme } from "../theme";
import { useToast } from "../toast";
import type { Artifact } from "../types";

type Pt = { x: number; y: number };
type Series = { name: string; points?: Pt[]; values?: number[] };
const PALETTE = ["#2563eb", "#0d9488", "#7c3aed", "#d97706", "#dc2626", "#64748b"];

function usePlotTheme() {
  const { theme } = useTheme();
  return theme === "dark" ? { grid: "#2a2a30", text: "#a1a1aa" } : { grid: "#ececef", text: "#71717a" };
}

function Chart({ a }: { a: Artifact }) {
  const t = usePlotTheme();
  const p = a.payload as { x?: string; y?: string; series?: Series[]; diagonal?: boolean; categories?: string[]; dimensions?: string[]; rows?: number[][] };
  const axis = (title?: string) => ({ title: { text: title ?? "" }, gridcolor: t.grid, zerolinecolor: t.grid, color: t.text });
  const { data, layout } = useMemo(() => {
    const series = p.series ?? [];
    if (a.kind === "bar") {
      return {
        data: series.map((s, i) => ({ type: "bar", name: s.name, x: p.categories, y: s.values, marker: { color: PALETTE[i % PALETTE.length] } })),
        layout: { barmode: "group", xaxis: axis(), yaxis: axis() },
      };
    }
    if (a.kind === "parallel") {
      const dims = p.dimensions ?? [];
      const rows = p.rows ?? [];
      return {
        data: [{
          type: "parcoords",
          line: { color: rows.map((r) => r[r.length - 1]), colorscale: [[0, "#2563eb"], [1, "#f59e0b"]] },
          dimensions: dims.map((d, i) => ({ label: d, values: rows.map((r) => r[i]) })),
        }],
        layout: { margin: { l: 60, r: 40, t: 30, b: 20 } },
      };
    }
    const data: Record<string, unknown>[] = series.map((s, i) => ({
      type: "scatter", mode: a.kind === "line" ? "lines+markers" : "markers", name: s.name,
      x: (s.points ?? []).map((q) => q.x), y: (s.points ?? []).map((q) => q.y),
      marker: { color: PALETTE[i % PALETTE.length], size: a.kind === "line" ? 5 : 8, opacity: 0.85 },
      line: { color: PALETTE[i % PALETTE.length], width: 2 },
    }));
    if (p.diagonal) {
      const all = series.flatMap((s) => (s.points ?? []).flatMap((q) => [q.x, q.y]));
      const lo = Math.min(...all), hi = Math.max(...all);
      data.push({ type: "scatter", mode: "lines", name: "y = x", x: [lo, hi], y: [lo, hi], line: { color: "#a1a1aa", dash: "dash", width: 1 }, hoverinfo: "skip" });
    }
    return { data, layout: { xaxis: axis(p.x), yaxis: axis(p.y), showlegend: data.length > 1, legend: { orientation: "h", y: -0.25 } } };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a, t]);
  return (
    <Plot data={data} layout={{ ...baseLayout, ...layout, height: 300, autosize: true, font: { ...baseLayout.font, color: t.text } }}
      config={plotConfig} style={{ width: "100%" }} useResizeHandler />
  );
}

function Table({ a }: { a: Artifact }) {
  const p = a.payload as { columns?: string[]; rows?: unknown[][] };
  const toast = useToast();
  const tsv = () => [p.columns ?? [], ...(p.rows ?? [])].map((r) => r.map((c) => (c ?? "")).join("\t")).join("\n");
  return (
    <>
      <div className="row" style={{ justifyContent: "flex-end", marginBottom: 6 }}>
        <button className="icon-btn" title="표 복사 (엑셀에 붙여넣기)" aria-label="표 복사"
          onClick={async () => toast((await copyText(tsv())) ? "표를 복사했습니다" : "복사하지 못했습니다", false)}><Copy size={14} /></button>
      </div>
      <div className="table-wrap art-table">
        <table>
          <thead><tr>{(p.columns ?? []).map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{(p.rows ?? []).map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j} className={typeof c === "number" ? "r" : ""}>{fmt(c)}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

function ImageArt({ a }: { a: Artifact }) {
  const p = a.payload as { path?: string; caption?: string };
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    if (p.path) blobUrl(`/api/projects/${a.project_id}/files/${p.path}`).then((u) => { url = u; setSrc(u); }).catch(() => setSrc(null));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [a.project_id, p.path]);
  return (
    <figure className="art-image">
      {src ? <img src={src} alt={p.caption ?? a.title} /> : <div className="busy"><span className="spinner" /></div>}
      {p.caption && <figcaption>{p.caption}</figcaption>}
      {src && <a className="icon-btn" href={src} download={`${a.title}.png`} title="이미지 저장" aria-label="이미지 저장"><Download size={14} /></a>}
    </figure>
  );
}

export function ArtifactCard({ a, onDelete }: { a: Artifact; onDelete?: () => void }) {
  return (
    <section className={`panel art-card kind-${a.kind}`}>
      <div className="panel-head">
        <h3>{a.title}</h3>
        <span className="small muted">{when(a.created_at)}</span>
        {onDelete && <button className="icon-btn" title="삭제" aria-label={`${a.title} 삭제`} onClick={onDelete}><Trash2 size={14} /></button>}
      </div>
      {a.kind === "table" ? <Table a={a} /> : a.kind === "markdown" ? <Markdown text={String((a.payload as { text?: string }).text ?? "")} />
        : a.kind === "image" ? <ImageArt a={a} /> : <Chart a={a} />}
    </section>
  );
}
