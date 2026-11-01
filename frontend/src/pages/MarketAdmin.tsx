import { W } from "../work/copy";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, get, post, put } from "../api";
import { LabelFields, labelsToMeta, labelValues, MarketChecks, MarketFiles, PinModal, useAcks, type LabelValues } from "../components/MarketParts";
import { Modal } from "../components/Modal";
import { when } from "../format";
import { useToast } from "../toast";
import type { MarketCoverage, MarketDiff, MarketReview, MarketReviewRow } from "../types";

const REVIEW = ["market", "review"];

/** 관리 > 마켓: 검토 대기 · 공개 항목 · 회수 현황 · 마켓 켜고 끄기. 관리자 권한을 받은 사람이 관리부서다 */
export default function MarketAdmin() {
  const { data } = useQuery({ queryKey: REVIEW, queryFn: () => get<MarketReview>("/api/market/review") });
  const [reviewing, setReviewing] = useState<MarketReviewRow | null>(null);
  const [recalling, setRecalling] = useState<MarketReviewRow | null>(null);
  const [lowering, setLowering] = useState<MarketReviewRow | null>(null);
  const [labelling, setLabelling] = useState<MarketReviewRow | null>(null);
  const [lowerNote, setLowerNote] = useState("");
  const [coverage, setCoverage] = useState<MarketReviewRow | null>(null);
  const [switching, setSwitching] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => qc.invalidateQueries({ queryKey: ["market"] });
  const fail = (e: Error) => toast(e.message, true);
  const toggle = useMutation({
    mutationFn: (pin: string) => put("/api/market/settings", { pin, enabled: !data?.settings.enabled }),
    onSuccess: () => { refresh(); setSwitching(false); toast(data?.settings.enabled ? "마켓을 껐습니다" : "마켓을 켰습니다"); }, onError: (e: Error) => { setSwitching(false); fail(e); },
  });
  const lower = useMutation({
    mutationFn: (pin: string) => post(`/api/market/packages/${lowering?.package_id}/versions/${lowering?.version}/deprecate`, { pin, note: lowerNote }),
    onSuccess: () => { refresh(); setLowering(null); setLowerNote(""); toast("내렸습니다. 새 설치만 막고, 이미 설치한 사람은 계속 씁니다"); }, onError: (e: Error) => { setLowering(null); fail(e); },
  });
  const queue = data?.queue ?? [];
  const published = data?.published ?? [];
  const on = data?.settings.enabled ?? true;

  return (
    <>
      <div className="market-admin-bar">
        <div className="todo-pills market-pills"><span>검토 대기<b>{queue.length}</b></span><span>공개·회수 버전<b>{published.length}</b></span></div>
        <span className="grow" />
        <span className={`chip ${on ? "ok" : "warn"}`}>{on ? "마켓 켜짐" : "마켓 꺼짐"}</span>
        <button className="small" onClick={() => setSwitching(true)}>{on ? "마켓 끄기" : "마켓 켜기"}</button>
      </div>

      <h3 className="market-admin-h">검토 대기</h3>
      <div className="table-wrap panel market-table"><table>
        <thead><tr><th>항목</th><th>만든 사람</th><th>종류</th><th>표시</th><th>요청</th><th /></tr></thead>
        <tbody>
          {queue.map((r) => (
            <tr key={`${r.package_id}-${r.version}`}>
              <td><b>{r.display_name}</b> <span className="small muted">{r.name} {r.version}</span></td>
              <td>{r.owner_name}</td>
              <td>{r.code ? <span className="chip warn">코드 있음</span> : <span className="chip">문서뿐</span>}</td>
              <td>{r.report.flags?.length ?? 0}건</td>
              <td>{when(r.created_at)}</td>
              <td className="r"><button className="small primary" onClick={() => setReviewing(r)}>검토</button></td>
            </tr>
          ))}
          {data && !queue.length && <tr><td colSpan={6} className="small muted empty-row">검토를 기다리는 항목이 없습니다.</td></tr>}
        </tbody>
      </table></div>

      <h3 className="market-admin-h">공개 항목</h3>
      <div className="table-wrap panel market-table"><table>
        <thead><tr><th>항목</th><th>버전</th><th>등급</th><th>상태</th><th className="r" title="PC가 마지막으로 알린 값입니다">이 버전의 PC</th><th /></tr></thead>
        <tbody>
          {published.map((r) => (
            <tr key={`${r.package_id}-${r.version}`}>
              <td><b>{r.display_name}</b> <span className="small muted">{r.name} · 설치 {r.installs}명</span></td>
              <td>{r.version}</td><td>{r.grade_label}</td>
              <td>{r.status === "withdrawn" ? <span className="chip err">회수</span> : r.status === "deprecated" ? <span className="chip warn">내림</span> : <span className="chip ok">공개</span>}
                {r.recall_reason && <div className="small muted">{r.recall_reason}</div>}
                {r.status === "deprecated" && r.review_note && <div className="small muted">{r.review_note}</div>}</td>
              <td className="r">{r.pcs_on_version}</td>
              <td className="r market-row-acts">
                {r.status === "withdrawn" && <button className="small" onClick={() => setCoverage(r)}>회수 현황</button>}
                {r.status !== "withdrawn" && <button className="small" onClick={() => setLabelling(r)}>이름표</button>}
                {r.status === "approved" && <button className="small" onClick={() => setLowering(r)}>내리기</button>}
                {r.status !== "withdrawn" && <button className="small danger" onClick={() => setRecalling(r)}>회수</button>}
              </td>
            </tr>
          ))}
          {data && !published.length && <tr><td colSpan={6} className="small muted empty-row">아직 공개한 항목이 없습니다.</td></tr>}
        </tbody>
      </table></div>

      {reviewing && <ReviewModal row={reviewing} onClose={() => setReviewing(null)} />}
      {recalling && <RecallModal row={recalling} onClose={() => setRecalling(null)} onDone={(r) => { setRecalling(null); setCoverage(r); }} />}
      {coverage && <CoverageModal row={coverage} onClose={() => setCoverage(null)} />}
      {labelling && <LabelModal row={labelling} onClose={() => setLabelling(null)} />}
      {lowering && <PinModal title={`${lowering.display_name} 내리기`} confirm="내리기" busy={lower.isPending} disabled={!lowerNote.trim()} onClose={() => setLowering(null)} onConfirm={(pin) => lower.mutate(pin)}>
        새 설치만 막습니다. 이미 설치한 사람은 계속 씁니다. 쓰지 못하게 하려면 회수를 쓰세요.
        <div className="field market-note-field"><span className="lbl">내리는 이유 (카드에 그대로 보입니다)</span><textarea rows={2} value={lowerNote} onChange={(e) => setLowerNote(e.target.value)} /></div>
      </PinModal>}
      {switching && <PinModal title={on ? "마켓 끄기" : "마켓 켜기"} confirm={on ? "끄기" : "켜기"} danger={on} busy={toggle.isPending} onClose={() => setSwitching(false)} onConfirm={(pin) => toggle.mutate(pin)}>
        {on ? "설치·등록을 막고 모든 PC에서 마켓 스킬을 쓰지 않게 합니다. 켜면 PC Agent가 다시 받습니다. 회수는 꺼져 있어도 전달됩니다." : "설치·등록을 다시 받고, 설치한 스킬을 PC Agent가 다시 받습니다."}</PinModal>}
    </>
  );
}

function ReviewModal({ row, onClose }: { row: MarketReviewRow; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const flags = row.report.flags ?? [];
  const acks = useAcks(flags);
  const [mode, setMode] = useState<"approve" | "reject" | null>(null);
  const [official, setOfficial] = useState(row.official);
  const [note, setNote] = useState("");
  const [labels, setLabels] = useState<LabelValues>(() => labelValues(row));
  const base = `/api/market/packages/${row.package_id}/versions/${row.version}`;
  const { data: diff } = useQuery({ queryKey: ["market", "diff", row.package_id, row.version], queryFn: () => get<MarketDiff>(`${base}/diff`) });
  const act = useMutation({
    mutationFn: (pin: string) => (mode === "approve"
      ? post(`${base}/approve`, { pin, acks: flags.map((f) => f.rule), official, note, fingerprint: row.fingerprint, meta: labelsToMeta(labels) })
      : post(`${base}/reject`, { pin, note })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["market"] }); toast(mode === "approve" ? "승인했습니다. 설치한 사람의 PC에 곧 반영됩니다" : "반려했습니다. 만든 사람은 계속 쓸 수 있습니다"); onClose(); },
    onError: (e: Error) => { setMode(null); toast(e.message, true); },
  });
  return (
    <Modal wide title={`${row.display_name} ${row.version} 검토`} onClose={onClose} footer={<>
      <span className="small muted grow">{row.self_approve_blocked ? "내가 올린 코드 항목은 다른 관리자가 승인합니다." : acks.allChecked ? "" : "표시 항목을 모두 확인하면 승인할 수 있습니다."}</span>
      <button onClick={onClose}>닫기</button>
      <button disabled={!note.trim()} title={note.trim() ? "" : "아래에 사유를 적으면 반려할 수 있습니다"} onClick={() => setMode("reject")}>반려</button>
      <button className="primary" disabled={!acks.allChecked || row.self_approve_blocked} onClick={() => setMode("approve")}>승인</button>
    </>}>
      <p className="small">{row.description}</p>
      <p className="small muted">만든 사람 {row.owner_name} · {row.code ? "코드 있음" : "문서뿐"} · 파일 {row.file_count}개{row.requires_tools.length ? ` · 필요한 툴: ${row.requires_tools.join(", ")}` : ""}</p>
      <div className="notice info">
        <div><b>사람이 읽어 확인할 것</b> (자동 검사로 찾지 못합니다)
          <ul>
            <li>공학 판단이 맞는가: 계수, 허용값, 실패 기준</li>
            <li>{W.project} 고유 값이 남아 있지 않은가: 재료 물성, 고객사 모델명, 설계값</li>
            <li>툴을 실행한다면 CPU 수와 동시 실행 수가 라이선스를 소진하지 않는가</li>
          </ul>
        </div>
      </div>
      {row.evidence != null && <div className="notice warn"><div>
        <b>시험 기록은 등록자가 적은 내용입니다.</b> 확인된 기록이 아닙니다.
        <pre className="code-block market-code">{JSON.stringify(row.evidence, null, 1).slice(0, 2000)}</pre>
      </div></div>}
      <h4 className="market-admin-h">자동 검사</h4>
      <MarketChecks report={row.report} acks={acks} />
      <h4 className="market-admin-h">직전 버전과의 차이{diff?.previous ? ` (${diff.previous} → ${row.version})` : ""}</h4>
      {diff && !diff.previous && <p className="small muted">첫 버전입니다. 아래 파일을 모두 읽어 주세요.</p>}
      {diff?.previous && !diff.files.length && <p className="small muted">파일이 달라지지 않았습니다.</p>}
      {diff?.previous && diff.files.map((f) => (
        <details key={f.path} className="market-flag" open={diff.files.length <= 3}>
          <summary><code>{f.path}</code> <span className="small muted">{f.status === "added" ? "새 파일" : f.status === "removed" ? "지운 파일" : "바뀜"}</span></summary>
          {f.diff != null
            ? <pre className="code-block market-code">{f.diff}</pre>
            : <p className="small muted">{f.reason === "too_big" ? "너무 커서 비교하지 않았습니다." : "글자로 비교할 수 없는 파일입니다."} 아래 파일 목록에서 직접 읽어 주세요.</p>}
          {/* 잘린 차이가 전부인 것처럼 보이면 끝에 붙은 코드를 놓친다 */}
          {f.truncated && <p className="small"><b>전체 {f.total_lines}줄 중 앞 2000줄만 보여 줍니다.</b> 나머지는 아래 파일 목록에서 직접 읽어 주세요.</p>}
        </details>
      ))}
      <MarketFiles pkg={row.package_id} version={row.version} />
      <details className="market-flag market-label-box">
        <summary>이름표 고치기 (영문 폴더 이름 그대로면 카드에 그대로 보입니다)</summary>
        <LabelFields value={labels} onChange={setLabels} />
      </details>
      <div className="field market-note-field"><span className="lbl">검토 의견 (반려할 때는 꼭 적습니다. 만든 사람에게 보입니다)</span>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></div>
      <label className="small market-check"><input type="checkbox" checked={official} onChange={(e) => setOfficial(e.target.checked)} />관리부서 제공으로 공개 (부서가 유지 책임을 집니다)</label>
      {mode && <PinModal title={mode === "approve" ? "승인" : "반려"} confirm={mode === "approve" ? "승인" : "반려"} danger={mode === "reject"} busy={act.isPending} onClose={() => setMode(null)} onConfirm={(pin) => act.mutate(pin)}>
        {mode === "approve" ? "내용을 읽었다는 기록이 남고, 회사 전체가 설치할 수 있게 됩니다." : "사유가 만든 사람에게 보입니다. 만든 사람은 자기 PC에서 계속 쓸 수 있습니다."}</PinModal>}
    </Modal>
  );
}

/** 공개한 항목의 이름표는 관리부서가 고친다. 이름표는 AI 안내로 가므로 PIN을 다시 받는다 */
function LabelModal({ row, onClose }: { row: MarketReviewRow; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [labels, setLabels] = useState<LabelValues>(() => labelValues(row));
  const [askPin, setAskPin] = useState(false);
  const act = useMutation({
    mutationFn: (pin: string) => api(`/api/market/packages/${row.package_id}/versions/${row.version}/labels`, { method: "PATCH", body: { ...labelsToMeta(labels), pin } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["market"] }); toast("이름표를 고쳤습니다. 파일은 그대로입니다"); onClose(); },
    onError: (e: Error) => { setAskPin(false); toast(e.message, true); },
  });
  return (
    <Modal title={`${row.display_name} ${row.version} 이름표`} onClose={onClose} footer={<><button onClick={onClose}>취소</button><button className="primary" onClick={() => setAskPin(true)}>저장</button></>}>
      <p className="small market-modal-text">카드와 AI 안내에 보이는 값입니다. 파일은 바뀌지 않아 새 버전이 되지 않습니다.</p>
      <LabelFields value={labels} onChange={setLabels} />
      {askPin && <PinModal title="이름표 저장" confirm="저장" busy={act.isPending} onClose={() => setAskPin(false)} onConfirm={(pin) => act.mutate(pin)} />}
    </Modal>
  );
}

function RecallModal({ row, onClose, onDone }: { row: MarketReviewRow; onClose: () => void; onDone: (r: MarketReviewRow) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [fallback, setFallback] = useState<"none" | "previous">("none");
  const [replacement, setReplacement] = useState("");
  const [askPin, setAskPin] = useState(false);
  const act = useMutation({
    mutationFn: (pin: string) => post(`/api/market/packages/${row.package_id}/versions/${row.version}/withdraw`, { pin, reason, fallback, replacement: replacement.trim() || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["market"] }); toast("회수했습니다. 연결된 PC부터 적용되고, 쓴 사람에게 알립니다"); onDone(row); },
    onError: (e: Error) => { setAskPin(false); toast(e.message, true); },
  });
  return (
    <Modal title={`${row.display_name} ${row.version} 회수`} onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className="danger" disabled={!reason.trim()} onClick={() => setAskPin(true)}>회수</button>
    </>}>
      <p className="small market-modal-text">설치한 PC의 모든 {W.project}에서 스킬을 지우고, 진행 중인 {W.task}에는 다음 요청 때 쓰지 말라고 알립니다. 설치했거나 AI가 읽은 사람에게는 사유를 알립니다. 지금 돌고 있는 요청은 멈추지 않습니다. AI가 <code>scripts/</code>나 레시피로 복사한 것과 이미 읽은 내용은 지우지 못합니다.</p>
      <div className="field"><span className="lbl">회수 사유 (설치한 사람의 카드와 {W.task}에 보입니다)</span><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      <div className="field market-radio"><span className="lbl">회수한 뒤 쓸 버전</span>
        <label className="small"><input type="radio" name="fallback" checked={fallback === "none"} onChange={() => setFallback("none")} />복귀 없음 (기본). 새 버전이 나올 때까지 아무도 쓰지 못합니다</label>
        <label className="small"><input type="radio" name="fallback" checked={fallback === "previous"} onChange={() => setFallback("previous")} />이전 버전으로 복귀. 이전 버전에 같은 오류가 없을 때만 고르세요</label>
      </div>
      <div className="field"><span className="lbl">대체 항목 이름 (없으면 비워 둡니다)</span><input type="text" value={replacement} onChange={(e) => setReplacement(e.target.value)} /></div>
      {askPin && <PinModal title="회수" confirm="회수" danger busy={act.isPending} onClose={() => setAskPin(false)} onConfirm={(pin) => act.mutate(pin)}>되돌릴 수 없습니다. 다시 공개하려면 새 버전을 올려 검토를 받습니다.</PinModal>}
    </Modal>
  );
}

function CoverageModal({ row, onClose }: { row: MarketReviewRow; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["market", "coverage", row.package_id], queryFn: () => get<MarketCoverage>(`/api/market/packages/${row.package_id}/coverage`), refetchInterval: 5000 });
  return (
    <Modal title={`${row.display_name} 회수 현황`} onClose={onClose} footer={<button onClick={onClose}>닫기</button>}>
      {data && (
        <>
          <div className="todo-pills market-pills"><span>적용<b>{data.applied}</b></span><span>꺼져 있는 PC<b>{data.off}</b></span><span>적용 중<b>{data.pending}</b></span>{data.outdated > 0 && <span>업데이트 필요<b>{data.outdated}</b></span>}</div>
          <p className="small muted market-modal-text">꺼져 있는 PC는 켜질 때 적용됩니다. 켜져 있는데 회수를 받지 못한 PC의 {W.task}는 시작하지 않습니다.{data.outdated > 0 && " '업데이트 필요'는 마켓을 모르는 옛 PC Agent입니다. 항목을 받은 적이 없어 지울 것이 없고, 답을 기다리지 않아도 됩니다."}</p>
          <div className="table-wrap panel market-table"><table>
            <thead><tr><th>사용자</th><th>PC</th><th>상태</th><th>마지막 연결</th></tr></thead>
            <tbody>
              {data.rows.map((r, i) => <tr key={i}><td>{r.user}</td><td>{r.hostname}</td><td>{r.state}</td><td>{when(r.last_seen)}</td></tr>)}
              {!data.rows.length && <tr><td colSpan={4} className="small muted empty-row">이 항목을 설치한 PC가 없습니다.</td></tr>}
            </tbody>
          </table></div>
        </>
      )}
    </Modal>
  );
}
