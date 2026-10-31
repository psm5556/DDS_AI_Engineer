import { useQuery } from "@tanstack/react-query";
import { Bot, Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";
import type { AgentStatus } from "../types";

const KEY = "dds.agent";

/** 새 대화에 쓸 AI CLI — 마지막으로 고른 것을 기억하고, 없으면 PC Agent의 기본값 */
export function usePreferredAgent(): [string | null, (name: string) => void] {
  const [v, setV] = useState<string | null>(() => { try { return localStorage.getItem(KEY); } catch { return null; } });
  const set = (name: string) => { setV(name); try { localStorage.setItem(KEY, name); } catch { /* 저장 못 해도 동작 */ } };
  return [v, set];
}

/** 내 PC에 설치된 에이전트 중 실제로 쓸 이름 (선호값이 설치돼 있지 않으면 기본값).
 *  아직 모르면 null이다: 정해 둔 이름으로 채우면 사용자가 고르지 않은 CLI가 뜬다(연결 직후 hello가 오기 전에 실제로 있었다) */
export function resolveAgent(agent: AgentStatus | undefined, preferred: string | null): string | null {
  const list = agent?.info?.agents ?? [];
  const ok = (n?: string | null) => !!n && list.some((a) => a.name === n && a.installed);
  if (ok(preferred)) return preferred!;
  if (ok(agent?.info?.default_agent)) return agent!.info!.default_agent!;
  return list.find((a) => a.installed)?.name ?? preferred;
}

/** 입력창 아래 에이전트 선택 메뉴 (Gemini · Claude Code · Codex · 데모) */
export function AgentPicker({ value, onChange, disabled, note }: {
  value: string; onChange: (name: string) => void; disabled?: boolean; note?: string;
}) {
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const list = agent?.info?.agents ?? [];
  const label = list.find((a) => a.name === value)?.label ?? (value || "에이전트 확인 중");

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [open]);

  return (
    <div className="agent-pick" ref={ref}>
      <button type="button" className="agent-pick-btn" disabled={disabled || !list.length} aria-haspopup="listbox" aria-expanded={open}
        title="사용할 AI 에이전트" onClick={() => setOpen((o) => !o)}>
        <Bot size={13} />{label}{note ? <span className="muted"> · {note}</span> : null}<ChevronDown size={12} />
      </button>
      {open && (
        <div className="agent-pick-menu" role="listbox" aria-label="AI 에이전트">
          {list.map((a) => (
            <button type="button" key={a.name} role="option" aria-selected={a.name === value} disabled={!a.installed}
              onClick={() => { setOpen(false); if (a.name !== value) onChange(a.name); }}>
              <span className="check">{a.name === value && <Check size={13} />}</span>
              <span className="grow">{a.label}</span>
              {!a.installed && <span className="small muted">미설치</span>}
              {a.installed && a.name === agent?.info?.default_agent && <span className="small muted">기본</span>}
            </button>
          ))}
          {list.some((a) => !a.installed) && <Link className="agent-pick-foot small" to="/settings" onClick={() => setOpen(false)}>설정에서 설치하기</Link>}
        </div>
      )}
    </div>
  );
}
