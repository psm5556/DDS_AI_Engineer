import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Ban, LayoutGrid, List, Search, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, del, get, optionalBlob } from "../api";
import { AgentGlyph } from "../components/AgentGlyph";
import { Skeleton } from "../components/Skeleton";
import { when } from "../format";
import { useFreshKeys } from "../motion";
import { useToast } from "../toast";

interface Item {
  study_id: number; study_name: string; model_id: number; version: number | null; outputs: string[]; n_variables: number; n_train: number; url: string;
  thumbnail_url: string | null;
  source: "mine" | "project" | "link"; status: "active" | "stopped" | "expired"; project_id: number | null; project_name: string | null;
  shared_by: string | null; note: string; share_id: number | null; updated_at: string | null;
}
type ViewMode = "cards" | "list";
const OFF = { stopped: "공유가 중지되었습니다", expired: "공유 기간이 끝났습니다" } as const;
const VIEW_KEY = "dds.simulatorView";
const CARD_OUTPUTS = 3; // 카드 한 줄에 보이는 출력 수. 나머지는 +N과 툴팁으로

function savedView(): ViewMode {
  try { return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "cards"; } catch { return "cards"; }
}

function meta(x: Item): string {
  const who = x.source === "link" ? `${x.shared_by ?? "다른 사용자"}님이 공유` : x.source === "project" ? `${x.shared_by ?? "멤버"}님이 배포` : "";
  return [who, x.project_name, x.note].filter(Boolean).join(" · ");
}

/** 찾는 대상은 화면에 보이는 글자다. 공유가 끊긴 줄은 이름·공유한 사람·메모만 보이므로 출력 이름으로는 찾지 않는다 */
function matches(x: Item, term: string): boolean {
  const words = [x.study_name, x.shared_by, x.note, ...(x.status === "active" ? [x.project_name, ...x.outputs] : [])];
  return words.some((w) => w?.toLowerCase().includes(term));
}

// 카드 그림은 처음 그릴 때 수 초 걸릴 수 있다. 카드가 100장이어도 동시에 받는 수를 줄여 서버에 차례대로 맡긴다
const MAX_LOADS = 4;
let loading = 0;
const waiting: (() => void)[] = [];
function pump() {
  while (loading < MAX_LOADS && waiting.length) {
    loading += 1;
    waiting.shift()!();
  }
}
/** 차례가 오면 work를 시작한다. 돌려주는 함수는 줄에서 빠지는 것으로, 이미 지나간 카드가 지금 보이는 카드를 앞서지 않게 한다 */
function takeTurn(work: () => Promise<unknown>): () => void {
  const run = () => { void work().finally(() => { loading -= 1; pump(); }); };
  waiting.push(run);
  pump();
  return () => { const i = waiting.indexOf(run); if (i >= 0) waiting.splice(i, 1); };
}

// 받은 그림은 화면을 다시 그려도 쓴다. 보기 전환·검색·페이지 이동마다 다시 받으면 그림이 깜빡인다.
// 잠시 뒤에는 뒤에서 확인해 바뀐 때만 바꾼다(같은 주소에 3D를 다시 학습할 수 있다). 보관 수를 넘으면 안 보이는 것부터 지운다
const KEEP_PICS = 64;
const FRESH_MS = 60_000;
interface Pic { src: string | null; etag: string | null; at: number }
const pics = new Map<string, Pic>(); // 넣은 순서가 오래된 순서다
const fetching = new Map<string, Promise<Pic | null>>();
const shown = new Map<string, number>(); // 지금 화면에 붙은 카드 수. 쓰는 그림은 지우지 않는다

function dropPic(url: string) {
  const old = pics.get(url);
  if (old?.src) URL.revokeObjectURL(old.src);
  pics.delete(url);
}
function storePic(url: string, pic: Pic) {
  const old = pics.get(url);
  if (old?.src && old.src !== pic.src) URL.revokeObjectURL(old.src);
  pics.delete(url);
  pics.set(url, pic);
  for (const u of [...pics.keys()]) {
    if (pics.size <= KEEP_PICS) break;
    if (!shown.get(u)) dropPic(u);
  }
}
function fetchPic(url: string): Promise<Pic | null> {
  const running = fetching.get(url);
  if (running) return running; // 받는 중에 카드가 다시 그려져도 같은 요청을 함께 기다린다
  const work = (async () => {
    const old = pics.get(url);
    try {
      const got = await optionalBlob(url);
      if (old && (got?.etag ?? null) === old.etag) { old.at = Date.now(); return old; }
      const pic: Pic = { src: got ? URL.createObjectURL(got.blob) : null, etag: got?.etag ?? null, at: Date.now() };
      storePic(url, pic);
      return pic;
    } catch (e) {
      // 권한이 끊겼으면 가진 그림도 보이지 않는다. 일시적인 오류면 그대로 둔다
      if (e instanceof ApiError && (e.status === 403 || e.status === 404)) { dropPic(url); return null; }
      return old ?? null;
    } finally {
      fetching.delete(url);
    }
  })();
  fetching.set(url, work);
  return work;
}

/** 카드 그림: 화면에 들어올 때만 받는다. 그림이 없거나 받지 못하면 아는 값(설계변수·출력 수)을 그대로 보인다. 그럴듯한 가짜 그림은 그리지 않는다 */
function Thumb({ x }: { x: Item }) {
  const box = useRef<HTMLDivElement>(null);
  const url = x.thumbnail_url;
  // url: 이 값을 받은 요청. 새 버전이면 옛 그림을 쓰지 않는다. arrive: 처음 받은 그림만 나타나는 움직임을 쓴다
  const [state, setState] = useState<{ url: string; src: string | null; arrive: boolean } | null>(() => {
    const pic = url ? pics.get(url) : undefined;
    return pic ? { url: url!, src: pic.src, arrive: false } : null;
  });
  const [asked, setAsked] = useState<string | null>(null); // 화면 밖 카드는 움직이지 않는다. 실제로 받는 동안만 표시한다
  useEffect(() => {
    const el = box.current;
    if (!url || !el) return;
    let dead = false, leave: (() => void) | null = null;
    const had = pics.get(url);
    if (had) setState({ url, src: had.src, arrive: false });
    shown.set(url, (shown.get(url) ?? 0) + 1);
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) { // 지나간 카드는 줄에서 뺀다. 다시 보이면 줄을 다시 선다
        leave?.();
        leave = null;
        setAsked(null);
        return;
      }
      if (leave) return;
      if (!had) setAsked(url);
      leave = takeTurn(async () => {
        const pic = await fetchPic(url);
        if (dead) return;
        io.disconnect();
        setAsked(null);
        setState({ url, src: pic?.src ?? null, arrive: !had && !!pic?.src });
      });
    }, { rootMargin: "120px" });
    if (!had || Date.now() - had.at >= FRESH_MS) io.observe(el);
    return () => {
      dead = true;
      io.disconnect();
      leave?.();
      shown.set(url, (shown.get(url) ?? 1) - 1);
    };
  }, [url]);

  const done = state?.url === url ? state : null;
  if (x.status !== "active") {
    return <div className="sim-thumb none"><Ban size={20} /><span>{OFF[x.status]}</span></div>;
  }
  const isWaiting = !!url && asked === url && !done;
  return (
    <div ref={box} className={`sim-thumb ${isWaiting ? "waiting" : ""}`}>
      {done?.src
        ? <img src={done.src} alt="" className={done.arrive ? "arrive" : ""} onError={() => { dropPic(url!); setState({ url: url!, src: null, arrive: false }); }} />
        : (done || !url) && <div className="sim-thumb-mark"><SlidersHorizontal size={22} /><span>설계변수 {x.n_variables}개 · 출력 {x.outputs.length}개</span></div>}
      {isWaiting && <span className="sim-thumb-wait">그림 준비 중</span>}
    </div>
  );
}

/** 내 Simulator와 공유받은 Simulator만 모은 화면. 프로젝트에 들어가지 않고 바로 연다. 기본은 그림 카드, 목록으로 바꿀 수 있다 */
export default function SimulatorsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [view, setView] = useState<ViewMode>(savedView);
  const [q, setQ] = useState("");
  const findBox = useRef<HTMLInputElement>(null);
  const { data, error } = useQuery({ queryKey: ["simulators"], queryFn: () => get<{ mine: Item[]; shared: Item[] }>("/api/simulators") });
  const fresh = useFreshKeys(!!data);
  const hide = useMutation({
    mutationFn: (id: number) => del(`/api/simulators/shared/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["simulators"] }),
    onError: (e: Error) => toast(e.message, true),
  });
  if (error) return <div className="page"><div className="notice err">{(error as Error).message}</div></div>;
  if (!data) return <Skeleton page rows={5} />;

  // 검색으로 가려져 있어도 처음 본 Simulator를 기억한다. 검색어를 지울 때 다시 등장하지 않게
  for (const x of [...data.mine, ...data.shared]) fresh(String(x.study_id));
  const isNew = (x: Item) => (fresh(String(x.study_id)) ? " fresh" : "");
  const chooseView = (v: ViewMode) => {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* 이번 화면에서만 기억한다 */ }
  };

  const removeBtn = (x: Item, title: string) => x.share_id != null && (
    <button className="icon-btn sim-remove" title={title} aria-label={`${x.study_name} 목록에서 빼기`} onClick={(e) => {
      // 키보드로 눌렀다면 버튼이 사라져도 갈 곳을 잃지 않게 한다. 마우스로 누른 사람의 화면 키보드는 띄우지 않는다
      const keyboard = e.currentTarget.matches(":focus-visible");
      hide.mutate(x.share_id!, { onSuccess: () => { if (keyboard) findBox.current?.focus(); } });
    }}><X size={14} /></button>
  );

  const row = (x: Item) => {
    const body = (
      <>
        <b>{x.study_name}{meta(x) && <small className="sim-row-meta">{meta(x)}</small>}</b>
        <span className="small sim-row-out" title={x.outputs.join(", ")}>{x.outputs.join(", ")}</span>
        {x.version != null && <span className="chip">v{x.version}</span>}
        <span className="small">{x.updated_at ? when(x.updated_at) : ""}</span>
      </>
    );
    return x.status === "active" ? (
      <div key={x.study_id} className={`sim-row${isNew(x)}`}>
        <Link className="recent-row" to={x.url}>{body}<ArrowRight size={14} className="go" /></Link>
        {removeBtn(x, "목록에서 빼기. 링크를 다시 열면 돌아옵니다")}
      </div>
    ) : (
      <div key={x.study_id} className={`sim-row off${isNew(x)}`}>
        <div className="recent-row"><b>{x.study_name}<small className="sim-row-meta">{OFF[x.status]}{x.shared_by ? ` · ${x.shared_by}님에게 다시 요청합니다` : ""}{x.note ? ` · ${x.note}` : ""}</small></b></div>
        {removeBtn(x, "목록에서 빼기")}
      </div>
    );
  };

  const card = (x: Item) => {
    if (x.status !== "active") {
      const offMeta = `${x.shared_by ? `${x.shared_by}님에게` : "공유한 사람에게"} 다시 요청합니다${x.note ? ` · ${x.note}` : ""}`;
      return (
        <article key={x.study_id} className={`sim-card off${isNew(x)}`}>
          <Thumb x={x} />
          <div className="sim-card-body">
            <div className="sim-card-title"><b title={x.study_name}>{x.study_name}</b></div>
            <p className="sim-card-meta" title={offMeta}><span>{offMeta}</span></p>
          </div>
          {removeBtn(x, "목록에서 빼기")}
        </article>
      );
    }
    const rest = x.outputs.length - CARD_OUTPUTS;
    return (
      <article key={x.study_id} className={`sim-card${isNew(x)}`}>
        <Link to={x.url} className="sim-card-link">
          <Thumb x={x} />
          <div className="sim-card-body">
            <div className="sim-card-title"><b title={x.study_name}>{x.study_name}</b>{x.version != null && <span className="chip">v{x.version}</span>}</div>
            {x.outputs.length > 0 && (
              <div className="sim-card-outs" title={x.outputs.join(", ")}>
                {x.outputs.slice(0, CARD_OUTPUTS).map((o) => <span key={o}>{o}</span>)}
                {rest > 0 && <em>+{rest}</em>}
              </div>
            )}
            <p className="sim-card-meta" title={meta(x)}><span>{meta(x)}</span>{x.updated_at && <time>{when(x.updated_at)}</time>}</p>
          </div>
        </Link>
        {removeBtn(x, "목록에서 빼기. 링크를 다시 열면 돌아옵니다")}
      </article>
    );
  };

  const total = data.mine.length + data.shared.length;
  const term = q.trim().toLowerCase();
  const mine = term ? data.mine.filter((x) => matches(x, term)) : data.mine;
  const shared = term ? data.shared.filter((x) => matches(x, term)) : data.shared;
  const section = (title: string, xs: Item[]) => xs.length > 0 && (
    <section className="sims-sec">
      <h2 className="home-sec">{title} <span className="count">{xs.length}</span></h2>
      {view === "cards" ? <div className="sim-cards">{xs.map(card)}</div> : <div className="recent-rows">{xs.map(row)}</div>}
    </section>
  );

  return (
    <div className="page sims-page">
      <h1>Simulator</h1>
      <p className="page-sub">해석 없이 설계변수를 바꿔 바로 예측합니다. 프로젝트에 들어가지 않아도 됩니다.</p>
      {total === 0 ? (
        <div className="empty-hero"><AgentGlyph size={32} /><b>아직 Simulator가 없습니다</b>
          <p>프로젝트에서 예측 모델을 배포하면 여기에 보입니다. 공유 링크를 한 번 열면 그 Simulator도 여기에 들어옵니다.</p></div>
      ) : (
        <>
          <div className="sims-tools">
            <div className="sims-find">
              <Search size={15} />
              <input ref={findBox} type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setQ(""); }}
                placeholder="이름 · 출력 · 프로젝트 · 공유한 사람 · 메모로 찾기" aria-label="Simulator 검색" />
              {/* 지우면 버튼이 사라지므로 입력창으로 돌아가 바로 다시 찾게 한다 */}
              {q && <button className="icon-btn" onClick={() => { setQ(""); findBox.current?.focus(); }} title="검색 지우기" aria-label="검색 지우기"><X size={14} /></button>}
            </div>
            <div className="view-tools" role="group" aria-label="보기 방식">
              <button className={`tog ${view === "cards" ? "on" : ""}`} aria-pressed={view === "cards"} onClick={() => chooseView("cards")}><LayoutGrid size={14} />카드</button>
              <button className={`tog ${view === "list" ? "on" : ""}`} aria-pressed={view === "list"} onClick={() => chooseView("list")}><List size={14} />목록</button>
            </div>
          </div>
          {section("내 Simulator", mine)}
          {section("공유받은 Simulator", shared)}
          {term && !mine.length && !shared.length && (
            <p className="sims-none" role="status">'{q.trim()}'에 맞는 Simulator가 없습니다. 검색창의 X를 눌러 지우거나 다른 말로 찾아 보세요.</p>
          )}
        </>
      )}
    </div>
  );
}
