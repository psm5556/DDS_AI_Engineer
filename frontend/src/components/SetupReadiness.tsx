import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Rocket, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../toast";

export interface SetupStatus { setup_exe: boolean; runtime: boolean; release: string | null; ready: boolean; runtime_dir: string | null }

/** PC Agent 설치에 필요한 것이 서버에 준비됐는지 (설치 프로그램 · 포터블 런타임 · 배포된 Agent 버전) */
export function useSetupStatus() {
  return useQuery({ queryKey: ["agent", "setup"], queryFn: () => get<SetupStatus>("/api/agent/setup/status"), staleTime: 60_000 });
}

/** 설정 화면: 준비가 안 됐으면 다운로드 대신 무엇이 빠졌는지 알려 준다 */
export function SetupNotice({ st }: { st: SetupStatus }) {
  const { user } = useAuth();
  if (st.ready) return null;
  return (
    <div className="notice warn setup-notice"><AlertTriangle size={16} />
      <span className="grow">서버에 PC Agent 설치 파일이 아직 다 준비되지 않았습니다
        ({[!st.setup_exe && "설치 프로그램", !st.runtime && "Python·Node 런타임", !st.release && "배포된 Agent 버전"].filter(Boolean).join(", ")} 없음).
        {user?.is_admin ? <> <Link to="/admin?tab=releases">관리 &gt; Agent 배포</Link>에서 준비하세요.</> : " 관리자에게 준비를 요청하세요."}</span>
    </div>
  );
}

/** 관리 화면: 설치 준비 점검표 — 빠진 것마다 바로 할 일 */
export function SetupChecklist() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: st } = useSetupStatus();
  const publish = useMutation({
    mutationFn: () => post<{ version: string }>("/api/agent/releases/from-server"),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["agent"] }); toast(`Agent ${r.version}을(를) 배포했습니다`); },
    onError: (e: Error) => toast(e.message, true),
  });
  if (!st) return null;
  const Row = ({ ok, title, children }: { ok: boolean; title: string; children?: React.ReactNode }) => (
    <li className={ok ? "ok" : ""}>
      {ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
      <div className="grow"><b>{title}</b>{!ok && children && <div className="small">{children}</div>}</div>
    </li>
  );
  return (
    <section className="block setup-check">
      <div className="block-head"><h3>PC Agent 설치 준비</h3>
        <span className={`chip ${st.ready ? "ok" : "warn"}`}>{st.ready ? "사용자가 바로 설치할 수 있습니다" : "준비가 더 필요합니다"}</span></div>
      <ul>
        <Row ok={st.setup_exe} title="설치 프로그램 (DDS-Agent-Setup.exe)">
          서버에서 자동으로 빌드하지 못했습니다(Windows의 .NET Framework csc 필요). 서버에서 <code>powershell -File scripts\build_setup_exe.ps1</code>을 실행하세요.
        </Row>
        <Row ok={st.runtime} title="포터블 런타임 (Python · Node · 패키지)">
          저장소(git)에는 들어 있지 않습니다. 서버에서 <code>python scripts\prepare_onsite.py</code>를 실행하면 이 서버의 Python·Node와
          사내 pip 미러로 <code>{st.runtime_dir ?? "data\\agent_runtime"}</code>에 만듭니다(인터넷 불필요, 수 분).
          사내 AI에게 맡기려면 "docs/onsite-ai-runbook.md대로 서버를 준비해 줘"라고 요청하세요.
        </Row>
        <Row ok={!!st.release} title={st.release ? `배포된 Agent 버전 ${st.release}` : "배포된 Agent 버전"}>
          <button className="small primary" disabled={publish.isPending} onClick={() => publish.mutate()}>
            <Rocket size={13} />{publish.isPending ? "배포 중…" : "서버 코드로 첫 버전 배포"}</button>
        </Row>
      </ul>
    </section>
  );
}
