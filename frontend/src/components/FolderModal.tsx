import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderSearch } from "lucide-react";
import { useState } from "react";
import { get, post, put } from "../api";
import { useToast } from "../toast";
import { Modal } from "./Modal";

export interface LocalFolderInfo { path: string; custom: boolean; root: string }

/** 내 PC 작업 폴더 고르기.
 *  - pid가 있으면 그 프로젝트의 폴더를 바꾼다(저장 즉시 반영).
 *  - pid가 없으면(첫 화면, 프로젝트를 만들기 전) 확인만 하고 onPicked로 경로를 넘긴다(null = 기본 위치).
 *  '찾아보기'는 PC Agent가 내 PC에 Windows 폴더 선택 창을 띄운다. */
export function FolderModal({ pid, initial, onPicked, onClose }: {
  pid?: number; initial?: string | null; onPicked?: (path: string | null) => void; onClose: () => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: cur } = useQuery({ queryKey: ["local-folder", pid], enabled: !!pid, retry: false,
    queryFn: () => get<LocalFolderInfo>(`/api/projects/${pid}/local/folder`) });
  const [path, setPath] = useState<string | null>(initial ?? null);
  const value = path ?? (cur?.custom ? cur.path : "");
  const [busy, setBusy] = useState<"" | "pick" | "save">("");
  const [err, setErr] = useState("");

  const browse = async () => {
    setBusy("pick"); setErr("");
    try {
      const r = await post<{ path: string }>("/api/agent/pick-folder", { initial: value || cur?.path || null, title: "작업 폴더 선택" });
      if (r.path) setPath(r.path);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };
  const save = async (p: string | null) => {
    setBusy("save"); setErr("");
    try {
      if (pid) {
        await put<LocalFolderInfo>(`/api/projects/${pid}/local/folder`, { path: p });
        qc.invalidateQueries({ queryKey: ["local-folder", pid] });
        qc.invalidateQueries({ queryKey: ["local-tree", pid] });
        toast(p ? "작업 폴더를 바꿨습니다. 다음 요청부터 이 폴더에서 작업합니다" : "기본 작업 폴더로 되돌렸습니다");
        onClose();
      } else {
        const r = p ? await post<{ path: string }>("/api/agent/check-folder", { path: p }) : null;
        onPicked?.(r ? r.path : null);
        onClose();
      }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };

  return (
    <Modal title="내 PC 작업 폴더" onClose={onClose} footer={<>
      {(cur?.custom || (!pid && initial)) && <button className="ghost" style={{ marginRight: "auto" }} disabled={!!busy} onClick={() => void save(null)}>기본 위치 사용</button>}
      <button onClick={onClose}>취소</button>
      <button className="primary" disabled={!!busy || !value.trim()} onClick={() => void save(value.trim())}>{busy === "save" ? "확인 중…" : "이 폴더 사용"}</button>
    </>}>
      <div className="stack folder-modal">
        {pid && cur && (
          <p className="small muted">지금: <code>{cur.path}</code> {cur.custom ? "(직접 지정)" : "(기본 위치)"}</p>
        )}
        <div className="row folder-input">
          <input type="text" value={value} autoFocus placeholder="예: D:\Work\브래킷_개선" aria-label="작업 폴더 경로"
            onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) void save(value.trim()); }} />
          <button onClick={() => void browse()} disabled={!!busy} title="내 PC에 폴더 선택 창을 띄웁니다">
            <FolderSearch size={14} />{busy === "pick" ? "PC에서 고르는 중…" : "찾아보기…"}</button>
        </div>
        {busy === "pick" && <p className="small muted">PC Agent가 설치된 PC 화면에 폴더 선택 창이 열렸습니다.</p>}
        {err && <p className="small" style={{ color: "var(--err)" }}>{err}</p>}
        <ul className="small muted folder-notes">
          <li>이 폴더에 에이전트 설정 파일과 작업 하위 폴더(inputs·scripts·runs·studies·reports)가 만들어집니다. 기존 파일은 건드리지 않습니다.</li>
          {pid && <li>이미 만든 파일은 옮기지 않습니다. 대화는 이어지고, 다음 요청부터 새 폴더에서 작업합니다.</li>}
          <li>폴더는 내 PC에만 적용됩니다. 함께하는 멤버는 각자 자기 PC에서 정합니다.</li>
        </ul>
      </div>
    </Modal>
  );
}

/** 폴더 경로의 마지막 이름 (표시용) */
export const folderName = (p: string) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

