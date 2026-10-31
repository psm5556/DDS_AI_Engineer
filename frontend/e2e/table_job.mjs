// 표를 만드는 동안의 입력창과, 화면을 떠난 뒤의 진행 알림을 재는 점검. 서버만 떠 있으면 된다(AI·PC Agent 없음).
//   node e2e/table_job.mjs       환경변수: DDS_URL · DDS_USER · DDS_PIN · DDS_SHOTS
// 마지막 줄이 PASS면 통과. 클립보드는 건드리지 않고 합성 붙여넣기 이벤트를 쓴다.
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.DDS_URL ?? "http://127.0.0.1:7014";
const OUT = process.env.DDS_SHOTS ?? path.join(import.meta.dirname, "shots");
const MAKING = "표로 Simulator를 만드는 중입니다. 끝나면 이어서 요청할 수 있습니다.";
const QUEUED = "지금 표를 만드는 중이라 붙여 넣은 표는 아직 받지 않았습니다. 끝나면 바로 물어보겠습니다.";
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ channel: process.env.DDS_BROWSER === "chrome" ? "chrome" : "msedge", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "ko-KR" });
const page = await ctx.newPage();
const fails = [];
const check = (ok, label) => { console.log(`  ${ok ? "ok" : "실패"} ${label}`); if (!ok) fails.push(label); };
const shot = (name) => page.screenshot({ path: path.join(OUT, `job-${name}.png`), animations: "disabled" });

await page.goto(BASE + "/login");
await page.getByLabel("아이디").fill(process.env.DDS_USER ?? "admin");
await page.getByLabel("PIN 1번째 자리").click();
await page.keyboard.type(process.env.DDS_PIN ?? "123456");
await page.waitForURL(BASE + "/", { timeout: 15000 });
await page.waitForTimeout(800);
const token = await page.evaluate(() => localStorage.getItem("dds.token") ?? "");
const api = async (method, url, body) => {
  const r = await page.request.fetch(BASE + url, { method, data: body, headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${await r.text()}`);
  return r.json();
};
const table = (names, n, seed = 7) => {
  let s = seed;
  const R = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const rows = [];
  for (let i = 0; i < n; i++) {
    const a = 1 + R() * 9, b = 10 + R() * 30, c = 1 + R() * 3;
    rows.push([a.toFixed(4), b.toFixed(4), c.toFixed(4), (1000 / (a * b) + c).toFixed(5)].join("\t"));
  }
  return `${names.join("\t")}\n${rows.join("\n")}\n`;
};
const input = () => page.getByLabel("에이전트에게 요청");
const paste = (text) => input().evaluate((el, t) => {
  const data = new DataTransfer();
  data.setData("text/plain", t);
  el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}, text);
const make = () => page.getByRole("button", { name: "Simulator 만들기" });
const untilDone = async (pid) => {
  for (let i = 0; i < 300; i++) {
    const jobs = await api("GET", "/api/table-jobs");
    if (!jobs.some((j) => j.project_id === pid && j.status === "running")) return;
    await page.waitForTimeout(1000);
  }
  throw new Error("표 작업이 끝나지 않습니다");
};

try {
  // 만드는 중의 입력창: 보내지 않고, 둘째 표를 잃지 않는다
  const p1 = (await api("POST", "/api/projects", { name: "표 두 개" })).id;
  await page.goto(`${BASE}/projects/${p1}`);
  await input().waitFor();
  await page.waitForTimeout(800);
  await paste(table(["두께", "폭", "높이", "응력"], 150, 11));
  await make().click();
  await page.locator(".ti-run").waitFor({ timeout: 8000 });
  await input().focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(400);
  check(await page.getByText(MAKING).count() > 0, "만드는 중에 보내면 이유를 말한다");
  check((await api("GET", `/api/projects/${p1}/attachments`)).length === 0, "표가 AI 첨부로 올라가지 않음");
  check((await api("GET", `/api/projects/${p1}/sessions`)).length === 0, "대화가 시작되지 않음");
  await paste(table(["길이", "각도", "두께2", "변위"], 20, 77));
  await page.waitForTimeout(300);
  check(await page.locator(".composer-ask", { hasText: QUEUED }).count() === 1, "둘째 표는 받지 않았다고 말한다");
  await shot("queued");
  await untilDone(p1);
  await page.getByRole("button", { name: "Simulator 만들기" }).waitFor({ timeout: 15000 });
  check(await page.locator(".ti-q-head").innerText().then((t) => t.includes("20행")), "끝나면 둘째 표를 바로 묻는다");
  check(await page.locator(".ti-chip", { hasText: "변위" }).count() === 1, "둘째 표의 열이 보임");
  check(await page.locator(".composer-ask", { hasText: QUEUED }).count() === 0, "기다린다는 안내가 사라짐");
  await page.getByRole("button", { name: "취소" }).click();

  // 학습 중 새로고침: 서버가 끝내고, 돌아온 화면이 알리고, 열 수 있다
  const p2 = (await api("POST", "/api/projects", { name: "새로고침" })).id;
  await page.goto(`${BASE}/projects/${p2}`);
  await input().waitFor();
  await page.waitForTimeout(800);
  await paste(table(["두께", "폭", "높이", "수명"], 200, 31));
  await make().click();
  await page.locator(".ti-run").waitFor({ timeout: 8000 });
  await page.waitForTimeout(1500);
  await page.reload();
  await input().waitFor();
  await page.waitForTimeout(1200);
  const jobs = await api("GET", "/api/table-jobs");
  const job = jobs.find((j) => j.project_id === p2);
  check(!!job, "새로고침한 뒤에도 서버에 작업이 있음");
  if (job?.status === "running") {
    check(await page.locator(".table-job.running", { hasText: "수명 예측" }).count() === 1, "돌아온 화면에 '만드는 중' 표시");
    await input().focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    check(await page.getByText(MAKING).count() > 0, "새로고침한 뒤에도 보내기를 막음");
    await shot("after-reload-running");
  }
  await untilDone(p2);
  await page.locator(".table-job.done", { hasText: "수명 예측" }).waitFor({ timeout: 35000 });
  const models = await api("GET", `/api/projects/${p2}/models`);
  check(models.length === 1 && models[0].deployed, "새로고침했어도 배포까지 끝남");
  await shot("after-reload-done");
  await page.locator(".table-job.done", { hasText: "수명 예측" }).getByRole("button", { name: "열기" }).click();
  await page.locator(".sim-var").first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(1500);
  check(await page.locator(".table-job", { hasText: "수명 예측" }).count() === 0, "연 뒤에는 알림이 사라짐");
  await page.reload();
  await input().waitFor();
  await page.waitForTimeout(1000);
  check(await page.locator(".table-job", { hasText: "수명 예측" }).count() === 0, "확인한 작업은 다시 알리지 않음");

  // 첫 화면: 만드는 중에 붙여 넣은 표가 프로젝트 화면으로 넘어가도 사라지지 않는다
  await page.goto(`${BASE}/`);
  await input().waitFor();
  await page.waitForTimeout(800);
  await paste(table(["두께", "폭", "높이", "처짐"], 60, 41));
  await make().click();
  await page.locator(".ti-run").waitFor({ timeout: 8000 });
  await paste(table(["길이", "각도", "두께3", "질량"], 20, 43));
  await page.getByRole("button", { name: "Simulator 만들기" }).waitFor({ timeout: 120000 });
  await page.waitForURL(/\/projects\/\d+/, { timeout: 5000 });
  check(await page.locator(".ti-chip", { hasText: "질량" }).count() === 1, "첫 화면에서 기다린 표를 프로젝트 화면에서 묻는다");
  await page.getByRole("button", { name: "취소" }).click();
} catch (e) {
  fails.push(String(e).split("\n")[0]);
  await shot("error").catch(() => {});
}
await browser.close();
console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" | ")}` : "PASS");
process.exit(fails.length ? 1 : 0);
