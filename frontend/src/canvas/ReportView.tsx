import { useQuery } from "@tanstack/react-query";
import { FileDown, FileText, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { download, get } from "../api";
import { ArtifactBody } from "../artifacts/ArtifactView";
import { Markdown } from "../components/Markdown";
import { sendToAgent, when } from "../format";
import { useToast } from "../toast";
import type { Artifact, Report, ReportSummary } from "../types";
import { useCanvas } from "./CanvasContext";

/** 본문에서 결과를 넣는 줄: ![캡션](artifact:12) — 서버 report.EMBED와 같은 규칙 */
const EMBED = /^\s*!\[(.*)\]\(artifact:(\d+)\)\s*$/;
type Part = { md: string } | { caption: string; id: number };

function splitBody(body: string): Part[] {
  const parts: Part[] = [];
  let buf: string[] = [];
  for (const line of body.split("\n")) {
    const m = line.match(EMBED);
    if (!m) { buf.push(line); continue; }
    if (buf.length) { parts.push({ md: buf.join("\n") }); buf = []; }
    parts.push({ caption: m[1].trim(), id: Number(m[2]) });
  }
  if (buf.length) parts.push({ md: buf.join("\n") });
  return parts;
}

const WRITING_LIMIT_MS = 15 * 60_000; // 그 안에 새 판이 안 오면 '작성 중'을 거둔다

/** 보고서 탭: AI가 작업 흐름·결과·스터디를 읽고 새로 쓴 종합 보고서(단순히 결과를 모은 것이 아니다).
 *  'AI 보고서 작성'을 누르면 최신 결과로 다시 쓰고, 새 판이 쌓인다. Word·HTML은 보고 있는 판으로 받는다. */
export function ReportView({ pid, arts }: { pid: number; arts: Artifact[] }) {
  const c = useCanvas();
  const toast = useToast();
  const [rid, setRid] = useState<number | null>(null); // null = 가장 최근 판
  const { data: list } = useQuery({ queryKey: ["reports", pid, "list"], queryFn: () => get<ReportSummary[]>(`/api/projects/${pid}/reports`) });
  const { data: rep, isLoading } = useQuery({
    queryKey: ["reports", pid, rid ?? "latest"],
    queryFn: () => (rid ? get<Report>(`/api/reports/${rid}`) : get<Report | null>(`/api/projects/${pid}/reports/latest`)),
  });
  const latestId = list?.[0]?.id ?? 0;
  const ask = c.reportAsk;
  const writing = !!ask && latestId <= ask.after && Date.now() - ask.at < WRITING_LIMIT_MS;
  const { setReportAsk } = c;
  useEffect(() => { if (ask && latestId > ask.after) setReportAsk(null); }, [ask, latestId, setReportAsk]);

  const byId = useMemo(() => new Map(arts.map((a) => [a.id, a])), [arts]);
  const parts = useMemo(() => splitBody(rep?.body ?? ""), [rep?.body]);

  const write = () => {
    setRid(null);
    setReportAsk({ at: Date.now(), after: latestId });
    sendToAgent(latestId ? "최신 결과로 보고서를 다시 작성해 줘" : "지금까지의 작업과 결과로 보고서를 작성해 줘");
  };
  const writeBtn = (big?: boolean) => (
    <button className={`${big ? "" : "small "}primary`} onClick={write} disabled={writing || !arts.length}
      title={!arts.length ? "결과가 생기면 쓸 수 있습니다" : "AI가 작업 흐름·결과·스터디를 읽고 보고서를 새로 씁니다"}>
      {writing ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <Sparkles size={13} />}
      {writing ? "작성 중…" : "AI 보고서 작성"}</button>
  );
  const save = (fmt: "docx" | "html") =>
    download(`/api/projects/${pid}/report?format=${fmt}${rep ? `&report_id=${rep.id}` : ""}`, `보고서.${fmt}`).catch((e: Error) => toast(e.message, true));

  if (isLoading) return <div className="busy"><span className="spinner" /></div>;
  if (!rep) {
    return (
      <div className="empty-hero">
        <FileText size={30} />
        <b>{writing ? "AI가 보고서를 쓰고 있습니다" : "아직 보고서가 없습니다"}</b>
        <p>{writing ? "작업 흐름과 결과를 읽고 해석하는 중입니다. 다 쓰면 여기에 바로 보입니다."
          : "'AI 보고서 작성'을 누르면 AI가 작업 흐름과 결과(그래프·표·이미지)를 해석해 요약·결론·권고가 담긴 보고서를 씁니다. 결과가 바뀌면 다시 누르세요."}</p>
        {writeBtn(true)}
      </div>
    );
  }

  let fig = 0, tbl = 0;
  return (
    <div className="report-view">
      <div className="report-bar">
        <div className="grow report-meta">
          <span className="small muted">작성 {when(rep.created_at)} · 결과 {parts.filter((p) => "id" in p).length}개 반영</span>
          {(list?.length ?? 0) > 1 && (
            <select value={rid ?? ""} onChange={(e) => setRid(e.target.value ? Number(e.target.value) : null)} aria-label="보고서 판 고르기">
              {list!.map((r, i) => <option key={r.id} value={i === 0 ? "" : r.id}>{i === 0 ? "최신 판" : `이전 판 ${list!.length - i}`} · {when(r.created_at)}</option>)}
            </select>
          )}
        </div>
        {writeBtn()}
        <span className="split-btn">
          <button className="small" title="이 보고서를 Word로" onClick={() => void save("docx")}><FileDown size={13} />Word</button>
          <button className="small" title="같은 보고서를 HTML로" onClick={() => void save("html")}>HTML</button>
        </span>
      </div>
      {writing ? <p className="notice-inline info report-note"><span className="spinner" style={{ width: 11, height: 11 }} />AI가 최신 결과로 보고서를 다시 쓰고 있습니다. 다 쓰면 새 판으로 바뀝니다.</p>
        : rid == null && rep.new_results > 0 && (
          <p className="notice-inline warn report-note">이 보고서를 쓴 뒤 새로 생기거나 바뀐 결과가 {rep.new_results}개 있습니다 → 'AI 보고서 작성'으로 반영하세요</p>
        )}
      <article className="report-doc">
        <h1>{rep.title}</h1>
        {parts.map((p, i) => {
          if ("md" in p) return <Markdown key={i} text={p.md} />;
          const a = byId.get(p.id);
          if (!a) return <p key={i} className="small muted rep-missing">(삭제된 결과입니다: {p.caption})</p>;
          const label = a.kind === "table" ? `표 ${++tbl}` : `그림 ${++fig}`;
          return (
            <figure key={i} className={`rep-fig kind-${a.kind}`}>
              <ArtifactBody a={a} />
              <figcaption><b>{label}.</b> {p.caption || a.title}
                <button className="link-btn" onClick={() => c.showArtifact(a.id, a.flow_id)} title="작업 흐름 탭에서 이 결과가 나온 단계를 봅니다">단계에서 보기</button>
              </figcaption>
            </figure>
          );
        })}
      </article>
    </div>
  );
}
