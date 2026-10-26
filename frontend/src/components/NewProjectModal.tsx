import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { post } from "../api";
import { useToast } from "../toast";
import type { Project } from "../types";
import { Modal } from "./Modal";

export function NewProjectModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const create = useMutation({
    mutationFn: () => post<Project>("/api/projects", { name: name.trim(), description: description.trim() }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      toast("프로젝트를 만들었습니다");
      onClose();
      nav(`/projects/${p.id}`);
    },
    onError: (e: Error) => toast(e.message ?? "만들지 못했습니다", true),
  });
  return (
    <Modal title="새 프로젝트" onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className="primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>만들기</button>
    </>}>
      <div className="stack">
        <label className="field"><span className="lbl">이름<span className="req-mark" aria-label="필수" /></span>
          <input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 브래킷 경량화"
            onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) create.mutate(); }} /></label>
        <label className="field"><span className="lbl">설명</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="무엇을 하려는지 한두 줄 (에이전트도 참고합니다)" /></label>
      </div>
    </Modal>
  );
}
