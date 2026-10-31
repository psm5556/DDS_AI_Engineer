// UI 점검(스모크): 주요 화면을 실제 브라우저(설치된 Edge)로 열어 스크린샷을 남기고, 화면 오류(콘솔 error·예외)가 있으면 실패한다.
// 화면을 고친 뒤 반드시 돌린다. 서버(+PC Agent)가 떠 있어야 한다: .venv\Scripts\python scripts\dev_live.py --agent demo
//
//   cd frontend
//   npm run smoke                        # 화면 둘러보기 (로그인 → 첫 화면 → 툴 → 프로젝트 탭들 → 사이드바 순서·프로젝트 보기 → Simulator 카드·목록·검색 → 설정 → 관리)
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
await step("로그인 화면 테마 전환", async () => {
  const theme = () => page.evaluate(() => document.documentElement.dataset.theme);
  const before = await theme();
  const button = () => page.locator(".login-top button");
  await button().click();
  if ((await theme()) === before) throw new Error("테마가 바뀌지 않았습니다");
  await button().click();
  if ((await theme()) !== before) throw new Error("테마가 돌아오지 않았습니다");
});
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

await step("해석 결과 파일", async () => {
  // 로그인 화면이 쓰는 세 해석 결과가 있고 형식이 맞는지(없으면 화면이 조용히 건너뛰므로 여기서 잡는다)
  for (const id of ["structural", "thermal", "drop"]) {
    const m = await page.evaluate((u) => fetch(u).then((r) => r.json()), `/hero/${id}.json`);
    if (!m.arrays?.pos || !m.steps?.length) throw new Error(`${id}: 해석 결과 형식이 아닙니다`);
  }
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
  await step("보고서 목차", async () => {
    await page.getByRole("tab", { name: "보고서" }).first().click({ timeout: 5000 });
    await page.waitForTimeout(800);
    const links = page.locator(".report-toc button");
    if (!(await links.count())) { console.log("  (목차가 있는 보고서가 없어 건너뜀)"); return; }
    if (!(await page.locator(".report-cover .wordmark").count()) || !(await page.locator(".report-colophon").count())) throw new Error("보고서 틀(표지·맺음말)이 없습니다");
    const top = () => page.locator(".canvas-body").evaluate((el) => el.scrollTop);
    await links.last().click();
    await page.waitForTimeout(1000);
    if ((await top()) <= 0) throw new Error("목차를 눌러도 패널이 움직이지 않습니다");
    if (await page.evaluate(() => location.hash)) throw new Error("목차가 주소를 바꿨습니다");
    await shot("report-toc-jump");
  });
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

await step("사이드바 순서", async () => {
  await page.goto(BASE + "/");
  await page.locator(".side-nav-list").waitFor();
  const label = (e) => (e.tagName === "HR" ? "구분선" : e.getAttribute("aria-label") || e.querySelector("input")?.placeholder || e.textContent.trim());
  const order = await page.locator(".side-nav-list > *").evaluateAll((els, f) => els.map(new Function("e", `return (${f})(e)`)), label.toString());
  if (order.join(",") !== "Simulator,구분선,새 프로젝트,프로젝트 검색") throw new Error(`순서가 다릅니다: ${order}`);
  await page.getByRole("button", { name: "사이드바 접기" }).click();
  const rail = await page.locator(".sidebar.rail > *").evaluateAll((els, f) => els.map(new Function("e", `return (${f})(e)`)), label.toString());
  const at = rail.indexOf("Simulator");
  if (at < 0 || rail[at + 1] !== "구분선" || rail[at + 2] !== "새 프로젝트") throw new Error(`접은 사이드바 순서가 다릅니다: ${rail}`);
  await shot("sidebar-rail");
  await page.getByRole("button", { name: "사이드바 펼치기" }).click();
});

await step("프로젝트 보기", async () => {
  const { me, projects } = await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem("dds.token")}` };
    const json = (u) => fetch(u, { headers }).then((r) => r.json());
    return { me: await json("/api/auth/me"), projects: await json("/api/projects") };
  });
  // 화면과 따로 같은 정의를 적어 맞춰 본다. 관리자가 멤버가 아닌 프로젝트는 '공유 받은 것'이 아니다
  const others = (p) => p.members.some((m) => m.user_id !== me.id);
  const member = (p) => p.members.some((m) => m.user_id === me.id);
  const views = {
    all: ["전체", () => true],
    favorite: ["즐겨찾기", (p) => p.is_favorite],
    sent: ["내가 공유한 것", (p) => p.owner_id === me.id && others(p)],
    received: ["공유 받은 것", (p) => p.owner_id !== me.id && member(p)],
  };
  const names = () => page.locator(".side-body .side-name").allTextContents();
  for (const [id, [title, has]] of Object.entries(views)) {
    const btn = page.locator(`.side-views button[data-view="${id}"]`);
    await btn.click();
    if ((await btn.getAttribute("aria-pressed")) !== "true") throw new Error(`${title}: 선택 표시가 없습니다`);
    if ((await page.locator('.side-views button[aria-pressed="true"]').count()) !== 1) throw new Error(`${title}: 둘 이상이 선택되었습니다`);
    if ((await page.locator(".side-body h4").allTextContents()).join() !== title) throw new Error(`${title}: 제목이 다릅니다`);
    const want = projects.filter(has).map((p) => p.name);
    const got = await names();
    if (got.join("|") !== want.join("|")) throw new Error(`${title}: 목록이 다릅니다 (화면 ${got.length}개, 기대 ${want.length}개)`);
    if (!want.length && !(await page.locator(".side-body .side-empty").count())) throw new Error(`${title}: 빈 안내가 없습니다`);
    await shot(`sidebar-${id}`);
  }
  // 고른 보기는 다시 열어도 그대로이고, 검색은 그 안에서만 찾는다
  await page.locator('.side-views button[data-view="favorite"]').click();
  await page.reload();
  await page.locator(".side-views").waitFor();
  if ((await page.locator('.side-views button[data-view="favorite"]').getAttribute("aria-pressed")) !== "true") throw new Error("고른 보기를 기억하지 못합니다");
  await page.locator('.side-views button[data-view="all"]').click();
  await page.getByLabel("프로젝트 검색").fill("없는-프로젝트-이름");
  if ((await names()).length || !(await page.locator(".side-body .side-empty").count())) throw new Error("검색이 보기 안에서 동작하지 않습니다");
  await page.getByLabel("프로젝트 검색").fill("");
  // 메뉴의 즐겨찾기가 그대로 동작하고 표시가 따라온다
  if (projects.length) {
    const first = projects[0];
    const stars = () => page.locator(".side-item").first().locator(".side-fav").count();
    const before = await stars();
    const toggleFav = async () => {
      await page.locator(".side-item").first().hover();
      await page.locator(".side-item .side-more").first().click();
      await page.getByRole("menuitem", { name: /즐겨찾기/ }).click();
    };
    await toggleFav();
    await page.waitForFunction(([sel, n]) => (document.querySelector(sel)?.querySelector(".side-fav") ? 1 : 0) !== n, [".side-item", before]);
    await toggleFav();
    await page.waitForFunction(([sel, n]) => (document.querySelector(sel)?.querySelector(".side-fav") ? 1 : 0) === n, [".side-item", before]);
    if (first.is_favorite !== (before === 1)) throw new Error("즐겨찾기 표시가 서버 값과 다릅니다");
  }
});

await step("프로젝트 보기 규칙", async () => {
  // 혼자 쓰는 서버에는 공유 프로젝트가 없어 규칙이 시험되지 않는다. 데이터를 남기지 않도록 응답만 바꿔 넣어 본다
  const me = await page.evaluate(() => fetch("/api/auth/me", { headers: { Authorization: `Bearer ${localStorage.getItem("dds.token")}` } }).then((r) => r.json()));
  const other = me.id + 1000;
  const member = (user_id) => ({ user_id, display_name: `사용자${user_id}`, username: `u${user_id}`, role: "member" });
  const at = new Date().toISOString();
  const make = (id, name, owner_id, members, extra = {}) => ({ id, name, description: "", owner_id, version: 1, created_at: at, updated_at: at, members: members.map(member),
    is_favorite: false, user_role: "member", flow_status: null, flow_title: null, ...extra });
  const fixture = [
    make(9001, "규칙-혼자", me.id, [me.id]),
    make(9002, "규칙-보냄", me.id, [me.id, other]),
    make(9003, "규칙-받음", other, [other, me.id], { flow_status: "review" }),
    make(9004, "규칙-남의것", other, [other]), // 관리자에게만 보이는 프로젝트. 공유받은 것이 아니다
    make(9005, "규칙-남의것별", other, [other], { is_favorite: true }),
    make(9006, "규칙-빠진내것", me.id, [other]),
  ];
  const route = (r) => (r.request().method() === "GET" ? r.fulfill({ json: fixture }) : r.fallback());
  await page.route("**/api/projects", route);
  try {
    await page.goto(BASE + "/");
    await page.locator(".side-views").waitFor();
    const names = () => page.locator(".side-body .side-name").allTextContents();
    const pick = (id) => page.locator(`.side-views button[data-view="${id}"]`).click();
    const want = {
      all: fixture.map((p) => p.name),
      favorite: ["규칙-남의것별"],
      sent: ["규칙-보냄", "규칙-빠진내것"],
      received: ["규칙-받음"],
    };
    for (const [id, list] of Object.entries(want)) {
      await pick(id);
      const got = await names();
      if (got.join("|") !== list.join("|")) throw new Error(`${id}: ${got.join(", ")}`);
    }
    // 검색은 고른 보기 안에서만 찾는다
    const find = page.getByLabel("프로젝트 검색");
    await pick("received");
    await find.fill("규칙-혼자");
    if ((await names()).length || !(await page.locator(".side-body .side-empty").count())) throw new Error("검색이 고른 보기 밖까지 찾습니다");
    await find.fill("규칙-받음");
    if ((await names()).join() !== "규칙-받음") throw new Error("검색이 고른 보기 안에서 찾지 못합니다");
    await find.fill("");
    // 보기 밖의 승인 대기는 전체 아이콘에 표시하고, 그 프로젝트가 보이는 보기에서는 표시하지 않는다
    const dot = page.locator('.side-views button[data-view="all"] .side-st-dot');
    if (await dot.count()) throw new Error("승인 대기가 보이는데도 전체 아이콘에 점이 있습니다");
    await pick("favorite");
    if (!(await dot.count())) throw new Error("보기 밖의 승인 대기 표시가 없습니다");
    // 관리자라서 보이는 남의 프로젝트의 승인 대기는 내 일이 아니다
    fixture.find((p) => p.id === 9003).flow_status = null;
    fixture.find((p) => p.id === 9004).flow_status = "review";
    await page.goto(BASE + "/");
    await page.locator(".side-views").waitFor();
    await page.waitForFunction(() => document.querySelector('.side-views button[aria-pressed="true"]')?.dataset.view === "favorite");
    if (await dot.count()) throw new Error("멤버가 아닌 프로젝트의 승인 대기까지 표시합니다");
    // 아이콘 이름은 키보드로 왔을 때도 보인다
    const sent = page.locator('.side-views button[data-view="sent"]');
    await sent.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    const tip = await sent.evaluate((b) => { const s = getComputedStyle(b, "::after"); return { text: s.content, shown: s.visibility }; });
    if (!tip.text.includes("내가 공유한 것") || tip.shown !== "visible") throw new Error(`키보드로 온 아이콘의 이름이 보이지 않습니다: ${JSON.stringify(tip)}`);
    await shot("sidebar-rules");
    await pick("all");
    if (!(await page.locator('.side-views button[data-view="all"][aria-pressed="true"]').count())) throw new Error("전체로 돌아오지 못했습니다");
  } finally {
    await page.unroute("**/api/projects", route);
  }
});

await step("지금 연 프로젝트", async () => {
  const projects = await page.evaluate(() => fetch("/api/projects", { headers: { Authorization: `Bearer ${localStorage.getItem("dds.token")}` } }).then((r) => r.json()));
  const p = projects.find((x) => !x.is_favorite);
  if (!p) { console.log("  (즐겨찾기가 아닌 프로젝트가 없어 건너뜀)"); return; }
  await page.goto(`${BASE}/projects/${p.id}`);
  await page.locator(".side-views").waitFor();
  await page.locator('.side-views button[data-view="favorite"]').click();
  // 고른 보기 밖이어도 연 프로젝트는 사라지지 않고 표시된다
  const pinned = page.locator(".side-pinned .side-item.active .side-name");
  await pinned.waitFor({ timeout: 8000 });
  if ((await pinned.textContent()) !== p.name) throw new Error("지금 연 프로젝트가 아닙니다");
  await shot("sidebar-pinned");
  await page.locator('.side-views button[data-view="all"]').click();
  if (await page.locator(".side-pinned").count()) throw new Error("보기 안에 있는데 따로 또 보입니다");
  if ((await page.locator(".side-body .side-item.active").count()) !== 1) throw new Error("연 프로젝트 표시가 없습니다");
});

await step("Simulator 목록", async () => {
  await page.goto(BASE + "/simulators");
  await page.waitForTimeout(1200);
  await shot("simulators");
  if (!(await page.locator(".sims-find").count())) { console.log("  (배포된 Simulator 없음 — 건너뜀)"); return; }
  const cards = page.locator(".sim-card");
  const n = await cards.count();
  if (!n || (await page.locator(".sim-row").count())) throw new Error("기본 보기가 카드가 아닙니다");
  // 그림은 받거나 대체 표시로 끝나야 한다. 가운데 멈춰 있으면 안 된다
  await page.waitForFunction(() => {
    const seen = [...document.querySelectorAll(".sim-thumb")].filter((t) => { const r = t.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; });
    return seen.length > 0 && seen.every((t) => t.querySelector("img, .sim-thumb-mark") || t.classList.contains("none"));
  }, null, { timeout: 30000 });
  await page.waitForFunction(() => !document.querySelector(".sim-thumb.waiting"), null, { timeout: 30000 }); // 멈춰 있는 표시가 남으면 여기서 실패한다
  if (await page.locator(".sim-thumb-mark .agent-glyph").count()) throw new Error("그림 없음 표시가 도는 기호처럼 보입니다");
  // 그림을 다 받으면 카드는 멈춰 있다. 화면 밖 카드도 움직이지 않는다
  const moving = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running" && a.effect?.target?.closest?.(".sim-thumb") && a.effect.getTiming().iterations === Infinity).length);
  if (moving) throw new Error(`카드 그림 자리에서 ${moving}개가 계속 움직입니다`);
  await shot("simulators-cards");
  const toggle = (name) => page.getByRole("group", { name: "보기 방식" }).getByRole("button", { name });
  // 받은 그림은 보기를 오가도 다시 받지 않고, 그 자리에서 바로 보이며, 다시 나타나는 움직임이 없다
  const pictures = await page.locator(".sim-thumb img").count();
  let refetched = 0;
  const countThumb = (r) => { if (r.url().includes("/thumbnail")) refetched += 1; };
  page.on("request", countThumb);
  await toggle("목록").click();
  if ((await cards.count()) || !(await page.locator(".sim-row").count())) throw new Error("목록으로 바뀌지 않습니다");
  if ((await toggle("목록").getAttribute("aria-pressed")) !== "true") throw new Error("목록 선택 표시가 없습니다");
  await shot("simulators-list");
  await toggle("카드").click();
  const instantly = await page.locator(".sim-thumb img").count();
  const replay = await page.locator(".sim-thumb img.arrive").count();
  page.off("request", countThumb);
  await page.waitForTimeout(400);
  if (refetched) throw new Error(`보기를 바꿨더니 그림을 ${refetched}번 다시 받았습니다`);
  if (instantly < pictures) throw new Error(`보기를 바꿨더니 그림이 비었습니다 (${pictures} → ${instantly})`);
  if (replay) throw new Error("보기를 바꿨더니 그림이 다시 나타나는 움직임을 씁니다");
  await toggle("목록").click();
  await page.reload();
  await page.locator(".sims-find").waitFor();
  if (!(await page.locator(".sim-row").count())) throw new Error("보기 방식을 기억하지 못합니다");
  await toggle("카드").click();

  // 검색: 찾은 것만, 대소문자 구분 없이. 없으면 안내하고, 지우면 돌아온다
  const search = page.getByLabel("Simulator 검색", { exact: true });
  const title = (await cards.first().locator(".sim-card-title b").textContent()).trim();
  await search.fill(title.toUpperCase());
  const hits = await cards.locator(".sim-card-title b").allTextContents();
  if (!hits.includes(title)) throw new Error(`검색 '${title}'에 그 Simulator가 없습니다`);
  await search.fill("없는-시뮬레이터-이름");
  if ((await cards.count()) || !(await page.locator(".sims-none").count()) || (await page.locator(".sims-sec").count())) throw new Error("검색 결과 없음 안내가 틀립니다");
  await shot("simulators-none");
  await page.getByRole("button", { name: "검색 지우기" }).click();
  if ((await cards.count()) !== n) throw new Error("검색을 지워도 돌아오지 않습니다");
  if (!(await search.evaluate((el) => el === document.activeElement))) throw new Error("검색을 지운 뒤 입력창으로 돌아오지 않습니다");

  const open = page.locator(".sim-card-link").first();
  if (!(await open.count())) return;
  await open.click();
  await page.waitForURL(/\/(simulator|s)\//);
  await page.waitForTimeout(3500);
  await shot("simulator");
});

await step("Simulator 긴 값", async () => {
  // 실제 서버 값과 상관없이 넘치기 쉬운 값을 넣어 본다. 응답만 바꾸므로 데이터가 남지 않는다
  const at = new Date().toISOString();
  const outputs = Array.from({ length: 20 }, (_, i) => `최대_폰미제스_응력_위치${i + 1}`);
  const item = (id, study_name, extra = {}) => ({ study_id: id, study_name, model_id: id, version: 1, outputs, n_variables: 3, n_train: 10, url: `/simulator/${id}`, thumbnail_url: null,
    source: "mine", status: "active", project_id: 1, project_name: "프로젝트", shared_by: null, note: "", share_id: null, updated_at: at, ...extra });
  const longName = "브래킷리브높이두께최적화를위한매우긴이름의시뮬레이터입니다그리고조금더길게";
  const fixture = {
    mine: Array.from({ length: 12 }, (_, i) => item(9100 + i, i === 0 ? `${longName} A` : i === 1 ? `${longName} B` : `시뮬레이터 ${i}`)),
    shared: [
      item(9200, "중지된 공유", { source: "link", status: "stopped", shared_by: "김", share_id: 91, note: "메모".repeat(180) }),
      item(9201, "끝난 공유", { source: "link", status: "expired", shared_by: "이", share_id: 92, note: "x".repeat(400) }),
    ],
  };
  const route = (r) => r.fulfill({ json: fixture });
  await page.route("**/api/simulators", route);
  try {
    await page.goto(BASE + "/simulators");
    await page.locator(".sims-find").waitFor();
    const toggle = (name) => page.getByRole("group", { name: "보기 방식" }).getByRole("button", { name });
    await toggle("카드").click();
    const noSideScroll = async (where) => {
      const w = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      if (w[0] > w[1]) throw new Error(`${where}: 가로로 넘칩니다 (${w[0]} > ${w[1]})`);
    };
    // 끝만 다른 이름은 구분되어야 하고, 끊긴 공유의 메모는 한 줄이다
    const titleLines = await page.locator(".sim-card-title b").first().evaluate((b) => Math.round(b.getBoundingClientRect().height / parseFloat(getComputedStyle(b).lineHeight)));
    if (titleLines !== 2) throw new Error(`긴 이름이 ${titleLines}줄입니다`);
    const offMeta = await page.locator(".sim-card.off .sim-card-meta span").evaluateAll((els) => els.map((e) => [e.getBoundingClientRect().height, e.scrollHeight, e.closest(".sim-card").getBoundingClientRect().width, e.closest(".sim-card").scrollWidth]));
    for (const [h, sh, w, sw] of offMeta) if (h > 24 || sh > 24 || sw > w + 1) throw new Error(`끊긴 공유의 메모가 한 줄이 아닙니다 (${h}, ${sw}/${w})`);
    if (await page.locator(".sim-thumb-mark .agent-glyph").count()) throw new Error("그림 없음 표시가 도는 기호처럼 보입니다");
    await shot("simulators-long-cards");

    // 출력이 많아도 목록의 이름 칸이 남는다
    await toggle("목록").click();
    for (const width of [1440, 1024]) {
      await page.setViewportSize({ width, height: 900 });
      const w = await page.locator(".sim-row .recent-row b").evaluateAll((els) => Math.min(...els.map((e) => e.getBoundingClientRect().width)));
      if (w < 120) throw new Error(`${width} 폭에서 목록의 이름 칸이 ${Math.round(w)}px입니다`);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await shot("simulators-long-list");
    await toggle("카드").click();

    // 긴 검색어가 페이지를 넘치지 않는다
    const search = page.getByLabel("Simulator 검색", { exact: true });
    await search.fill("없는값".repeat(90));
    await noSideScroll("긴 검색어");
    await search.fill("");

    // 좁은 창에서 검색해도 입력창이 움직이지 않는다
    await page.setViewportSize({ width: 800, height: 1000 });
    const y = () => search.evaluate((el) => Math.round(el.getBoundingClientRect().top));
    const before = await y();
    await search.fill("없는값");
    const after = await y();
    if (before !== after) throw new Error(`검색 중에 입력창이 움직입니다 (${before} → ${after})`);
    await noSideScroll("좁은 창");
    await search.fill("");
  } finally {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.unroute("**/api/simulators", route);
  }
});

await step("좁은 창의 접힌 사이드바", async () => {
  await page.goto(BASE + "/simulators");
  await page.getByRole("button", { name: "사이드바 접기" }).click();
  await page.setViewportSize({ width: 800, height: 600 });
  const sep = await page.locator(".rail-sep").evaluate((e) => { const r = e.getBoundingClientRect(); return [r.width, r.height]; });
  if (sep[1] < sep[0]) throw new Error(`가로로 놓인 사이드바의 구분선이 세로가 아닙니다 (${sep})`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "사이드바 펼치기" }).click();
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
