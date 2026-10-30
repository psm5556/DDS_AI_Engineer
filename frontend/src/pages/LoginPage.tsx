import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { post } from "../api";
import { useAuth } from "../auth";
import { Wordmark } from "../components/AgentGlyph";
import { HeroMesh } from "../components/HeroMesh";
import { PinInput } from "../components/PinInput";
import { shake } from "../motion";

/** 아래 띠: 일하는 흐름. 주홍 = 엔지니어가 하는 일, 청록 = AI가 하는 일(소개 자료와 같은 뜻) */
const FLOW: [string, string, "human" | "ai"][] = [
  ["요청", "말로 시킵니다", "human"], ["AI 계획", "작업 흐름을 그립니다", "ai"], ["승인", "보고 승인합니다", "human"],
  ["실행", "CAD·CAE를 돌립니다", "ai"], ["보고서", "해석해 씁니다", "ai"],
];

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
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => { if (user) nav(safeReturn, { replace: true }); }, [user, nav, safeReturn]);
  const switchMode = (m: typeof mode) => { setMode(m); setPin(""); setPin2(""); setErr(null); };

  const doLogin = async (p = pin) => {
    if (!username || p.length !== 6 || busy) return;
    setErr(null); setBusy(true);
    try {
      await login(username, p);
      nav(safeReturn, { replace: true });
    } catch (e) { setErr((e as Error).message); setPin(""); shake(form.current); } finally { setBusy(false); }
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
    <div className={`login-stage ${username || pin ? "quiet" : ""}`}>
      <header className="login-top">
        <Wordmark size="l" />
        <span className="login-tag">CAE 에이전트</span>
      </header>
      <section className="login-hero" aria-label="DDS Conversa 소개">
        <h1>
          <span className="line"><span>말로 요청하면,</span></span>
          <span className="line"><span>AI가 CAD·CAE를</span></span>
          <span className="line"><span>돌립니다</span></span>
        </h1>
        <HeroMesh />
      </section>
      <div className="login-side">
        <form ref={form} className="login-card stack" onSubmit={(e) => { e.preventDefault(); void (mode === "login" ? doLogin() : doRegister()); }}>
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
            {busy ? (mode === "login" ? "로그인 중…" : "신청 중…") : mode === "login" ? "로그인" : "가입 신청"}
          </button>
          {mode === "login" && <p className="small muted">PIN을 5번 틀리면 10분간 잠깁니다. 잊었으면 관리자에게 초기화를 요청하세요.</p>}
        </form>
      </div>
      <ol className="login-flow" aria-label="일하는 흐름">
        {FLOW.map(([t, d, who], i) => (
          <li key={t} className={who}><span className="n">{String(i + 1).padStart(2, "0")}</span><b>{t}</b><small>{d}</small></li>
        ))}
      </ol>
    </div>
  );
}
