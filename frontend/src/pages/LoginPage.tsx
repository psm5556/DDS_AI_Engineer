import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { post } from "../api";
import { useAuth } from "../auth";
import { PinInput } from "../components/PinInput";

/** 로그인(아이디 + PIN 6자리) · 가입 신청 (첫 가입자는 관리자, 이후 가입은 관리자 승인 후 사용) */
export default function LoginPage() {
  const [params] = useSearchParams();
  const ret = params.get("return_to") || "/";
  const safeReturn = ret.startsWith("/") && !ret.startsWith("//") ? ret : "/";
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pin, setPin] = useState("");
  const [pin2, setPin2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (user) nav(safeReturn, { replace: true }); }, [user, nav, safeReturn]);
  const switchMode = (m: typeof mode) => { setMode(m); setPin(""); setPin2(""); setErr(null); };

  const doLogin = async (p = pin) => {
    if (!username || p.length !== 6 || busy) return;
    setErr(null); setBusy(true);
    try {
      await login(username, p);
      nav(safeReturn, { replace: true });
    } catch (e) { setErr((e as Error).message); setPin(""); } finally { setBusy(false); }
  };

  const doRegister = async () => {
    if (pin !== pin2) { setErr("PIN 확인이 일치하지 않습니다"); return; }
    setErr(null); setInfo(null); setBusy(true);
    try {
      const u = await post<{ is_active: boolean }>("/api/auth/register", { username, pin, display_name: displayName || username });
      if (!u.is_active) { setInfo("가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."); switchMode("login"); return; }
      await login(username, pin);
      nav(safeReturn, { replace: true });
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="login-wrap">
      <div className="login-box">
        <div className="login-brand">
          <div className="logo">AI</div>
          <div>
            <h1>DDS AI Engineer</h1>
            <p>말로 요청하면 CAD·CAE 자동화부터 예측 모델까지 에이전트가 함께 합니다</p>
          </div>
        </div>
        <form className="login-card stack" onSubmit={(e) => { e.preventDefault(); void (mode === "login" ? doLogin() : doRegister()); }}>
          <span className="seg" style={{ alignSelf: "flex-start" }}>
            <button type="button" className={mode === "login" ? "on" : ""} onClick={() => switchMode("login")}>로그인</button>
            <button type="button" className={mode === "register" ? "on" : ""} onClick={() => switchMode("register")}>가입 신청</button>
          </span>
          {err && <div className="notice err">{err}</div>}
          {info && <div className="notice ok">{info}</div>}
          <label className="field"><span className="lbl">아이디</span>
            <input type="text" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
          {mode === "register" && (
            <label className="field"><span className="lbl">이름</span>
              <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="화면에 보일 이름" /></label>
          )}
          <div className="field"><span className="lbl">PIN (숫자 6자리)</span>
            <PinInput value={pin} onChange={setPin} disabled={busy} onComplete={(v) => { if (mode === "login") void doLogin(v); }} /></div>
          {mode === "register" && (
            <div className="field"><span className="lbl">PIN 확인</span><PinInput value={pin2} onChange={setPin2} label="PIN 확인" disabled={busy} /></div>
          )}
          <button type="submit" className="primary big" disabled={busy || !username || pin.length !== 6 || (mode === "register" && pin2.length !== 6)}>
            {mode === "login" ? "로그인" : "가입 신청"}
          </button>
          {mode === "login" && <p className="small muted" style={{ textAlign: "center" }}>PIN을 5번 틀리면 10분간 잠깁니다. 잊었으면 관리자에게 초기화를 요청하세요.</p>}
        </form>
      </div>
    </div>
  );
}
