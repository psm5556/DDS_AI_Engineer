import { W } from "../work/copy";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileCode2, FolderUp, Search, ShieldCheck, Store, Upload, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { api, del, download, get, post, put } from "../api";
import { useAuth } from "../auth";
import { Markdown } from "../components/Markdown";
import { LabelFields, labelsToMeta, labelValues, MarketChecks, MarketFiles, PinModal, useAcks, type LabelValues } from "../components/MarketParts";
import { when } from "../format";
import { useToast } from "../toast";
import type { MarketFlag, MarketList, MarketNotices, MarketPackage, MarketSubmission } from "../types";

const LIST = ["market", "packages"];
const JUNK = new Set([".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"]);
const JUNK_FILES = new Set([".ds_store", "thumbs.db", "desktop.ini", ".gitattributes", ".gitignore"]);
// 서버 한도와 같다. 큰 폴더를 다 보내고 나서 거절당하지 않게 보내기 전에 알린다. 결과가 든 폴더를 잘못 고르는 일이 흔하다
const MAX_FILES = 200;
const MB = 1024 * 1024;
const MAX_FILE_BYTES = 5 * MB;
const MAX_ZIP_BYTES = 20 * MB;
const MAX_FOLDER_BYTES = 60 * MB;
const TABS = [["all", "전체"], ["skill", "스킬"], ["tool", "기능"], ["installed", "설치됨"], ["mine", "내가 등록"]] as const;
type Tab = (typeof TABS)[number][0];

/** 끼어들지 않는 버튼 글자. 설치는 실제로 PC에 들어간 뒤에야 "설치됨"이 된다 */
function buttonFor(p: MarketPackage, off?: boolean): { label: string; can: boolean } {
  const s = p.install.state;
  if (p.recalled && !p.install.version) return { label: "회수됨", can: false };
  if (s === "paused" || (off && s === "none")) return { label: "마켓 꺼짐", can: false };
  if (s === "none") return p.installable ? { label: "설치", can: true } : { label: p.status === "deprecated" ? "새 설치 안 됨" : "쓸 수 없음", can: false };
  if (s === "installing") return { label: "설치 중", can: false };
  if (s === "reserved") return { label: "설치 예약됨", can: false };
  if (s === "installed") return { label: "설치됨", can: false };
  if (s === "needs_consent") return { label: "다시 확인 필요", can: true };
  if (s === "failed") return { label: "다시 시도", can: !off };
  if (s === "conflict") return { label: `일부 ${W.project} 겹침`, can: false };
  if (s === "agent_old" || s === "agent_too_old") return { label: "업데이트 필요", can: false };
  return { label: "쓸 수 없음", can: false };
}

const STATE_TEXT: Partial<Record<MarketPackage["install"]["state"], string>> = {
  reserved: "설치 예약됨 → PC Agent를 켜면 자동으로 설치됩니다",
  installing: "PC Agent가 받고 있습니다. 끝나면 이 화면이 저절로 바뀝니다",
  installed: `설치했습니다. AI가 쓸 수 있는 ${W.task}에는 다음 요청 때 알려 줍니다. 기록은 그대로입니다.`,
  paused: "관리부서가 마켓을 잠시 꺼서 지금은 쓰지 않습니다 → 다시 켜지면 저절로 돌아옵니다",
  agent_old: "PC Agent를 업데이트하면 설치됩니다 → 트레이 메뉴의 '업데이트 확인'을 누르세요",
  needs_consent: "새 버전이 더 많은 일을 합니다. 확인할 때까지 이전 버전을 씁니다",
};

/** PC Agent가 꺼져 있거나 옛 버전이면 무엇이 있는지 모른다. 있다고도 없다고도 하지 않는다 */
function Chips({ p, tools, known }: { p: MarketPackage; tools: Record<string, string>; known: boolean }) {
  return (
    <div className="market-chips">
      {p.grade && p.status !== "review" && <span className={`chip ${p.grade === "official" ? "ok" : p.grade === "reviewed" ? "info" : ""}`}>{p.grade_label}</span>}
      {p.status && !["approved", "private"].includes(p.status) && <span className={`chip ${p.status === "rejected" || p.status === "withdrawn" ? "err" : "warn"}`}>{p.status_label}</span>}
      {p.draft_version && p.status !== "draft" && <span className="chip warn">초안 {p.draft_version}</span>}
      {p.code && <span className="chip warn">코드 있음</span>}
      {p.requires_tools.map((t) => (
        <span key={t} className="chip">{p.tools_missing.includes(t) ? `이 PC에 ${tools[t] ?? t} 없음` : `${tools[t] ?? t}${known ? " 있음" : ""}`}</span>
      ))}
    </div>
  );
}

/** 검토 결과와 회수. 찾아가야만 보이면 어제 보고서를 낸 엔지니어가 회수를 영영 모른다 */
function Notices() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["market", "notices"], queryFn: () => get<MarketNotices>("/api/market/notices") });
  const read = useMutation({
    mutationFn: (id: number) => post(`/api/market/notices/${id}/read`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["market", "notices"] }),
    onError: (e: Error) => toast(e.message, true),
  });
  if (!data) return null;
  return (
    <>
      {data.review_waiting > 0 && <div className="notice info market-notice"><span className="grow">검토를 기다리는 항목이 {data.review_waiting}건 있습니다.</span><Link className="btn small" to="/admin?tab=market">검토하러 가기</Link></div>}
      {data.items.map((n) => (
        <div key={n.id} className={`notice ${n.kind === "recalled" ? "err" : n.kind === "approved" ? "ok" : "warn"} market-notice`}>
          <span className="grow">{n.text} <span className="muted small">{when(n.created_at)}</span></span>
          <button className="small" disabled={read.isPending} onClick={() => read.mutate(n.id)}>확인</button>
        </div>
      ))}
    </>
  );
}

/** 마켓: 관리부서와 동료가 만든 스킬을 찾아 설치하고, 내가 만든 것을 올린다. 깊이는 페이지 하나 + 오른쪽 서랍 하나다 */
export default function MarketPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [draftOf, setDraftOf] = useState<number | null>(null);
  const [registering, setRegistering] = useState(false);
  const { data } = useQuery({
    queryKey: LIST, queryFn: () => get<MarketList>("/api/market/packages"),
    // 설치 결과는 PC Agent가 알린다(market_updated). 알림을 놓쳐도 "설치 중"에 갇히지 않게 받는 동안만 다시 읽는다
    refetchInterval: (query) => (query.state.data?.items.some((i) => i.install.state === "installing") ? 4000 : false),
  });
  const { data: cat } = useQuery({ queryKey: ["tools", "catalog"], queryFn: () => get<{ tools: { id: string; name: string }[] }>("/api/tools/catalog"), staleTime: Infinity });
  const tools = useMemo(() => Object.fromEntries((cat?.tools ?? []).map((t) => [t.id, t.name])), [cat]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["market"] });
  const fail = (e: Error) => toast(e.message, true);
  const install = useMutation({ mutationFn: (id: number) => put<MarketPackage>(`/api/market/installs/${id}`, {}), onSuccess: refresh, onError: fail });
  const retry = useMutation({ mutationFn: (id: number) => post<MarketPackage>(`/api/market/installs/${id}/retry`, {}), onSuccess: refresh, onError: fail });

  const items = data?.items ?? [];
  // 띄어 쓴 말은 단어마다 찾는다. 찾기 칸의 보기("Abaqus 보고서")가 그대로 찾아져야 한다
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hasTools = items.some((i) => i.kind !== "skill_text" && i.kind !== "skill_code");
  const shown = items.filter((p) => {
    if (tab === "skill" && !p.kind.startsWith("skill")) return false;
    if (tab === "tool" && p.kind.startsWith("skill")) return false;
    if (tab === "installed" && !p.install.installed) return false;
    if (tab === "mine" && !p.mine) return false;
    if (tab !== "mine" && tab !== "installed" && p.mine && !p.public && !p.install.installed && p.status === "draft") return false; // 초안은 내가 등록에만 둔다
    const hay = `${p.search_text ?? ""} ${p.requires_tools.map((t) => tools[t] ?? "").join(" ")}`.toLowerCase();
    return terms.every((t) => hay.includes(t));
  });
  const recommended = tab === "all" && !terms.length ? shown.filter((p) => p.recommended) : [];
  const rest = shown.filter((p) => !recommended.includes(p));
  const groups = [...new Set(rest.map((p) => p.category || "기타"))];
  const readyTools = recommended.flatMap((p) => p.requires_tools).filter((t, i, a) => a.indexOf(t) === i).map((t) => tools[t] ?? t);
  const sel = items.find((p) => p.id === open);
  const off = !!data && !data.enabled;
  const known = !!data?.agent.connected && !!data.agent.market_api;
  const closeSel = () => { setOpen(null); setDraftOf(null); };

  const card = (p: MarketPackage) => {
    const b = buttonFor(p, off);
    return (
      <article key={p.id} className="market-card" onClick={() => setOpen(p.id)}>
        <h3>{p.display_name ?? p.name}</h3>
        <p className="small">{p.description}</p>
        <Chips p={p} tools={tools} known={known} />
        <div className="market-card-foot">
          <span className="small muted">{p.owner_name}{p.installs ? ` · 설치 ${p.installs}` : ""}</span>
          {/* 코드 있는 항목은 서랍에서 내용을 보고 설치한다 */}
          <button className={`small ${b.can ? "primary" : ""}`} disabled={!b.can || install.isPending || retry.isPending}
            onClick={(e) => { e.stopPropagation(); if (p.install.state === "failed") retry.mutate(p.id); else if (p.code) setOpen(p.id); else install.mutate(p.id); }}>{b.label}</button>
        </div>
      </article>
    );
  };

  return (
    <div className="page market-page">
      <div className="market-head">
        <div className="grow">
          <h1>마켓</h1>
          <p className="page-sub">스킬(에이전트의 작업 방법)을 골라 설치하면 AI가 쓸 수 있습니다. 설치는 내 계정의 모든 {W.project}에 적용됩니다.</p>
        </div>
        <button className="primary" disabled={off} onClick={() => setRegistering(true)}><Upload size={15} />등록</button>
      </div>
      <Notices />
      {off && <div className="notice warn">관리부서가 마켓을 잠시 껐습니다. 설치한 스킬도 지금은 쓰지 않습니다 → 다시 켜지면 저절로 돌아옵니다</div>}
      {data && !data.agent.connected && <div className="notice info market-note">PC Agent가 꺼져 있어도 설치를 누를 수 있습니다. 켜지면 자동으로 설치됩니다 · <Link to="/settings">설치·연결</Link></div>}

      <div className="tools-filter">
        <label className="tools-search"><Search size={15} />
          <input type="search" placeholder="하고 싶은 일이나 툴 이름 (예: 피로, Abaqus 보고서)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="마켓 검색" /></label>
      </div>
      <nav className="stepper proj-tabs market-tabs" aria-label="마켓 보기">
        {TABS.filter(([k]) => k !== "tool" || hasTools).map(([k, l]) => <button key={k} className={`ptab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{l}</button>)}
      </nav>

      {recommended.length > 0 && (
        <section className="market-group">
          <h2>이 PC의 {readyTools.join("·")}에 맞는 추천</h2>
          <div className="market-grid">{recommended.map(card)}</div>
        </section>
      )}
      {groups.map((g) => (
        <section key={g} className="market-group">
          <h2>{g}</h2>
          <div className="market-grid">{rest.filter((p) => (p.category || "기타") === g).map(card)}</div>
        </section>
      ))}
      {data && !shown.length && (
        <div className="empty-hero"><Store size={28} />
          <b>{terms.length ? "조건에 맞는 항목이 없습니다" : tab === "mine" ? "아직 등록한 항목이 없습니다" : tab === "installed" ? "아직 설치한 항목이 없습니다" : "아직 올라온 항목이 없습니다"}</b>
          <p>{terms.length ? "다른 말로 찾아보세요." : "내가 만든 스킬 폴더를 등록하면 나는 바로 쓰고, 부서 검토를 거치면 다른 사람도 쓸 수 있습니다."}</p>
        </div>
      )}
      {data && <p className="small muted market-base">기본 스킬 {data.base_skills}종은 이미 들어 있습니다 → <Link to="/tools">CAD·CAE 툴</Link></p>}

      {sel && ((sel.status === "draft" && sel.mine) || draftOf === sel.id
        ? <RegisterDrawer draft={sel} onClose={closeSel} />
        : <DetailDrawer id={sel.id} tools={tools} known={known} off={off} onDraft={() => setDraftOf(sel.id)} onClose={closeSel} />)}
      {registering && <RegisterDrawer onClose={() => setRegistering(false)} />}
    </div>
  );
}

function Drawer({ title, sub, wide, onClose, children, foot }: { title: string; sub?: ReactNode; wide?: boolean; onClose: () => void; children: ReactNode; foot?: ReactNode }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector(".modal-back")) onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return createPortal(
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className={`drawer ${wide ? "market-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="drawer-head">
          <div className="grow"><h3>{title}</h3>{sub && <span className="small muted">{sub}</span>}</div>
          <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label="닫기" title="닫기 (Esc)"><X size={17} /></button>
        </header>
        <div className="drawer-body">{children}</div>
        {foot && <footer className="market-drawer-foot">{foot}</footer>}
      </aside>
    </>,
    document.body,
  );
}

/** 항목 자세히. 무엇을 할 수 있고, 내 PC에서 무엇을 하는지를 먼저 보여 준다 */
function DetailDrawer({ id, tools, known, off, onDraft, onClose }: { id: number; tools: Record<string, string>; known: boolean; off: boolean; onDraft: () => void; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [showFiles, setShowFiles] = useState(false);
  const [labels, setLabels] = useState<LabelValues | null>(null);
  const { data: p } = useQuery({ queryKey: ["market", "package", id], queryFn: () => get<MarketPackage>(`/api/market/packages/${id}`) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["market"] });
  const fail = (e: Error) => toast(e.message, true);
  const install = useMutation({ mutationFn: (useLatest: boolean) => put(`/api/market/installs/${id}`, { consent: true, use_latest: useLatest }), onSuccess: refresh, onError: fail });
  const retry = useMutation({ mutationFn: () => post(`/api/market/installs/${id}/retry`, {}), onSuccess: () => { refresh(); toast("다시 받습니다. 끝나면 이 화면이 바뀝니다"); }, onError: fail });
  const remove = useMutation({ mutationFn: () => del(`/api/market/installs/${id}`), onSuccess: () => { refresh(); toast("제거했습니다. 복사된 스크립트는 남습니다"); setConfirmRemove(false); }, onError: fail });
  const row = p?.versions?.find((v) => v.version === p.version);
  const askReview = useMutation({
    mutationFn: () => post(`/api/market/packages/${id}/versions/${p?.version}/request-review`, { fingerprint: row?.fingerprint }),
    onSuccess: () => { refresh(); toast("부서에 검토를 요청했습니다"); }, onError: fail,
  });
  const saveLabels = useMutation({
    mutationFn: () => api(`/api/market/packages/${id}/versions/${p?.version}/labels`, { method: "PATCH", body: labelsToMeta(labels as LabelValues) }),
    onSuccess: () => { refresh(); setLabels(null); toast("이름표를 고쳤습니다"); }, onError: fail,
  });
  if (!p) return <Drawer title="불러오는 중" onClose={onClose}><p className="small muted">항목을 읽고 있습니다.</p></Drawer>;
  const b = buttonFor(p, off);
  const report = p.check_report;
  const st = p.install.state;
  const stateText = st === "failed" || st === "agent_too_old" ? p.install.error : st === "installed" && p.install.offline ? "이 PC에 설치되어 있습니다. PC Agent가 꺼져 있어 지금은 쓰지 않습니다 → 켜면 바로 씁니다" : st === "conflict" ? null : STATE_TEXT[st];
  const pinned = p.install.pinned ?? [];
  const conflicts = p.install.conflicts ?? [];
  const canEditLabels = p.mine && !!p.status && ["private", "review", "rejected"].includes(p.status);
  const canTryExample = st === "installed" || st === "conflict";
  return (
    <Drawer title={p.display_name ?? p.name} sub={`${p.name} ${p.version ?? ""}`} onClose={onClose} foot={
      <>
        {b.can && st === "failed" && <button className="primary" disabled={retry.isPending} onClick={() => retry.mutate()}>다시 시도</button>}
        {b.can && st !== "failed" && <button className="primary" disabled={install.isPending} onClick={() => install.mutate(st === "needs_consent")}>{st === "needs_consent" ? "확인하고 새 버전 쓰기" : p.code ? "내용을 확인했습니다. 설치" : "설치"}</button>}
        {p.install.installed && (confirmRemove
          ? <><span className="small muted grow">제거해도 AI가 복사한 스크립트는 남습니다.</span><button onClick={() => setConfirmRemove(false)}>취소</button><button className="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>제거</button></>
          : <button onClick={() => setConfirmRemove(true)}>제거</button>)}
        {p.mine && (p.status === "private" || p.status === "rejected") && <button disabled={askReview.isPending} onClick={() => askReview.mutate()}>{p.status === "rejected" ? "다시 검토 요청" : "부서에 검토 요청"}</button>}
        {p.mine && p.draft && <button onClick={onDraft}>초안 {p.draft.version} 이어서 고치기</button>}
      </>
    }>
      <Chips p={p} tools={tools} known={known} />
      {p.recalled && !p.install.version
        ? <div className="notice err">관리부서가 회수했습니다. 사유: {p.recalled.reason}{p.recalled.replacement && <> → 대체: {p.recalled.replacement}</>}</div>
        : stateText && <div className={`notice ${st === "failed" ? "err" : st === "installed" && !p.install.offline ? "ok" : "info"}`}>{stateText}</div>}
      {conflicts.length > 0 && (
        <div className="notice warn"><div>
          <b>이 {W.project}에서는 쓰지 못합니다.</b> 다른 {W.project}에서는 씁니다.
          <ul>{conflicts.map((c) => <li key={c.project}>{c.project}: {c.message}</li>)}</ul>
        </div></div>
      )}
      {pinned.length > 0 && (
        <div className="notice info market-pin"><div>
          {pinned.map((x) => <div key={x.project}>{x.project}: 진행 중인 {W.task}가 쓰던 {x.version}을 계속 씁니다. 계산이 섞이지 않게 붙들어 둔 것입니다.</div>)}
          <div className="small">그 {W.task}에 한 시간 동안 요청이 없으면 {p.version}(으)로 바뀝니다.</div>
          <button className="small" disabled={install.isPending} onClick={() => install.mutate(true)}>지금 {p.version}(으)로 바꾸기</button>
        </div></div>
      )}
      {p.status === "rejected" && p.review_note && <div className="notice warn">관리부서가 전사 공개를 반려했습니다: {p.review_note} → 나는 계속 쓸 수 있습니다. 고쳐서 다시 요청해 주세요.</div>}
      {p.status === "review" && p.mine && <div className="notice info">관리부서가 검토하고 있습니다. 승인되면 다른 사람도 쓸 수 있습니다. 결과는 마켓 메뉴의 숫자로 알려 줍니다.</div>}
      {p.status === "deprecated" && <div className="notice warn">관리부서가 내렸습니다{p.deprecated_note ? `: ${p.deprecated_note}` : "."} → 새로 설치할 수 없습니다. 이미 설치한 사람은 계속 씁니다. 비슷한 다른 항목을 찾아보세요.</div>}

      <h4>할 수 있는 일</h4>
      {labels
        ? <>
            <LabelFields value={labels} onChange={setLabels} />
            <div className="market-row-acts"><button className="small" onClick={() => setLabels(null)}>취소</button><button className="small primary" disabled={saveLabels.isPending} onClick={() => saveLabels.mutate()}>저장</button></div>
          </>
        : <>
            <p className="small">{p.description}</p>
            {canEditLabels && <button className="link-btn small" onClick={() => setLabels(labelValues(p))}>이름표 고치기</button>}
          </>}
      {!!p.skills?.length && <div className="tool-tags">{p.skills.map((s) => <span key={s}>스킬 {s}</span>)}</div>}

      {p.examples.length > 0 && (
        <>
          <h4>이렇게 말해 보세요</h4>
          {!canTryExample && <p className="small muted">설치한 뒤에 누를 수 있습니다. 설치하지 않으면 AI에게 이 스킬이 없습니다.</p>}
          <div className="market-examples">
            {p.examples.map((x) => <button key={x} className="small" disabled={!canTryExample} onClick={() => nav("/", { state: { seed: x } })} title={`새 ${W.project}의 입력창에 채웁니다`}>{x}</button>)}
          </div>
        </>
      )}
      <p className="small muted">설치해도 AI가 반드시 쓰는 것은 아닙니다. 요청에 맞을 때 AI가 쓸 수 있습니다.</p>

      <h4>필요한 툴</h4>
      {p.requires_tools.length
        ? <div className="tool-tags">{p.requires_tools.map((t) => <span key={t}>{tools[t] ?? t}{known ? (p.tools_missing.includes(t) ? " · 이 PC에 없음" : " · 이 PC에 있음") : ""}</span>)}</div>
        : <p className="small muted">특정 툴이 없어도 됩니다.</p>}

      <h4>내 PC에서 하는 일</h4>
      {p.code ? (
        <div className="notice warn market-consent">
          <div>
            <b>내 PC에서 내 권한으로 실행됩니다.</b>
            {p.capabilities.length > 0 && <p>자동 검사가 찾은 일: {p.capabilities.map((c) => c.label).join(", ")}. 이 외의 일도 할 수 있습니다.</p>}
            {!!p.install.new_caps?.length && <p>새 버전에서 늘어난 일: {p.install.new_caps.map((c) => c.label).join(", ")}</p>}
            <p>자동 검사는 Python 파일만 봅니다. 다른 스크립트는 사람이 읽어야 합니다.</p>
          </div>
        </div>
      ) : <p className="small">문서뿐입니다. AI가 읽고 따릅니다. 문서만으로도 AI의 행동은 바뀔 수 있습니다.</p>}
      {p.code && <p className="small muted">같은 {W.project}에 진행 중인 {W.task}가 있으면 그 {W.task}가 쓰던 버전을 유지합니다. 그 {W.task}가 끝나거나 한 시간 쉬면 새 버전이 적용됩니다.</p>}

      <h4>시험 기록</h4>
      {p.evidence
        ? <><p className="small muted">등록자가 적은 내용입니다. 관리부서가 확인한 기록이 아닙니다.</p><pre className="code-block market-code">{JSON.stringify(p.evidence, null, 1).slice(0, 2000)}</pre></>
        : <p className="small">실제 툴 시험 기록 없음</p>}
      {p.grade !== "mine" && <p className="small muted">"검토 완료"는 사람이 내용을 읽었다는 뜻입니다. 실제 툴에서 돌려 봤다는 뜻은 아닙니다.</p>}

      {report && (report.flags.length > 0 || report.warnings.length > 0) && (
        <>
          <h4>자동 검사</h4>
          <MarketChecks report={report} />
        </>
      )}

      <h4>변경 내역</h4>
      <ul className="market-history">
        {(p.versions ?? []).map((v) => (
          <li key={v.version}><b>{v.version}</b> <span className="muted small">{v.status_label} · {when(v.created_at)}</span>{v.notes && <div className="small">{v.notes}</div>}
            {v.recall_reason && <div className="small">회수 사유: {v.recall_reason}</div>}</li>
        ))}
      </ul>
      <p className="small muted">만든 사람: {p.owner_name}{row?.reviewed_by ? ` · 검토: ${row.reviewed_by}` : ""}</p>

      {p.version && (showFiles
        ? <MarketFiles pkg={p.id} version={p.version} />
        : <button className="link-btn small" onClick={() => setShowFiles(true)}><FileCode2 size={13} />파일 보기</button>)}
    </Drawer>
  );
}

/** 폴더를 끌어다 놓을 때 하위 항목을 읽는다. 뺄 폴더는 들어가지 않는다: .venv가 있으면 수만 개를 읽느라 멈춘다 */
async function readEntry(entry: FileSystemEntry, prefix: string, out: { path: string; file: File }[]): Promise<void> {
  if (entry.isFile) {
    if (JUNK_FILES.has(entry.name.toLowerCase())) return;
    const file = await new Promise<File>((ok, no) => (entry as FileSystemFileEntry).file(ok, no));
    out.push({ path: prefix + entry.name, file });
  } else if (!JUNK.has(entry.name)) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) { // 한 번에 100개씩 돌려준다
      const batch = await new Promise<FileSystemEntry[]>((ok, no) => reader.readEntries(ok, no));
      if (!batch.length) return;
      for (const child of batch) await readEntry(child, `${prefix}${entry.name}/`, out);
    }
  }
}

function fromInput(files: FileList): { path: string; file: File }[] {
  const out: { path: string; file: File }[] = [];
  for (const file of Array.from(files)) {
    const path = file.webkitRelativePath || file.name;
    const parts = path.split("/");
    if (parts.slice(0, -1).some((x) => JUNK.has(x)) || JUNK_FILES.has(parts[parts.length - 1].toLowerCase())) continue;
    out.push({ path, file });
  }
  return out;
}

/** 형식은 부서가 정하고 등록하는 사람이 볼 수 있어야 한다. 엔지니어에게는 웹앱과 PC Agent뿐이고 저장소가 없다 */
function FormatGuide() {
  const toast = useToast();
  const [full, setFull] = useState(false);
  const { data } = useQuery({ queryKey: ["market", "format"], queryFn: () => get<{ spec: string; templates: { name: string; title: string; description: string }[] }>("/api/market/format"), staleTime: Infinity });
  return (
    <div className="market-format">
      <h4>처음이면</h4>
      <p className="small"><b>SKILL.md</b>는 AI에게 주는 작업 설명서입니다. 첫머리에 언제 쓰는 스킬인지 한 줄을 적으면 됩니다. 시작 틀을 받아 고쳐 올리세요.</p>
      <div className="market-format-list">
        {(data?.templates ?? []).map((t) => (
          <button key={t.name} className="small" title={t.description} onClick={() => download(`/api/market/templates/${t.name}`, `${t.name}.zip`).catch((e: Error) => toast(e.message, true))}>
            <Download size={13} />{t.title}
          </button>
        ))}
      </div>
      {data?.spec && (full
        ? <div className="market-spec"><button className="link-btn small" onClick={() => setFull(false)}>닫기</button><Markdown text={data.spec} /></div>
        : <button className="link-btn small" onClick={() => setFull(true)}>형식과 기준 전체 보기</button>)}
    </div>
  );
}

/** 등록: 폴더나 zip을 놓으면 서버가 읽어 양식을 채우고 검사한다. 왼쪽은 채운 값, 오른쪽은 검사 결과다 */
function RegisterDrawer({ draft, onClose }: { draft?: MarketPackage; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { user } = useAuth();
  const [sub, setSub] = useState<MarketSubmission | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [official, setOfficial] = useState(false);
  const [form, setForm] = useState({ display_name: "", description: "", category: "", examples: "", notes: "" });
  const [reqTools, setReqTools] = useState<string[]>([]); // 서버의 어림값이다. 틀리면 카드에 "이 PC에 없음"이 잘못 뜨므로 사람이 고친다
  const { data: cat } = useQuery({ queryKey: ["tools", "catalog"], queryFn: () => get<{ tools: { id: string; name: string }[] }>("/api/tools/catalog"), staleTime: Infinity });
  const folderRef = useRef<HTMLInputElement>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  useEffect(() => { folderRef.current?.setAttribute("webkitdirectory", ""); }, []);
  const { data: fullDraft } = useQuery({ queryKey: ["market", "package", draft?.id], enabled: !!draft, queryFn: () => get<MarketPackage>(`/api/market/packages/${draft?.id}`) });
  const pkg = sub?.package ?? fullDraft;
  // 공개 중인 항목의 새 버전 초안도 같은 길로 고친다. 값은 언제나 초안 쪽을 본다
  const d = pkg?.draft;
  const report = d?.check_report ?? null;
  const version = d?.version ?? null;
  const blocked = d?.self_approve_blocked ?? false;
  const flags: MarketFlag[] = report?.flags ?? [];
  const acks = useAcks(flags);
  useEffect(() => {
    if (d) {
      setForm({ display_name: d.display_name ?? "", description: d.description ?? "", category: d.category ?? "", examples: (d.examples ?? []).join("\n"), notes: d.notes ?? "" });
      setReqTools(d.requires_tools ?? []);
    }
  }, [pkg?.id, d?.fingerprint]); // eslint-disable-line react-hooks/exhaustive-deps

  const toolName = (id: string) => cat?.tools.find((t) => t.id === id)?.name ?? id;
  const meta = () => ({ display_name: form.display_name, description: form.description, category: form.category, notes: form.notes, requires_tools: reqTools,
    examples: form.examples.split("\n").map((x) => x.trim()).filter(Boolean) });
  const send = async (rows: { path: string; file: File }[] | null, zip?: File) => {
    setError(null);
    if (rows && !rows.length) { setError("올릴 파일이 없습니다 → SKILL.md가 있는 폴더를 놓아 주세요. .venv·.git·__pycache__·node_modules는 뺍니다"); return; }
    if (rows && rows.length > MAX_FILES) { setError(`파일이 ${MAX_FILES}개를 넘습니다(${rows.length}개) → 필요한 스크립트와 문서만 남기거나 해석 결과 폴더를 빼 주세요`); return; }
    // 보내기 전에 본다. 결과 파일이 든 폴더를 고르면 다 올린 뒤에야 거절당하고 그동안 서버가 그것을 받느라 바쁘다
    const big = rows?.find((r) => r.file.size > MAX_FILE_BYTES);
    if (big) { setError(`파일이 5MB를 넘습니다: ${big.path} (${Math.ceil(big.file.size / MB)}MB) → 해석 결과·모델 파일은 빼고 스크립트와 문서만 올려 주세요`); return; }
    if (rows && rows.reduce((a, r) => a + r.file.size, 0) > MAX_FOLDER_BYTES) { setError("폴더가 60MB를 넘습니다 → 스크립트와 문서만 남겨 주세요"); return; }
    if (zip && zip.size > MAX_ZIP_BYTES) { setError(`zip이 20MB를 넘습니다(${Math.ceil(zip.size / MB)}MB) → 해석 결과·모델 파일은 빼고 다시 묶어 주세요`); return; }
    const data = new FormData();
    if (zip) data.append("file", zip, zip.name);
    else { for (const r of rows ?? []) data.append("files", r.file, r.file.name); data.append("paths", JSON.stringify((rows ?? []).map((r) => r.path))); }
    setBusy(true);
    try {
      setSub(await api<MarketSubmission>("/api/market/submissions", { form: data }));
      qc.invalidateQueries({ queryKey: ["market"] });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const onDrop = async (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const items = Array.from(e.dataTransfer.items).map((i) => i.webkitGetAsEntry()).filter(Boolean) as FileSystemEntry[];
    if (items.length === 1 && items[0].isFile && items[0].name.toLowerCase().endsWith(".zip")) { await send(null, e.dataTransfer.files[0]); return; }
    const rows: { path: string; file: File }[] = [];
    for (const it of items) await readEntry(it, "", rows);
    await send(rows);
  };
  const base = pkg && version ? `/api/market/packages/${pkg.id}/versions/${version}` : "";
  const done = (text: string) => { qc.invalidateQueries({ queryKey: ["market"] }); toast(text); onClose(); };
  const fail = (e: Error) => setError(e.message);
  const seen = d?.fingerprint; // 지금 화면에 보이는 내용에만 결정이 적용된다
  const mine = useMutation({ mutationFn: () => post(`${base}/private`, { meta: meta(), fingerprint: seen }), onSuccess: () => done(`나만 쓰기로 정했습니다. AI가 쓸 수 있는 ${W.task}에는 다음 요청 때 알려 줍니다`), onError: fail });
  const review = useMutation({ mutationFn: () => post(`${base}/request-review`, { meta: meta(), fingerprint: seen }), onSuccess: () => done("부서에 검토를 요청했습니다"), onError: fail });
  const publish = useMutation({
    mutationFn: (pin: string) => post(`${base}/approve`, { pin, acks: flags.map((f) => f.rule), official, meta: meta(), fingerprint: seen }),
    onSuccess: () => { setPinOpen(false); done("공개했습니다"); }, onError: (e: Error) => { setPinOpen(false); fail(e); },
  });
  const discard = useMutation({ mutationFn: () => del(base), onSuccess: () => done("초안을 버렸습니다"), onError: fail });
  const working = mine.isPending || review.isPending || publish.isPending || discard.isPending;
  const canPublish = !!user?.is_admin && !blocked;
  const ready = !!(pkg && version && report);

  return (
    <Drawer title={ready ? (form.display_name || pkg.name) : "등록"} sub={ready ? `${pkg.name} ${version ?? ""} · 초안` : "내가 만든 스킬을 올립니다"} wide={ready} onClose={onClose} foot={ready ? (
      <>
        <button className="link-btn small" disabled={working} onClick={() => discard.mutate()}>초안 버리기</button>
        <span className="grow" />
        <button disabled={working} onClick={() => mine.mutate()}>나만 쓰기</button>
        <button disabled={working} onClick={() => review.mutate()}>부서에 검토 요청</button>
        {canPublish && <button className="primary" disabled={working || !acks.allChecked} title={acks.allChecked ? "" : "자동 검사가 표시한 항목을 모두 확인하세요"} onClick={() => setPinOpen(true)}><ShieldCheck size={14} />바로 공개</button>}
      </>
    ) : undefined}>
      {error && <div className="notice err">{error}</div>}
      {!ready && !draft && (
        <>
          <div className={`market-drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
            <FolderUp size={28} />
            <b>{busy ? "읽고 검사하는 중입니다" : "폴더나 zip을 여기에 놓으세요"}</b>
            <span className="small muted">SKILL.md가 있는 폴더면 됩니다. 나머지는 서버가 읽어 채웁니다.</span>
            <div className="market-drop-btns">
              <button className="small" disabled={busy} onClick={() => folderRef.current?.click()}>폴더 고르기</button>
              <button className="small" disabled={busy} onClick={() => zipRef.current?.click()}>zip 고르기</button>
            </div>
            <input ref={folderRef} type="file" multiple hidden aria-label="폴더 고르기" onChange={(e) => { if (e.target.files) send(fromInput(e.target.files)); e.target.value = ""; }} />
            <input ref={zipRef} type="file" accept=".zip" hidden aria-label="zip 고르기" onChange={(e) => { const f = e.target.files?.[0]; if (f) send(null, f); e.target.value = ""; }} />
          </div>
          <ul className="small muted market-rules">
            <li>올린 코드는 서버에서 실행하지 않습니다.</li>
            <li>나만 쓰기는 바로 내 PC에 설치됩니다. 다른 사람이 쓰려면 부서 검토가 필요합니다.</li>
            <li>해석 결과·모델 파일과 비밀값은 넣지 마세요. 파일당 5MB까지입니다.</li>
          </ul>
          <FormatGuide />
        </>
      )}
      {!ready && draft && <p className="small muted">초안을 읽고 있습니다.</p>}
      {ready && (
        <div className="market-reg">
          <div className="market-reg-form">
            <h4>서버가 채운 값</h4>
            <div className="field"><span className="lbl">화면에 보일 이름</span><input type="text" value={form.display_name} maxLength={100} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></div>
            <div className="field"><span className="lbl">한 줄 설명 (100자까지)</span><textarea rows={3} value={form.description} maxLength={100} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
            <div className="field"><span className="lbl">분류 (예: 후처리, 보고서)</span><input type="text" value={form.category} maxLength={40} onChange={(e) => setForm({ ...form, category: e.target.value })} /></div>
            <div className="field"><span className="lbl">이렇게 말해 보세요 (한 줄에 하나)</span><textarea rows={3} value={form.examples} onChange={(e) => setForm({ ...form, examples: e.target.value })} /></div>
            <div className="field"><span className="lbl">이번 버전에서 바뀐 내용</span><input type="text" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
            <div className="field"><span className="lbl">필요한 툴 (누르면 뺍니다)</span>
              <div className="market-tool-edit">
                {reqTools.map((t) => <button key={t} type="button" className="small" title="빼기" onClick={() => setReqTools(reqTools.filter((x) => x !== t))}>{toolName(t)} <X size={12} /></button>)}
                <select aria-label="필요한 툴 더하기" value="" onChange={(e) => { if (e.target.value) setReqTools([...reqTools, e.target.value]); }}>
                  <option value="">툴 더하기</option>
                  {(cat?.tools ?? []).filter((t) => !reqTools.includes(t.id)).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
            </div>
            <p className="small muted">종류: {d?.code ? "스킬 · 코드 있음" : "스킬"}</p>
            {user?.is_admin && !blocked && <label className="small market-check"><input type="checkbox" checked={official} onChange={(e) => setOfficial(e.target.checked)} />관리부서 제공으로 공개 (부서가 유지 책임을 집니다)</label>}
            {blocked && <div className="notice info">코드가 있는 항목은 다른 관리자가 승인합니다 → 부서에 검토를 요청하세요</div>}
          </div>
          <div className="market-reg-check">
            <h4>검사 결과</h4>
            <div className="notice ok">자동 검사 통과. 자동 검사는 탐지일 뿐이며 안전을 보장하지 않습니다.</div>
            <MarketChecks report={report} acks={canPublish ? acks : undefined} />
            <MarketFiles pkg={pkg.id} version={version} />
          </div>
        </div>
      )}
      {pinOpen && (
        <PinModal title="바로 공개" confirm="공개" busy={publish.isPending} onClose={() => setPinOpen(false)} onConfirm={(pin) => publish.mutate(pin)}>
          회사 전체가 이 항목을 설치할 수 있게 됩니다. 내용을 읽었다는 기록이 남습니다.
        </PinModal>
      )}
    </Drawer>
  );
}
