/** 서버 API. 브라우저 로그인 토큰(JWT)은 이 브라우저에 보관한다(저장 실패 시 메모리). */
const TOKEN_KEY = "dds.token";
let memToken: string | null = null;

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) ?? memToken; } catch { return memToken; }
}
export function setToken(t: string | null) {
  memToken = t;
  try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); } catch { /* 메모리만 */ }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

function messageFrom(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string } | undefined;
    if (first?.msg) return first.msg.replace(/^Value error, /, "");
  }
  if (status === 401) return "로그인이 필요합니다.";
  if (status >= 500) return "서버에서 문제가 발생했습니다. 잠시 후 다시 시도하세요.";
  return "요청을 처리하지 못했습니다.";
}

async function request(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<Response> {
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  let res: Response;
  try {
    res = await fetch(path, { method: opts.method ?? (body ? "POST" : "GET"), headers, body });
  } catch {
    throw new ApiError(0, "서버에 연결할 수 없습니다. 네트워크 상태를 확인하세요.");
  }
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = (await res.json()).detail; } catch { /* 본문 없음 */ }
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      setToken(null);
      const back = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.assign(`/login?return_to=${back}`);
    }
    throw new ApiError(res.status, messageFrom(detail, res.status));
  }
  return res;
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const res = await request(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** 인증이 필요한 파일(이미지 등)을 blob URL로 */
export async function blobUrl(path: string): Promise<string> {
  const res = await request(path);
  return URL.createObjectURL(await res.blob());
}

export const get = <T,>(p: string) => api<T>(p);
export const post = <T,>(p: string, body?: unknown) => api<T>(p, { method: "POST", body: body ?? {} });
export const put = <T,>(p: string, body?: unknown) => api<T>(p, { method: "PUT", body: body ?? {} });
export const del = <T,>(p: string) => api<T>(p, { method: "DELETE" });
