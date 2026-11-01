import { useQuery } from "@tanstack/react-query";
import { get } from "../api";
import type { Project, WorkBrief } from "../types";

const NONE: never[] = [];

/** 오른쪽 탭과 기록이 함께 쓰는 목록: 이 Task의 것만 읽는다. 새 Task 화면(wid 없음)은 아직 아무것도 없다.
 *  쿼리 키는 [종류, pid, wid]다. 실시간 소식은 [종류, pid]로 무효화하므로 앞부분만 맞으면 함께 다시 읽는다. */
export function useWorkList<T>(kind: "flows" | "artifacts" | "studies" | "models", pid: number, wid: number | null): T[] | undefined {
  const q = useQuery({
    queryKey: [kind, pid, wid], enabled: wid != null,
    queryFn: () => get<T[]>(`/api/projects/${pid}/${kind}?work_id=${wid}`),
  });
  return wid == null ? (NONE as T[]) : q.data;
}

/** 프로젝트 줄을 누르면 열 Task: 내 가장 최근 Task → 최근 Task → 없으면 새 Task 화면. works는 최신이 위다 */
export function pickWork(works: WorkBrief[] | undefined): WorkBrief | null {
  if (!works?.length) return null;
  return works.find((w) => w.mine) ?? works[0];
}

export function statusOf(w: Pick<WorkBrief, "status">): "run" | "review" | "failed" | null {
  const s = w.status;
  return s === "in_progress" || s === "approved" ? "run" : s === "review" ? "review" : s === "failed" ? "failed" : null;
}

/** 지금 주소가 보는 Task. 다른 Task 소식인지 가릴 때 쓴다 */
export function currentWorkId(pathname = location.pathname): number | null {
  return Number(pathname.match(/^\/projects\/\d+\/w\/(\d+)/)?.[1]) || null;
}

export function findWork(projects: Project[] | undefined, workId: number): { project: Project; work: WorkBrief } | null {
  for (const project of projects ?? []) {
    const work = project.works?.find((w) => w.id === workId);
    if (work) return { project, work };
  }
  return null;
}
