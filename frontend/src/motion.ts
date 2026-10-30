import { useRef } from "react";
import { token } from "./design/token";

/** 움직임 도구(의존성 없음). 규칙: 기본 CSS가 최종 상태이고, 움직임은 그 위에 얹는다 — '움직임 줄이기'면 그대로 정지 화면이 된다.
 *  CSS로 못 하는 것(자바스크립트 스크롤·Web Animations)만 여기를 거친다. */
export function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** scrollIntoView·scrollTo에 넘길 값: 줄이기 설정이면 즉시 이동 */
export function scrollMode(): ScrollBehavior {
  return reducedMotion() ? "auto" : "smooth";
}

function ms(name: string, fallback: number): number {
  const v = parseFloat(token(name));
  return Number.isFinite(v) ? v : fallback;
}

/** 값이 바뀐 곳을 잠깐 강조한다. 클래스와 달리 같은 요소를 다시 눌러도 다시 재생된다. */
export function flash(el: Element | null | undefined, name = "--dur-flash") {
  if (!el || reducedMotion() || !el.animate) return;
  const ring = token("--brand-200");
  el.animate(
    [{ boxShadow: `0 0 0 4px ${ring}` }, { boxShadow: "0 0 0 0 transparent" }],
    { duration: ms(name, 800), easing: token("--ease-out") || "ease-out" },
  );
}

/** 짧게 흔들기: PIN 오류처럼 입력이 거절됐을 때 */
export function shake(el: Element | null | undefined) {
  if (!el || reducedMotion() || !el.animate) return;
  el.animate(
    [0, -5, 5, -3, 3, 0].map((x) => ({ transform: `translateX(${x}px)` })),
    { duration: ms("--dur-slow", 240), easing: "ease-out" },
  );
}

/** 처음 본 키만 '새것'으로 돌려준다. 자료가 다 오기 전(ready=false)에 본 키와 첫 묶음은 옛것이다.
 *  한 번 정한 값은 바뀌지 않아 다시 그려도 등장 움직임이 되풀이되지 않는다. */
export function useFreshKeys(ready: boolean): (key: string) => boolean {
  const seen = useRef(new Map<string, boolean>());
  const baseline = useRef(false);
  const settled = ready && baseline.current;
  if (ready) baseline.current = true;
  return (key) => {
    const known = seen.current.get(key);
    if (known !== undefined) return known;
    seen.current.set(key, settled);
    return settled;
  };
}
