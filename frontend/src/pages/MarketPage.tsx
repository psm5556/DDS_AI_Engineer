import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, Store, Wrench } from "lucide-react";
import { useState } from "react";
import { get, post, put } from "../api";
import { OPTION_PENDING_TEXT } from "../components/MarketParts";
import { Modal } from "../components/Modal";
import { when } from "../format";
import { useToast } from "../toast";
import type { MarketBuiltin, MarketNotices, MarketOption, MarketOptionList } from "../types";

const LIST = ["market", "options"];

/** 내리거나 회수한 이유. 찾아가야만 보이면 왜 사라졌는지 영영 모른다 */
function Notices() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["market", "notices"], queryFn: () => get<MarketNotices>("/api/market/notices") });
  const read = useMutation({
    mutationFn: (id: number) => post(`/api/market/notices/${id}/read`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["market", "notices"] }),
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <>
      {(data?.items ?? []).map((n) => (
        <div key={n.id} className={`notice ${n.kind === "recalled" ? "err" : n.kind === "approved" ? "ok" : "warn"} market-notice`}>
          <span className="grow">{n.text} <span className="muted small">{when(n.created_at)}</span></span>
          <button className="small" disabled={read.isPending} onClick={() => read.mutate(n.id)}>확인</button>
        </div>
      ))}
    </>
  );
}

/** 켜고 끄는 단추. 글자로도 상태를 말한다 */
function Switch({ on, label, disabled, onChange }: { on: boolean; label: string; disabled?: boolean; onChange: (next: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className={`market-switch ${on ? "on" : ""}`} onClick={() => onChange(!on)}>
      <span className="market-switch-knob" />
    </button>
  );
}

/** 마켓: 기본 제공 묶음과 관리자가 제공하는 옵션을 카드로 본다. 사용자가 올리는 화면은 없다 */
export default function MarketPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [showTools, setShowTools] = useState(false);
  const { data } = useQuery({ queryKey: LIST, queryFn: () => get<MarketOptionList>("/api/market/options") });
  const { data: builtin } = useQuery({ queryKey: ["market", "builtin"], queryFn: () => get<MarketBuiltin>("/api/market/builtin"), staleTime: Infinity });
  const choose = useMutation({
    mutationFn: ({ id, on }: { id: number; on: boolean }) => put<MarketOption>(`/api/market/options/${id}/choice`, { on }),
    onSuccess: (row) => { qc.invalidateQueries({ queryKey: LIST }); toast(row.on ? `${row.name}: 켜 두었습니다. 지금은 선택만 저장됩니다` : `${row.name}: 껐습니다`); },
    onError: (e: Error) => { qc.invalidateQueries({ queryKey: LIST }); toast(e.message, true); },
  });

  // 띄어 쓴 말은 단어마다 찾는다
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (...texts: string[]) => { const hay = texts.join(" ").toLowerCase(); return terms.every((t) => hay.includes(t)); };
  const tools = builtin?.tools ?? [];
  const mcp = (builtin?.mcp ?? []).filter((m) => hit(m.name, m.description, m.id));
  const toolsHit = tools.length > 0 && hit("CAD CAE 도구", ...tools.flatMap((t) => [t.name, t.description]));
  const options = (data?.items ?? []).filter((o) => hit(o.name, o.description));
  const off = !!data && !data.enabled;
  const noneAtAll = !!data && !data.items.length;

  return (
    <div className="page market-page">
      <h1>마켓</h1>
      <p className="page-sub">AI가 쓰는 MCP(AI가 쓰는 도구 연결)를 봅니다. 기본 제공은 항상 켜져 있고, 옵션은 관리자가 제공하는 것 중에서 고릅니다.</p>
      <Notices />
      {off && <div className="notice warn">관리자가 마켓을 잠시 껐습니다 → 다시 켜지면 옵션을 켤 수 있습니다</div>}

      <div className="tools-filter">
        <label className="tools-search"><Search size={15} />
          <input type="search" placeholder="하고 싶은 일이나 이름 (예: Abaqus, 보고서)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="마켓 검색" /></label>
      </div>

      <section className="market-group">
        <h2>기본 제공</h2>
        <div className="market-grid">
          {mcp.map((m) => (
            <article key={m.id} className="market-card">
              <h3>{m.name}</h3>
              <p className="small">{m.description}</p>
              <div className="market-card-foot"><span className="chip ok">기본 제공 · 켜짐</span><span className="small muted">내 PC에서 동작</span></div>
            </article>
          ))}
          {toolsHit && (
            <button type="button" className="market-card market-card-btn" onClick={() => setShowTools(true)}>
              <h3><Wrench size={15} />CAD·CAE 도구 {tools.length}종</h3>
              <p className="small">Abaqus, NX 같은 툴을 다루는 방법입니다. 눌러서 목록을 봅니다.</p>
              <div className="market-card-foot"><span className="chip ok">기본 제공 · 켜짐</span><span className="small muted">내 PC에서 동작</span></div>
            </button>
          )}
        </div>
      </section>

      <section className="market-group">
        <h2>옵션</h2>
        <p className="notice info market-pending">{OPTION_PENDING_TEXT}</p>
        {options.length > 0 && (
          <div className="market-grid">
            {options.map((o) => (
              <article key={o.id} className={`market-card ${o.on ? "on" : ""}`}>
                <h3>{o.name}</h3>
                <p className="small">{o.description}</p>
                <div className="market-card-foot">
                  <span className="small muted">{o.on ? "켜짐 · 선택만 저장됨" : "꺼짐"}</span>
                  <Switch on={o.on} label={`${o.name} 켜기`} disabled={choose.isPending || (off && !o.on)} onChange={(on) => choose.mutate({ id: o.id, on })} />
                </div>
              </article>
            ))}
          </div>
        )}
        {noneAtAll && <div className="empty-hero"><Store size={28} /><b>관리자가 제공하는 옵션이 아직 없습니다</b></div>}
        {data && !noneAtAll && !options.length && <p className="small muted">찾는 말에 맞는 옵션이 없습니다. 다른 말로 찾아보세요.</p>}
      </section>

      {showTools && (
        <Modal wide title={`CAD·CAE 도구 ${tools.length}종`} onClose={() => setShowTools(false)} footer={<button onClick={() => setShowTools(false)}>닫기</button>}>
          <p className="small muted market-modal-text">앱에 들어 있어 끌 수 없습니다. 요청에 맞을 때 AI가 읽고 씁니다.</p>
          <ul className="market-tool-list">
            {tools.map((t) => <li key={t.id}><b>{t.name}</b><span className="small muted">{t.description}</span></li>)}
          </ul>
        </Modal>
      )}
    </div>
  );
}
