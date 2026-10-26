import { ArrowUp, FileUp, Loader2, Paperclip, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { uploadFile } from "../api";
import { useToast } from "../toast";
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

const fmtSize = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

/** 입력창: → 키로 추천 프롬프트 받기(회색 글자로 미리 보임) · 파일 첨부(버튼·끌어 놓기·붙여넣기) · Enter 보내기
 *  projectId가 있으면 첨부를 바로 올리고, 없으면(첫 화면) 보낼 때 pendingFiles로 넘긴다. */
export function Composer({
  projectId, suggestions, onSend, busy, onStop, disabled, placeholder, big, leftBar, autoFocus, hint, seed,
}: {
  projectId?: number; suggestions: string[]; onSend: (o: Outgoing) => void | Promise<void>; busy?: boolean; onStop?: () => void;
  disabled?: boolean; placeholder: string; big?: boolean; leftBar?: ReactNode; autoFocus?: boolean; hint?: string;
  /** 밖에서 입력창을 채울 때(예: 시작 버튼) — n이 바뀔 때마다 text로 채운다 */
  seed?: { text: string; n: number };
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [pick, setPick] = useState(0); // 빈 입력에서 ↑↓로 고르는 추천 순번
  const [drag, setDrag] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (autoFocus) ta.current?.focus(); }, [autoFocus]);
  useEffect(() => {
    if (!seed) return;
    setText(seed.text);
    requestAnimationFrame(() => { const el = ta.current; if (el) { el.focus(); el.setSelectionRange(seed.text.length, seed.text.length); } });
  }, [seed]);

  // 추천: 비어 있으면 상황별 추천을 차례로, 입력 중이면 그 글자로 시작하는 추천·지난 요청
  const ghost = useMemo(() => {
    if (disabled) return "";
    if (!text) return suggestions.length ? suggestions[pick % suggestions.length] : "";
    if (text.length < 2) return "";
    const low = text.toLowerCase();
    const pool = [...suggestions, ...history()];
    const hit = pool.find((s) => s.toLowerCase().startsWith(low) && s.length > text.length);
    return hit ? text + hit.slice(text.length) : "";
  }, [text, suggestions, pick, disabled]);

  const accept = () => {
    if (!ghost) return false;
    setText(ghost);
    requestAnimationFrame(() => { const el = ta.current; if (el) { el.focus(); el.setSelectionRange(ghost.length, ghost.length); } });
    return true;
  };

  const addFiles = (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    const fresh: Item[] = list.map((f) => ({ key: `${f.name}-${f.size}-${Math.random()}`, file: f, progress: projectId ? 0 : 1 }));
    setItems((xs) => [...xs, ...fresh]);
    if (!projectId) return;
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
  const canSend = !disabled && !busy && !uploading && (text.trim() || items.some((i) => i.att || !projectId));
  const submit = async () => {
    if (!canSend) return;
    const t = text.trim();
    if (t) rememberPrompt(t);
    const out: Outgoing = {
      text: t, attachmentIds: items.filter((i) => i.att).map((i) => i.att!.id),
      pendingFiles: projectId ? [] : items.map((i) => i.file),
    };
    setText("");
    setItems([]);
    await onSend(out);
  };

  return (
    <form className={`composer ${big ? "big" : ""} ${drag ? "drag" : ""}`}
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (!disabled) addFiles(e.dataTransfer.files); }}>
      {items.length > 0 && (
        <div className="composer-files">
          {items.map((i) => (
            <span key={i.key} className={`file-chip ${i.error ? "err" : ""}`} title={i.error ?? i.file.name}>
              {projectId && !i.att && !i.error ? <Loader2 size={12} className="spin" /> : <FileUp size={12} />}
              <span className="n">{i.file.name}</span>
              <span className="s">{i.error ? "실패" : projectId && !i.att ? `${Math.round(i.progress * 100)}%` : fmtSize(i.file.size)}</span>
              <button type="button" className="x" aria-label={`${i.file.name} 빼기`} onClick={() => setItems((xs) => xs.filter((x) => x.key !== i.key))}><X size={11} /></button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-input">
        {/* 회색 추천 글자: 입력한 글자 뒤에 이어서 보인다 (→ 또는 Tab으로 받기) */}
        <div className="composer-ghost" aria-hidden="true"><span className="typed">{text}</span>{ghost.slice(text.length)}</div>
        <textarea ref={ta} value={text} rows={big ? 3 : 1} disabled={disabled} placeholder={ghost ? "" : placeholder}
          aria-label="에이전트에게 요청" onChange={(e) => setText(e.target.value)}
          onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}
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
        <button type="button" className="icon-btn" onClick={() => fileRef.current?.click()} disabled={disabled}
          title="파일 첨부 (CAD 모델·입력 파일·자료) — 끌어 놓거나 붙여넣어도 됩니다" aria-label="파일 첨부"><Paperclip size={16} /></button>
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        {leftBar}
        <span className="grow" />
        <span className="small muted hint">{hint ?? (ghost && ghost !== text ? "→ 추천 받기 · ↑↓ 다른 추천 · Enter 보내기" : "Enter 보내기 · Shift+Enter 줄바꿈")}</span>
        {busy && onStop ? (
          <button type="button" className="send" onClick={onStop} aria-label="중지" title="중지"><Square size={14} /></button>
        ) : (
          <button type="submit" className="primary send" disabled={!canSend} aria-label="보내기"><ArrowUp size={16} /></button>
        )}
      </div>
      {drag && <div className="composer-drop">여기에 놓으면 첨부됩니다 — 에이전트가 내 PC 프로젝트 폴더(inputs)에 받아 둡니다</div>}
    </form>
  );
}
