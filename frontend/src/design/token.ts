/** 디자인 토큰(design/tokens.css) 값을 읽는다 — 그래프·3D 보기도 화면과 같은 색을 쓰고, 테마가 바뀌면 다시 읽는다 */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
