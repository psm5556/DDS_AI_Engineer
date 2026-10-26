import { useEffect, useRef } from "react";

/** 6자리 PIN 입력: 칸마다 숫자 하나, 숫자 키패드, 붙여넣기·지우기·화살표 이동 지원. 6자리가 차면 onComplete. */
export function PinInput({ value, onChange, onComplete, autoFocus, label = "PIN", disabled }: {
  value: string; onChange: (v: string) => void; onComplete?: (v: string) => void; autoFocus?: boolean; label?: string; disabled?: boolean;
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  // 포커스 이동이 렌더보다 먼저 일어나므로, 최신 값은 ref로 본다 (빠르게 입력해도 자리가 밀리지 않게)
  const cur = useRef(value);
  cur.current = value;
  useEffect(() => { if (autoFocus) refs.current[0]?.focus(); }, [autoFocus]);

  const set = (next: string) => {
    const v = next.replace(/\D/g, "").slice(0, 6);
    cur.current = v;
    onChange(v);
    refs.current[Math.min(v.length, 5)]?.focus();
    if (v.length === 6) onComplete?.(v);
  };

  return (
    <div className="pin-input" role="group" aria-label={label}>
      {Array.from({ length: 6 }, (_, i) => (
        <input key={i} ref={(el) => { refs.current[i] = el; }} type="password" inputMode="numeric" autoComplete="off" maxLength={1}
          aria-label={`${label} ${i + 1}번째 자리`} value={value[i] ?? ""} disabled={disabled}
          onFocus={(e) => { const n = cur.current.length; if (i > n) refs.current[n]?.focus(); else e.target.select(); }}
          onChange={(e) => {
            const d = e.target.value.replace(/\D/g, "");
            if (!d) return;
            const v = cur.current;
            set(v.slice(0, i) + d + v.slice(i + 1));
          }}
          onPaste={(e) => { e.preventDefault(); set(e.clipboardData.getData("text")); }}
          onKeyDown={(e) => {
            if (e.key === "Backspace") {
              e.preventDefault();
              const old = cur.current;
              const v = old.slice(0, old[i] ? i : Math.max(0, i - 1));
              cur.current = v;
              onChange(v);
              refs.current[Math.max(0, v.length)]?.focus();
            } else if (e.key === "ArrowLeft") refs.current[Math.max(0, i - 1)]?.focus();
            else if (e.key === "ArrowRight") refs.current[Math.min(5, i + 1)]?.focus();
          }} />
      ))}
    </div>
  );
}
