import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Maximize2, Share2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api";
import { AgentGlyph } from "../components/AgentGlyph";
import { Skeleton } from "../components/Skeleton";
import { sendToAgent, when } from "../format";
import { memberFieldList, ShareModal, SimulatorView } from "../pages/SimulatorPage";
import { useToast } from "../toast";
import type { Study, SurrogateModel } from "../types";
import { useCanvas } from "./CanvasContext";

/** Simulator 탭: 대화와 결과를 그대로 둔 채 패널 안에서 바로 예측한다. Simulator는 스터디 하나이고, 모델은 그 버전이다 */
export function SimulatorTab({ pid }: { pid: number }) {
  const c = useCanvas();
  const { data: studies } = useQuery({ queryKey: ["studies", pid], queryFn: () => get<Study[]>(`/api/projects/${pid}/studies`) });
  const { data: models } = useQuery({ queryKey: ["models", pid], queryFn: () => get<SurrogateModel[]>(`/api/projects/${pid}/models`) });
  if (!studies || !models) return <Skeleton rows={5} />;
  if (!studies.length) {
    return <div className="empty-hero"><AgentGlyph size={32} /><b>아직 Simulator가 없습니다</b>
      <p>에이전트가 해석 데이터로 예측 모델을 만들면 여기에서 바로 예측할 수 있습니다.</p></div>;
  }
  const cur = studies.find((s) => s.id === c.studyId) ?? studies[0];
  return (
    <>
      {studies.length > 1 && (
        <div className="row" style={{ marginBottom: 16 }}>
          <span className="seg">{studies.map((s) => <button key={s.id} className={cur.id === s.id ? "on" : ""} onClick={() => c.setStudyId(s.id)}>{s.name}</button>)}</span>
        </div>
      )}
      <StudySimulator key={cur.id} pid={pid} study={cur} versions={models.filter((m) => m.study_id === cur.id).sort((a, b) => b.id - a.id)} />
    </>
  );
}

function StudySimulator({ pid, study, versions }: { pid: number; study: Study; versions: SurrogateModel[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const live = versions.find((m) => m.deployed) ?? null;
  const [picked, setPicked] = useState<number | null>(null); // 다른 버전을 미리 보는 중이면 그 id
  const [listOpen, setListOpen] = useState(false);
  const [sharing, setSharing] = useState(false);
  const shown = versions.find((m) => m.id === picked) ?? live ?? versions[0] ?? null;
  const deploy = useMutation({
    mutationFn: (m: SurrogateModel) => post<SurrogateModel>(`/api/models/${m.id}/deploy`, {}),
    onSuccess: (m) => { qc.invalidateQueries({ queryKey: ["models", pid] }); setPicked(null); toast(`현재 버전: v${m.version}. 공유 링크도 이 버전을 보여 줍니다`); },
    onError: (e: Error) => toast(e.message, true),
  });

  if (!shown) {
    return (
      <div className="row"><span className="small muted grow">아직 예측 모델이 없습니다. 케이스가 쌓이면 학습할 수 있습니다.</span>
        <button className="small" onClick={() => sendToAgent(`스터디 '${study.name}'(#${study.id})의 완료된 케이스로 예측 모델을 학습해 줘`)}>학습 요청</button></div>
    );
  }
  const preview = !live || shown.id !== live.id;
  return (
    <div className="sim-tab">
      <div className="sim-tab-head">
        <button className="sim-ver-toggle" aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)} title="모델 버전 목록">
          {listOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<b>v{shown.version}</b>
          {!preview && <span className="chip ok">현재</span>}
          <span className="small muted">버전 {versions.length}개</span>
        </button>
        <span className="grow" />
        {live && <button className="small" onClick={() => setSharing(true)} title="이 Simulator를 링크로 공유합니다. 링크는 항상 현재 버전을 보여 줍니다"><Share2 size={13} />공유</button>}
        <Link className="btn small" to={`/simulator/${shown.id}`} title="패널 없이 넓은 화면으로 엽니다"><Maximize2 size={13} />넓게 보기</Link>
      </div>

      {listOpen && (
        <div className="table-wrap sim-versions"><table>
          <thead><tr><th>버전</th><th>정확도(R²)</th><th className="r">학습</th><th>만든 날</th></tr></thead>
          <tbody>{versions.map((m) => (
            <tr key={m.id} className={m.id === shown.id ? "on" : ""} tabIndex={0} aria-selected={m.id === shown.id} title="눌러서 이 버전을 미리 봅니다"
              onClick={() => setPicked(m.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPicked(m.id); } }}>
              <td className="nowrap"><b>v{m.version}</b>{m.deployed && <span className="chip ok">현재</span>}</td>
              <td><span className="model-r2">{Object.entries(m.metrics).map(([k, v]) => <span key={k} className={`chip ${v.r2 >= 0.9 ? "ok" : v.r2 >= 0.7 ? "warn" : "err"}`}>{k} {v.r2.toFixed(3)}</span>)}</span></td>
              <td className="r nowrap">{m.n_train}건</td><td className="nowrap">{when(m.created_at)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}

      {preview && (
        <div className="notice sim-preview">
          <span className="grow">{live ? `미리 보는 중: v${shown.version}. 공유 링크는 현재 버전(v${live.version})을 보여 줍니다.` : `배포 전: v${shown.version}. 프로젝트 멤버만 볼 수 있습니다.`}</span>
          <button className="small primary" disabled={deploy.isPending} onClick={() => deploy.mutate(shown)}>{!live ? "배포" : shown.id < live.id ? "이 버전으로 되돌리기" : "현재 버전으로"}</button>
        </div>
      )}

      <SimulatorView key={shown.id} embedded base={`/api/models/${shown.id}`} specPath={`/api/models/${shown.id}/spec`}
        fieldList={memberFieldList(shown.id)} fieldBase={(id) => `/api/field-models/${id}`} />
      {sharing && live && <ShareModal modelId={live.id} onClose={() => setSharing(false)} />}
    </div>
  );
}
