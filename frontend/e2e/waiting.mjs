// 기다리는 화면 점검: scripts/e2e_sessions.py가 실제 서버·Agent·가짜 CLI를 띄우고 부른다. 창 없이(headless) 돌아 포커스를 뺏지 않는다.
// 표준 입출력으로 파이썬과 맞물린다: "STEP 이름"을 내보내면 파이썬이 그 일을 하고 한 줄을 돌려준다(연결 끊기 등).
//   환경변수: DDS_URL · DDS_PID(프로젝트) · DDS_SHOTS(스크린샷 폴더) · DDS_USER · DDS_PIN · DDS_BROWSER
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

const BASE = process.env.DDS_URL;
const PID = process.env.DDS_PID;
const OUT = process.env.DDS_SHOTS;
fs.mkdirSync(OUT, { recursive: true });

const lines = readline.createInterface({ input: process.stdin });
const queue = [];
const waiters = [];
lines.on("line", (l) => { const w = waiters.shift(); if (w) w(l); else queue.push(l); });
const nextLine = () => new Promise((res) => { if (queue.length) res(queue.shift()); else waiters.push(res); });
const step = async (name) => { console.log(`STEP ${name}`); return nextLine(); };

const browser = await chromium.launch({ channel: process.env.DDS_BROWSER === "chrome" ? "chrome" : "msedge", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "ko-KR" });
await ctx.addInitScript(() => { try { localStorage.setItem("dds.theme", "light"); } catch { /* 무시 */ } });
const page = await ctx.newPage();
const errors = [];
// 서버를 일부러 끄는 단계의 연결 실패와, 일부러 돌려준 503은 오류가 아니다
page.on("console", (m) => { if (m.type() === "error" && !/\b(404|409|503)\b|ERR_CONNECTION|WebSocket connection/.test(m.text())) errors.push(`[console] ${m.text()}`); });
page.on("pageerror", (e) => errors.push(`[page] ${e}`));

let n = 0;
const shot = async (name) => {
  const f = path.join(OUT, `${String(++n).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: f, animations: "disabled" });
  console.log(`SHOT ${f}`);
};
const fail = (msg) => { throw new Error(msg); };
const bubble = () => page.locator(".waiting-bubble");
const input = () => page.getByLabel("에이전트에게 요청");
/** "1분 20초" → 80 */
const clockSec = async () => {
  const t = await page.locator(".waiting-clock").innerText();
  const m = /(?:(\d+)분\s*)?(\d+)초/.exec(t);
  if (!m) fail(`시간 표시를 읽지 못했습니다: ${t}`);
  return Number(m[1] ?? 0) * 60 + Number(m[2]);
};
const send = async (text) => {
  await input().fill(text);
  await input().press("Enter");
};
const idle = async (timeout = 90000) => {
  await bubble().waitFor({ state: "detached", timeout });
  await page.waitForFunction(() => !document.querySelector('[aria-label="에이전트에게 요청"]')?.readOnly, undefined, { timeout: 10000 });
};

try {
  await page.goto(`${BASE}/login`);
  await page.getByLabel("아이디").fill(process.env.DDS_USER ?? "admin");
  await page.getByLabel("PIN 1번째 자리").click();
  await page.keyboard.type(process.env.DDS_PIN ?? "123456");
  await page.waitForURL(`${BASE}/`, { timeout: 30000 });

  // 1. 새 대화의 첫 메시지: 재마운트돼도 말풍선·중지 버튼·경과 시간이 이어진다
  await page.goto(`${BASE}/projects/${PID}/new`);
  await input().waitFor();
  await page.waitForFunction(() => !document.querySelector('[aria-label="에이전트에게 요청"]')?.readOnly, undefined, { timeout: 30000 });
  await send("느린 시작을 확인해 주세요");
  await page.waitForURL(/\/projects\/\d+\/c\/\d+/, { timeout: 15000 });
  await page.locator(".waiting-bubble", { hasText: "시작하는 중" }).waitFor({ timeout: 15000 });
  // 표를 붙여 넣을 수 있게 disabled가 아니라 readOnly로 막는다. 글자는 넣을 수 없고 보내기 대신 중지 버튼이 보인다
  await page.waitForFunction(() => document.querySelector('[aria-label="에이전트에게 요청"]')?.readOnly, undefined, { timeout: 5000 }).catch(() => fail("기다리는 동안 입력창이 막혀야 합니다"));
  await page.getByRole("button", { name: "중지" }).waitFor({ timeout: 5000 });
  if (!(await bubble().innerText()).includes("멈춘 것이 아닙니다")) fail("보안 검사 안내가 없습니다");
  const t1 = await clockSec();
  await page.waitForTimeout(2500);
  const t2 = await clockSec();
  if (t2 <= t1) fail(`시간이 흐르지 않습니다: ${t1} → ${t2}`);
  console.log(`CLOCK ${t1} -> ${t2}`);
  await shot("cli-starting");

  // 2. 새로고침해도 요청·진행 표시·경과 시간이 그대로다
  await page.reload();
  await page.locator(".waiting-bubble").waitFor({ timeout: 15000 });
  const t3 = await clockSec();
  if (t3 < t2) fail(`새로고침 뒤 시간이 되돌아갔습니다: ${t2} → ${t3}`);
  if (!(await page.locator(".chat-msg.user .bubble").first().innerText()).includes("느린 시작")) fail("새로고침 뒤 내 요청이 사라졌습니다");
  console.log(`RELOAD clock=${t3}`);
  await shot("after-reload");
  await idle();
  await page.getByText("답:").first().waitFor({ timeout: 5000 });
  await shot("answered");

  // 3. 생각하는 중: 경과 시간이 흐른다
  await send("[quiet 5] 후속 요청");
  await page.locator(".waiting-bubble", { hasText: "생각하는 중" }).waitFor({ timeout: 15000 });
  // AI가 일하는 중에도 표를 받는다. 지금 할 수 있는 일과 나중에 할 일을 말한다
  await input().evaluate((el) => {
    const data = new DataTransfer();
    data.setData("text/plain", "두께\t폭\t응력\n1\t2\t3\n2\t3\t5\n3\t5\t8\n");
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  });
  await page.getByRole("button", { name: "Simulator 만들기" }).waitFor({ timeout: 5000 });
  const note = await input().getAttribute("placeholder");
  if (!note?.startsWith("AI가 일하는 중입니다. 지금은 위 버튼으로 바로 만들 수 있고")) throw new Error(`표를 받았을 때 안내가 다릅니다: ${note}`);
  await page.getByRole("button", { name: "취소" }).click();
  await page.waitForTimeout(2200);
  await shot("thinking");
  await idle();

  // 4. PC Agent 연결이 끊겨도 기록을 덮지 않고, 다시 잡는 중임을 보여 준다
  await send("[slow 10] 끊겨도 이어지는 요청");
  await page.locator(".chat-msg.agent .bubble", { hasText: "조각1" }).waitFor({ timeout: 15000 });
  await step("cut");
  await page.locator(".waiting-bubble", { hasText: "연결을 다시 잡는 중" }).waitFor({ timeout: 30000 });
  await page.locator(".chat-offline").waitFor({ timeout: 30000 });
  if (await page.getByText("설치되어 있지 않습니다").count()) fail("끊겼을 때 대화가 설치 안내로 바뀌었습니다");
  if (!(await page.locator(".chat-msg.user .bubble").first().isVisible())) fail("끊겼을 때 기록이 사라졌습니다");
  await shot("reconnecting");
  await step("resume");
  await idle();
  await page.getByText("끊겨도 이어지는 요청").last().waitFor({ timeout: 10000 });
  await shot("reconnected");

  // 4-1. 답이 흐르는 중에 새로고침해도(상태 응답이 글자 조각보다 늦어도) 말풍선·중지 버튼이 돌아온다
  await page.route("**/api/sessions/*/status", async (r) => { await new Promise((res) => setTimeout(res, 300)); await r.continue(); });
  await send("[slow 14] 흐르는 중 새로고침");
  await page.locator(".chat-msg.agent .bubble", { hasText: "조각1" }).last().waitFor({ timeout: 15000 });
  await page.route("**/api/sessions/*/messages", async (r) => { await new Promise((res) => setTimeout(res, 1200)); await r.continue(); });
  await page.reload();
  await input().waitFor();
  await page.waitForTimeout(600); // 기록을 아직 못 읽은 동안: 빈 새 대화 화면이 아니라 뼈대가 보여야 한다
  if (await page.getByText("무엇을 자동화할까요?").count()) fail("불러오는 중에 빈 새 대화 화면이 보입니다");
  if (await page.locator(".chat-suggest").count()) fail("불러오는 중에 추천 버튼이 보입니다");
  await page.unroute("**/api/sessions/*/messages");
  await bubble().waitFor({ timeout: 8000 });
  await page.getByRole("button", { name: "중지" }).waitFor({ timeout: 3000 });
  if (await input().isEditable()) fail("답이 흐르는 중 새로고침 뒤 입력창이 열렸습니다");
  await shot("streaming-reload");
  // 4-2. 다른 화면에 다녀와도 그사이 글자가 비지 않는다
  await page.locator('a[href="/settings"]').first().click();
  await page.waitForTimeout(1500);
  await page.goBack();
  await bubble().waitFor({ timeout: 8000 });
  const noGap = async () => {
    const t = await page.locator(".chat-msg.agent .bubble:not(.waiting-bubble)").last().innerText();
    const ns = [...t.matchAll(/조각(\d+)/g)].map((x) => Number(x[1]));
    return ns.length > 0 && ns.every((n, i) => i === 0 || n === ns[i - 1] + 1);
  };
  let clean = false;
  for (let i = 0; i < 14 && !clean; i += 1) { clean = await noGap(); if (!clean) await page.waitForTimeout(500); }
  if (!clean) fail("다른 화면에 다녀온 뒤 답 가운데가 비어 있습니다");
  await page.unroute("**/api/sessions/*/status");
  await idle();

  // 4-3. 아주 긴 도구 제목(명령줄)이 말풍선을 키우거나 대화 칸을 옆으로 넘치지 않는다
  await send("[longtool] 긴 제목");
  await page.locator(".cv-toolgroup-label").last().waitFor({ timeout: 15000 });
  await bubble().waitFor({ timeout: 5000 });
  const wide = await page.evaluate(() => {
    const b = document.querySelector(".chat-body");
    return { over: b.scrollWidth - b.clientWidth, bubble: document.querySelector(".waiting-bubble").getBoundingClientRect().height };
  });
  if (wide.over > 1) fail(`긴 도구 제목으로 대화 칸이 옆으로 ${wide.over}px 넘칩니다`);
  if (wide.bubble > 120) fail(`긴 도구 제목으로 말풍선이 ${Math.round(wide.bubble)}px로 커졌습니다`);
  await shot("long-tool");
  await idle();

  // 4-4. 1시간이 넘어도 초가 흐른다
  await send("[quiet 14] 한 시간 넘은 작업");
  await bubble().waitFor({ timeout: 10000 });
  await page.waitForTimeout(1000);
  await page.route("**/api/sessions/*/status", async (r) => {
    const res = await r.fetch();
    const j = await res.json();
    await r.fulfill({ response: res, json: { ...j, stage_elapsed_sec: j.stage_elapsed_sec + 3700, elapsed_sec: j.elapsed_sec + 3700 } });
  });
  await page.reload(); // 새로고침 뒤에는 상태 응답이 시각의 출처다
  await page.locator(".waiting-clock", { hasText: /1시간 \d+분 \d+초/ }).waitFor({ timeout: 10000 });
  const hour1 = await page.locator(".waiting-clock").innerText();
  await page.waitForTimeout(2500);
  const hour2 = await page.locator(".waiting-clock").innerText();
  if (hour2 === hour1) fail(`1시간이 넘자 시간이 흐르지 않습니다: ${hour1} → ${hour2}`);
  await page.unroute("**/api/sessions/*/status");
  await idle();

  // 4-5. 웹앱 서버가 꺼지면 "멈춘 것이 아닙니다"라고 안심시키지 않고 끊겼다고 말한다. 돌아오면 그 턴을 이어 보여 준다
  await send("[slow 25] 서버가 꺼져도");
  await page.locator(".chat-msg.user .bubble", { hasText: "서버가 꺼져도" }).waitFor({ timeout: 15000 });
  await page.waitForFunction(() => !!document.querySelector(".waiting-bubble") && [...document.querySelectorAll(".chat-msg.user .bubble")].at(-1).textContent.includes("서버가 꺼져도"), undefined, { timeout: 15000 });
  await page.waitForTimeout(2500); // 요청이 서버에 닿고 답이 흐르기 시작할 때까지
  await step("server_down");
  await page.locator(".waiting-bubble", { hasText: "서버 연결을 다시 잡는 중" }).waitFor({ timeout: 30000 });
  if ((await bubble().innerText()).includes("멈춘 것이 아닙니다")) fail("서버가 꺼졌는데 멈춘 것이 아니라고 말합니다");
  await page.locator(".chat-offline", { hasText: "서버에 연결할 수 없습니다" }).waitFor({ timeout: 5000 });
  if (await page.locator(".agent-row", { hasText: "PC Agent 연결됨" }).count()) fail("서버가 꺼졌는데 사이드바가 연결됨입니다");
  await shot("server-down");
  await step("server_up");
  const answered = () => page.locator(".chat-msg.agent .bubble", { hasText: /답:[\s\S]*서버가 꺼져도/ }).count();
  let gone = 0;
  for (let i = 0; i < 120 && !(await answered()); i += 1) { // 서버가 돌아온 뒤 Agent가 붙기 전에도 말풍선이 사라지면 안 된다
    gone = (await bubble().count()) ? 0 : gone + 1;
    if (gone >= 4 && !(await answered())) fail("서버가 돌아온 뒤 말풍선이 사라졌습니다");
    await page.waitForTimeout(500);
  }
  await page.locator(".chat-msg.agent .bubble", { hasText: /답:[\s\S]*서버가 꺼져도/ }).last().waitFor({ timeout: 10000 });
  await idle();
  if (await page.getByText("서버에 연결할 수 없습니다").count()) fail("서버가 돌아왔는데 끊김 안내가 남았습니다");
  await shot("server-back");

  // 4-6. 새 대화의 첫 요청이 서버에 닿기 전: 내 메시지가 보이고, 중지하면 받자마자 멈춘다
  await page.route("**/api/sessions/*/prompt", async (r) => { await new Promise((res) => setTimeout(res, 2000)); await r.continue(); }, { times: 1 });
  await page.goto(`${BASE}/projects/${PID}/new`);
  await page.waitForFunction(() => !document.querySelector('[aria-label="에이전트에게 요청"]')?.readOnly, undefined, { timeout: 30000 });
  await send("[wait] 보내자마자 중지");
  await page.locator(".chat-msg.user .bubble", { hasText: "보내자마자 중지" }).waitFor({ timeout: 1500 });
  if (await page.getByText("무엇을 자동화할까요?").count()) fail("보내는 중에 빈 새 대화 화면이 보입니다");
  await page.getByRole("button", { name: "중지" }).click();
  await page.locator(".waiting-bubble", { hasText: "중지하는 중" }).waitFor({ timeout: 2000 });
  await page.waitForURL(/\/projects\/\d+\/c\/\d+/, { timeout: 15000 });
  await idle(30000);

  // 4-7. 첫 화면에서 쓴 요청의 첫 전송이 실패해도 입력창에 그대로 돌아온다
  await page.route("**/api/sessions/*/prompt", (r) => r.fulfill({ status: 503, json: { detail: "잠시 후 다시 시도하세요" } }), { times: 1 });
  await page.goto(`${BASE}/`);
  await input().waitFor();
  await input().fill("첫 화면에서 쓴 긴 요청을 잃으면 안 된다");
  await input().press("Enter");
  await page.waitForURL(/\/projects\/\d+\/(new|c\/\d+)/, { timeout: 30000 });
  await page.waitForFunction(() => document.querySelector('[aria-label="에이전트에게 요청"]')?.value.includes("잃으면 안 된다"), undefined, { timeout: 30000 });
  await shot("home-send-failed");
  await input().press("Enter");
  await page.locator(".chat-msg.agent .bubble", { hasText: "잃으면 안 된다" }).waitFor({ timeout: 90000 });
  await idle();
  await page.goto(`${BASE}/projects/${PID}/new`);
  await input().waitFor();

  // 4-8. 연결 직후 hello 전: 쓸 CLI를 모르면 보내지 못하게 하고, 정해 둔 이름으로 채우지 않는다
  await page.route("**/api/agent/status", (r) => r.fulfill({ json: { connected: true, active_project_id: null, info: {} } }));
  await ctx.addInitScript(() => { try { localStorage.removeItem("dds.agent"); } catch { /* 무시 */ } });
  await page.reload();
  await input().waitFor();
  await page.waitForTimeout(500);
  if (await input().isEditable()) fail("쓸 에이전트를 모르는데 입력창이 열렸습니다");
  const picked = await page.locator(".agent-pick-btn").innerText();
  if (/gemini/i.test(picked)) fail(`고르지 않은 에이전트가 보입니다: ${picked}`);
  await page.unroute("**/api/agent/status");
  await page.waitForFunction(() => !document.querySelector('[aria-label="에이전트에게 요청"]')?.readOnly, undefined, { timeout: 10000 });

  // 5. 멈춘 도구: 중지해도 답이 없으면 30초 뒤 다시 시작 버튼이 나온다. 긴 대화에서 스크롤 없이 보여야 한다
  await page.goto(`${BASE}/projects/${PID}/new`);
  await page.waitForFunction(() => !document.querySelector('[aria-label="에이전트에게 요청"]')?.readOnly, undefined, { timeout: 30000 });
  await send("첫 번째 긴 요청 " + "내용을 길게 적습니다 ".repeat(300));
  await page.waitForURL(/\/projects\/\d+\/c\/\d+/, { timeout: 15000 });
  await idle();
  await send("두 번째 긴 요청 " + "내용을 길게 적습니다 ".repeat(300));
  await page.waitForTimeout(500);
  await idle();
  await send("[hang] 멈춘 스크립트");
  await page.locator(".waiting-bubble", { hasText: /실행 중|도구를 실행/ }).waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "중지" }).click();
  await page.locator(".waiting-bubble", { hasText: "중지하는 중" }).waitFor({ timeout: 10000 });
  const restart = page.getByRole("button", { name: "AI 도구 다시 시작" });
  await restart.waitFor({ timeout: 45000 });
  // 스크롤을 건드리지 않아도 버튼이 입력창 아래로 숨지 않는다. 멈춘 스크립트에서 빠져나올 유일한 길이다
  await page.waitForTimeout(200);
  const hidden = await page.evaluate(() => {
    const b = document.querySelector(".waiting-restart").getBoundingClientRect();
    const v = document.querySelector(".chat-body").getBoundingClientRect();
    return b.bottom - v.bottom;
  });
  if (hidden > 1) fail(`다시 시작 버튼이 화면 아래로 ${Math.round(hidden)}px 숨었습니다`);
  await shot("stop-no-reply");
  await restart.click();
  await idle(30000);
  await page.getByText("AI 도구를 다시 시작합니다").waitFor({ timeout: 10000 });
  await page.waitForTimeout(1500); // CLI가 끝났다는 알림까지 도착한 뒤에도 안내는 하나여야 한다
  const notes = await page.locator(".chat-msg.agent.err .bubble", { hasText: "다시 시작" }).count();
  if (notes !== 1) fail(`다시 시작 안내가 ${notes}개입니다`);
  if (await page.locator(".cv-toolgroup .spin").count()) fail("끝난 도구가 계속 실행 중으로 보입니다");
  await send("다시 시작한 뒤 요청");
  await page.locator(".chat-msg.agent .bubble", { hasText: "다시 시작한 뒤 요청" }).waitFor({ timeout: 60000 });
  await idle();
  await shot("after-restart");

  if (errors.length) fail(`화면 오류: ${errors.join(" | ")}`);
  console.log("PASS");
} catch (e) {
  await page.screenshot({ path: path.join(OUT, "failed.png") }).catch(() => {});
  console.log(`FAIL ${String(e).split("\n")[0]}`);
  for (const line of errors) console.log(`ERROR ${line}`);
  process.exitCode = 1;
} finally {
  await browser.close();
  lines.close();
}
