import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLive } from "../live";
import type { Project } from "../types";
import { COPY, taskPath } from "./copy";
import { currentWorkId, findWork } from "./scope";

interface Notice { key: string; text: string; path: string }
const SHOW_MS = 12000;

/** 다른 Task의 소식: 지금 보는 탭을 빼앗지 않고 알림으로 띄운다. '2차 M9' 해석이 끝났습니다 · 열기
 *  내 Task만 알린다. 같은 흐름의 같은 상태는 한 번만(단계를 갱신할 때마다 소식이 오기 때문이다). */
export function WorkNotices() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [items, setItems] = useState<Notice[]>([]);
  const said = useRef(new Set<string>());
  const close = useCallback((key: string) => setItems((xs) => xs.filter((x) => x.key !== key)), []);

  useLive((e) => {
    if (e.type !== "flow_updated" || typeof e.work_id !== "number" || e.work_id === currentWorkId()) return;
    const status = (e.data as { status?: string } | undefined)?.status;
    const words = status === "complete" ? COPY.noticeDone : status === "review" ? COPY.noticeReview : status === "failed" ? COPY.noticeFailed : null;
    if (!words) return;
    const key = `${(e.data as { id?: number }).id}:${status}`;
    if (said.current.has(key)) return;
    const hit = findWork(qc.getQueryData<Project[]>(["projects"]), e.work_id);
    if (!hit?.work.mine) return;
    said.current.add(key);
    setItems((xs) => [...xs, { key, text: words(hit.work.name), path: taskPath(hit.project.id, hit.work.id) }]);
    setTimeout(() => close(key), SHOW_MS);
  });

  if (!items.length) return null;
  return (
    <div className="work-notices" role="status">
      {items.map((n) => (
        <div key={n.key} className="work-notice">
          <span className="grow">{n.text}</span>
          <button type="button" className="link-btn" onClick={() => { close(n.key); nav(n.path); }}>{COPY.noticeOpen}</button>
          <button type="button" className="icon-btn" aria-label="닫기" title="닫기" onClick={() => close(n.key)}><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}
