/** 에이전트 기호: 고리처럼 놓인 점 8개. 이 앱의 얼굴로, 상태마다 다르게 움직인다.
 *  idle 천천히 숨쉼 · thinking 빛이 돈다 · working 고리 전체가 돈다 · done 멈춘 채 가운데 점.
 *  CSS만으로 움직이므로 '움직임 줄이기'면 그대로 멈춘다. */
export type GlyphState = "idle" | "thinking" | "working" | "done";

export function AgentGlyph({ state = "idle", size = 28, label }: { state?: GlyphState; size?: number; label?: string }) {
  const dots = Array.from({ length: 8 }, (_, i) => {
    const a = (i / 8) * Math.PI * 2 - Math.PI / 2;
    return [12 + Math.cos(a) * 9, 12 + Math.sin(a) * 9];
  });
  return (
    <svg className={`agent-glyph ${state}`} viewBox="0 0 24 24" width={size} height={size} role={label ? "img" : undefined}
      aria-label={label} aria-hidden={label ? undefined : true}>
      <g>{dots.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={1.7} />)}</g>
      {state === "done" && <circle className="core" cx={12} cy={12} r={2.6} />}
    </svg>
  );
}

/** DDS 로고: D · D · S 세 글자가 큐브의 세 면이 된다(위 → 왼쪽 → 오른쪽). 삼성 블루. 파일은 public/brand/ */
const LOGO_D = "M3 3H42Q61 3 61 22V42Q61 61 42 61H3ZM21 21H38Q43 21 43 26V38Q43 43 38 43H21Z";
export function DdsLogo() {
  return (
    <svg className="dds-logo" viewBox="0 0 64 64" aria-hidden="true">
      <path className="f-top" fillRule="evenodd" transform="matrix(.378 -.219 .378 .219 7.8 18)" d={LOGO_D} />
      <path className="f-left" fillRule="evenodd" transform="matrix(.378 .219 0 .4375 7.8 18)" d={LOGO_D} />
      <path className="f-right" transform="matrix(.378 -.219 0 .4375 32 32)" d="M3 3H61V17H20V25.5H61V61H3V47H44V38.5H3Z" />
    </svg>
  );
}

/** 로고 + 앱 이름 */
export function Wordmark({ size }: { size?: "l" }) {
  return <span className={`wordmark ${size ?? ""}`} aria-label="DDS Conversa"><DdsLogo /><b aria-hidden="true">DDS</b><span aria-hidden="true">Conversa</span></span>;
}
