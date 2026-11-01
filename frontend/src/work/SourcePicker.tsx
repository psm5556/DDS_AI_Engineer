import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { get } from "../api";
import type { WorkSource } from "../types";
import { COPY, W } from "./copy";

/** 지난 Task처럼 시작: 입력창 곁의 목록. 창을 띄워 막지 않고, 고르지 않으면 처음부터 한다. 지난 Task가 없으면 아예 보이지 않는다.
 *  pid가 없으면 첫 화면이다. 내가 멤버인 Project 전체에서 고르고, 그 값은 새 Project로 옮겨 간다(서버가 other_project로 알린다). */
export function SourcePicker({ pid, picked, onPick, preselect }: { pid: number | null; picked: WorkSource | null; onPick: (s: WorkSource | null) => void; preselect?: number }) {
  const [searching, setSearching] = useState(false);
  const [q, setQ] = useState("");
  const term = q.trim();
  const { data } = useQuery({
    queryKey: ["work-sources", pid, term],
    queryFn: () => get<WorkSource[]>(`/api/works/sources?${new URLSearchParams({ ...(pid ? { project_id: String(pid) } : {}), ...(term ? { q: term } : {}) })}`),
  });
  // 경로 카드에서 왔으면 그 Task를 고른 채로 시작한다
  const applied = useRef(false);
  useEffect(() => {
    if (!preselect || applied.current || !data) return;
    applied.current = true;
    const hit = data.find((s) => s.work_id === preselect);
    if (hit) onPick(hit);
  }, [preselect, data, onPick]);
  if (picked) return <SourceCard s={picked} onClear={() => onPick(null)} />;
  if (!data?.length && !searching) return null;
  return (
    <div className="src-pick" aria-label={COPY.sourceTitle}>
      <div className="src-head">
        <b>{COPY.sourceTitle}</b><span className="small muted">{COPY.sourceHint}</span>
        <span className="grow" />
        {searching
          ? <button type="button" className="icon-btn" aria-label="찾기 닫기" title="찾기 닫기" onClick={() => { setSearching(false); setQ(""); }}><X size={13} /></button>
          : <button type="button" className="link-btn" onClick={() => setSearching(true)}><Search size={12} />{COPY.sourceMore}</button>}
      </div>
      {searching && <input autoFocus type="text" className="src-find" value={q} placeholder={`${W.project}나 ${W.task} 이름`} aria-label={COPY.sourceMore} onChange={(e) => setQ(e.target.value)} />}
      <div className="src-rows">
        {(data ?? []).map((s) => (
          <button type="button" key={s.work_id} className="src-row" onClick={() => onPick(s)} title={COPY.sourceRowTip}>
            <span className="t">{s.other_project ? `${s.project_name} › ` : ""}{s.name}</span>
            <span className="small muted">{[s.mine ? "나" : s.owner_name, s.n_steps ? COPY.sourceSteps(s.n_steps) : null, s.result || null].filter(Boolean).join(" · ")}</span>
          </button>
        ))}
        {searching && term && data && !data.length && <p className="small muted">{COPY.sourceNone}</p>}
      </div>
    </div>
  );
}

function SourceCard({ s, onClear }: { s: WorkSource; onClear: () => void }) {
  return (
    <div className="src-card" role="status">
      <div className="src-head"><b>{COPY.sourcePicked(s.name)}</b><span className="grow" /><button type="button" className="link-btn" onClick={onClear}>{COPY.sourceClear}</button></div>
      <p className="small">{COPY.sourceBring}</p>
      <p className="small">{COPY.sourceLeave}</p>
      {s.script_author && <p className="small muted">{COPY.sourceAuthor(s.script_author)}</p>}
      {s.other_project && <p className="small muted">{COPY.sourceSecret}</p>}
      {!s.has_recipe && <p className="small muted">{COPY.sourceNoRecipe}</p>}
    </div>
  );
}
