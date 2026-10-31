import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, Maximize2, Share2 } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api";
import { AgentGlyph } from "../components/AgentGlyph";
import { Skeleton } from "../components/Skeleton";
import { when } from "../format";
import { memberFieldList, ShareModal, SimulatorView } from "../pages/SimulatorPage";
import { useToast } from "../toast";
import type { Study, SurrogateModel, TrainJob } from "../types";
import { useCanvas } from "./CanvasContext";
import { askInsightFocus } from "./StudyInsights";

/** Simulator 탭: 대화와 결과를 그대로 둔 채 패널 안에서 바로 예측한다. Simulator는 스터디 하나이고, 모델은 그 버전이다 */
export function SimulatorTab({ pid }: { pid: number }) {
  const c = useCanvas();
  const { data: studies } = useQuery({ queryKey: ["studies", pid], queryFn: () => get<Study[]>(`/api/projects/${pid}/studies`) });
  const { data: models } = useQuery({ queryKey: ["models", pid], queryFn: () => get<SurrogateModel[]>(`/api/projects/${pid}/models`) });
  if (!studies || !models) return <Skeleton rows={5} />;
  if (!studies.length) {
    return <div className="empty-hero"><AgentGlyph size={32} /><b>아직 Simulator가 없습니다</b>
      <p>예측 모델을 만들면 여기에서 바로 예측할 수 있습니다. 엑셀 표가 있으면 데이터 탭의 '표로 바로 Simulator'로 지금 만들 수 있고, 해석 결과가 있으면 대화로 요청하세요.</p></div>;
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
  const c = useCanvas();
  const live = versions.find((m) => m.deployed) ?? null;
  const [picked, setPicked] = useState<number | null>(null); // 다른 버전을 미리 보는 중이면 그 id
  const [listOpen, setListOpen] = useState(false);
  const [sharing, setSharing] = useState(false);
  const shown = versions.find((m) => m.id === picked) ?? live ?? versions[0] ?? null;
  // 넓은 화면에서 돌아오면 보던 버전을 그대로 고른다. 읽고 비워 두어 탭을 다시 열 때 그 버전으로 되돌아가지 않게 한다
  const { simModel, clearSimModel } = useCanvas();
  useLayoutEffect(() => {
    if (simModel != null && versions.some((m) => m.id === simModel)) { setPicked(simModel); clearSimModel(); }
  }, [simModel, versions, clearSimModel]);
  // AI 없이 서버에서 학습한다. 데이터가 이미 있으면 PC Agent가 꺼져 있어도 모델을 만들 수 있어야 한다
  const [training, setTraining] = useState<TrainJob | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const train = useMutation({
    mutationFn: async () => {
      let job = await post<TrainJob>(`/api/studies/${study.id}/train/start`, {});
      while (job.status === "running" && alive.current) {
        setTraining(job);
        await new Promise((r) => setTimeout(r, 1500));
        job = await get<TrainJob>(`/api/train-jobs/${job.job_id}`);
      }
      if (job.status === "failed") throw new Error(job.error ?? "학습하지 못했습니다 → 다시 시도해 주세요");
      return job;
    },
    onSettled: () => { setTraining(null); qc.invalidateQueries({ queryKey: ["models", pid] }); },
    onError: (e: Error) => toast(e.message, true),
  });
  const deploy = useMutation({
    mutationFn: (m: SurrogateModel) => post<SurrogateModel>(`/api/models/${m.id}/deploy`, {}),
    onSuccess: (m) => { qc.invalidateQueries({ queryKey: ["models", pid] }); setPicked(null); toast(`현재 버전: v${m.version}. 공유 링크도 이 버전을 보여 줍니다`); },
    onError: (e: Error) => toast(e.message, true),
  });

  if (!shown) {
    return (
      <div className="row"><span className="small muted grow">{train.isPending
        ? `${training?.rows ?? ""}${training ? "행으로 " : ""}학습 중입니다. 멈춘 것이 아닙니다. 행이 많으면 몇 분 걸립니다.`
        : "아직 예측 모델이 없습니다. 완료된 케이스가 3건 이상이면 학습할 수 있습니다."}</span>
        <button className="small" disabled={train.isPending} onClick={() => train.mutate()}>{train.isPending && <Loader2 size={13} className="spin" />}예측 모델 학습</button></div>
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

      <SimulatorView key={shown.id} embedded onMore={() => { askInsightFocus("trust"); c.setTab("data"); }} base={`/api/models/${shown.id}`} specPath={`/api/models/${shown.id}/spec`}
        fieldList={memberFieldList(shown.id)} fieldBase={(id) => `/api/field-models/${id}`} />
      {sharing && live && <ShareModal modelId={live.id} onClose={() => setSharing(false)} />}
    </div>
  );
}
