import { useCallback, useState } from "react";

/** 밝은/어두운 화면. 처음에는 OS 설정을 따르고, 한 번 바꾸면 이 브라우저에 기억한다 (저장 실패해도 동작). */
export type Theme = "light" | "dark";
const KEY = "dds.theme";

export function initialTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
  } catch { /* 저장소를 못 쓰면 OS 설정 */ }
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme | undefined) ?? initialTheme());
  const toggle = useCallback(() => {
    setTheme((cur) => {
      const next: Theme = cur === "dark" ? "light" : "dark";
      applyTheme(next);
      try { localStorage.setItem(KEY, next); } catch { /* 기억 못 해도 지금 화면은 바뀜 */ }
      return next;
    });
  }, []);
  return { theme, toggle };
}
