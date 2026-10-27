// 슬라이드용 그림 만들기: 선 아이콘(색별 PNG)·브래킷 그림(응력 색)·색 막대 — HTML 보고서와 같은 그림
// 프론트엔드에 이미 있는 playwright-core와 설치된 Edge를 쓴다(cd frontend; npm ci)
const { chromium } = await import(new URL("../../../frontend/node_modules/playwright-core/index.mjs", import.meta.url));
import fs from "node:fs";
import path from "node:path";

const OUT = path.join(import.meta.dirname, "assets");
fs.mkdirSync(OUT, { recursive: true });

const ICONS = {
  person: '<circle cx="24" cy="15" r="7"/><path d="M10 41c1.5-8 7-12 14-12s12.5 4 14 12"/>',
  chat: '<path d="M8 11h32v21H22l-8 7v-7H8z"/><path d="M16 21.5h.01M24 21.5h.01M32 21.5h.01" stroke-width="4"/>',
  bot: '<rect x="10" y="15" width="28" height="22" rx="7"/><path d="M24 9v6"/><circle cx="24" cy="7.5" r="2"/><path d="M18.5 25.5h.01M29.5 25.5h.01" stroke-width="5"/><path d="M19.5 31.5h9"/><path d="M6 23v7M42 23v7"/>',
  pc: '<rect x="6" y="9" width="36" height="24" rx="3"/><path d="M17 41h14M24 33v8"/>',
  doc: '<path d="M13 6h15l9 9v27H13z"/><path d="M28 6v9h9"/><path d="M18 24h14M18 30h14M18 36h9"/>',
  plan: '<rect x="11" y="9" width="26" height="33" rx="3"/><path d="M18 9V5.5h12V9"/><path d="M17 20l2.5 2.5 4-4M27 21h4M17 30l2.5 2.5 4-4M27 31h4"/>',
  cube: '<path d="M24 6l16 9v18l-16 9-16-9V15z"/><path d="M8 15l16 9 16-9M24 24v18"/>',
  flask: '<path d="M19 6h10M21 6v12L10 38a3 3 0 0 0 2.7 4h22.6a3 3 0 0 0 2.7-4L27 18V6"/><path d="M14.5 31h19"/>',
  bulb: '<path d="M17 30.5a11 11 0 1 1 14 0c-1.4 1.2-2 2.6-2 4.5H19c0-1.9-.6-3.3-2-4.5z"/><path d="M19 40h10M21.5 44h5"/>',
  trophy: '<path d="M16 7h16v11a8 8 0 0 1-16 0z"/><path d="M16 10H9a7 7 0 0 0 7.5 8M32 10h7a7 7 0 0 1-7.5 8"/><path d="M24 26v7M18 41h12M20 33h8v8h-8z"/>',
  db: '<ellipse cx="24" cy="11" rx="14" ry="5"/><path d="M10 11v26c0 2.8 6.3 5 14 5s14-2.2 14-5V11"/><path d="M10 24c0 2.8 6.3 5 14 5s14-2.2 14-5"/>',
  check: '<path d="M10 25l9 9 19-19"/>',
  shield: '<path d="M24 5l15 5.5v11c0 10-6.5 17.5-15 20.5-8.5-3-15-10.5-15-20.5v-11z"/><path d="M17 24l5 5 9-10"/>',
  hand: '<path d="M17 25V11a3 3 0 0 1 6 0v12M23 22V8a3 3 0 0 1 6 0v14M29 22V11a3 3 0 0 1 6 0v15c0 9-5 16-13 16-6 0-9-4-12-9l-4-7a3 3 0 0 1 5-3l3 4"/>',
  lock: '<rect x="10" y="21" width="28" height="21" rx="4"/><path d="M16 21v-6a8 8 0 0 1 16 0v6"/><path d="M24 29v6"/>',
  people: '<circle cx="17" cy="16" r="6"/><circle cx="33" cy="18" r="5"/><path d="M5 40c1-7 5.5-11 12-11s11 4 12 11"/><path d="M29 30c1.3-.6 2.6-1 4-1 5.5 0 9 3.5 10 9"/>',
  web: '<rect x="5" y="8" width="38" height="32" rx="3"/><path d="M5 16h38"/><path d="M10 12h.01M14 12h.01M18 12h.01" stroke-width="3"/><path d="M12 24h14M12 31h22"/>',
  building: '<path d="M8 42V8h20v34M28 18h12v24"/><path d="M5 42h38"/><path d="M14 15h3M20 15h3M14 22h3M20 22h3M14 29h3M20 29h3M33 25h3M33 32h3"/>',
  car: '<path d="M7 31l3.5-9.5A4 4 0 0 1 14.3 19h19.4a4 4 0 0 1 3.8 2.5L41 31v6H7z"/><path d="M7 31h34"/><circle cx="15" cy="37" r="3.5"/><circle cx="33" cy="37" r="3.5"/>',
  thermo: '<path d="M20 9a4 4 0 0 1 8 0v19.5a8 8 0 1 1-8 0z"/><path d="M24 20v13"/><path d="M33 12h5M33 18h5M33 24h3"/>',
  light: '<path d="M14 16h20l-3 9H17z"/><path d="M24 6v4"/><path d="M17 25l-7 15M24 25v15M31 25l7 15"/>',
  part: '<path d="M8 12h32v9h-9v15H17V21H8z"/><path d="M24 4v8" stroke-dasharray="2 3"/>',
};
const COLORS = { human: "#D9671F", ai: "#0C7882", ink: "#15232D", ok: "#23905A", white: "#FFFFFF", humanL: "#F29A5A", aiL: "#45BCC6", okL: "#4CC584", inkL: "#E7EEF1" };

const browser = await chromium.launch({ channel: "msedge" });
const page = await browser.newPage({ deviceScaleFactor: 1 });
async function render(html, file, w, h) {
  await page.setViewportSize({ width: w, height: h });
  await page.setContent(`<html><body style="margin:0;background:transparent">${html}</body></html>`);
  await page.locator("#a").screenshot({ path: path.join(OUT, file), omitBackground: true });
}
for (const [name, body] of Object.entries(ICONS)) {
  for (const [cn, c] of Object.entries(COLORS)) {
    await render(`<svg id="a" width="256" height="256" viewBox="0 0 48 48" fill="none" stroke="${c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" style="display:block">${body}</svg>`, `${name}_${cn}.png`, 256, 256);
  }
}

// 브래킷 그림: 벽 + 판 + 리브(삼각), 응력 색(벽 쪽 빨강 → 끝 파랑), 끝에 2 kN 하중
const grad = '<defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#E5483B"/><stop offset=".3" stop-color="#F0C43A"/><stop offset=".55" stop-color="#3DBB6A"/><stop offset=".8" stop-color="#19A7C9"/><stop offset="1" stop-color="#2F5BD3"/></linearGradient></defs>';
const bracket = (t, rib) => `<svg id="a" width="960" height="510" viewBox="0 0 320 170" style="display:block">${grad}
  <rect x="8" y="14" width="22" height="140" fill="#D7E1E5"/>
  <path d="M8 24l22-10M8 44l22-10M8 64l22-10M8 84l22-10M8 104l22-10M8 124l22-10M8 144l22-10" stroke="#586A75" stroke-width="1.2" opacity=".6"/>
  <rect x="30" y="40" width="250" height="${t}" fill="url(#g)"/>
  <polygon points="30,${40 + t} 30,${40 + t + rib} 250,${40 + t}" fill="url(#g)" opacity=".85"/>
  <path d="M280 2v32" stroke="#15232D" stroke-width="3"/><path d="M272 24l8 10 8-10" fill="none" stroke="#15232D" stroke-width="3" stroke-linejoin="round"/>
  <text x="290" y="20" font-size="14" font-weight="700" fill="#15232D" font-family="Malgun Gothic, sans-serif">2 kN</text></svg>`;
await render(bracket(30, 75), "bracket_before.png", 960, 510);
await render(bracket(18, 79), "bracket_after.png", 960, 510);
await render('<div id="a" style="width:600px;height:40px;border-radius:20px;background:linear-gradient(90deg,#2F5BD3,#19A7C9,#3DBB6A,#F0C43A,#E5483B)"></div>', "scale.png", 620, 60);
await browser.close();
console.log("assets:", fs.readdirSync(OUT).length);
