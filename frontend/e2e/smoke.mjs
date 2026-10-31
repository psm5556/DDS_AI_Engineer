// UI 점검(스모크): 주요 화면을 실제 브라우저(설치된 Edge)로 열어 스크린샷을 남기고, 화면 오류(콘솔 error·예외)가 있으면 실패한다.
// 화면을 고친 뒤 반드시 돌린다. 서버(+PC Agent)가 떠 있어야 한다: .venv\Scripts\python scripts\dev_live.py --agent demo
//
//   cd frontend
//   npm run smoke                        # 화면 둘러보기 (로그인 → 첫 화면 → 표로 바로 Simulator → 툴 → 프로젝트 탭들 → 사이드바 순서·프로젝트 보기 → Simulator 카드·목록·검색 → 설정 → 관리)
//   npm run smoke -- --demo              # + 데모 에이전트로 전체 과정(CAD→보고서)을 실제로 돌려 승인·실행·완료까지 확인 (3~5분)
//   npm run smoke -- --reduced           # '움직임 줄이기' 설정으로 점검(애니메이션 0개여야 통과)
//   npm run smoke -- --insights          # + 데모로 두 목표·능동학습을 돌린 뒤 판단 도우미를 펼쳐 점검(1~2분)
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
const INSIGHTS = process.argv.includes("--insights");
const THEME = process.env.DDS_THEME === "dark" ? "dark" : "light";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: process.env.DDS_BROWSER === "chrome" ? "chrome" : "msedge", headless: !process.argv.includes("--headed") });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "ko-KR", reducedMotion: REDUCED ? "reduce" : "no-preference" });
await ctx.addInitScript((t) => { try { localStorage.setItem("dds.theme", t); } catch { /* 무시 */ } }, THEME);
const page = await ctx.newPage();
const errors = [];
// 404(아직 없는 데이터)·409(PC Agent 미연결 등 안내용 응답)는 화면 오류가 아니다. 400은 일부러 나쁜 표를 올리는 동안만 괜찮다
let expect400 = false;
page.on("console", (m) => { if (m.type() === "error" && !/\b(404|409)\b/.test(m.text()) && !(expect400 && /\b400\b/.test(m.text()))) errors.push(`[console] ${m.text()}`); });
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
  await step("이 흐름을 새 모델로 다시", async () => {
    await page.getByRole("tab", { name: "작업 흐름" }).click();
    const again = page.getByRole("button", { name: "이 흐름을 새 모델로 다시" });
    if ((await again.count()) !== 1) throw new Error(`버튼이 ${await again.count()}개입니다. 한 곳에만 있어야 합니다`);
    await again.click();
    const input = page.getByLabel("에이전트에게 요청");
    await page.waitForFunction((el) => el.value.includes("새 모델로 다시"), await input.elementHandle());
    await page.locator(".composer-ask").waitFor({ timeout: 3000 });
    await shot("flow-rerun-prefill");
    await input.fill("");
    await page.locator(".composer-ask .icon-btn").click();
  });
  await step("DOE 계획표 받기", async () => {
    await page.getByRole("tab", { name: "데이터" }).click();
    const btn = page.getByRole("button", { name: /DOE 계획표 받기/ });
    await btn.waitFor({ timeout: 8000 });
    await shot("data-doe-csv");
    const [file] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), btn.click()]);
    const text = fs.readFileSync(await file.path(), "utf8");
    if (!file.suggestedFilename().endsWith(".csv") || text.split("\n").length < 3) throw new Error("DOE 계획표가 CSV가 아닙니다");
  });
  await step("입력창의 표를 AI로 보내기", async () => {
    // 행은 요청 글이나 도구 인자가 아니라 서버에 저장된 첨부로 간다
    const lines = ["두께\t폭\t응력"];
    for (let i = 1; i <= 12; i++) lines.push([i, 20 + (i * 7) % 12, 300 / i + Date.now() % 7].join("\t"));
    const input = page.getByLabel("에이전트에게 요청");
    await input.evaluate((el, t) => {
      const data = new DataTransfer();
      data.setData("text/plain", t);
      el.focus();
      el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    }, lines.join("\n"));
    await page.locator(".composer-table .ti-chips").waitFor({ timeout: 15000 });
    await input.fill("응력이 결과야. 이 표로 Simulator 만들어 줘"); // 출력을 적으면 AI가 묻지 않는다. 안 적으면 그 하나만 묻는다
    await input.press("Enter");
    await page.locator(".msg-files .file-chip", { hasText: "붙여넣은_표" }).last().waitFor({ timeout: 30000 });
    const pid = page.url().match(/projects\/(\d+)/)[1];
    await page.waitForFunction(async (p) => {
      const h = { Authorization: `Bearer ${localStorage.getItem("dds.token")}` };
      const studies = await fetch(`/api/projects/${p}/studies`, { headers: h }).then((r) => r.json());
      const s = studies.find((x) => x.import_info?.source?.startsWith("붙여넣은_표"));
      if (!s) return false;
      const models = await fetch(`/api/projects/${p}/models`, { headers: h }).then((r) => r.json());
      return models.some((m) => m.study_id === s.id && m.deployed);
    }, pid, { timeout: 180000, polling: 2000 });
    await page.getByRole("tab", { name: "Simulator" }).click();
    await page.waitForTimeout(1500);
    await shot("demo-composer-table");
  });
}

// 표로 바로 Simulator: AI·PC Agent 없이 표를 붙여 넣어 Simulator까지. 같은 표를 다시 돌려도 되도록 값에 실행 시각을 넣는다
const stamp = Date.now() % 100000;
// 엑셀에서 복사한 모양: 탭 구분, 따옴표 안 줄바꿈, 천 단위 쉼표, 퍼센트, 빈 열, 빈 행, 병합 셀 빈칸, 수식 오류
const bracketTsv = () => {
  const lines = ['번호\t두께 [mm]\t폭 [mm]\t\t재질\t하중 [N]\t"최대\n응력 [MPa]"\t효율\t질량 [kg]'];
  for (let i = 1; i <= 30; i++) {
    const t = 1 + ((i * 7) % 30) / 6, w = 20 + ((i * 11) % 30);
    const stress = (400000 / t + 300 / w + stamp % 7).toLocaleString("en-US", { maximumFractionDigits: 3 });
    lines.push([i, t, w, "", i === 1 ? "SS400" : "", 1000, `"${stress}"`, `${(50 + t * 3).toFixed(1)}%`, 0.1 * t * w].join("\t"));
    if (i === 15) lines.push("\t\t\t\t\t\t\t\t");
  }
  lines.push([31, 2, 30, "", "", 1000, "#DIV/0!", "60.0%", 6].join("\t"));
  return lines.join("\r\n");
};
// 입력과 관계없는 출력. 시드를 고정한 간단한 난수로, 어느 실행에서나 R²가 낮다
let seedN = 12345 + stamp;
const rnd = () => { seedN = (seedN * 16807) % 2147483647; return seedN / 2147483647; };
const noisyTsv = ["두께\t폭\t응력", ...Array.from({ length: 24 }, () => [rnd() * 5, rnd() * 30, rnd() * 100].join("\t"))].join("\n");

// 실제 붙여넣기 이벤트. 엑셀처럼 글과 그림을 함께 넣는다. 사용자 PC의 클립보드는 건드리지 않는다
const pasteInto = (locator, text) => locator.evaluate((el, t) => {
  const data = new DataTransfer();
  data.setData("text/plain", t);
  data.items.add(new File([new Uint8Array([137, 80, 78, 71])], "image.png", { type: "image/png" }));
  el.focus();
  el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}, text);
const apiGet = (url) => page.evaluate((u) => fetch(u, { headers: { Authorization: `Bearer ${localStorage.getItem("dds.token")}` } }).then((r) => r.json()), url);
const chip = (scope, name) => scope.getByRole("group", { name: "결과(출력) 열" }).getByRole("button", { name: new RegExp(`^${name}`) });
const noFilePick = async (where) => {
  // 회사의 표 파일은 암호화되어 있어 표를 파일로 받는 입구를 두지 않는다
  if (await page.locator(".starters-data input[type=file], .study-bar input[type=file], .modal input[type=file], .empty-hero input[type=file]").count()) throw new Error(`${where}에 표 파일 선택이 남아 있습니다`);
};

let bracketStudy = "";
await step("표로 바로 Simulator", async () => {
  await page.goto(BASE + "/");
  await noFilePick("첫 화면");
  await page.locator(".starters-data").getByRole("button", { name: /표로 바로 Simulator/ }).click();
  const dlg = page.getByRole("dialog", { name: "표로 바로 Simulator" });
  const box = dlg.getByLabel("표 붙여넣기");
  await box.waitFor({ timeout: 5000 });
  await noFilePick("붙여넣기 창");
  await shot("table-paste");
  const before = (await apiGet("/api/projects")).length;
  await pasteInto(box, bracketTsv());
  await chip(dlg, "최대 응력").waitFor({ timeout: 15000 });
  // 알아서 읽었는가: 단위·퍼센트·추측. 묻는 것은 출력뿐이다
  for (const name of ["최대 응력", "효율", "질량"]) if ((await chip(dlg, name).getAttribute("aria-pressed")) !== "true") throw new Error(`'${name}'이(가) 출력으로 미리 골라져 있지 않습니다`);
  for (const name of ["두께", "폭"]) if ((await chip(dlg, name).getAttribute("aria-pressed")) !== "false") throw new Error(`'${name}'은(는) 입력이어야 합니다`);
  if (await dlg.locator("select, .seg").count()) throw new Error("열마다 고르는 칸이 남아 있습니다");
  if (await chip(dlg, "번호").count() || await chip(dlg, "하중").count()) throw new Error("순번·상수 열을 물어봅니다");
  await shot("table-question");
  await dlg.locator(".ti-note summary").click();
  const note = await dlg.locator(".ti-note").innerText();
  for (const want of ["자릿수", "하중=1000", "병합", "수식 오류", "번호"]) if (!note.includes(want)) throw new Error(`인식한 내용에 '${want}'이(가) 없습니다: ${note}`);
  await shot("table-note");
  if ((await apiGet("/api/projects")).length !== before) throw new Error("붙여 넣기만 했는데 프로젝트가 생겼습니다");
  // 출력을 다 빼면 이유를 보이고 막는다
  for (const name of ["최대 응력", "효율", "질량"]) await chip(dlg, name).click();
  await dlg.getByText("결과(출력) 열을 하나 이상").waitFor({ timeout: 3000 });
  if (await dlg.getByRole("button", { name: "Simulator 만들기" }).isEnabled()) throw new Error("출력이 없는데 만들기가 눌립니다");
  for (const name of ["최대 응력", "효율", "질량"]) await chip(dlg, name).click();
  // 버튼 하나로 등록·학습·배포까지 하고 Simulator 탭이 열린다
  await dlg.getByRole("button", { name: "Simulator 만들기" }).click();
  await page.waitForURL(/\/projects\/\d+/, { timeout: 180000 });
  await page.locator(".sim-tab .model-trust").waitFor({ timeout: 15000 });
  await page.waitForTimeout(2500);
  await shot("table-simulator-tab");
  const trust = await page.locator(".sim-tab .model-trust").innerText();
  // #DIV/0! 행은 다른 출력에 값이 있어 등록되고, 그 출력을 학습할 때만 빠진다. 고른 출력은 모델에서 사라지지 않는다
  for (const want of ["불러온 표 31행", "최대 응력 R²", "효율 R²", "질량 R²"]) if (!trust.includes(want)) throw new Error(`Simulator에 '${want}'이(가) 보이지 않습니다: ${trust}`);
  await page.getByRole("tab", { name: "데이터" }).click();
  await page.locator(".case-table").waitFor({ timeout: 8000 });
  await noFilePick("데이터 탭");
  await shot("table-data-tab");
  const io = await page.locator(".io-block").innerText();
  for (const want of ["mm", "재질=SS400", "하중=1000", "효율", "[%]"]) if (!io.includes(want)) throw new Error(`입력·출력에 '${want}'이(가) 없습니다: ${io}`);
  await page.getByRole("tab", { name: "작업 흐름" }).click();
  await page.waitForTimeout(800);
  await shot("table-flow-tab");
  if (!(await page.locator(".flow-none .art-card").count())) throw new Error("작업 흐름 없이 만든 결과가 보이지 않습니다");
  bracketStudy = (await apiGet(`/api/projects/${page.url().match(/projects\/(\d+)/)[1]}/studies`))[0].name;

  // 데이터 탭에서 나쁜 표: 500 없이 이유와 할 일
  await page.getByRole("tab", { name: "데이터" }).click();
  expect400 = true;
  const open = () => page.locator(".study-bar").getByRole("button", { name: /표로 바로 Simulator/ }).click();
  await open();
  await pasteInto(dlg.getByLabel("표 붙여넣기"), "이것은\n표가\n아닙니다");
  await dlg.getByRole("alert").waitFor({ timeout: 15000 });
  await shot("table-bad");
  if (!(await dlg.getByRole("alert").innerText()).includes("→")) throw new Error("나쁜 표 오류에 할 일이 없습니다");
  await dlg.getByRole("button", { name: "다시 붙여넣기" }).click();
  await pasteInto(dlg.getByLabel("표 붙여넣기"), "두께\t응력\n1\t2\n");
  await dlg.getByText("3행 이상이 필요합니다").waitFor({ timeout: 15000 });
  await dlg.getByRole("button", { name: "다시 붙여넣기" }).click();
  expect400 = false;
  // 정확도가 낮은 표: 배포는 되지만 경고가 보이고, 공유할 때도 알린다
  await dlg.getByLabel("표 붙여넣기").fill(noisyTsv); // 글자로 넣은 경우는 '표 읽기'로
  await dlg.getByRole("button", { name: "표 읽기" }).click();
  await dlg.getByRole("button", { name: "Simulator 만들기" }).click();
  const warn = page.locator(".sim-tab .model-trust .notice.warn");
  await warn.waitFor({ timeout: 180000 });
  if (!(await warn.innerText()).includes("→")) throw new Error("정확도 경고에 할 일이 없습니다");
  await page.waitForTimeout(2000);
  await shot("table-poor-simulator");
  await page.locator(".sim-tab-head").getByRole("button", { name: "공유" }).click();
  await page.getByRole("button", { name: "링크 만들기" }).click();
  await page.locator(".share-warning").waitFor({ timeout: 8000 });
  await shot("table-poor-share");
  await page.keyboard.press("Escape");
  // 링크로 받은 사람도 같은 경고를 보고, 프로젝트로 가는 버튼은 없다
  const sims = await apiGet("/api/simulators");
  const noisy = sims.mine.filter((x) => x.study_name.startsWith("응력 예측")).sort((a, b) => b.model_id - a.model_id)[0];
  const token = (await apiGet(`/api/models/${noisy.model_id}/shares`))[0].token;
  await page.goto(`${BASE}/s/${token}`);
  await page.locator(".model-trust .notice.warn").waitFor({ timeout: 15000 });
  await page.waitForTimeout(2000);
  await shot("table-poor-shared-link");
  if (await page.getByRole("link", { name: /프로젝트 열기/ }).count()) throw new Error("공유 링크 화면에 프로젝트 버튼이 있습니다");
  // 쓰는 중에 공유가 끊기면 옛 숫자를 남기지 않는다: 값을 지우고 이유를 보이고 입력을 막는다
  await page.locator(".sim-out li b").first().filter({ hasNotText: "…" }).waitFor({ timeout: 15000 });
  const stopped = await page.evaluate((u) => fetch(u, { method: "DELETE", headers: { Authorization: `Bearer ${localStorage.getItem("dds.token")}` } }).then((r) => r.status), `/api/shares/${token}`);
  if (stopped >= 400) throw new Error(`공유를 중지하지 못했습니다: ${stopped}`);
  await page.locator('.sim-var input[type="number"]').first().fill(String(await page.locator('.sim-var input[type="range"]').first().getAttribute("min")));
  const gone = page.locator(".sim-failed");
  await gone.waitFor({ timeout: 8000 });
  const why = await gone.innerText();
  if (!why.includes("예측값을 지웠습니다") || !why.includes("공유가 중지") || !why.includes("→")) throw new Error(`공유 중지 안내가 이상합니다: ${why}`);
  if ((await page.locator(".sim-out li b").first().innerText()) !== "없음") throw new Error("공유가 끊겼는데 옛 예측값이 남아 있습니다");
  if (!(await page.locator('.sim-var input[type="range"]').first().isDisabled())) throw new Error("공유가 끊겼는데 입력을 막지 않았습니다");
  await shot("table-share-stopped");
});

await step("넓은 Simulator의 ±와 자세히", async () => {
  const sims = await apiGet("/api/simulators");
  const one = sims.mine.find((x) => x.study_name === bracketStudy);
  await page.goto(`${BASE}/simulator/${one.model_id}`);
  await page.locator(".sim-out li b").first().filter({ hasNotText: "…" }).waitFor({ timeout: 15000 });
  await page.locator(".trust-line").waitFor({ timeout: 15000 });
  await page.waitForTimeout(600);
  // ±는 맞춰 본 오차와 이 위치에서 AI가 말한 폭 중 큰 값이다
  const u = page.locator(".sim-out li .u").first();
  const [shown, title] = [await u.innerText(), await u.getAttribute("title")];
  const num = (s) => Number(s.replace(/,/g, ""));
  const both = [...(title ?? "").matchAll(/± ([\d.,e+-]+)/g)].map((m) => num(m[1]));
  if (both.length !== 2) throw new Error(`±의 근거가 툴팁에 없습니다: ${title}`);
  if (num(shown.match(/± ([\d.,e+-]+)/)[1]) !== Math.max(...both)) throw new Error(`±(${shown})가 큰 값이 아닙니다: ${title}`);
  await shot("simulator-wide-error-bar");
  // 넓은 화면의 자세히: 프로젝트만 여는 것이 아니라 데이터 탭의 그 항목을 펼친다
  await page.locator(".trust-line a.trust-more").click();
  await page.waitForURL(/\/projects\/\d+/, { timeout: 15000 });
  await page.locator("#insight-trust .insight-body").waitFor({ timeout: 30000 });
  if ((await page.getByRole("tab", { name: "데이터" }).first().getAttribute("aria-selected")) !== "true") throw new Error("자세히가 데이터 탭을 열지 않습니다");
  await shot("simulator-more-trust");
});

await step("넓은 Simulator에서 프로젝트로 돌아오기", async () => {
  await page.goto(BASE + "/simulators");
  await page.waitForTimeout(1000);
  await shot("table-simulators");
  // 카드(기본)와 목록 모두 어디서 온 데이터인지 보인다
  if (!(await page.locator(".sim-card").filter({ hasText: "불러온 표 31행" }).count())) throw new Error("Simulator 카드에 데이터 출처가 없습니다");
  await page.getByRole("button", { name: "목록" }).click();
  if (!(await page.locator(".sim-row").filter({ hasText: "불러온 표 31행" }).count())) throw new Error("Simulator 목록에 데이터 출처가 없습니다");
  if (!(await page.locator(".sim-row .chip.err").filter({ hasText: "정확도 낮음" }).count())) throw new Error("Simulator 목록에 낮은 정확도 표시가 없습니다");
  await page.getByRole("button", { name: "카드" }).click();
  if (!(await page.locator(".sim-card .chip.err").filter({ hasText: "정확도 낮음" }).count())) throw new Error("Simulator 카드에 낮은 정확도 표시가 없습니다");
  const sims = await apiGet("/api/simulators");
  const bracket = sims.mine.find((x) => x.study_name === bracketStudy);
  // 현재 버전이 아닌 v2를 넓게 본 뒤 돌아와도 그 버전이 골라져 있어야 한다. 캔버스를 닫아 둔 경우까지 본다
  const v2 = await page.evaluate(async (sid) => {
    const h = { Authorization: `Bearer ${localStorage.getItem("dds.token")}`, "Content-Type": "application/json" };
    let job = await fetch(`/api/studies/${sid}/train/start`, { method: "POST", headers: h, body: JSON.stringify({ force: true }) }).then((r) => r.json());
    while (job.status === "running") { await new Promise((r) => setTimeout(r, 500)); job = await fetch(`/api/train-jobs/${job.job_id}`, { headers: h }).then((r) => r.json()); }
    return job.model;
  }, bracket.study_id);
  await page.evaluate(() => localStorage.setItem("dds.canvas.layout", JSON.stringify({ collapsed: true, width: 560 })));
  await page.goto(`${BASE}/simulator/${v2.id}`);
  const back = page.getByRole("link", { name: /프로젝트 열기/ });
  await back.waitFor({ timeout: 8000 });
  if (!(await back.getAttribute("title")).includes("Simulator 탭")) throw new Error("버튼 설명에 Simulator 탭이 없습니다");
  await back.click();
  await page.waitForURL(/\/projects\/\d+/, { timeout: 15000 });
  await page.locator(".sim-tab").waitFor({ timeout: 15000 });
  const selected = page.getByRole("tab", { name: "Simulator" });
  if ((await selected.getAttribute("aria-selected")) !== "true") throw new Error("Simulator 탭이 열리지 않았습니다");
  const study = await page.locator(".canvas .seg button.on").first().innerText(); // 첫 번째가 스터디 선택, 뒤는 민감도 변수 선택
  if (study !== bracketStudy) throw new Error(`다른 스터디가 골라졌습니다: ${study}`);
  const ver = await page.locator(".sim-ver-toggle b").innerText();
  if (ver !== `v${v2.version}`) throw new Error(`다른 버전이 골라졌습니다: ${ver}`);
  await page.waitForTimeout(1500);
  await shot("simulator-back-to-tab");
  // 자동 따라가기에 밀려나지 않는다: 새 결과가 생겨도 Simulator 탭 그대로
  const here = page.url();
  await page.waitForTimeout(1500);
  if ((await selected.getAttribute("aria-selected")) !== "true") throw new Error("도착한 뒤 탭이 바뀌었습니다");
  // 값이 남아 있지 않다: 새로고침하면 평범한 프로젝트 화면이다
  await page.reload();
  await page.getByRole("tab", { name: "작업 흐름" }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(800);
  if ((await page.getByRole("tab", { name: "작업 흐름" }).getAttribute("aria-selected")) !== "true") throw new Error(`새로고침해도 Simulator 탭이 강제됩니다: ${here}`);

  // 대화가 있는 프로젝트는 최근 대화 주소로 한 번 더 옮겨 간다. 그래도 Simulator 탭을 잃지 않는다
  const req = (method, url, body) => page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { Authorization: `Bearer ${localStorage.getItem("dds.token")}`, "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, json: await r.json().catch(() => null) };
  }, [method, url, body]);
  const chat = await req("POST", `/api/projects/${bracket.project_id}/sessions`, {});
  if (chat.status >= 400) { console.log(`  (대화를 만들지 못해 건너뜀: ${chat.status})`); return; }
  const arrivedOnChat = async (what) => {
    await page.waitForURL(/\/projects\/\d+\/c\/\d+/, { timeout: 15000 });
    await page.locator(".sim-tab .sim-var").first().waitFor({ timeout: 15000 });
    await page.waitForTimeout(800); // 옮겨 간 뒤에 탭이 바뀌지 않는지 본다
    if ((await page.getByRole("tab", { name: "Simulator" }).getAttribute("aria-selected")) !== "true") throw new Error(`${what}: Simulator 탭이 아닙니다`);
    if (!(await page.locator(".sim-tab .sim-var").count())) throw new Error(`${what}: Simulator 입력이 없습니다`);
  };
  await page.goto(`${BASE}/simulator/${v2.id}`);
  await back.click();
  await arrivedOnChat("프로젝트 열기");
  await shot("simulator-back-with-chat");
  const again = await page.locator(".sim-ver-toggle b").innerText();
  if (again !== `v${v2.version}`) throw new Error(`대화가 있는 프로젝트에서 다른 버전이 골라졌습니다: ${again}`);
  await page.reload();
  await page.getByRole("tab", { name: "작업 흐름" }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(800);
  if ((await page.getByRole("tab", { name: "작업 흐름" }).getAttribute("aria-selected")) !== "true") throw new Error("대화가 있는 프로젝트에서 새로고침해도 Simulator 탭이 강제됩니다");
  // 끝난 표 작업 알림의 '열기'도 같은 길이다. 다른 화면에서 끝난 작업을 띄워 누른다
  const rows = ["얇기\t너비\t수명", ...Array.from({ length: 14 }, (_, i) => `${1 + i * 0.3}\t${10 + ((i * 7) % 11)}\t${(100 + 9 * i + ((i * 7) % 11) * 4).toFixed(1)}`)].join("\n");
  const job = await req("POST", "/api/table-jobs", { project_id: bracket.project_id, text: rows, name: "수명 예측", outputs: [{ name: "수명" }] });
  if (job.status >= 400) throw new Error(`표 작업을 시작하지 못했습니다: ${JSON.stringify(job.json)}`);
  await page.goto(BASE + "/simulators");
  const opener = page.locator(".table-job.done").filter({ hasText: "수명 예측" }).getByRole("button", { name: "열기" });
  await opener.waitFor({ timeout: 60000 });
  await opener.click();
  await arrivedOnChat("알림의 열기");
  await req("DELETE", `/api/sessions/${chat.json.id}`); // 뒤의 단계는 대화 없는 프로젝트를 본다
});

await step("입력창에 표 붙여넣기", async () => {
  await page.goto(BASE + "/");
  const input = page.getByLabel("에이전트에게 요청");
  await input.waitFor({ timeout: 8000 });
  await pasteInto(input, noisyTsv.replace("응력", "처짐"));
  const inline = page.locator(".composer-table");
  await chip(inline, "처짐").waitFor({ timeout: 15000 });
  await shot("composer-table");
  if (await input.inputValue()) throw new Error("표가 요청 글로 들어갔습니다");
  if (await page.locator(".composer-files .file-chip").count()) throw new Error("엑셀 복사의 그림이 첨부되었습니다");
  await inline.getByRole("button", { name: "Simulator 만들기" }).click();
  await page.waitForURL(/\/projects\/\d+/, { timeout: 180000 });
  await page.locator(".sim-tab .model-trust").waitFor({ timeout: 15000 });
  await shot("composer-table-simulator");
  // 프로젝트 안 입력창: PC Agent가 없어 요청은 못 보내도 표는 붙여 넣는다. disabled면 실제 붙여넣기 이벤트가 오지 않으므로 합성 이벤트만 믿지 않고 속성도 본다
  const inside = page.getByLabel("에이전트에게 요청");
  await inside.waitFor({ timeout: 15000 });
  if (await inside.isDisabled()) throw new Error("입력창이 잠겨 표를 붙여 넣을 수 없습니다");
  await pasteInto(inside, noisyTsv.replace("응력", "변위"));
  await chip(page.locator(".composer-table"), "변위").waitFor({ timeout: 15000 });
  await shot("composer-table-in-project");
  await page.locator(".composer-table").getByRole("button", { name: "취소" }).click();
});

if (INSIGHTS) {
  await step("판단 도우미", async () => {
    await page.goto(BASE + "/");
    await page.getByTitle("사용할 AI 에이전트").click();
    await page.getByRole("option", { name: /데모/ }).click();
    const input = page.getByLabel("에이전트에게 요청");
    await input.fill("세탁기 진동과 바닥 전달력을 두 목표로 능동학습까지 해서 보고서를 써 줘");
    await input.press("Enter");
    await page.waitForURL(/\/projects\/\d+\/c\/\d+/, { timeout: 30000 });
    await page.getByRole("button", { name: /승인하고 실행/ }).first().click({ timeout: 90000 });
    await page.getByRole("button", { name: "허용" }).click({ timeout: 90000 });
    await page.getByText("두 목표 최적화와 능동학습을 마쳤습니다").waitFor({ timeout: 420000 });
    await page.getByRole("tab", { name: "데이터" }).first().click();
    for (const q of ["지금 어디까지 왔나요?", "설계 범위를 고르게 살폈나요?", "무엇이 결과를 좌우하나요?", "이 예측을 믿어도 되나요?", "더 돌리면 좋아질까요?", "두 목표를 함께 좋게 할 수 있나요?", "정확도를 높이려고 더 돌린 해석이 효과가 있었나요?"]) {
      await page.getByText(q, { exact: false }).first().waitFor({ timeout: 20000 });
    }
    await page.locator(".insight-sec .chip", { hasText: "계산 중" }).first().waitFor({ state: "detached", timeout: 60000 }).catch(() => {});
    await shot("insights-closed");
    await page.setViewportSize({ width: 1440, height: 1600 }); // 펼친 그림 전체가 한 장에 들어오게
    const heads = page.locator(".insight-head:not([disabled])");
    for (let i = 0; i < (await heads.count()); i++) {
      await heads.nth(i).click();
      const name = (await heads.nth(i).getAttribute("aria-expanded")) === "true" ? i : `${i}-closed`;
      await page.waitForTimeout(500);
      await heads.nth(i).scrollIntoViewIfNeeded();
      await shot(`insights-open-${name}`);
      await heads.nth(i).click();
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    if (await page.locator(".insight-sec .js-plotly-plot").count()) throw new Error("접으면 그림이 사라져야 합니다");
    // 이 시나리오는 단위를 note에만 적는다("스프링 강성 [%]"). 입력 칸에도 단위가 보여야 한다
    await page.getByRole("tab", { name: "Simulator" }).first().click();
    await page.locator(".sim-tab .sim-var-unit").first().waitFor({ timeout: 15000 });
    await shot("insights-simulator-units");
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

// 파일을 내려받은 뒤 브라우저를 닫으면 playwright 내부에서 이 거절이 처리되지 않은 채 올라와 프로세스가 죽는다. 점검 결과와 상관없으므로 닫는 동안만 무시한다
process.on("unhandledRejection", (e) => { if (!/Target page, context or browser has been closed/.test(String(e))) throw e; });
await browser.close();
console.log(`\n스크린샷: ${OUT}`);
if (errors.length) {
  console.log(`\nFAIL — 화면 오류 ${errors.length}건`);
  for (const e of errors) console.log("  " + e);
  process.exit(1);
}
console.log("\nPASS — 화면 오류 없음");
