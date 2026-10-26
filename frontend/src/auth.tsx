import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { get, getToken, post, setToken } from "./api";
import type { User } from "./types";

interface AuthCtx {
  user: User | null; loading: boolean;
  login: (username: string, pin: string) => Promise<void>; logout: () => void; refresh: () => Promise<void>;
}
const Ctx = createContext<AuthCtx>({ user: null, loading: true, login: async () => {}, logout: () => {}, refresh: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try { setUser(await get<User>("/api/auth/me")); } catch { setUser(null); }
    setLoading(false);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const login = async (username: string, pin: string) => {
    const r = await post<{ access_token: string; user: User }>("/api/auth/login", { username, pin });
    setToken(r.access_token);
    setUser(r.user);
  };
  const logout = () => { setToken(null); setUser(null); };
  return <Ctx.Provider value={{ user, loading, login, logout, refresh }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="busy" style={{ padding: 40 }}><span className="spinner" /> 불러오는 중</div>;
  if (!user) return <Navigate to={`/login?return_to=${encodeURIComponent(loc.pathname)}`} replace />;
  return <>{children}</>;
}
