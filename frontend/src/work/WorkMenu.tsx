import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, Pencil, Power, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api, del, get, post, put } from "../api";
import { Confirm } from "../components/Modal";
import { RenameModal } from "../components/ProjectMenu";
import { useToast } from "../toast";
import type { WorkBrief, WorkDetail } from "../types";
import { COPY, FEATURE, taskPath } from "./copy";

/** Task 메뉴(사이드바 ⋯, 머리글 ▾): 이름 바꾸기 · Task 폴더 열기 · 이 Task의 AI 끝내기 · 삭제.
 *  남의 Task는 일반 멤버에게 메뉴가 없고, master·관리자에게는 이름 바꾸기·삭제만 보인다. 서버가 같은 규칙을 다시 본다. */
export function WorkMenu({ work, at, onClose }: { work: WorkBrief; at: { x: number; y: number }; onClose: () => void }) {
  const [dialog, setDialog] = useState<"rename" | "delete" | "end" | null>(null);
  const [detail, setDetail] = useState<WorkDetail | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const err = (e: Error) => toast(e.message ?? "처리하지 못했습니다", true);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["project", work.project_id] });
    qc.invalidateQueries({ queryKey: ["work", work.id] });
  };

  useEffect(() => {
    if (dialog) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    setTimeout(() => window.addEventListener("mousedown", h), 0);
    window.addEventListener("keydown", k);
    return () => { window.removeEventListener("mousedown", h); window.removeEventListener("keydown", k); };
  }, [dialog, onClose]);

  const remove = useMutation({
    mutationFn: () => del(`/api/works/${work.id}`),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ["work", work.id] });
      refresh();
      toast(COPY.deleted);
      onClose();
      if (location.pathname === taskPath(work.project_id, work.id)) nav(taskPath(work.project_id), { replace: true }); // 다음 Task로 간다. 없으면 새 Task 화면이다
    },
    onError: (e: Error) => { err(e); onClose(); },
  });
  const endAi = useMutation({
    mutationFn: () => post(`/api/works/${work.id}/end-session`),
    onSuccess: () => { toast(COPY.endAiDone); onClose(); },
    onError: (e: Error) => { err(e); onClose(); },
  });
  const openFolder = useMutation({
    mutationFn: () => api(`/api/projects/${work.project_id}/local/open?work_id=${work.id}`, { method: "POST", body: { path: "" } }),
    onSuccess: () => onClose(),
    onError: (e: Error) => { err(e); onClose(); },
  });

  if (dialog === "rename") {
    return <RenameModal title={COPY.renameTaskTitle} value={work.name} onClose={onClose}
      onSave={(name) => put(`/api/works/${work.id}`, { name }).then(() => { refresh(); toast(COPY.renamed); onClose(); }).catch((e: Error) => { err(e); onClose(); })} />;
  }
  if (dialog === "delete") {
    // 지우기 전에 사라지는 것을 숫자로 보여 준다. 숫자를 못 읽어도 창은 뜬다
    return <Confirm title={COPY.deleteTitle(work.name)} danger confirmLabel={COPY.remove} onClose={onClose} onConfirm={() => remove.mutate()}
      message={<>{COPY.deleteBody(detail?.n_simulators ?? 0, detail?.n_share_links ?? 0)}<br />{COPY.deleteKeep}</>} />;
  }
  if (dialog === "end") {
    return <Confirm title={COPY.endAi} confirmLabel={COPY.endAiConfirm} onClose={onClose} onConfirm={() => endAi.mutate()} message={COPY.endAiBody} />;
  }
  const askDelete = () => { void get<WorkDetail>(`/api/works/${work.id}`).then(setDetail, () => setDetail(null)); setDialog("delete"); };
  const style = { left: Math.min(at.x, window.innerWidth - 230), top: Math.min(at.y, window.innerHeight - 200) };
  return createPortal(
    <div ref={ref} className="ctx-menu" role="menu" style={style} aria-label={COPY.menuTask}>
      {work.can_manage && <button role="menuitem" onClick={() => setDialog("rename")}><Pencil size={14} />{COPY.renameTask}</button>}
      {work.mine && !work.legacy && <button role="menuitem" onClick={() => openFolder.mutate()}><FolderOpen size={14} />{COPY.openTaskFolder}</button>}
      {FEATURE.endAi && work.mine && <button role="menuitem" onClick={() => setDialog("end")}><Power size={14} />{COPY.endAi}</button>}
      {work.can_manage && <><div className="ctx-sep" /><button role="menuitem" className="danger" onClick={askDelete}><Trash2 size={14} />{COPY.remove}</button></>}
    </div>,
    document.body,
  );
}

/** 메뉴를 보여 줄 것이 있는 Task인가 */
export const hasWorkMenu = (w: WorkBrief) => w.can_manage || (w.mine && (!w.legacy || FEATURE.endAi));
