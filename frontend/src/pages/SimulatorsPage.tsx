import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, X } from "lucide-react";
import { Link } from "react-router-dom";
import { del, get } from "../api";
import { AgentGlyph } from "../components/AgentGlyph";
import { Skeleton } from "../components/Skeleton";
import { when } from "../format";
import { useToast } from "../toast";

interface Item {
  study_id: number; study_name: string; model_id: number; version: number | null; outputs: string[]; n_train: number; url: string;
  source: "mine" | "project" | "link"; status: "active" | "stopped" | "expired"; project_id: number | null; project_name: string | null;
  shared_by: string | null; note: string; share_id: number | null; updated_at: string | null;
}
const OFF = { stopped: "공유가 중지되었습니다", expired: "공유 기간이 끝났습니다" } as const;

/** 내 Simulator와 공유받은 Simulator만 모은 화면. 프로젝트에 들어가지 않고 바로 연다 */
export default function SimulatorsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, error } = useQuery({ queryKey: ["simulators"], queryFn: () => get<{ mine: Item[]; shared: Item[] }>("/api/simulators") });
  const hide = useMutation({
    mutationFn: (id: number) => del(`/api/simulators/shared/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["simulators"] }),
    onError: (e: Error) => toast(e.message, true),
  });
  if (error) return <div className="page"><div className="notice err">{(error as Error).message}</div></div>;
  if (!data) return <Skeleton page rows={5} />;

  const row = (x: Item) => {
    const meta = [x.source === "link" ? `${x.shared_by ?? "다른 사용자"}님이 공유` : x.source === "project" ? `${x.shared_by ?? "멤버"}님이 배포` : "",
      x.project_name, x.note].filter(Boolean).join(" · ");
    const body = (
      <>
        <b>{x.study_name}{meta && <small className="sim-row-meta">{meta}</small>}</b>
        <span className="small sim-row-out" title={x.outputs.join(", ")}>{x.outputs.join(", ")}</span>
        {x.version != null && <span className="chip">v{x.version}</span>}
        <span className="small">{x.updated_at ? when(x.updated_at) : ""}</span>
      </>
    );
    return x.status === "active" ? (
      <div key={x.study_id} className="sim-row">
        <Link className="recent-row" to={x.url}>{body}<ArrowRight size={14} className="go" /></Link>
        {x.share_id != null && <button className="icon-btn" title="목록에서 빼기. 링크를 다시 열면 돌아옵니다" aria-label={`${x.study_name} 목록에서 빼기`} onClick={() => hide.mutate(x.share_id!)}><X size={14} /></button>}
      </div>
    ) : (
      <div key={x.study_id} className="sim-row off">
        <div className="recent-row"><b>{x.study_name}<small className="sim-row-meta">{OFF[x.status]}{x.shared_by ? ` · ${x.shared_by}님에게 다시 요청합니다` : ""}</small></b></div>
        {x.share_id != null && <button className="icon-btn" title="목록에서 빼기" aria-label={`${x.study_name} 목록에서 빼기`} onClick={() => hide.mutate(x.share_id!)}><X size={14} /></button>}
      </div>
    );
  };

  const empty = !data.mine.length && !data.shared.length;
  return (
    <div className="page sims-page">
      <h1>Simulator</h1>
      <p className="page-sub">해석 없이 설계변수를 바꿔 바로 예측합니다. 프로젝트에 들어가지 않아도 됩니다.</p>
      {empty && (
        <div className="empty-hero"><AgentGlyph size={32} /><b>아직 Simulator가 없습니다</b>
          <p>프로젝트에서 예측 모델을 배포하면 여기에 보입니다. 공유 링크를 한 번 열면 그 Simulator도 여기에 들어옵니다.</p></div>
      )}
      {data.mine.length > 0 && (
        <section className="sims-sec"><h2 className="home-sec">내 Simulator</h2><div className="recent-rows">{data.mine.map(row)}</div></section>
      )}
      {data.shared.length > 0 && (
        <section className="sims-sec"><h2 className="home-sec">공유받은 Simulator</h2><div className="recent-rows">{data.shared.map(row)}</div></section>
      )}
    </div>
  );
}
