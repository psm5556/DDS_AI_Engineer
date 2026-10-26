import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { get, post, put } from "../api";
import { useAuth } from "../auth";
import { Modal } from "../components/Modal";
import { PinInput } from "../components/PinInput";
import { SetupChecklist } from "../components/SetupReadiness";
import { when } from "../format";
import { useToast } from "../toast";
import type { AgentInstance, AgentRelease, User } from "../types";

/** 관리: 사용자 승인 · PC Agent 현황(버전·연결·작업) · Agent 배포 버전 */
export default function AdminPage() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"agents" | "users" | "releases">(() => {
    const t = params.get("tab");
    return t === "users" || t === "releases" ? t : "agents";
  });
  if (!user?.is_admin) return <Navigate to="/" replace />;
  return (
    <div className="page wide">
      <div className="proj-head"><div className="grow"><h1>관리</h1><div className="proj-sub">사용자 승인 · PC Agent 모니터링 · 버전 배포</div></div></div>
      <nav className="stepper proj-tabs">
        {([["agents", "PC Agent 현황"], ["users", "사용자"], ["releases", "Agent 배포"]] as const).map(([k, l]) => (
          <button key={k} className={`ptab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </nav>
      {tab === "agents" ? <Agents /> : tab === "users" ? <Users /> : <Releases />}
    </div>
  );
}

function Agents() {
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["agent", "instances"], queryFn: () => get<AgentInstance[]>("/api/agent/instances"), refetchInterval: 10000 });
  const update = useMutation({ mutationFn: (uid: number) => post(`/api/agent/instances/${uid}/update`), onSuccess: () => toast("업데이트를 지시했습니다"), onError: (e: Error) => toast(e.message, true) });
  const list = data ?? [];
  const online = list.filter((i) => i.connected);
  return (
    <>
      <div className="todo-pills" style={{ marginTop: 0, marginBottom: 16 }}>
        <span>연결<b>{online.length}</b></span><span>전체<b>{list.length}</b></span>
        <span>구버전<b>{list.filter((i) => i.outdated).length}</b></span>
        <span>실행 중 케이스<b>{online.reduce((a, i) => a + (i.status?.running_cases ?? 0), 0)}</b></span>
      </div>
      <div className="table-wrap panel" style={{ padding: 0 }}><table>
        <thead><tr><th>사용자</th><th>PC</th><th>상태</th><th>버전</th><th>AI 에이전트</th><th>작업</th><th>마지막</th><th /></tr></thead>
        <tbody>{list.map((i) => (
          <tr key={i.id}>
            <td>{i.user_display_name}</td><td>{i.hostname}</td>
            <td className="status-cell"><span className={`st ${i.connected ? "done" : ""}`}><i />{i.connected ? "연결" : "끊김"}</span></td>
            <td>{i.version} {i.outdated && <span className="chip warn">구버전</span>}</td>
            <td className="small">{(i.agents ?? []).filter((a) => a.installed && a.name !== "demo").map((a) => a.label).join(", ") || "-"}</td>
            <td className="small">케이스 {i.status?.running_cases ?? 0}/{i.status?.queued_cases ?? 0} · 대화 {i.status?.active_prompts ?? 0}
              {i.status?.update_state && i.status.update_state !== "idle" && <div className="muted">{i.status.update_state}</div>}</td>
            <td>{when(i.last_seen)}</td>
            <td className="r">{i.connected && i.outdated && <button className="small" onClick={() => update.mutate(i.user_id)}><RefreshCw size={12} />업데이트</button>}</td>
          </tr>))}</tbody>
      </table></div>
    </>
  );
}

function Users() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/api/users") });
  const set = useMutation({
    mutationFn: (v: { id: number; body: Partial<User> & { pin?: string } }) => put(`/api/users/${v.id}`, v.body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast("저장했습니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  const pending = (data ?? []).filter((u) => !u.is_active);
  const [reset, setReset] = useState<User | null>(null);
  const [pin, setPin] = useState("");
  return (
    <>
    {reset && (
      <Modal title={`${reset.display_name} PIN 초기화`} onClose={() => { setReset(null); setPin(""); }} footer={<>
        <button onClick={() => { setReset(null); setPin(""); }}>취소</button>
        <button className="primary" disabled={pin.length !== 6} onClick={() => { set.mutate({ id: reset.id, body: { pin } as Partial<User> & { pin: string } }); setReset(null); setPin(""); }}>초기화</button>
      </>}>
        <div className="field"><span className="lbl">새 PIN (사용자에게 전달 후 설정에서 바꾸도록 안내)</span><PinInput value={pin} onChange={setPin} autoFocus label="새 PIN" /></div>
      </Modal>
    )}
    <div className="table-wrap panel" style={{ padding: 0 }}><table>
      <thead><tr><th>이름</th><th>아이디</th><th>상태</th><th>관리자</th><th /></tr></thead>
      <tbody>{[...pending, ...(data ?? []).filter((u) => u.is_active)].map((u) => (
        <tr key={u.id}>
          <td>{u.display_name}</td><td>{u.username}</td>
          <td>{u.is_active ? <span className="chip ok">사용 중</span> : <span className="chip warn">승인 대기</span>}</td>
          <td><input type="checkbox" checked={u.is_admin} onChange={(e) => set.mutate({ id: u.id, body: { is_admin: e.target.checked } })} aria-label={`${u.display_name} 관리자`} /></td>
          <td className="r"><button className="small ghost" onClick={() => setReset(u)}>PIN 초기화</button>{" "}
            {u.is_active
            ? <button className="small danger" onClick={() => set.mutate({ id: u.id, body: { is_active: false } })}>사용 중지</button>
            : <button className="small primary" onClick={() => set.mutate({ id: u.id, body: { is_active: true } })}>승인</button>}</td>
        </tr>))}</tbody>
    </table></div>
    </>
  );
}

function Releases() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["agent", "releases"], queryFn: () => get<AgentRelease[]>("/api/agent/releases") });
  const latest = useMutation({
    mutationFn: (v: string) => put(`/api/agent/releases/${v}/latest`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["agent"] }); toast("최신 버전으로 지정했습니다. 연결된 PC가 유휴 시 자동 업데이트합니다"); },
    onError: (e: Error) => toast(e.message, true),
  });
  return (
    <>
      <SetupChecklist />
      <div className="notice info" style={{ margin: "16px 0" }}>
        새 버전 배포: <code>python scripts/build_agent_bundle.py --publish --server {location.origin} --notes "변경 내용"</code>
        · 이전 버전을 [최신으로 지정]하면 롤백됩니다.
      </div>
      <div className="table-wrap panel" style={{ padding: 0 }}><table>
        <thead><tr><th>버전</th><th>변경 내용</th><th className="r">크기</th><th>등록</th><th /></tr></thead>
        <tbody>{(data ?? []).map((r) => (
          <tr key={r.id}>
            <td><b>{r.version}</b> {r.is_latest && <span className="chip ok">최신</span>}</td>
            <td className="small">{r.notes || "-"}</td><td className="r">{Math.round(r.size / 1024)} KB</td><td>{when(r.created_at)}</td>
            <td className="r">{!r.is_latest && <button className="small" onClick={() => latest.mutate(r.version)}>최신으로 지정</button>}</td>
          </tr>))}
          {data && !data.length && <tr><td colSpan={5} className="small muted empty-row">아직 배포한 버전이 없습니다. 위 명령으로 첫 버전을 올리면 연결된 PC가 자동으로 업데이트합니다.</td></tr>}
        </tbody>
      </table></div>
    </>
  );
}
