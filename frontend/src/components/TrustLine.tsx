import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { get } from "../api";
import type { TrustLineData } from "../types";

const CHIP: Record<string, string> = { good: "ok", fair: "warn", caution: "err", unknown: "" };

/** 한 줄과 예측 결과 칸이 같은 값을 쓴다 */
export function useTrustLine(url: string) {
  return useQuery({ queryKey: ["trust-line", url], queryFn: () => get<TrustLineData>(url) }).data;
}

/** Simulator 위 한 줄: 이 예측을 믿어도 되는지 쉬운 말로. 공유 화면에는 '자세히'가 없다(프로젝트 정보를 보이지 않는다) */
export function TrustLine({ url, onMore, moreTo }: { url: string; onMore?: () => void; moreTo?: string }) {
  const data = useTrustLine(url);
  if (!data) return null;
  return (
    <div className="trust-line">
      <b>이 예측을 믿어도 되나요?</b>
      <span className={`chip ${CHIP[data.state] ?? ""}`}>{data.state_label}</span>
      <span>{data.sentence}</span>
      {onMore && <button className="trust-more" onClick={onMore}>자세히</button>}
      {moreTo && <Link className="trust-more" to={moreTo}>자세히</Link>}
    </div>
  );
}
