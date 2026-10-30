// UI 점검(스모크): 주요 화면을 실제 브라우저(설치된 Edge)로 열어 스크린샷을 남기고, 화면 오류(콘솔 error·예외)가 있으면 실패한다.
// 화면을 고친 뒤 반드시 돌린다. 서버(+PC Agent)가 떠 있어야 한다: .venv\Scripts\python scripts\dev_live.py --agent demo
//
//   cd frontend
//   npm run smoke                        # 화면 둘러보기 (로그인 → 첫 화면 → 툴 → 프로젝트 탭들 → Simulator → 설정 → 관리)
//   npm run smoke -- --demo              # + 데모 에이전트로 전체 과정(CAD→보고서)을 실제로 돌려 승인·실행·완료까지 확인 (3~5분)
//   npm run smoke -- --reduced           # '움직임 줄이기' 설정으로 점검(애니메이션 0개여야 통과)
//   환경변수: DDS_URL(기본 http://127.0.0.1:7014) · DDS_USER(admin) · DDS_PIN(123456) · DDS_SHOTS(스크린샷 폴더, 기본 e2e/shots) · DDS_THEME(light|dark)
//   Edge가 없으면 DDS_BROWSER=chrome
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.DDS_URL ?? "http://127.0.0.1:7014";
const USER = process.env.DDS_USER ?? "admin";
const PIN = process.env.DDS_PIN ?? "123456";
const OUT = process.env.DDS_SHOTS ?? path.join(import.meta.dirname, "shots");
const DEMO = process.argv.includes("--demo");
const REDUCED = process.argv.includes("--reduced"); // '움직임 줄이기'로 점검: 도는 애니메이션이 하나도 없어야 한다
const THEME = process.env.DDS_THEME === "dark" ? "dark" : "light";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: process.env.DDS_BROWSER === "chrome" ? "chrome" : "msedge", headless: !process.argv.includes("--headed") });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "ko-KR", reducedMotion: REDUCED ? "reduce" : "no-preference" });
await ctx.addInitScript((t) => { try { localStorage.setItem("dds.theme", t); } catch { /* 무시 */ } }, THEME);
const page = await ctx.newPage();
const errors = [];
// 404(아직 없는 데이터)·409(PC Agent 미연결 등 안내용 응답)는 화면 오류가 아니다
page.on("console", (m) => { if (m.type() === "error" && !/\b(404|409)\b/.test(m.text())) errors.push(`[console] ${m.text()}`); });
page.on("pageerror", (e) => errors.push(`[page] ${e}`));
let n = 0;
const shot = async (name) => {
  await page.waitForTimeout(600);
  const f = path.join(OUT, `${String(++n).padStart(2, "0")}-${name}.png`);
  const running = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length);
  await page.screenshot({ path: f, animations: "disabled" });
  console.log("  shot", path.basename(f), `(도는 애니메이션 ${running})`);
  if (REDUCED && running) errors.push(`[reduced] ${name}: 움직임 줄이기인데 애니메이션 ${running}개가 돈다`);
};
const step = async (label, fn) => {
  try { await fn(); } catch (e) { errors.push(`[step] ${label}: ${String(e).split("\n")[0]}`); console.log("  ! 실패:", label); }
};

console.log(`UI 점검: ${BASE}${DEMO ? " (데모 전체 과정 포함)" : ""}`);
await page.goto(BASE + "/login");
await page.getByLabel("아이디").fill(USER);
await page.getByLabel("PIN 1번째 자리").click();
await page.keyboard.type(PIN);
await page.waitForURL(BASE + "/", { timeout: 15000 });
await page.waitForTimeout(1500);
await shot("home");

if (DEMO) {
  await step("데모 전체 과정", async () => {
    await page.getByTitle("사용할 AI 에이전트").click();
    await page.getByRole("option", { name: /데모/ }).click();
    await page.locator(".starter").first().click();
    const input = page.getByLabel("에이전트에게 요청");
    await page.waitForFunction((el) => el.value.length > 0, await input.elementHandle());
    await input.press("Enter");
    await page.waitForURL(/\/projects\/\d+\/c\/\d+/, { timeout: 30000 });
    await page.getByRole("button", { name: /승인하고 실행/ }).first().waitFor({ timeout: 90000 });
    await shot("demo-review");
    await page.getByRole("button", { name: /승인하고 실행/ }).first().click();
    const allow = page.getByRole("button", { name: "허용" });
    await allow.waitFor({ timeout: 90000 });
    await shot("demo-permission");
    await allow.click();
    await page.getByText("전체 과정을 마쳤습니다").waitFor({ timeout: 420000 });
    await shot("demo-done");
  });
}

await step("CAD·CAE 툴", async () => {
  await page.goto(BASE + "/tools");
  await page.waitForTimeout(2500);
  await shot("tools");
  await page.locator(".tool-app").first().click();
  await shot("tools-drawer");
  await page.keyboard.press("Escape");
});

await step("프로젝트", async () => {
  await page.goto(BASE + "/");
  const first = page.locator(".recent-row").first();
  await first.waitFor({ timeout: 8000 }).catch(() => {});
  if (!(await first.count())) { console.log("  (프로젝트가 없어 건너뜀 — --demo로 하나 만드세요)"); return; }
  await first.click();
  await page.waitForURL(/\/projects\//, { timeout: 15000 });
  await page.waitForTimeout(2000);
  await shot("workspace");
  for (const t of ["작업 흐름", "보고서", "데이터", "파일"]) {
    await step(`탭 ${t}`, async () => { await page.getByRole("tab", { name: t }).first().click({ timeout: 5000 }); await shot(`tab-${t}`); });
  }
  await step("대화 전환 메뉴", async () => { await page.locator(".ws-conv .ws-crumb").click(); await shot("conv-menu"); await page.keyboard.press("Escape"); });
  await step("Simulator 탭", async () => {
    await page.getByRole("tab", { name: "Simulator" }).click();
    await page.waitForTimeout(3000);
    await shot("tab-simulator");
    // 탭을 열어도 대화가 그대로 있어야 한다
    if (!(await page.locator(".canvas").count()) || !(await page.locator(".ws-conv").count())) throw new Error("Simulator 탭에서 대화나 패널이 사라졌습니다");
    const toggle = page.locator(".sim-ver-toggle");
    if (await toggle.count()) { await toggle.click(); await page.waitForTimeout(300); await shot("tab-simulator-versions"); }
  });
});

await step("Simulator 목록", async () => {
  await page.goto(BASE + "/simulators");
  await page.waitForTimeout(1200);
  await shot("simulators");
  const row = page.locator(".sim-row a.recent-row").first();
  if (!(await row.count())) { console.log("  (배포된 Simulator 없음 — 건너뜀)"); return; }
  await row.click();
  await page.waitForURL(/\/(simulator|s)\//);
  await page.waitForTimeout(3500);
  await shot("simulator");
});

await step("설정", async () => { await page.goto(BASE + "/settings"); await page.waitForTimeout(1500); await shot("settings"); });
await step("관리", async () => {
  await page.goto(BASE + "/admin?tab=releases");
  await page.waitForTimeout(1500);
  await shot("admin-releases");
});

await browser.close();
console.log(`\n스크린샷: ${OUT}`);
if (errors.length) {
  console.log(`\nFAIL — 화면 오류 ${errors.length}건`);
  for (const e of errors) console.log("  " + e);
  process.exit(1);
}
console.log("\nPASS — 화면 오류 없음");
