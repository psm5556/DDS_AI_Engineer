import { Marked } from "marked";
import { useMemo } from "react";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// 에이전트·아티팩트가 쓴 마크다운을 그린다. 원문 HTML은 글자로만 보이게(스크립트 주입 방지), 링크는 새 창.
const marked = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html(token) { return esc(typeof token === "string" ? token : token.text); },
    link(token) {
      const href = /^(https?:|\/)/.test(token.href) ? token.href : "#";
      return `<a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(token.text)}</a>`;
    },
    image(token) { return esc(`[이미지: ${token.text || token.href}]`); },
  },
});

export function Markdown({ text, className }: { text: string; className?: string }) {
  const html = useMemo(() => marked.parse(text ?? "", { async: false }) as string, [text]);
  return <div className={`md ${className ?? ""}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
