import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, Pencil, Star, Trash2, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api, del, put } from "../api";
import { MembersPanel } from "../canvas/views";
import { useToast } from "../toast";
import type { Project } from "../types";
import { Confirm, Modal } from "./Modal";

/** 프로젝트 메뉴 (⋯ 또는 우클릭): 이름 바꾸기 · 즐겨찾기 · 멤버·공유 · 내 PC 폴더 열기 · 삭제 */
export function ProjectMenu({ p, at, onClose }: { p: Project; at: { x: number; y: number }; onClose: () => void }) {
  const [dialog, setDialog] = useState<"rename" | "members" | "delete" | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const isMaster = p.user_role === "master";
  const refresh = () => { qc.invalidateQueries({ queryKey: ["projects"] }); qc.invalidateQueries({ queryKey: ["project", p.id] }); };
  const err = (e: Error) => toast(e.message ?? "처리하지 못했습니다", true);

  useEffect(() => {
    if (dialog) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    setTimeout(() => window.addEventListener("mousedown", h), 0);
    window.addEventListener("keydown", k);
    return () => { window.removeEventListener("mousedown", h); window.removeEventListener("keydown", k); };
  }, [dialog, onClose]);

  const fav = useMutation({
    mutationFn: () => api(`/api/projects/${p.id}/favorite`, { method: p.is_favorite ? "DELETE" : "PUT" }),
    onSuccess: () => { refresh(); toast(p.is_favorite ? "즐겨찾기에서 뺐습니다" : "즐겨찾기에 넣었습니다"); onClose(); },
    onError: err,
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/projects/${p.id}`),
    onSuccess: () => { refresh(); toast("프로젝트를 삭제했습니다 (내 PC의 작업 폴더는 그대로 남아 있습니다)"); onClose(); nav("/"); },
    onError: err,
  });
  const openLocal = useMutation({
    mutationFn: () => api(`/api/projects/${p.id}/local/open`, { method: "POST", body: { path: "" } }),
    onSuccess: () => { toast("내 PC에서 작업 폴더를 열었습니다"); onClose(); },
    onError: err,
  });

  if (dialog === "rename") return <ProjectInfoModal p={p} onClose={onClose}
    onSave={(name, description) => put(`/api/projects/${p.id}`, { name, description, version: p.version }).then(() => { refresh(); toast("저장했습니다"); onClose(); }).catch(err)} />;
  if (dialog === "members") return (
    <Modal title={`멤버·공유 — ${p.name}`} onClose={onClose}><MembersPanel p={p} /></Modal>
  );
  if (dialog === "delete") return (
    <Confirm title="프로젝트 삭제" danger confirmLabel="삭제" onClose={onClose} onConfirm={() => remove.mutate()}
      message={<>‘{p.name}’의 대화·작업 흐름·결과·스터디·모델이 모두 삭제됩니다. 멤버에게서도 사라집니다.<br />
        내 PC의 작업 폴더(해석 파일)는 지우지 않습니다.</>} />
  );

  const style = { left: Math.min(at.x, window.innerWidth - 230), top: Math.min(at.y, window.innerHeight - 260) };
  return createPortal(
    <div ref={ref} className="ctx-menu" role="menu" style={style} aria-label={`${p.name} 메뉴`}>
      <button role="menuitem" onClick={() => setDialog("rename")} disabled={!isMaster} title={isMaster ? "" : "프로젝트 관리자만 바꿀 수 있습니다"}><Pencil size={14} />이름·설명 바꾸기</button>
      <button role="menuitem" onClick={() => fav.mutate()}><Star size={14} fill={p.is_favorite ? "currentColor" : "none"} />{p.is_favorite ? "즐겨찾기 해제" : "즐겨찾기"}</button>
      <button role="menuitem" onClick={() => setDialog("members")}><Users size={14} />멤버·공유</button>
      <button role="menuitem" onClick={() => openLocal.mutate()}><FolderOpen size={14} />내 PC 작업 폴더 열기</button>
      <div className="ctx-sep" />
      <button role="menuitem" className="danger" onClick={() => setDialog("delete")} disabled={!isMaster} title={isMaster ? "" : "프로젝트 관리자만 삭제할 수 있습니다"}><Trash2 size={14} />삭제</button>
    </div>,
    document.body,
  );
}

/** 프로젝트 이름·설명 (설명은 사이드바에서 이름에 마우스를 올리면 보인다) */
function ProjectInfoModal({ p, onSave, onClose }: { p: Project; onSave: (name: string, description: string) => void; onClose: () => void }) {
  const [name, setName] = useState(p.name);
  const [desc, setDesc] = useState(p.description);
  const ok = !!name.trim() && (name.trim() !== p.name || desc.trim() !== p.description);
  return (
    <Modal title="프로젝트 이름·설명" onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className="primary" disabled={!ok} onClick={() => onSave(name.trim(), desc.trim())}>저장</button>
    </>}>
      <div className="stack">
        <label className="field"><span className="lbl">이름</span>
          <input type="text" autoFocus value={name} maxLength={200} onChange={(e) => setName(e.target.value)} onFocus={(e) => e.target.select()} /></label>
        <label className="field"><span className="lbl">설명</span>
          <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="무엇을 하는 프로젝트인지 한두 줄로" /></label>
      </div>
    </Modal>
  );
}

export function RenameModal({ title, value, onSave, onClose }: { title: string; value: string; onSave: (v: string) => void; onClose: () => void }) {
  const [v, setV] = useState(value);
  const ok = v.trim() && v.trim() !== value;
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className="primary" disabled={!ok} onClick={() => onSave(v.trim())}>저장</button>
    </>}>
      <input type="text" autoFocus value={v} maxLength={200} onChange={(e) => setV(e.target.value)} aria-label="새 이름"
        onKeyDown={(e) => { if (e.key === "Enter" && ok) onSave(v.trim()); }} onFocus={(e) => e.target.select()} />
    </Modal>
  );
}
