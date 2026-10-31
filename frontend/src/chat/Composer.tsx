import { ArrowUp, FileUp, Loader2, Plus, Square, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { uploadFile } from "../api";
import { TableQuestion, useTableJobs } from "../canvas/TableImport";
import { looksLikeTable, PASTE_MAX_BYTES, PASTE_TOO_BIG } from "../format";
import { useToast } from "../toast";
import { reducedMotion } from "../motion";
import type { Attachment } from "../types";

/** 보낼 때 넘기는 첨부: 이미 올린 것(id)과, 프로젝트가 아직 없어 나중에 올릴 파일 */
export interface Outgoing { text: string; attachmentIds: number[]; pendingFiles: File[] }

interface Item { key: string; file: File; progress: number; att?: Attachment; error?: string }

const HISTORY_KEY = "dds.prompt.history";
export function rememberPrompt(t: string) {
  try {
    const h = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]") as string[];
    localStorage.setItem(HISTORY_KEY, JSON.stringify([t, ...h.filter((x) => x !== t)].slice(0, 50)));
  } catch { /* 기억 못 해도 동작 */ }
}
function history(): string[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]") as string[]; } catch { return []; }
}

const MAKING_TEXT = "표로 Simulator를 만드는 중입니다. 끝나면 이어서 요청할 수 있습니다.";
const QUEUED_TEXT = "지금 표를 만드는 중이라 붙여 넣은 표는 아직 받지 않았습니다. 끝나면 바로 물어보겠습니다.";
const AI_BUSY_TABLE_TEXT = "AI가 일하는 중입니다. 지금은 위 버튼으로 바로 만들 수 있고, AI에게 맡기려면 끝난 뒤 보내세요.";
const QUEUE_KEY = "dds.queued.table"; // 첫 화면에서 만들면 프로젝트 화면으로 넘어간다. 기다리는 표를 잃지 않게 입력창 밖에 둔다
const CHECK_SUFFIX = /\.(csv|tsv|xlsx|xlsm|xlsb|xls)$/i;
function readQueued(): string | null {
  try { return sessionStorage.getItem(QUEUE_KEY); } catch { return null; }
}
function writeQueued(t: string | null) {
  try { if (t == null) sessionStorage.removeItem(QUEUE_KEY); else sessionStorage.setItem(QUEUE_KEY, t); } catch { /* 저장 못 해도 이 화면에서는 기억한다 */ }
}

const fmtSize = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

/** 입력창: → 키로 추천 프롬프트 받기(회색 글자로 미리 보임) · 파일 첨부(버튼·끌어 놓기·붙여넣기) · Enter 보내기
 *  projectId가 있으면 첨부를 바로 올리고, 없으면(첫 화면) 보낼 때 pendingFiles로 넘긴다. */
export function Composer({
  projectId, suggestions, onSend, busy, stopping, onStop, disabled, placeholder, big, leftBar, autoFocus, hint, seed, typing, onSimulator,
}: {
  /** 표를 붙여 넣으면 AI 없이 만든 Simulator를 여는 곳 */
  onSimulator: (projectId: number, studyId: number, modelId: number) => void;
  projectId?: number; suggestions: string[]; onSend: (o: Outgoing) => void | boolean | Promise<void | boolean>; busy?: boolean;
  /** 중지를 눌렀고 끝나기를 기다리는 중 */
  stopping?: boolean;
  onStop?: () => void;
  disabled?: boolean; placeholder: string; big?: boolean; leftBar?: ReactNode; autoFocus?: boolean; hint?: string;
  /** 밖에서 입력창을 채울 때(예: 시작 버튼) — n이 바뀔 때마다 text로 채운다. attachments: 이미 올린 첨부도 되돌린다. askFile: 파일을 달라는 안내 */
  seed?: { text: string; n: number; attachments?: Attachment[]; askFile?: string };
  /** 비어 있을 때 추천을 타이핑하듯 보이고 다음 추천으로 넘어간다(첫 화면). → 키는 그대로 전체를 받는다 */
  typing?: boolean;
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [pick, setPick] = useState(0); // 빈 입력에서 ↑↓로 고르는 추천 순번
  const [drag, setDrag] = useState(false);
  const [askFile, setAskFile] = useState<string | null>(null); // 채운 요청에 파일이 있어야 할 때의 안내. 파일을 고르거나 보내면 사라진다
  const [table, setTable] = useState<string | null>(null); // 붙여 넣은 표. 글자를 입력창에 넣지 않는다: 행이 AI 요청 글이 되면 안 된다
  const [makingHere, setMakingHere] = useState(false); // 이 입력창의 표를 만드는 중
  const [queued, setQueued] = useState<string | null>(readQueued); // 만드는 중에 붙여 넣은 다음 표
  const jobs = useTableJobs();
  // 새로고침하거나 창에서 시작했어도 서버 작업을 본다. 같은 프로젝트에서 표가 만들어지는 동안 AI로 같이 보내면 진행이 엉킨다
  const making = makingHere || (projectId != null && jobs.some((j) => j.status === "running" && j.project_id === projectId));
  const fromQueue = useRef(false);
  const leaving = useRef(false); // 첫 화면에서 다 만들면 프로젝트 화면으로 넘어간다. 기다리던 표는 그 화면의 입력창이 묻는다
  useEffect(() => { // 먼저 표가 끝나면 기다리던 표를 바로 묻는다
    if (queued == null || making || table != null || leaving.current) return;
    setTable(queued);
    setQueued(null);
    fromQueue.current = true; // 저장은 사용자가 답할 때 지운다. 화면이 입력창을 다시 만들어도 물음이 사라지지 않게
  }, [queued, making, table]);
  const answered = useCallback(() => { if (fromQueue.current) { fromQueue.current = false; writeQueued(null); } }, []);
  const onMaking = useCallback((m: boolean) => { setMakingHere(m); if (m) answered(); }, [answered]);
  const ta =useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (autoFocus) ta.current?.focus(); }, [autoFocus]);
  // 기다리는 동안 입력창이 막혀 커서가 사라졌다. 끝나면 돌려준다(다른 곳을 보고 있으면 빼앗지 않는다)
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !busy && !disabled && document.activeElement === document.body) ta.current?.focus();
    wasBusy.current = !!busy;
  }, [busy, disabled]);
  useLayoutEffect(() => { // 화면을 그리기 전에 채운다 — 바로 Enter를 눌러도 빈 입력으로 보내지 않게
    if (!seed) return;
    setText(seed.text);
    setAskFile(seed.askFile ?? null);
    if (seed.attachments?.length) {
      const back = seed.attachments.map((att) => ({ key: `att-${att.id}`, file: new File([], att.filename), progress: 1, att }));
      setItems((xs) => [...xs.filter((x) => !back.some((b) => b.key === x.key)), ...back]);
    }
    requestAnimationFrame(() => { const el = ta.current; if (el) { el.focus(); el.setSelectionRange(seed.text.length, seed.text.length); } });
  }, [seed]);

  // 추천: 비어 있으면 상황별 추천을 차례로, 입력 중이면 그 글자로 시작하는 추천·지난 요청
  const ghost = useMemo(() => {
    if (disabled || busy || table != null) return ""; // 표를 물어보는 동안 추천 글자가 돌면 산만하다
    if (!text) return suggestions.length ? suggestions[pick % suggestions.length] : "";
    if (text.length < 2) return "";
    const low = text.toLowerCase();
    const pool = [...suggestions, ...history()];
    const hit = pool.find((s) => s.toLowerCase().startsWith(low) && s.length > text.length);
    return hit ? text + hit.slice(text.length) : "";
  }, [text, suggestions, pick, disabled, busy, table]);

  // 타이핑 효과: 보이는 글자 수만 늘린다. 입력을 시작하면 바로 멈춘다
  const animate = !!typing && !text && !disabled && !!ghost && !reducedMotion();
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!animate) return;
    setShown(0);
    let n = 0;
    let hold: ReturnType<typeof setTimeout> | undefined;
    const tick = setInterval(() => {
      n += 1;
      setShown(n);
      if (n >= ghost.length) {
        clearInterval(tick);
        hold = setTimeout(() => setPick((p) => p + 1), 3200);
      }
    }, 26);
    return () => { clearInterval(tick); if (hold) clearTimeout(hold); };
  }, [animate, ghost]);
  const ghostShown = animate ? ghost.slice(0, shown) : ghost.slice(text.length);

  const accept = () => {
    if (!ghost) return false;
    setText(ghost);
    requestAnimationFrame(() => { const el = ta.current; if (el) { el.focus(); el.setSelectionRange(ghost.length, ghost.length); } });
    return true;
  };

  const addFiles = (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setAskFile(null);
    const fresh: Item[] = list.map((f) => ({ key: `${f.name}-${f.size}-${Math.random()}`, file: f, progress: projectId ? 0 : 1 }));
    setItems((xs) => [...xs, ...fresh]);
    if (!projectId) {
      // 첫 화면: 프로젝트가 아직 없어 올리지 않지만, 암호화된 표는 지금 알린다. AI가 시작된 뒤 몇 분 뒤에 듣지 않게
      for (const it of fresh.filter((x) => CHECK_SUFFIX.test(x.file.name))) {
        uploadFile("/api/tables/check", it.file).catch((e: Error) => {
          setItems((xs) => xs.map((x) => (x.key === it.key ? { ...x, error: e.message } : x)));
          toast(`${it.file.name}: ${e.message}`, true);
        });
      }
      return;
    }
    for (const it of fresh) {
      uploadFile<Attachment>(`/api/projects/${projectId}/attachments`, it.file,
        (r) => setItems((xs) => xs.map((x) => (x.key === it.key ? { ...x, progress: r } : x))))
        .then((att) => setItems((xs) => xs.map((x) => (x.key === it.key ? { ...x, att, progress: 1 } : x))))
        .catch((e: Error) => {
          setItems((xs) => xs.map((x) => (x.key === it.key ? { ...x, error: e.message } : x)));
          toast(`${it.file.name}: ${e.message}`, true);
        });
    }
  };

  const uploading = items.some((i) => projectId && !i.att && !i.error);
  const canSend = !disabled && !busy && !making && !uploading && (text.trim() || table != null || items.some((i) => (i.att || !projectId) && !i.error));
  const submit = async () => {
    if (making) { toast(MAKING_TEXT, true); return; }
    if (!canSend) return;
    answered();
    const t = text.trim();
    if (t) rememberPrompt(t);
    const out: Outgoing = {
      text: t, attachmentIds: items.filter((i) => i.att).map((i) => i.att!.id),
      pendingFiles: projectId ? [] : items.filter((i) => !i.error).map((i) => i.file),
    };
    // AI로 보내기로 한 표만 서버에 첨부로 저장한다. 행은 AI 도구 인자가 아니라 파일로 간다
    if (table != null) {
      const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
      const file = new File([table], `붙여넣은_표_${stamp}.tsv`, { type: "text/tab-separated-values" });
      if (projectId) {
        try { out.attachmentIds.push((await uploadFile<Attachment>(`/api/projects/${projectId}/attachments`, file)).id); }
        catch (e) { toast(`표를 저장하지 못했습니다: ${(e as Error).message}`, true); return; }
      } else out.pendingFiles.push(file);
    }
    const keep = { text, items, askFile, table };
    setText("");
    setItems([]);
    setAskFile(null);
    setTable(null);
    // 보내기가 실패했다고 알려 오면(false) 적은 요청과 첨부를 되돌린다 — 다시 적지 않아도 되게
    if ((await onSend(out)) === false) {
      setText(keep.text);
      setItems(keep.items);
      setAskFile(keep.askFile);
      setTable(keep.table);
    }
  };

  return (
    <form className={`composer ${big ? "big" : ""} ${drag ? "drag" : ""}`}
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (!disabled) addFiles(e.dataTransfer.files); }}>
      {askFile && (
        <div className="composer-ask" role="status">
          <FileUp size={14} /><span className="grow">{askFile}</span>
          <button type="button" className="small primary" disabled={disabled} onClick={() => fileRef.current?.click()}>파일 고르기</button>
          <button type="button" className="icon-btn" aria-label="안내 닫기" title="안내 닫기" onClick={() => setAskFile(null)}><X size={13} /></button>
        </div>
      )}
      {table != null && (
        <div className="composer-table">
          <TableQuestion pid={projectId ?? null} text={table} onCancel={() => { answered(); setTable(null); }} onMaking={onMaking}
            onOpen={(p, s, m) => { leaving.current = !projectId; setTable(null); onSimulator(p, s, m); }} />
        </div>
      )}
      {queued != null && making && <div className="composer-ask" role="status"><span className="grow">{QUEUED_TEXT}</span>
        <button type="button" className="icon-btn" aria-label="기다리는 표 버리기" title="기다리는 표 버리기" onClick={() => { setQueued(null); writeQueued(null); }}><X size={13} /></button></div>}
      {items.length > 0 && (
        <div className="composer-files">
          {items.map((i) => (
            <span key={i.key} className={`file-chip ${i.error ? "err" : ""}`} title={i.error ?? i.file.name}>
              {projectId && !i.att && !i.error ? <Loader2 size={12} className="spin" /> : <FileUp size={12} />}
              <span className="n">{i.file.name}</span>
              <span className="s">{i.error ? "실패" : projectId && !i.att ? `${Math.round(i.progress * 100)}%` : fmtSize(i.att?.size ?? i.file.size)}</span>
              <button type="button" className="x" aria-label={`${i.file.name} 빼기`} onClick={() => setItems((xs) => xs.filter((x) => x.key !== i.key))}><X size={11} /></button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-input">
        {/* 회색 추천 글자: 입력한 글자 뒤에 이어서 보인다 (→ 또는 Tab으로 받기) */}
        <div className="composer-ghost" aria-hidden="true"><span className="typed">{text}</span>{ghostShown}{animate && <i className="type-caret" />}</div>
        {/* PC Agent가 없거나 AI가 일하는 중에도 표는 붙여 넣을 수 있어야 한다. disabled면 붙여넣기 이벤트가 오지 않아 readOnly로 막는다 */}
        <textarea ref={ta} value={text} rows={big ? 3 : 1} readOnly={disabled || busy}
          placeholder={making ? MAKING_TEXT : table != null ? (busy ? AI_BUSY_TABLE_TEXT : disabled ? "AI에게 맡기려면 PC Agent 연결이 필요합니다. 위 버튼으로 바로 만들 수 있습니다" : "AI에게 맡기려면 요청을 적어 보내세요. 이 표를 첨부합니다") : ghost ? "" : placeholder}
          aria-label="에이전트에게 요청" onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            // 표를 먼저 본다. 엑셀 복사에는 그림 파일도 함께 들어 있어 파일부터 보면 표가 그림으로 첨부된다
            const pasted = e.clipboardData.getData("text/plain");
            if (looksLikeTable(pasted)) {
              e.preventDefault();
              if (pasted.length > PASTE_MAX_BYTES) toast(PASTE_TOO_BIG, true);
              else if (making) { setQueued(pasted); writeQueued(pasted); toast(QUEUED_TEXT); } // 만드는 표를 덮어쓰지 않고, 조용히 사라지지도 않게 한다
              else { answered(); setTable(pasted); }
            } else if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); }
          }}
          onKeyDown={(e) => {
            const el = e.currentTarget;
            const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length;
            if ((e.key === "ArrowRight" || e.key === "Tab") && atEnd && ghost && ghost !== text) { e.preventDefault(); accept(); return; }
            if (!text && suggestions.length > 1 && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
              e.preventDefault();
              setPick((n) => (n + (e.key === "ArrowDown" ? 1 : suggestions.length - 1)) % suggestions.length);
              return;
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit(); }
          }} />
      </div>
      <div className="composer-bar">
        <button type="button" className="icon-btn composer-attach" onClick={() => fileRef.current?.click()} disabled={disabled}
          title="파일 첨부 (CAD 모델·입력 파일·자료) — 끌어 놓거나 붙여넣어도 됩니다" aria-label="파일 첨부"><Plus size={18} /></button>
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        {/* 회색 추천이 보일 때만 짧게 안내 (Claude·Codex처럼 입력창은 비워 둔다) */}
        {ghost && ghost !== text && <span className="small muted hint">{hint ?? "→ 추천 입력 · ↑↓ 다른 추천"}</span>}
        <span className="grow" />
        {leftBar}
        {busy && onStop ? (
          <button type="button" className="send" onClick={onStop} disabled={stopping} aria-label={stopping ? "중지하는 중" : "중지"}
            title={stopping ? "중지하는 중" : "중지"}>{stopping ? <Loader2 size={14} className="spin" /> : <Square size={14} />}</button>
        ) : (
          <button type="submit" className="primary send" disabled={!canSend} aria-label="보내기" title={making ? MAKING_TEXT : undefined}><ArrowUp size={16} /></button>
        )}
      </div>
      {drag && <div className="composer-drop">여기에 놓으면 첨부됩니다 — 에이전트가 내 PC 프로젝트 폴더(inputs)에 받아 둡니다</div>}
    </form>
  );
}
