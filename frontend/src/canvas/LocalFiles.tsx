import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, Eye, File, Folder, FolderOpen, HardDrive, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api, get } from "../api";
import { when } from "../format";
import { useToast } from "../toast";
import { FolderModal } from "../components/FolderModal";
import type { AgentStatus, LocalTree } from "../types";

const VIEWABLE = /\.(odb|d3plot|op2|f06|rst|rth|h3d|sim|fem|prt|stp|step|igs|iges|stl|vtu|vtk|cae|inp|k|key|dyn|png|jpg|csv|json|txt|log)$/i;
// 에이전트가 쓰는 내부 파일(설정·지침) — 기본으로 숨긴다
const INTERNAL = /^([.]|(AGENTS|CLAUDE|GEMINI)[.]md$|project[.]json$)/;
const size = (n: number | null) => (n == null ? "" : n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

/** 서버 설정 web_viewer_url 템플릿을 채운다: {path}=그 PC의 절대경로, {rel}, {host}, {project_id} */
function webViewerUrl(tpl: string, root: string, rel: string, host: string, pid: number): string {
  const abs = `${root}\\${rel.replace(/\//g, "\\")}`;
  return tpl.replace(/\{path\}/g, encodeURIComponent(abs)).replace(/\{rel\}/g, encodeURIComponent(rel))
    .replace(/\{host\}/g, encodeURIComponent(host)).replace(/\{project_id\}/g, String(pid));
}

/** 내 PC 작업 폴더 열기(탐색기) / CAE 뷰어로 열기 — PC Agent 경유 */
export function useOpenLocal(pid: number) {
  const toast = useToast();
  return useMutation({
    mutationFn: (v: { path: string; viewer?: boolean }) =>
      api<{ opened: string; with?: string }>(`/api/projects/${pid}/local/open`, { method: "POST", body: { path: v.path, with_viewer: !!v.viewer } }),
    onSuccess: (r) => toast(r.with ? `뷰어로 열었습니다: ${r.opened}` : `내 PC에서 열었습니다: ${r.opened}`),
    onError: (e: Error) => toast(e.message ?? "열지 못했습니다", true),
  });
}

/** 케이스의 run_dir(그 PC의 절대 경로)에서 프로젝트 폴더 기준 상대 경로를 뽑는다 */
export function relFromRunDir(runDir: string | null): string | null {
  if (!runDir) return null;
  const parts = runDir.replace(/\\/g, "/").split("/");
  const i = parts.lastIndexOf("studies");
  return i >= 0 ? parts.slice(i).join("/") : null;
}

/** 캔버스 '파일' 탭: 내 PC의 프로젝트 작업 폴더 (규칙: inputs·scripts·runs·studies/S…/cases/C…·live·reports) */
export function LocalFiles({ pid }: { pid: number }) {
  const [path, setPath] = useState("");
  const [hidden, setHidden] = useState(false);
  const [picking, setPicking] = useState(false);
  const open = useOpenLocal(pid);
  const { data, error, refetch, isFetching } = useQuery({
    queryKey: ["local-tree", pid, path], queryFn: () => get<LocalTree>(`/api/projects/${pid}/local/tree?path=${encodeURIComponent(path)}`),
    retry: false,
  });
  const { data: cfg } = useQuery({ queryKey: ["config"], queryFn: () => get<{ web_viewer_url: string }>("/api/config/public"), staleTime: 600_000 });
  const { data: agent } = useQuery({ queryKey: ["agent", "status"], queryFn: () => get<AgentStatus>("/api/agent/status") });
  const crumbs = path ? path.split("/") : [];
  const entries = (data?.entries ?? []).filter((e) => hidden || path || !INTERNAL.test(e.name));
  const nHidden = (data?.entries.length ?? 0) - entries.length;
  if (error) {
    return <div className="empty-hero"><HardDrive size={28} /><b>내 PC 작업 폴더를 볼 수 없습니다</b><p>{(error as Error).message}</p></div>;
  }
  return (
    <div className="local-files">
      <div className="row local-head">
        <nav className="local-crumb" aria-label="폴더 경로">
          <button className="link-btn" onClick={() => setPath("")}>작업 폴더</button>
          {crumbs.map((c, i) => (
            <span key={i}><ChevronRight size={12} /><button className="link-btn" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>
          ))}
        </nav>
        <span className="grow" />
        <button className="icon-btn" title="새로 고침" aria-label="새로 고침" onClick={() => refetch()}><RefreshCw size={14} className={isFetching ? "spin" : ""} /></button>
        <button className="small" onClick={() => open.mutate({ path })}><FolderOpen size={13} />탐색기로 열기</button>
      </div>
      {data && (
        <div className="local-rootbar">
          <span className="small muted local-root" title={data.root}>{data.root}{path ? `\\${path.replace(/\//g, "\\")}` : ""}</span>
          <span className={`local-kind ${data.custom ? "custom" : ""}`}>{data.custom ? "직접 지정" : "기본 위치"}</span>
          <button className="link-btn small" onClick={() => setPicking(true)}>폴더 바꾸기</button>
        </div>
      )}
      {picking && <FolderModal pid={pid} onClose={() => { setPicking(false); setPath(""); }} />}
      <div className="table-wrap"><table className="local-table">
        <tbody>
          {entries.map((e) => (
            <tr key={e.path} className={e.dir ? "clickable" : ""} onClick={() => e.dir && setPath(e.path)}>
              <td>{e.dir ? <Folder size={14} className="dir" /> : <File size={14} />} {e.name}</td>
              <td className="r small muted">{size(e.size)}</td>
              <td className="small muted">{when(e.modified)}</td>
              <td className="r" onClick={(ev) => ev.stopPropagation()}>
                {!e.dir && VIEWABLE.test(e.name) && cfg?.web_viewer_url && data && (
                  <button className="icon-btn" title="사내 웹 CAE 뷰어로 열기 (새 창)" aria-label={`${e.name} 웹 뷰어로 열기`}
                    onClick={() => window.open(webViewerUrl(cfg.web_viewer_url, data.root, e.path, agent?.info?.hostname ?? "", pid), "_blank", "noopener")}>
                    <ExternalLink size={14} /></button>
                )}
                {!e.dir && VIEWABLE.test(e.name) && (
                  <button className="icon-btn" title="CAE 뷰어로 열기 (설정의 [viewers], 없으면 기본 프로그램)" aria-label={`${e.name} 뷰어로 열기`}
                    onClick={() => open.mutate({ path: e.path, viewer: true })}><Eye size={14} /></button>
                )}
                <button className="icon-btn" title="탐색기에서 보기" aria-label={`${e.name} 탐색기에서 보기`} onClick={() => open.mutate({ path: e.path })}>
                  <FolderOpen size={14} /></button>
              </td>
            </tr>
          ))}
          {data && !entries.length && <tr><td colSpan={4} className="small muted">비어 있습니다</td></tr>}
        </tbody>
      </table></div>
      {(nHidden > 0 || hidden) && !path && (
        <button className="link-btn small" style={{ marginTop: 8 }} onClick={() => setHidden(!hidden)}>
          {hidden ? "에이전트 설정 파일 숨기기" : `에이전트 설정 파일 ${nHidden}개 보기`}</button>
      )}
    </div>
  );
}
