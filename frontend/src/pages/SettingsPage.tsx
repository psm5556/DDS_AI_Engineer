import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Copy, Download, KeyRound, Laptop, RefreshCw, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, del, get, post } from "../api";
import { useAuth } from "../auth";
import { PinInput } from "../components/PinInput";
import { copyText, when } from "../format";
import { useToast } from "../toast";
import type { AgentInstance, AgentRelease, AgentStatus, ApiToken } from "../types";

/** 설정: 내 PC Agent 설치·상태·업데이트, AI 에이전트 CLI 설치, 토큰 */
export default function SettingsPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [issued, setIssued] = useState<ApiToken | null>(null);
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status"), refetchInterval: 15000 });
  const { data: mine } = useQuery({ queryKey: ["agent", "mine"], queryFn: () => get<AgentInstance[]>("/api/agent/instances/mine"), refetchInterval: 15000 });
  const { data: latest } = useQuery({ queryKey: ["agent", "latest"], queryFn: () => get<AgentRelease>("/api/agent/releases/latest"), retry: false });
  const { data: tokens } = useQuery({ queryKey: ["tokens"], queryFn: () => get<ApiToken[]>("/api/tokens") });
  const err = (e: Error) => toast(e.message ?? "처리하지 못했습니다", true);

  const issue = useMutation({
    mutationFn: () => post<ApiToken>("/api/tokens", { name: `${user?.display_name ?? ""}의 PC` }),
    onSuccess: (t) => { setIssued(t); qc.invalidateQueries({ queryKey: ["tokens"] }); },
    onError: err,
  });
  const revoke = useMutation({ mutationFn: (id: number) => del(`/api/tokens/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["tokens"] }); toast("토큰을 폐기했습니다"); }, onError: err });
  const update = useMutation({ mutationFn: () => post(`/api/agent/instances/${user!.id}/update`), onSuccess: () => toast("업데이트를 요청했습니다. 잠시 후 다시 연결됩니다"), onError: err });
  const install = useMutation({
    mutationFn: (name: string) => post<{ login: string }>("/api/agent/install", { agent: name }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["agent"] }); toast(`설치했습니다. 처음 한 번 로그인하세요: ${r.login}`); },
    onError: err,
  });

  const inst = mine?.find((i) => i.connected) ?? mine?.[0];
  const oneLiner = issued?.token ? `$env:DDS_TOKEN='${issued.token}'; $env:DDS_AGENTS='gemini'; irm ${location.origin}/api/agent/install.ps1 | iex` : "";
  const copy = async (text: string, what: string) => toast((await copyText(text)) ? `${what}을(를) 복사했습니다` : "복사하지 못했습니다", false);

  return (
    <div className="page">
      <div className="proj-head"><div className="grow"><h1>설정</h1><div className="proj-sub">내 PC Agent · AI 에이전트 · 토큰</div></div></div>

      <div className="stack" style={{ marginTop: 22 }}>
        <section className="block">
          <div className="block-head"><h3><Laptop size={16} />내 PC Agent</h3>
            {agent?.connected ? <span className="chip ok"><CheckCircle2 size={12} />연결됨</span> : <span className="chip err"><XCircle size={12} />연결 안 됨</span>}
            <span className="grow" />
            {inst && latest && inst.outdated && agent?.connected && (
              <button className="small" onClick={() => update.mutate()} disabled={update.isPending}><RefreshCw size={13} />지금 {latest.version}(으)로 업데이트</button>
            )}
          </div>
          {inst ? (
            <div className="kv">
              <span>PC</span><b>{inst.hostname}</b>
              <span>버전</span><b>{inst.version ?? "-"} {latest && (inst.outdated ? <span className="chip warn">최신 {latest.version}</span> : <span className="chip ok">최신</span>)}</b>
              <span>마지막 연결</span><b>{when(inst.last_seen)}</b>
              <span>작업</span><b>실행 중 케이스 {inst.status?.running_cases ?? 0} · 대기 {inst.status?.queued_cases ?? 0} · 진행 중 대화 {inst.status?.active_prompts ?? 0}</b>
              {inst.status?.update_state && inst.status.update_state !== "idle" && <><span>업데이트</span><b>{inst.status.update_state}</b></>}
            </div>
          ) : <p className="small muted">아직 이 계정으로 연결된 PC가 없습니다. 아래 순서로 설치하세요.</p>}
        </section>

        <section className="block">
          <div className="block-head"><h3><Download size={16} />설치 (처음 한 번)</h3>
            <span className="small muted">Python·Node 등 필요한 것은 모두 자동으로 설치됩니다. 관리자 권한도 필요 없습니다.</span></div>
          <ol className="install-steps">
            <li><b>설치 파일 받기</b><a className="btn primary" href="/api/agent/setup.exe" download><Download size={14} />DDS-Agent-Setup.exe</a></li>
            <li><b>토큰 발급</b>
              {issued?.token ? (
                <span className="token-box"><code>{issued.token}</code>
                  <button className="icon-btn" title="토큰 복사" aria-label="토큰 복사" onClick={() => void copy(issued.token!, "토큰")}><Copy size={14} /></button></span>
              ) : <button onClick={() => issue.mutate()} disabled={issue.isPending}><KeyRound size={14} />토큰 발급</button>}
              {issued?.token && <span className="small muted">이 토큰은 지금만 보입니다. 설치 창에 붙여 넣으세요.</span>}
            </li>
            <li><b>설치 파일 실행</b><span className="small muted">토큰을 붙여 넣고, 사용할 AI 에이전트(Gemini·Claude·Codex)를 고른 뒤 [설치]. 끝나면 이 화면에 "연결됨"이 표시됩니다.</span></li>
          </ol>
          {oneLiner && (
            <details className="more"><summary>설치 파일 대신 PowerShell 명령 한 줄로 설치</summary>
              <div className="row"><code className="code-block grow" style={{ whiteSpace: "pre-wrap" }}>{oneLiner}</code>
                <button className="icon-btn" title="명령 복사" aria-label="명령 복사" onClick={() => void copy(oneLiner, "명령")}><Copy size={14} /></button></div>
            </details>
          )}
        </section>

        <section className="block">
          <div className="block-head"><h3>AI 에이전트</h3><span className="small muted">대화를 시작할 때 고를 수 있습니다. 구독·사용 권한이 있는 것만 설치하세요.</span></div>
          {!agent?.connected ? <p className="small muted">PC Agent가 연결되면 설치 상태를 보여 줍니다.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>에이전트</th><th>상태</th><th>처음 로그인</th><th /></tr></thead>
              <tbody>{(agent.info?.agents ?? []).filter((a) => a.name !== "demo").map((a) => (
                <tr key={a.name}>
                  <td><b>{a.label}</b>{agent.info?.default_agent === a.name && <span className="chip" style={{ marginLeft: 6 }}>기본</span>}</td>
                  <td>{a.installed ? <span className="chip ok">설치됨</span> : <span className="chip">미설치</span>}</td>
                  <td className="small muted">{a.login}</td>
                  <td className="r">{!a.installed && a.installable && (
                    <button className="small" onClick={() => install.mutate(a.name)} disabled={install.isPending}>
                      {install.isPending && install.variables === a.name ? <><span className="spinner" style={{ width: 12, height: 12 }} />설치 중</> : "설치"}</button>)}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </section>

        <Diagnose connected={!!agent?.connected} />

        <PinChange />

        <section className="block">
          <div className="block-head"><h3><KeyRound size={16} />PC Agent 토큰</h3><span className="cnt">{tokens?.length ?? 0}개</span></div>
          <div className="table-wrap"><table>
            <thead><tr><th>이름</th><th>발급</th><th>마지막 사용</th><th /></tr></thead>
            <tbody>{(tokens ?? []).map((t) => (
              <tr key={t.id}><td>{t.name}</td><td>{when(t.created_at)}</td><td>{when(t.last_used_at)}</td>
                <td className="r"><button className="icon-btn danger" title="폐기" aria-label={`${t.name} 폐기`} onClick={() => revoke.mutate(t.id)}><Trash2 size={13} /></button></td></tr>
            ))}</tbody>
          </table></div>
        </section>
      </div>
    </div>
  );
}

function PinChange() {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api("/api/auth/pin", { method: "PUT", body: { current_pin: cur, new_pin: next } }),
    onSuccess: () => { toast("PIN을 바꿨습니다"); setCur(""); setNext(""); },
    onError: (e: Error) => toast(e.message ?? "바꾸지 못했습니다", true),
  });
  return (
    <section className="block">
      <div className="block-head"><h3>로그인 PIN</h3><span className="small muted">숫자 6자리</span></div>
      <div className="row" style={{ gap: 24, alignItems: "flex-end" }}>
        <div className="field"><span className="lbl">현재 PIN</span><PinInput value={cur} onChange={setCur} label="현재 PIN" /></div>
        <div className="field"><span className="lbl">새 PIN</span><PinInput value={next} onChange={setNext} label="새 PIN" /></div>
        <button disabled={cur.length !== 6 || next.length !== 6 || change.isPending} onClick={() => change.mutate()}>PIN 바꾸기</button>
      </div>
    </section>
  );
}

interface Diag {
  workspace: string; workspace_free_gb: number; hints: string[];
  viewers: { ext: string; command: string[]; found: boolean }[];
}

/** 내 PC 진단: CAD/CAE 툴 경로·버전, CAE 뷰어, 작업 폴더 */
function Diagnose({ connected }: { connected: boolean }) {
  const run = useMutation({ mutationFn: () => get<Diag>("/api/agent/diagnose") });
  const d = run.data;
  return (
    <section className="block">
      <div className="block-head"><h3>내 PC 진단</h3>
        <span className="small muted">작업 폴더와 CAE 뷰어를 확인합니다. CAD·CAE 툴 설치 상태는 <Link to="/tools">CAD·CAE 툴</Link> 화면에서 봅니다</span>
        <span className="grow" />
        <button className="small" disabled={!connected || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? <><span className="spinner" style={{ width: 12, height: 12 }} />확인 중</> : "진단 실행"}</button></div>
      {run.error && <div className="notice err">{(run.error as Error).message}</div>}
      {d && (
        <>
          <div className="kv" style={{ marginBottom: 12 }}>
            <span>작업 폴더</span><b>{d.workspace} <span className="chip">{d.workspace_free_gb} GB 남음</span></b>
            <span>CAE 뷰어</span><b>{d.viewers.length ? d.viewers.map((v) => <span key={v.ext} className={`chip ${v.found ? "ok" : "err"}`}>{v.ext} → {v.command[0].split(/[\/]/).pop()}</span>)
              : <span className="small muted">등록 없음 — 결과 파일은 Windows 기본 프로그램으로 열립니다</span>}</b>
          </div>
        </>
      )}
    </section>
  );
}
