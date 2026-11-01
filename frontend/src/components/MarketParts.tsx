import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { get } from "../api";
import type { MarketFileRow, MarketFlag, MarketReport } from "../types";
import { Modal } from "./Modal";
import { PinInput } from "./PinInput";

/** 표시 항목을 하나씩 확인한다. 전부 확인해야 승인 버튼이 열린다 */
export function useAcks(flags: MarketFlag[]) {
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const rules = flags.map((f) => f.rule).join(",");
  useEffect(() => { setChecked(new Set()); }, [rules]);
  return useMemo(() => ({
    checked,
    allChecked: flags.every((f) => checked.has(f.rule)),
    toggle: (rule: string) => setChecked((old) => { const next = new Set(old); if (next.has(rule)) next.delete(rule); else next.add(rule); return next; }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [checked, rules]);
}

/** 자동 검사 결과: 고친 것 · 주의할 것 · 표시 항목. acks를 주면 항목마다 체크칸이 생긴다 */
export function MarketChecks({ report, acks }: { report: MarketReport; acks?: ReturnType<typeof useAcks> }) {
  return (
    <div className="market-checks">
      {report.fixes.length > 0 && (
        <div className="small"><b>서버가 고친 것</b><ul>{report.fixes.map((f) => <li key={f}>{f}</li>)}</ul></div>
      )}
      {report.warnings.length > 0 && (
        <div className="notice warn"><ul>{report.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
      )}
      {report.other_scripts.length > 0 && <div className="notice warn">자동 검사가 읽지 못한 파일은 직접 읽어 주세요: {report.other_scripts.join(", ")}</div>}
      {report.flags.length === 0 && <p className="small muted">표시할 항목이 없습니다. 사람이 내용을 읽는 단계는 그대로 필요합니다.</p>}
      {report.flags.map((f) => (
        <details key={f.rule} className="market-flag">
          <summary>
            {acks && <input type="checkbox" aria-label={`${f.title} 확인`} checked={acks.checked.has(f.rule)} onClick={(e) => e.stopPropagation()} onChange={() => acks.toggle(f.rule)} />}
            <span className="grow"><b>{f.title}</b> <span className="muted">{f.total ?? f.hits.length}곳</span></span>
          </summary>
          <p className="small">{f.why}</p>
          <ul className="market-hits">
            {f.hits.map((h, i) => <li key={i}><code>{h.path}{h.line ? `:${h.line}` : ""}</code> {h.text}</li>)}
          </ul>
          {/* 잘린 목록이 전부인 것처럼 보이면 뒤에 있는 위험한 주소를 놓친다 */}
          {f.truncated && <p className="small"><b>앞의 {f.hits.length}곳만 보여 줍니다.</b> 나머지는 파일을 직접 읽어 주세요.</p>}
        </details>
      ))}
    </div>
  );
}

/** 파일 목록과 내용. 서버가 zip을 메모리에서 읽어 글자나 그림으로 보내 준다 */
export function MarketFiles({ pkg, version }: { pkg: number; version: string }) {
  const base = `/api/market/packages/${pkg}/versions/${version}/files`;
  const [path, setPath] = useState<string | null>(null);
  const { data } = useQuery({ queryKey: ["market", "files", pkg, version], queryFn: () => get<{ files: MarketFileRow[] }>(base) });
  const { data: file } = useQuery({
    queryKey: ["market", "file", pkg, version, path], enabled: !!path,
    queryFn: () => get<{ text: string | null; image?: string; truncated: boolean; size: number }>(`${base}/${(path ?? "").split("/").map(encodeURIComponent).join("/")}`),
  });
  return (
    <div className="market-files">
      <h4>파일 {data ? `${data.files.length}개` : ""}</h4>
      <ul>
        {(data?.files ?? []).map((f) => (
          <li key={f.path}><button className={`link-btn small ${path === f.path ? "on" : ""}`} onClick={() => setPath(path === f.path ? null : f.path)}>{f.path}</button>
            <span className="small muted"> {Math.max(1, Math.round(f.size / 1024))} KB</span></li>
        ))}
      </ul>
      {path && file && (file.image
        ? <img className="market-preview" src={file.image} alt={path} />
        : file.text === null
          ? <p className="small muted">글자로 볼 수 없는 파일입니다. 자동 검사도 내용을 보지 못했습니다 → 내용을 모르면 승인하지 말고 만든 사람에게 물어보세요.</p>
          : <pre className="code-block market-code">{file.text}{file.truncated ? "\n…(앞부분만 보여 줍니다)" : ""}</pre>)}
    </div>
  );
}

export interface LabelValues { display_name: string; description: string; category: string; examples: string }

export function labelValues(p: { display_name?: string | null; description?: string | null; category?: string; examples?: string[] }): LabelValues {
  return { display_name: p.display_name ?? "", description: p.description ?? "", category: p.category ?? "", examples: (p.examples ?? []).join("\n") };
}

export function labelsToMeta(v: LabelValues) {
  return { display_name: v.display_name, description: v.description, category: v.category, examples: v.examples.split("\n").map((x) => x.trim()).filter(Boolean) };
}

/** 이름표 입력칸. 파일이 아니라 카드와 AI 안내에 보이는 값이다. 고쳐도 새 버전이 되지 않는다 */
export function LabelFields({ value, onChange }: { value: LabelValues; onChange: (v: LabelValues) => void }) {
  return (
    <div className="market-label-fields">
      <div className="field"><span className="lbl">화면에 보일 이름</span><input type="text" value={value.display_name} maxLength={100} onChange={(e) => onChange({ ...value, display_name: e.target.value })} /></div>
      <div className="field"><span className="lbl">한 줄 설명 (100자까지)</span><textarea rows={2} value={value.description} maxLength={100} onChange={(e) => onChange({ ...value, description: e.target.value })} /></div>
      <div className="field"><span className="lbl">분류 (예: 후처리, 보고서)</span><input type="text" value={value.category} maxLength={40} onChange={(e) => onChange({ ...value, category: e.target.value })} /></div>
      <div className="field"><span className="lbl">이렇게 말해 보세요 (한 줄에 하나)</span><textarea rows={2} value={value.examples} onChange={(e) => onChange({ ...value, examples: e.target.value })} /></div>
    </div>
  );
}

/** 되돌리기 어렵거나 다른 관문을 무너뜨리는 결정은 PIN을 다시 받는다 */
export function PinModal({ title, confirm, danger, busy, disabled, children, onClose, onConfirm }: {
  title: string; confirm: string; danger?: boolean; busy?: boolean; disabled?: boolean; children?: ReactNode; onClose: () => void; onConfirm: (pin: string) => void;
}) {
  const [pin, setPin] = useState("");
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className={danger ? "danger" : "primary"} disabled={pin.length !== 6 || busy || disabled} onClick={() => onConfirm(pin)}>{confirm}</button>
    </>}>
      {children && <div className="small market-modal-text">{children}</div>}
      <div className="field"><span className="lbl">내 PIN</span><PinInput value={pin} onChange={setPin} autoFocus label="내 PIN" /></div>
    </Modal>
  );
}
