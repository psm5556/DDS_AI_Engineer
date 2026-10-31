// 열이 많거나 이름이 긴 표에서 화면이 밀리지 않는지 재는 점검. 서버만 떠 있으면 된다(AI·PC Agent 없음).
//   node e2e/table_layout.mjs       환경변수: DDS_URL · DDS_USER · DDS_PIN · DDS_SHOTS
// 마지막 줄이 PASS면 통과. 측정값은 JSON으로 찍는다.
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.DDS_URL ?? "http://127.0.0.1:7014";
const OUT = process.env.DDS_SHOTS ?? path.join(import.meta.dirname, "shots");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ channel: process.env.DDS_BROWSER === "chrome" ? "chrome" : "msedge", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "ko-KR" });
const page = await ctx.newPage();
const fails = [];
const out = {};
const check = (ok, label) => { console.log(`  ${ok ? "ok" : "실패"} ${label}`); if (!ok) fails.push(label); };
const shot = (name) => page.screenshot({ path: path.join(OUT, `layout-${name}.png`), animations: "disabled" });

await page.goto(BASE + "/login");
await page.getByLabel("아이디").fill(process.env.DDS_USER ?? "admin");
await page.getByLabel("PIN 1번째 자리").click();
await page.keyboard.type(process.env.DDS_PIN ?? "123456");
await page.waitForURL(BASE + "/", { timeout: 15000 });
await page.waitForTimeout(800);
const token = await page.evaluate((k) => localStorage.getItem(k) ?? "", "dds.token");
const api = async (method, url, body) => {
  const r = await page.request.fetch(BASE + url, { method, data: body, headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok()) throw new Error(`${method} ${url}: ${r.status()} ${await r.text()}`);
  return r.json();
};
const rand = (seed) => { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; };
const table = (names, n, seed = 7) => {
  const r = rand(seed);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const xs = names.slice(0, -1).map(() => (1 + r() * 9).toFixed(4));
    const y = xs.slice(0, 3).reduce((a, b, k) => a + (k + 1) * Number(b), 100).toFixed(5);
    rows.push([...xs, y].join("\t"));
  }
  return `${names.join("\t")}\n${rows.join("\n")}\n`;
};
const overflow = () => page.evaluate(() => {
  const body = document.querySelector(".canvas-body");
  return { doc: document.documentElement.scrollWidth, win: innerWidth, canvas: body ? body.scrollWidth : 0, canvasClient: body ? body.clientWidth : 0 };
});
const paste = (boxLabel, text) => page.getByLabel(boxLabel).evaluate((el, t) => {
  const data = new DataTransfer();
  data.setData("text/plain", t);
  el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}, text);

try {
  // 1) 60열 표를 프로젝트 입력 흐름으로 만들어 Simulator 폭을 잰다
  const names60 = [...Array.from({ length: 59 }, (_, i) => `플랜지두께_${i + 1}`), "최대응력"];
  const p60 = await api("POST", "/api/projects", { name: "열 많은 표" });
  const made = await api("POST", `/api/projects/${p60.id}/studies/import/text`, { text: table(names60, 120), name: "60열", outputs: [{ name: "최대응력" }] });
  let job = await api("POST", `/api/studies/${made.study_id}/train/start`, { outputs: ["최대응력"] });
  while (job.status === "running") { await page.waitForTimeout(1500); job = await api("GET", `/api/train-jobs/${job.job_id}`); }
  const m60 = await api("POST", `/api/models/${job.model.id}/deploy`, {});
  out.r2_59_inputs = m60.metrics["최대응력"].r2;
  check(m60.metrics["최대응력"].r2 > 0.8, `입력 59개 모델이 평균만 내지 않음 R² ${m60.metrics["최대응력"].r2.toFixed(3)}`);
  await page.goto(`${BASE}/simulator/${m60.id}`);
  await page.waitForSelector(".sim-var");
  await page.waitForTimeout(1200);
  const wide = await overflow();
  out.wide60 = wide;
  check(wide.doc <= wide.win, `넓은 화면 가로 스크롤 없음 ${wide.doc}/${wide.win}`);
  const outY = await page.locator(".sim-out").boundingBox();
  out.resultsY60 = outY?.y;
  check(!!outY && outY.y < 900, `변수가 많아도 예측 결과가 첫 화면에 보임 y=${outY?.y}`);
  check(await page.locator(".sim-sweep").count() === 1, "민감도 변수는 목록으로 고름");
  await shot("wide60");
  await page.goto(`${BASE}/projects/${p60.id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".canvas-tabs, .canvas", { timeout: 15000 });
  await page.getByRole("tab", { name: /Simulator/ }).first().click().catch(async () => page.getByRole("button", { name: /^Simulator/ }).first().click());
  await page.waitForSelector(".sim-var");
  await page.waitForTimeout(800);
  const tab = await overflow();
  out.tab60 = tab;
  check(tab.canvas <= tab.canvasClient + 1, `탭 가로 넘침 없음 ${tab.canvas}/${tab.canvasClient}`);
  await shot("tab60");

  // 2) 8변수 긴 이름: 민감도 버튼이 패널을 밀지 않는다
  const names8 = [...Array.from({ length: 6 }, (_, i) => `플랜지 두께 ${i + 1}`), "최대응력"];
  const made6 = await api("POST", `/api/projects/${p60.id}/studies/import/text`, { text: table(names8, 40, 11), name: "6변수", outputs: [{ name: "최대응력" }] });
  const v6 = await api("POST", `/api/studies/${made6.study_id}/train`, { outputs: ["최대응력"] });
  await api("POST", `/api/models/${v6.id}/deploy`, {});
  await page.goto(`${BASE}/simulator/${v6.id}`);
  await page.waitForSelector(".sim-var");
  await page.waitForTimeout(600);
  const six = await overflow();
  check(six.doc <= six.win, `6변수 긴 이름 가로 스크롤 없음 ${six.doc}/${six.win}`);

  // 3) 열 고르기 질문: 버튼이 화면 안에 있고 칩이 패널을 넘지 않는다
  await page.goto(`${BASE}/projects/${p60.id}`);
  await page.waitForSelector(".canvas");
  await page.getByRole("tab", { name: /데이터/ }).first().click().catch(async () => page.getByRole("button", { name: /^데이터/ }).first().click());
  await page.locator(".study-bar").getByRole("button", { name: /표로 바로 Simulator/ }).click();
  await paste("표 붙여넣기", table(names60, 12, 13));
  const dlg = page.getByRole("dialog", { name: "표로 바로 Simulator" });
  await dlg.getByRole("button", { name: /Simulator 만들기/ }).waitFor();
  const btn = await dlg.getByRole("button", { name: /Simulator 만들기/ }).boundingBox();
  out.makeButtonY = btn?.y;
  check(!!btn && btn.y + btn.height <= 900, `60열 표에서도 만들기 버튼이 화면 안 y=${btn?.y}`);
  const firstChip = await dlg.locator(".ti-chip").first().innerText();
  check(firstChip.includes("최대응력"), "추측한 출력 칩이 맨 앞");
  await shot("question60");
  await dlg.getByRole("button", { name: "취소" }).click();

  const longNames = [...Array.from({ length: 3 }, (_, i) => `아주 긴 이름의 설계 변수로 칩이 패널 밖으로 나가는지 재기 위한 열 이름 번호 ${i + 1}`), "최대응력"];
  await page.locator(".study-bar").getByRole("button", { name: /표로 바로 Simulator/ }).click();
  await paste("표 붙여넣기", table(longNames, 8, 17));
  await dlg.getByRole("button", { name: /Simulator 만들기/ }).waitFor();
  const chips = await dlg.locator(".ti-chip").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().right));
  const box = await dlg.boundingBox();
  out.chipRight = Math.max(...chips);
  check(Math.max(...chips) <= box.x + box.width, `긴 이름 칩이 창 안 ${Math.max(...chips)}/${box.x + box.width}`);
  await dlg.getByRole("button", { name: "취소" }).click();

  // 4) 만들기를 막는 문구는 진짜 이유를 말한다
  const blocked = async (text) => {
    await page.locator(".study-bar").getByRole("button", { name: /표로 바로 Simulator/ }).click();
    await paste("표 붙여넣기", text);
    await dlg.getByRole("button", { name: /Simulator 만들기/ }).waitFor();
    const msg = await dlg.locator(".ti-reason").innerText();
    await dlg.getByRole("button", { name: "취소" }).click();
    return msg;
  };
  const allNa = await blocked("두께\t폭\t응력\n1\t2\t#N/A\n2\t3\t#DIV/0!\n3\t5\t#N/A\n4\t6\t#N/A\n");
  out.allNa = allNa;
  check(allNa.includes("응력") && allNa.includes("수식 오류") && allNa.includes("→"), `수식 오류 열: ${allNa}`);
  const sameIn = await blocked("두께\t폭\t응력\n3\t2\t10\n3\t2\t11\n3\t2\t12\n3\t2\t14\n");
  out.sameIn = sameIn;
  check(sameIn.includes("값이 같습니다") && !sameIn.includes("입력 열을 빼고"), `입력이 상수: ${sameIn}`);
  const allSame = await blocked("두께\t폭\t응력\n3\t2\t10\n3\t2\t10\n3\t2\t10\n3\t2\t10\n");
  out.allSame = allSame;
  check(allSame.includes("값이 변하는 숫자 열이 없습니다"), `전부 상수: ${allSame}`);
  const sameOut = await blocked("두께\t폭\t응력\n1\t2\t10\n2\t3\t10\n3\t4\t10\n4\t6\t10\n");
  out.sameOut = sameOut;
  check(sameOut.includes("응력") && sameOut.includes("예측할 것이 없습니다"), `출력이 상수: ${sameOut}`);

  // 5) 불러온 행의 출처 칸은 한 줄
  const row = await page.locator("tbody tr").filter({ hasText: "불러옴" }).first();
  await row.waitFor();
  const h = await row.boundingBox();
  const cell = await row.locator("td").nth(1).boundingBox();
  out.sourceCell = { row: h.height, width: cell.width };
  check(h.height < 45, `출처 칸이 세로로 꺾이지 않음 행 높이 ${h.height}`);
  await shot("data-row");

  // 5-1) 입력창에 붙여 넣어도 만들기 버튼이 화면 안에 있고 작업 공간 틀이 밀리지 않는다
  for (const [label, url] of [["프로젝트", `${BASE}/projects/${p60.id}`], ["첫 화면", `${BASE}/`]]) {
    await page.goto(url);
    const input = page.getByLabel("에이전트에게 요청");
    await input.waitFor({ timeout: 15000 });
    await paste("에이전트에게 요청", table(names60, 12, 23));
    const inline = page.locator(".composer-table");
    const make = inline.getByRole("button", { name: /Simulator 만들기/ });
    await make.waitFor({ timeout: 15000 });
    await page.waitForTimeout(500);
    const b = await make.boundingBox();
    const size = await page.evaluate(() => ({ doc: document.documentElement.scrollHeight, y: scrollY }));
    out[`composer_${label}`] = { y: b?.y, doc: size.doc, scrollY: size.y };
    check(!!b && b.y + b.height <= 900 && size.y === 0 && (label !== "프로젝트" || size.doc <= 900), `${label} 입력창: 60열에서 버튼 y=${Math.round(b?.y ?? -1)}, 문서 높이 ${size.doc}, 틀 밀림 ${size.y}`);
    const chipsRight = await inline.locator(".ti-chip").evaluateAll((els) => Math.max(...els.map((e) => e.getBoundingClientRect().right)));
    const inlineBox = await inline.boundingBox();
    check(chipsRight <= inlineBox.x + inlineBox.width + 1, `${label} 입력창: 칩이 입력창 안`);
    await shot(`composer60-${label === "프로젝트" ? "project" : "home"}`);
  }

  // 6) 되돌린 버전을 같은 표 다시 붙여넣기가 바꾸지 않는다
  const pr = await api("POST", "/api/projects", { name: "되돌림" });
  const text = table(["두께", "폭", "응력"], 20, 19);
  const a = await api("POST", `/api/projects/${pr.id}/studies/import/text`, { text, name: "되돌림", outputs: [{ name: "응력" }] });
  const v1 = await api("POST", `/api/studies/${a.study_id}/train`, { outputs: ["응력"] });
  await api("POST", `/api/models/${v1.id}/deploy`, {});
  await api("POST", `/api/studies/${a.study_id}/cases/import`, { rows: [{ 두께: 2.2, 폭: 3.3, 응력: 9999 }] });
  const v2 = await api("POST", `/api/studies/${a.study_id}/train`, { outputs: ["응력"] });
  await api("POST", `/api/models/${v2.id}/deploy`, {});
  await api("POST", `/api/models/${v1.id}/deploy`, {});
  await page.goto(`${BASE}/projects/${pr.id}`);
  await page.waitForSelector(".canvas");
  await page.getByRole("tab", { name: /데이터/ }).first().click().catch(async () => page.getByRole("button", { name: /^데이터/ }).first().click());
  await page.locator(".study-bar").getByRole("button", { name: /표로 바로 Simulator/ }).click();
  await paste("표 붙여넣기", text);
  await dlg.getByRole("button", { name: /Simulator 만들기/ }).click();
  await page.waitForTimeout(2500);
  const models = await api("GET", `/api/projects/${pr.id}/models`);
  const live = models.find((x) => x.deployed);
  out.liveAfterRepaste = live?.version;
  check(live?.id === v1.id, `되돌린 v1이 그대로 현재 버전 (v${live?.version})`);

  // 7) 행 추가 화면
  await page.getByRole("tab", { name: /데이터/ }).first().click().catch(async () => page.getByRole("button", { name: /^데이터/ }).first().click());
  await page.locator(".study-bar").getByRole("button", { name: /^행 추가/ }).click();
  const add = page.getByRole("dialog", { name: /행 추가/ });
  await paste("더할 행 붙여넣기", "두께\t폭\t응력\n2.5\t3.5\t130.5\n3.5\t4.5\t140.5\n");
  await add.getByRole("button", { name: /^행 추가/ }).click();
  await add.getByRole("button", { name: /다시 학습해 배포/ }).waitFor();
  const summary = await add.locator(".ti-summary").innerText();
  out.addRows = summary;
  check(summary.includes("새 행 2개"), `행 추가 결과: ${summary}`);
  await shot("add-rows");
  await add.getByRole("button", { name: "지금은 등록만" }).click();
} catch (e) {
  fails.push(String(e).split("\n")[0]);
  await shot("error").catch(() => {});
}
console.log(JSON.stringify(out));
await browser.close();
console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" | ")}` : "PASS");
process.exit(fails.length ? 1 : 0);
