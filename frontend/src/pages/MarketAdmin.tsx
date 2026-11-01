import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { api, post, put, get } from "../api";
import { OPTION_PENDING_TEXT, PinModal } from "../components/MarketParts";
import { Modal } from "../components/Modal";
import { when } from "../format";
import { useToast } from "../toast";
import type { MarketOptionAdmin, MarketOptionAdminRow } from "../types";

const KEY = ["market", "admin-options"];
type PinAct = { kind: "publish" | "lower" | "delete"; row: MarketOptionAdminRow };

/** 관리 > 마켓: 옵션 목록 표 하나와 추가 버튼 하나. 공개·내리기·삭제는 PIN을 다시 받는다 */
export default function MarketAdmin() {
  const { data } = useQuery({ queryKey: KEY, queryFn: () => get<MarketOptionAdmin>("/api/market/admin/options") });
  const [editing, setEditing] = useState<MarketOptionAdminRow | "new" | null>(null);
  const [acting, setActing] = useState<PinAct | null>(null);
  const [note, setNote] = useState("");
  const [switching, setSwitching] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => qc.invalidateQueries({ queryKey: ["market"] });
  const on = data?.settings.enabled ?? true;
  const toggle = useMutation({
    mutationFn: (pin: string) => put("/api/market/settings", { pin, enabled: !on }),
    onSuccess: () => { refresh(); setSwitching(false); toast(on ? "마켓을 껐습니다" : "마켓을 켰습니다"); },
    onError: (e: Error) => { setSwitching(false); toast(e.message, true); },
  });
  const act = useMutation({
    mutationFn: ({ kind, row, pin }: PinAct & { pin: string }) => (kind === "delete"
      ? api(`/api/market/admin/options/${row.id}`, { method: "DELETE", body: { pin } })
      : post(`/api/market/admin/options/${row.id}/${kind}`, { pin, note })),
    onSuccess: (_d, { kind }) => {
      refresh();
      setActing(null);
      setNote("");
      toast(kind === "publish" ? "공개했습니다. 사용자의 마켓에 보입니다" : kind === "lower" ? "내렸습니다. 켠 사람의 값을 끄고 이유를 알렸습니다" : "삭제했습니다");
    },
    onError: (e: Error) => { setActing(null); toast(e.message, true); },
  });
  const rows = data?.items ?? [];

  return (
    <>
      <p className="notice info market-pending">{OPTION_PENDING_TEXT}</p>
      <div className="market-admin-bar">
        <button className="primary small" onClick={() => setEditing("new")}><Plus size={14} />옵션 추가</button>
        <span className="grow" />
        <span className={`chip ${on ? "ok" : "warn"}`}>{on ? "마켓 켜짐" : "마켓 꺼짐"}</span>
        <button className="small" onClick={() => setSwitching(true)}>{on ? "마켓 끄기" : "마켓 켜기"}</button>
      </div>
      {!!data?.legacy_count && <p className="small muted market-legacy">이전 방식으로 올린 스킬 항목이 {data.legacy_count}개 남아 있습니다. 이 화면에서는 다루지 않고, 설치한 PC는 그대로 씁니다.</p>}

      <div className="table-wrap panel market-table"><table>
        <thead><tr><th>이름</th><th>설명</th><th>서비스 주소</th><th>공개 상태</th><th className="r">켠 사람</th><th>수정일</th><th /></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><b>{r.name}</b></td>
              <td className="small">{r.description}</td>
              <td className="small market-url">{r.service_url || <span className="muted">없음</span>}</td>
              <td><span className={`chip ${r.status === "published" ? "ok" : r.status === "lowered" ? "warn" : ""}`}>{r.status_label}</span></td>
              <td className="r">{r.on_count}</td>
              <td className="small">{when(r.updated_at)}</td>
              <td className="r market-row-acts">
                {r.status !== "published" && <button className="small primary" onClick={() => setActing({ kind: "publish", row: r })}>공개</button>}
                {r.status === "published" && <button className="small" onClick={() => setActing({ kind: "lower", row: r })}>내리기</button>}
                <button className="small" onClick={() => setEditing(r)}>수정</button>
                <button className="small danger" onClick={() => setActing({ kind: "delete", row: r })}>삭제</button>
              </td>
            </tr>
          ))}
          {data && !rows.length && <tr><td colSpan={7} className="small muted empty-row">아직 옵션이 없습니다 → 옵션 추가를 눌러 만드세요.</td></tr>}
        </tbody>
      </table></div>

      {editing && <OptionForm row={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {acting && (
        <PinModal title={`${acting.row.name} ${acting.kind === "publish" ? "공개" : acting.kind === "lower" ? "내리기" : "삭제"}`} confirm={acting.kind === "publish" ? "공개" : acting.kind === "lower" ? "내리기" : "삭제"}
          danger={acting.kind === "delete"} busy={act.isPending} onClose={() => { setActing(null); setNote(""); }} onConfirm={(pin) => act.mutate({ ...acting, pin })}>
          {acting.kind === "publish" && "사용자의 마켓에 카드로 보입니다. 지금은 켜도 AI에게 전달되지 않습니다."}
          {acting.kind === "lower" && <>
            목록에서 사라지고, 켠 사람의 값을 끄고 이유를 알립니다. 다시 공개할 수 있습니다.
            <div className="field market-note-field"><span className="lbl">내리는 이유 (켠 사람에게 보입니다)</span><textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></div>
          </>}
          {acting.kind === "delete" && "목록에서 지웁니다. 되돌릴 수 없습니다. 켠 사람에게는 사라졌다고 알립니다."}
        </PinModal>
      )}
      {switching && <PinModal title={on ? "마켓 끄기" : "마켓 켜기"} confirm={on ? "끄기" : "켜기"} danger={on} busy={toggle.isPending} onClose={() => setSwitching(false)} onConfirm={(pin) => toggle.mutate(pin)}>
        {on ? "사용자가 옵션을 새로 켤 수 없게 합니다. 이전 방식으로 설치한 스킬도 PC에서 쓰지 않습니다. 회수는 꺼져 있어도 전달됩니다." : "옵션을 다시 켤 수 있게 하고, 이전 방식으로 설치한 스킬을 PC Agent가 다시 받습니다."}</PinModal>}
    </>
  );
}

function OptionForm({ row, onClose }: { row: MarketOptionAdminRow | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ name: row?.name ?? "", description: row?.description ?? "", service_url: row?.service_url ?? "" });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => (row ? api(`/api/market/admin/options/${row.id}`, { method: "PATCH", body: form }) : post("/api/market/admin/options", form)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["market"] }); toast(row ? "고쳤습니다" : "초안으로 추가했습니다. 공개를 누르면 사용자에게 보입니다"); onClose(); },
    onError: (e: Error) => setError(e.message),
  });
  return (
    <Modal title={row ? `${row.name} 수정` : "옵션 추가"} onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className="primary" disabled={!form.name.trim() || save.isPending} onClick={() => save.mutate()}>저장</button>
    </>}>
      <div className="market-form">
      <p className="notice info">{OPTION_PENDING_TEXT} 서비스 주소에 연결해 보지 않습니다.</p>
      {error && <div className="notice err">{error}</div>}
      <div className="field"><span className="lbl">이름 (카드에 보입니다)</span><input type="text" value={form.name} maxLength={100} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
      <div className="field"><span className="lbl">설명 (한 줄, 카드에 보입니다)</span><textarea rows={2} value={form.description} maxLength={200} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
      <div className="field"><span className="lbl">서비스 주소 (관리자에게만 보입니다)</span><input type="text" inputMode="url" placeholder="http://서버:포트/경로" value={form.service_url} onChange={(e) => setForm({ ...form, service_url: e.target.value })} /></div>
      </div>
    </Modal>
  );
}
