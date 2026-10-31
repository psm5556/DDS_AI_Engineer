import { useQuery } from "@tanstack/react-query";
import { FileDown, ListTree, Sparkles } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { download, get } from "../api";
import { ArtifactBody } from "../artifacts/ArtifactView";
import { AgentGlyph, DdsLogo, Wordmark } from "../components/AgentGlyph";
import { Markdown } from "../components/Markdown";
import { Skeleton } from "../components/Skeleton";
import { sendToAgent, when } from "../format";
import { useToast } from "../toast";
import type { Artifact, Report, ReportSummary } from "../types";
import { useCanvas } from "./CanvasContext";

/** 본문에서 결과를 넣는 줄: ![캡션](artifact:12) — 서버 report.EMBED와 같은 규칙. 코드 블록 안의 줄은 예시이므로 결과가 아니다 */
const EMBED = /^\s*!\[([^\n]*)\]\(artifact:(\d+)\)\s*$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})([^\n]*)$/; // 서버 report_md._FENCE와 같게. JS의 .은 U+2028에서 멈춘다
type Part = { md: string } | { caption: string; id: number };

function splitBody(body: string): Part[] {
  const parts: Part[] = [];
  let buf: string[] = [];
  let mark: string | null = null;
  for (const line of body.split(/\r\n|\r|\n/)) { // 서버 report_md.lines_of, marked와 같은 줄 나누기
    const f = line.match(FENCE);
    if (mark) {
      if (f && f[1][0] === mark[0] && f[1].length >= mark.length && !f[2].trim()) mark = null;
    } else if (f && !(f[1][0] === "`" && f[2].includes("`"))) {
      mark = f[1];
    }
    const m = !mark && !f ? line.match(EMBED) : null;
    if (!m) { buf.push(line); continue; }
    if (buf.length) { parts.push({ md: buf.join("\n") }); buf = []; }
    parts.push({ caption: m[1].trim(), id: Number(m[2]) });
  }
  if (buf.length) parts.push({ md: buf.join("\n") });
  return parts;
}

const WRITING_LIMIT_MS = 15 * 60_000; // 그 안에 새 판이 안 오면 '작성 중'을 거둔다
const NOW_LINE = 72; // 패널 위에서 이만큼 안쪽을 지난 제목이 '지금 읽는 곳'이다

type TocItem = { text: string; level: 2 | 3; el: HTMLElement };

/** 본문 제목으로 목차를 만들고 지금 읽는 곳을 알려 준다.
 *  그려진 제목을 그대로 쓰므로(번호·이름표 없이 요소를 바로 가리킴) 같은 제목·한글·기호·코드 블록에 흔들리지 않는다.
 *  주소(#)를 쓰면 앱 라우터와 부딪히므로 패널 안에서 scrollIntoView로만 움직인다. */
function useContents(reportId: number | undefined, parts: Part[]) {
  const body = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLElement>(null);
  const [items, setItems] = useState<TocItem[]>([]);
  const [current, setCurrent] = useState(0);
  const [listGone, setListGone] = useState(false); // 목록이 위로 지나갔는지(좁은 패널의 '목차로' 버튼)
  // 끝에 가까운 제목은 맨 위까지 올라오지 못하므로, 누른 제목을 사용자가 다시 스크롤할 때까지 현재로 둔다
  const pinned = useRef<number | null>(null);

  useLayoutEffect(() => {
    const root = body.current;
    if (!root) return;
    // 그림·표 안의 글(결과 본문)에 있는 제목은 구역이 아니다
    const found = Array.from(root.querySelectorAll<HTMLElement>(":scope > .md > h1, :scope > .md > h2, :scope > .md > h3"))
      .map((el): TocItem => ({ text: (el.textContent ?? "").trim(), level: el.tagName === "H3" ? 3 : 2, el }))
      .filter((t) => t.text);
    setItems(found);
    setCurrent(0);
    pinned.current = null;
  }, [reportId, parts]); // 새 판이면 글이 같아도 요소가 새로 그려진다

  useEffect(() => {
    const panel = body.current?.closest<HTMLElement>(".canvas-body");
    if (!panel || !items.length) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const top = panel.getBoundingClientRect().top;
      let at = 0;
      items.forEach((t, i) => { if (t.el.getBoundingClientRect().top - top <= NOW_LINE) at = i; });
      if (panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 2) at = items.length - 1;
      setCurrent(pinned.current ?? at);
      const box = list.current?.getBoundingClientRect();
      setListGone(!!box && box.bottom < top);
    };
    const ask = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const unpin = () => { pinned.current = null; };
    const hands = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
    panel.addEventListener("scroll", ask, { passive: true });
    hands.forEach((h) => panel.addEventListener(h, unpin, { passive: true }));
    measure();
    return () => {
      panel.removeEventListener("scroll", ask);
      hands.forEach((h) => panel.removeEventListener(h, unpin));
      if (frame) cancelAnimationFrame(frame);
    };
  }, [items]);

  useEffect(() => {
    // 고정된 목차가 길면 지금 읽는 곳이 목록 밖으로 나가지 않게 따라간다. scrollIntoView는 패널까지 움직이므로 목록만 옮긴다
    const box = list.current;
    const el = box?.querySelectorAll("button")[current];
    if (!box || !el || box.scrollHeight <= box.clientHeight) return;
    const b = box.getBoundingClientRect(), a = el.getBoundingClientRect();
    if (a.top < b.top || a.bottom > b.bottom) box.scrollTop += a.top - b.top - box.clientHeight / 2;
  }, [current, items]);

  const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth") as ScrollBehavior;
  const go = (i: number) => {
    // pointerdown이 먼저 풀어 둔 뒤 여기서 다시 잡는다
    pinned.current = i;
    setCurrent(i);
    items[i]?.el.scrollIntoView({ behavior: smooth(), block: "start" });
  };
  const toList = () => list.current?.scrollIntoView({ behavior: smooth(), block: "start" });
  return { body, list, items, current, listGone, go, toList };
}

/** 보고서 탭: AI가 작업 흐름·결과·스터디를 읽고 새로 쓴 종합 보고서(단순히 결과를 모은 것이 아니다).
 *  'AI 보고서 작성'을 누르면 최신 결과로 다시 쓰고, 새 판이 쌓인다. Word·HTML은 보고 있는 판으로 받는다.
 *  표지·목차·맺음말은 파일(server/app/report.py)과 같은 틀이고, 글은 서버가 함께 준다. */
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
  const toc = useContents(rep?.id, parts);

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

  if (isLoading) return <Skeleton rows={6} />;
  if (!rep) {
    return (
      <div className="empty-hero">
        <AgentGlyph size={32} state={writing ? "thinking" : "idle"} />
        <b>{writing ? "AI가 보고서를 쓰고 있습니다" : "아직 보고서가 없습니다"}</b>
        <p>{writing ? "작업 흐름과 결과를 읽고 해석하는 중입니다. 다 쓰면 여기에 바로 보입니다."
          : "'AI 보고서 작성'을 누르면 AI가 작업 흐름과 결과(그래프·표·이미지)를 해석해 요약·결론·권고가 담긴 보고서를 씁니다. 결과가 바뀌면 다시 누르세요."}</p>
        {writeBtn(true)}
      </div>
    );
  }

  let fig = 0, tbl = 0;
  const hasToc = toc.items.length > 0;
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
      {writing ? <p className="notice-inline info report-note"><AgentGlyph size={14} state="thinking" />AI가 최신 결과로 보고서를 다시 쓰고 있습니다. 다 쓰면 새 판으로 바뀝니다.</p>
        : rid == null && rep.new_results > 0 && (
          <p className="notice-inline warn report-note">이 보고서를 쓴 뒤 새로 생기거나 바뀐 결과가 {rep.new_results}개 있습니다 → 'AI 보고서 작성'으로 반영하세요</p>
        )}
      {/* 새 판이 오면 표지부터 다시 등장한다 */}
      <article key={rep.id} className="report-doc">
        <header className="report-cover">
          <Wordmark size="l" />
          <h1>{rep.title}</h1>
          <p className="report-project">{rep.project_name}</p>
          <dl className="report-issue">
            <div><dt>문서 번호</dt><dd className="report-no">{rep.doc_no}</dd></div>
            <div><dt>판</dt><dd>{rep.edition}판</dd></div>
            <div><dt>발행 일시</dt><dd>{rep.issued_at}</dd></div>
            <div><dt>작성자</dt><dd>{rep.author}</dd></div>
          </dl>
          <p className="report-provenance">{rep.provenance}</p>
        </header>
        <div className={`report-layout ${hasToc ? "" : "solo"}`}>
          {hasToc && (
            <nav className="report-toc" aria-label="목차" ref={toc.list}>
              <p className="report-toc-title">목차</p>
              <ol>
                {toc.items.map((t, i) => (
                  <li key={i} className={`l${t.level}`}>
                    <button type="button" aria-current={i === toc.current ? "true" : undefined} onClick={() => toc.go(i)}>{t.text}</button>
                  </li>
                ))}
              </ol>
            </nav>
          )}
          <div className="report-body" ref={toc.body}>
            {parts.map((p, i) => {
              if ("md" in p) return <Markdown key={i} text={p.md} />;
              const a = byId.get(p.id);
              if (!a) return <p key={i} className="small muted rep-missing">(삭제된 결과입니다: {p.caption})</p>;
              const label = a.kind === "table" ? `표 ${++tbl}` : `그림 ${++fig}`;
              return (
                <figure key={i} className={`rep-fig kind-${a.kind}`}>
                  <ArtifactBody a={a} />
                  <figcaption><b>{label}</b> {p.caption || a.title}
                    <button className="link-btn" onClick={() => c.showArtifact(a.id, a.flow_id)} title="작업 흐름 탭에서 이 결과가 나온 단계를 봅니다">단계에서 보기</button>
                  </figcaption>
                </figure>
              );
            })}
          </div>
        </div>
        <footer className="report-colophon">
          <DdsLogo />
          <div>
            <p>{rep.colophon}</p>
            <p className="small muted">{rep.doc_no} · {rep.edition}판 · {rep.issued_at}</p>
          </div>
        </footer>
        {/* 글 위에 떠 있으면 줄 끝이 가려지므로, 패널 아래에 붙는 띠 안에 둔다 */}
        {hasToc && toc.listGone && (
          <div className="report-toc-dock">
            <span className="small muted">{toc.items[toc.current]?.text}</span>
            <button type="button" className="small" onClick={toc.toList}><ListTree size={13} />목차로</button>
          </div>
        )}
      </article>
    </div>
  );
}
