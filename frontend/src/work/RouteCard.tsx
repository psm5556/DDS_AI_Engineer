import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { get } from "../api";
import type { TakeoverPreview } from "../types";
import { COPY } from "./copy";

/** 입력창 위의 한 문장 카드: 주 버튼 하나, 글자 링크 하나. 카드가 떠 있을 때 Enter를 한 번 더 누르면 글자 링크로 처리한다(입력창이 맡는다). */
export function RouteCard({ text, primary, link, busy, lines, onPrimary, onLink }: {
  text: string; primary: string; link: string; busy?: boolean; lines?: React.ReactNode; onPrimary: () => void; onLink: () => void;
}) {
  return (
    <div className="route-card" role="group" aria-label="어디서 할까요">
      <p className="route-text">{text}</p>
      {lines}
      <div className="route-actions">
        <button type="button" className="primary small" disabled={busy} onClick={onPrimary}>{primary}</button>
        <button type="button" className="link-btn" disabled={busy} onClick={onLink}>{link}</button>
      </div>
    </div>
  );
}

/** 카드 C: 남의 Task. 내 PC로 복사하기 전에 없는 툴과 받지 못하는 원본을 미리 알린다 */
export function TakeoverCard({ wid, owner, busy, onPrimary, onLink }: { wid: number; owner: string; busy?: boolean; onPrimary: () => void; onLink: () => void }) {
  const { data: pv } = useQuery({ queryKey: ["takeover-preview", wid], queryFn: () => get<TakeoverPreview>(`/api/works/${wid}/takeover-preview`) });
  return (
    <RouteCard text={COPY.cardC(owner)} primary={COPY.copyToPc} link={COPY.cardCancel} busy={busy} onPrimary={onPrimary} onLink={onLink}
      lines={pv && (
        <div className="route-lines small muted">
          {pv.n_copied > 0 && <p>{COPY.copyCases(pv.n_copied)}</p>}
          {pv.missing_tools.map((t) => <p key={t.key}>{COPY.toolMissing(t.label)} <Link to="/tools">{COPY.toolMissingLink}</Link></p>)}
          {pv.n_raw > 0 && <p>{COPY.rawLeft(pv.n_raw, pv.owner_name)}</p>}
          {!pv.has_recipe && <p>{COPY.sourceNoRecipe}</p>}
          {pv.script_author && <p>{COPY.sourceAuthor(pv.script_author)}</p>}
          {!pv.agent_connected && <p>{COPY.copyAgentOff}</p>}
        </div>
      )} />
  );
}
