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

/** 글자 로고. 그라디언트 사각형 대신 이름과 주홍 점으로 */
export function Wordmark({ size }: { size?: "l" }) {
  return <span className={`wordmark ${size ?? ""}`} aria-label="DDS Conversa"><small aria-hidden="true">DDS</small><span aria-hidden="true">Conversa</span><i aria-hidden="true" /></span>;
}
