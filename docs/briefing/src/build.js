// DDS AI Engineer 경영진 보고 슬라이드 (13.33 x 7.5in). 사람이 하는 일 = 주황, AI 조수가 하는 일 = 청록 — 모든 장에서 같은 뜻
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || path.join(__dirname, "..", "DDS_AI_Engineer_경영진보고.pptx");
const A = (f) => "image/png;base64," + fs.readFileSync(path.join(__dirname, "assets", f)).toString("base64");
const icon = (name, color) => A(`${name}_${color}.png`);

const C = {
  bg: "F4F7F8", white: "FFFFFF", ink: "15232D", muted: "586A75", line: "D7E1E5", faint: "C3CFD5",
  ai: "0C7882", aiSoft: "DFF1F2", human: "D9671F", humanSoft: "FCEADB", ok: "23905A", okSoft: "DDF2E5",
  dark: "15232D", dark2: "1E3240", darkLine: "2E4756", darkText: "B8C7CE", aiL: "45BCC6", humanL: "F29A5A",
};
const F = "Malgun Gothic";
const W = 13.333, M = 0.6, CW = W - 2 * M;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = "DDS AI Engineer 경영진 보고";
pres.author = "DDS AI Engineer";

// ---------- 공통 도구 ----------
const shadow = () => ({ type: "outer", color: "000000", opacity: 0.07, blur: 8, offset: 2, angle: 90 });
function text(slide, t, o) {
  slide.addText(t, { fontFace: F, margin: 0, valign: "top", color: C.ink, fontSize: 14, ...o });
}
function card(slide, x, y, w, h, o = {}) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: o.r ?? 0.14, fill: { color: o.fill ?? C.white },
    line: { color: o.line ?? C.line, width: o.lw ?? 1, ...(o.dash ? { dashType: "dash" } : {}) },
    ...(o.shadow === false || o.fill ? {} : { shadow: shadow() }),
  });
}
function circleIcon(slide, x, y, d, name, color, bg, line) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: bg }, line: { color: line ?? bg, width: 1 } });
  const p = d * 0.56;
  slide.addImage({ data: icon(name, color), x: x + (d - p) / 2, y: y + (d - p) / 2, w: p, h: p });
}
function squareIcon(slide, x, y, d, name, color, bg) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: d, h: d, rectRadius: 0.12, fill: { color: bg }, line: { color: bg, width: 1 } });
  const p = d * 0.6;
  slide.addImage({ data: icon(name, color), x: x + (d - p) / 2, y: y + (d - p) / 2, w: p, h: p });
}
function chip(slide, x, y, label, kind, dark = false) {
  const w = 0.22 + label.length * 0.135;
  const fill = kind === "ai" ? (dark ? "12333A" : C.aiSoft) : kind === "ok" ? C.okSoft : (dark ? "3A2516" : C.humanSoft);
  const color = kind === "ai" ? (dark ? C.aiL : C.ai) : kind === "ok" ? C.ok : (dark ? C.humanL : C.human);
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 0.3, rectRadius: 0.15, fill: { color: fill }, line: { color: fill, width: 1 } });
  text(slide, label, { x, y, w, h: 0.3, fontSize: 11, bold: true, color, align: "center", valign: "middle" });
  return w;
}
function head(slide, eyebrow, title, sub) {
  slide.background = { color: C.bg };
  text(slide, eyebrow, { x: M, y: 0.42, w: CW, h: 0.3, fontSize: 12, bold: true, color: C.muted, charSpacing: 1 });
  text(slide, title, { x: M, y: 0.72, w: CW, h: 0.7, fontSize: 30, bold: true, color: C.ink });
  if (sub) text(slide, sub, { x: M, y: 1.42, w: CW, h: 0.4, fontSize: 15, color: C.muted });
}
function arrowRight(slide, x, y, color = C.faint) {
  slide.addShape(pres.shapes.RIGHT_ARROW, { x, y, w: 0.34, h: 0.3, fill: { color }, line: { color, width: 1 } });
}

// ---------- 1. 표지: 한 문장 + 큰 흐름 ----------
{
  const s = pres.addSlide();
  s.background = { color: C.dark };
  text(s, "DDS AI Engineer  ·  경영진 보고  ·  2026년 10월", { x: 0.7, y: 0.6, w: 11, h: 0.35, fontSize: 13, bold: true, color: "9DB0BA", charSpacing: 1 });
  text(s, [
    { text: "말로 부탁하면, ", options: { color: C.white } },
    { text: "AI 조수", options: { color: C.aiL } },
    { text: "가", options: { color: C.white, breakLine: true } },
    { text: "설계 실험을 대신 해 줍니다", options: { color: C.white } },
  ], { x: 0.7, y: 1.05, w: 12, h: 1.75, fontSize: 44, bold: true, lineSpacingMultiple: 1.05 });
  text(s, "엔지니어가 “이 부품을 더 가볍게 만들어 줘”라고 말하면, AI 조수가 회사 PC의 설계·해석 프로그램을 직접 움직여 여러 번 실험하고, 가장 좋은 설계와 보고서를 돌려줍니다.",
    { x: 0.7, y: 2.95, w: 11.2, h: 0.75, fontSize: 17, color: "C9D6DC", lineSpacingMultiple: 1.2 });
  let cx = 0.7;
  cx += chip(s, cx, 3.85, "사람이 하는 일", "human", true) + 0.12;
  chip(s, cx, 3.85, "AI 조수가 하는 일", "ai", true);
  const nodes = [
    ["chat", "humanL", "3A2516", "말로 부탁", "엔지니어가 평소 말로 요청합니다. 명령어를 몰라도 됩니다."],
    ["bot", "aiL", "12333A", "AI 조수가 계획", "할 일 순서를 그림으로 보여 주고, 사람이 OK하면 시작합니다."],
    ["pc", "inkL", "2A3F4D", "프로그램이 계산", "회사가 이미 쓰는 설계 프로그램(NX, Abaqus 등)을 AI가 대신 조작합니다."],
    ["trophy", "okL", "133426", "가장 좋은 답", "최적 설계, 3D로 미리 보는 시뮬레이터, 보고서를 받습니다."],
  ];
  const bw = 2.68, gap = 0.47, y = 4.5, h = 2.4;
  nodes.forEach(([ic, col, bg, t, d], i) => {
    const x = 0.7 + i * (bw + gap);
    card(s, x, y, bw, h, { fill: C.dark2, line: C.darkLine });
    circleIcon(s, x + 0.25, y + 0.25, 0.72, ic, col, bg);
    text(s, t, { x: x + 0.25, y: y + 1.1, w: bw - 0.45, h: 0.38, fontSize: 17, bold: true, color: C.white });
    text(s, d, { x: x + 0.25, y: y + 1.5, w: bw - 0.45, h: 0.68, fontSize: 12.5, color: C.darkText, lineSpacingMultiple: 1.15 });
    if (i < 3) arrowRight(s, x + bw + (gap - 0.34) / 2, y + h / 2 - 0.15, "6E828D");
  });
  s.addNotes("이 시스템을 한 문장으로 말씀드리면, 엔지니어가 말로 부탁하면 AI 조수가 설계 프로그램을 대신 움직여 실험하고 가장 좋은 답을 찾아 주는 도우미입니다. 주황색은 사람이 하는 일, 청록색은 AI가 하는 일입니다. 사람은 부탁하고 확인만 합니다.");
}

// ---------- 2. 지금의 어려움: 손으로 돌리는 바퀴 ----------
{
  const s = pres.addSlide();
  head(s, "1 · 지금은 무엇이 어려운가", "설계 하나 바꿀 때마다, 사람이 이 바퀴를 손으로 돌립니다");
  const cx = 3.45, cy = 4.5, r = 1.78;
  s.addShape(pres.shapes.OVAL, { x: cx - r, y: cy - r, w: 2 * r, h: 2 * r, fill: { type: "none" }, line: { color: C.humanSoft, width: 16 } });
  const labels = ["① 모양 고치기", "② 계산 준비", "③ 몇 시간 기다리기", "④ 결과 옮겨 적기", "⑤ 비교하고 다시"];
  labels.forEach((l, i) => {
    const a = (-90 + 72 * i) * Math.PI / 180;
    const w = 1.9, h = 0.44;
    const x = cx + r * Math.cos(a) - w / 2, y = cy + r * Math.sin(a) - h / 2;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.1, fill: { color: C.white }, line: { color: C.line, width: 1 } });
    text(s, l, { x, y, w, h, fontSize: 13, bold: true, color: C.human, align: "center", valign: "middle" });
    const m = (-90 + 72 * i + 36) * Math.PI / 180; // 다음 단계로 가는 방향(시계 방향)
    const d = 0.26;
    s.addShape(pres.shapes.ISOSCELES_TRIANGLE, { x: cx + r * Math.cos(m) - d / 2, y: cy + r * Math.sin(m) - d / 2, w: d, h: d,
      rotate: (-90 + 72 * i + 36) + 180, fill: { color: C.human }, line: { color: C.human, width: 1 } });
  });
  text(s, "한 바퀴마다\n사람 손이 필요", { x: cx - 1.1, y: cy - 0.42, w: 2.2, h: 0.84, fontSize: 16, bold: true, color: C.muted, align: "center", valign: "middle" });
  const probs = [
    ["오래 걸려요", "한 바퀴에 반나절에서 며칠. 그래서 설계안을 몇 개밖에 못 비교합니다."],
    ["할 줄 아는 사람이 적어요", "자동으로 돌리려면 컴퓨터 명령문(스크립트)을 짜야 해서, 일부 전문가만 할 수 있습니다."],
    ["결과가 흩어져요", "계산 결과가 개인 PC와 엑셀에 흩어져, 다음 설계에 다시 쓰기 어렵습니다."],
  ];
  probs.forEach(([t, d], i) => {
    const x = 6.75, y = 2.0 + i * 1.6, w = W - M - 6.75, h = 1.38;
    card(s, x, y, w, h);
    s.addShape(pres.shapes.OVAL, { x: x + 0.3, y: y + 0.3, w: 0.5, h: 0.5, fill: { color: C.humanSoft }, line: { color: C.humanSoft, width: 1 } });
    text(s, String(i + 1), { x: x + 0.3, y: y + 0.3, w: 0.5, h: 0.5, fontSize: 16, bold: true, color: C.human, align: "center", valign: "middle" });
    text(s, t, { x: x + 1.0, y: y + 0.27, w: w - 1.25, h: 0.38, fontSize: 18, bold: true });
    text(s, d, { x: x + 1.0, y: y + 0.68, w: w - 1.25, h: 0.6, fontSize: 13.5, color: C.muted, lineSpacingMultiple: 1.15 });
  });
  s.addNotes("지금은 설계를 하나 바꿀 때마다 사람이 이 다섯 단계를 손으로 돌립니다. 한 바퀴에 반나절에서 며칠이 걸리니 설계안을 몇 개밖에 비교하지 못하고, 자동화하려면 스크립트를 짤 줄 알아야 해서 소수 전문가만 할 수 있습니다. 결과도 개인 PC에 흩어져 다시 쓰기 어렵습니다.");
}

// ---------- 3. 식당 비유 ----------
{
  const s = pres.addSlide();
  head(s, "2 · 쉽게 비유하면", "식당과 똑같습니다", "손님은 주문만 하면 됩니다. 요리 순서를 정하고 주방을 움직이는 일은 매니저가 합니다.");
  const lx = M, lw = 5.75, ex = lx + lw, ew = 0.55, rx = ex + ew, rw = W - M - rx;
  text(s, "식당에서는", { x: lx, y: 1.98, w: lw, h: 0.3, fontSize: 13, bold: true, color: C.muted });
  text(s, "우리 시스템에서는", { x: rx, y: 1.98, w: rw, h: 0.3, fontSize: 13, bold: true, color: C.muted });
  const rows = [
    ["person", "손님이 주문", "“매콤한 걸로 주세요”", "chat", "엔지니어가 말로 부탁", "“더 가볍게, 강도는 그대로”", "human"],
    ["plan", "매니저가 주문서 확인", "“이 순서로 만들까요?”", "bot", "AI 조수가 작업 계획표", "사람이 OK해야 시작", "ai"],
    ["flask", "주방에서 요리", "여러 번 맛보며 간 맞추기", "pc", "설계 프로그램이 계산", "모양을 바꿔 가며 수십 번", "ai"],
    ["db", "레시피 노트에 기록", "다음엔 맛을 미리 압니다", "bulb", "AI가 결과를 공부", "계산 안 해도 답을 바로 예측", "ai"],
    ["trophy", "최고의 한 접시", "사진과 설명까지", "doc", "최적 설계와 보고서", "3D로 보고, 팀과 공유", "ai"],
  ];
  rows.forEach(([li, lt, ld, ri, rt, rd, who], i) => {
    const y = 2.35 + i * 0.93, h = 0.8;
    card(s, lx, y, lw, h, { shadow: false });
    circleIcon(s, lx + 0.18, y + 0.12, 0.56, li, "ink", C.bg, C.line);
    text(s, lt, { x: lx + 0.95, y: y + 0.12, w: lw - 1.1, h: 0.32, fontSize: 15.5, bold: true });
    text(s, ld, { x: lx + 0.95, y: y + 0.45, w: lw - 1.1, h: 0.28, fontSize: 12.5, color: C.muted });
    text(s, "=", { x: ex, y, w: ew, h, fontSize: 22, bold: true, color: C.muted, align: "center", valign: "middle" });
    const col = who === "human" ? C.human : C.ai;
    card(s, rx, y, rw, h, { line: col, lw: 1.5, shadow: false });
    circleIcon(s, rx + 0.18, y + 0.12, 0.56, ri, who, who === "human" ? C.humanSoft : C.aiSoft);
    text(s, rt, { x: rx + 0.95, y: y + 0.12, w: rw - 1.1, h: 0.32, fontSize: 15.5, bold: true });
    text(s, rd, { x: rx + 0.95, y: y + 0.45, w: rw - 1.1, h: 0.28, fontSize: 12.5, color: C.muted });
  });
  s.addNotes("식당에 비유하면 쉽습니다. 손님은 주문만 하고, 매니저가 주문서를 확인한 뒤 주방을 움직입니다. 우리 시스템에서 손님은 엔지니어, 매니저는 AI 조수, 주방은 회사의 설계·해석 프로그램입니다. 레시피 노트처럼 결과가 쌓여서 다음에는 계산하지 않고도 답을 미리 맞힙니다.");
}

// ---------- 4. 6단계 ----------
{
  const s = pres.addSlide();
  head(s, "3 · 한눈에 보는 일의 순서", "사람은 부탁하고 확인만, 나머지는 AI 조수가");
  const steps = [
    ["chat", "human", "말로 부탁하기", "“무게를 줄여 줘. 강도는 지키고.”", [["사람", "human"]]],
    ["plan", "ai", "계획표 보여 주기", "할 일 순서를 그림으로 보여 줍니다. 사람이 승인해야 다음으로 갑니다.", [["AI 조수", "ai"], ["사람이 OK", "human"]]],
    ["cube", "ai", "모양 만들고 계산하기", "설계 프로그램(CAD)으로 모양을 만들고, 해석 프로그램(CAE)으로 힘을 계산합니다.", [["AI 조수", "ai"]]],
    ["flask", "ai", "여러 번 바꿔 보며 실험", "두께·높이를 골고루 바꿔 수십 번 자동으로 계산합니다. 밤에도 돕니다.", [["AI 조수", "ai"]]],
    ["bulb", "ai", "결과를 공부해 예측", "“이 치수면 이런 결과”를 배워, 계산하지 않아도 답을 바로 예측합니다.", [["AI 조수", "ai"]]],
    ["trophy", "ai", "최고의 답과 보고서", "최적 설계, 3D 시뮬레이터, 보고서를 받고 사람이 최종 결정합니다.", [["AI 조수", "ai"], ["사람이 결정", "human"]]],
  ];
  const gw = 0.3, w = (CW - 2 * gw) / 3, h = 2.45;
  steps.forEach(([ic, who, t, d, chips], i) => {
    const x = M + (i % 3) * (w + gw), y = 1.8 + Math.floor(i / 3) * (h + 0.25);
    card(s, x, y, w, h);
    squareIcon(s, x + 0.28, y + 0.26, 0.64, ic, who, who === "human" ? C.humanSoft : C.aiSoft);
    text(s, String(i + 1), { x: x + w - 0.95, y: y + 0.12, w: 0.7, h: 0.75, fontSize: 40, bold: true, color: "E1E8EB", align: "right" });
    let cx = x + 0.28;
    chips.forEach(([l, k]) => { cx += chip(s, cx, y + 1.05, l, k) + 0.08; });
    text(s, t, { x: x + 0.28, y: y + 1.45, w: w - 0.5, h: 0.36, fontSize: 17, bold: true });
    if (i === 0) {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + 0.28, y: y + 1.84, w: w - 0.56, h: 0.4, rectRadius: 0.08, fill: { color: C.bg }, line: { color: C.bg, width: 1 } });
      text(s, d, { x: x + 0.38, y: y + 1.84, w: w - 0.76, h: 0.4, fontSize: 12, color: C.ink, valign: "middle" });
    } else {
      text(s, d, { x: x + 0.28, y: y + 1.83, w: w - 0.5, h: 0.52, fontSize: 12, color: C.muted, lineSpacingMultiple: 1.1 });
    }
    if (i % 3 < 2) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + w + 0.04, y: y + h / 2 - 0.11, w: 0.22, h: 0.22, fill: { color: C.faint }, line: { color: C.faint, width: 1 } });
  });
  s.addNotes("일의 순서는 여섯 단계입니다. 사람은 1단계에서 말로 부탁하고, 2단계에서 계획표를 보고 OK를 누르고, 6단계에서 결과를 보고 결정합니다. 그 사이의 모양 만들기, 계산, 수십 번의 실험, 결과 공부, 보고서 작성은 AI 조수가 합니다.");
}

// ---------- 5. 실제 사례 ① 과정 ----------
{
  const s = pres.addSlide();
  head(s, "4 · 실제로 돌려 본 사례", "선반 받침대(브래킷)를 더 가볍게", "벽에 붙어 끝에 2 kN(약 200 kg 무게)을 버티는 금속 받침대입니다. 이 시스템에 실제로 말을 걸어 끝까지 돌렸습니다.");
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: M, y: 2.0, w: CW, h: 1.0, rectRadius: 0.18, fill: { color: C.humanSoft }, line: { color: C.humanSoft, width: 1 } });
  circleIcon(s, M + 0.22, 2.17, 0.66, "person", "human", C.white);
  text(s, "엔지니어가 한 말", { x: M + 1.05, y: 2.12, w: 8, h: 0.28, fontSize: 11.5, bold: true, color: C.human });
  text(s, "“브래킷 무게를 최대한 줄여 줘. 단, 힘을 받는 정도(응력)는 250 MPa를 넘으면 안 돼.”", { x: M + 1.05, y: 2.42, w: CW - 1.3, h: 0.45, fontSize: 16.5, bold: true });
  const tl = [
    ["계획표 8단계 제안", "모양 → 해석 준비 → 실험 → 공부 → 3D → 보고서. 엔지니어가 승인"],
    ["3D 모양 만들기", "판 두께와 갈비뼈(리브) 높이를 바꿀 수 있게 설계"],
    ["20가지 모양 자동 계산", "두께 6~14 mm, 리브 10~40 mm를 골고루. 실행 전 “내 PC에서 해도 될까요?” 확인"],
    ["결과를 공부", "무게·응력을 바로 맞히는 예측 모델과 3D 응력 그림을 학습"],
    ["최적 설계 + 보고서", "3D 시뮬레이터 공개, Word 보고서 자동 작성"],
  ];
  const gap = 0.42, w = (CW - 4 * gap) / 5, y = 3.3, h = 2.35;
  tl.forEach(([t, d], i) => {
    const x = M + i * (w + gap);
    card(s, x, y, w, h);
    text(s, `AI 조수 ${"①②③④⑤"[i]}`, { x: x + 0.2, y: y + 0.2, w: w - 0.4, h: 0.28, fontSize: 12, bold: true, color: C.ai });
    text(s, t, { x: x + 0.2, y: y + 0.52, w: w - 0.4, h: 0.62, fontSize: 15.5, bold: true, lineSpacingMultiple: 1.05 });
    text(s, d, { x: x + 0.2, y: y + 1.17, w: w - 0.4, h: 1.08, fontSize: 12, color: C.muted, lineSpacingMultiple: 1.12 });
    if (i < 4) arrowRight(s, x + w + (gap - 0.34) / 2, y + h / 2 - 0.15, C.ai);
  });
  card(s, M, 5.95, CW, 0.82, { fill: C.okSoft, line: C.okSoft });
  circleIcon(s, M + 0.2, 6.06, 0.6, "check", "ok", C.white);
  text(s, [
    { text: "사람이 한 일은 ", options: {} },
    { text: "부탁 한 번, 승인 두 번", options: { bold: true, color: C.ok } },
    { text: "이 전부였습니다. 나머지는 AI 조수가 이어서 했습니다.", options: {} },
  ], { x: M + 1.0, y: 5.95, w: CW - 1.2, h: 0.82, fontSize: 15.5, valign: "middle" });
  s.addNotes("실제 사례입니다. 벽에 붙어 끝에 200kg 정도를 버티는 금속 받침대를 더 가볍게 만들어 달라고 말로 부탁했습니다. AI 조수가 8단계 계획을 보여 주었고, 승인하자 3D 모양을 만들고, 두께와 리브 높이를 20가지로 바꿔 자동 계산하고, 결과를 공부한 뒤 최적 설계와 보고서를 만들었습니다. 사람이 한 일은 부탁 한 번과 승인 두 번이었습니다.");
}

// ---------- 6. 실제 사례 ② 결과 ----------
{
  const s = pres.addSlide();
  head(s, "4 · 실제로 돌려 본 사례", "결과: 강도는 지키면서 31% 더 가벼워졌습니다");
  const lw = 7.35;
  card(s, M, 1.75, lw, 4.75);
  text(s, "모양이 이렇게 바뀌었어요", { x: M + 0.3, y: 1.95, w: 6, h: 0.36, fontSize: 17, bold: true });
  const iw = 3.25, ih = iw * 510 / 960;
  s.addImage({ data: A("bracket_before.png"), x: M + 0.3, y: 2.5, w: iw, h: ih });
  s.addImage({ data: A("bracket_after.png"), x: M + 0.3 + iw + 0.25, y: 2.5, w: iw, h: ih });
  text(s, [{ text: "처음 설계", options: { bold: true, breakLine: true } }, { text: "두께 10 mm · 리브 25 mm", options: { color: C.muted } }], { x: M + 0.3, y: 2.55 + ih, w: iw, h: 0.55, fontSize: 12.5 });
  text(s, [{ text: "AI가 찾은 설계", options: { bold: true, color: C.ai, breakLine: true } }, { text: "두께 6 mm · 리브 26 mm", options: { color: C.muted } }], { x: M + 0.55 + iw, y: 2.55 + ih, w: iw, h: 0.55, fontSize: 12.5 });
  text(s, "색: 힘을 받는 정도", { x: M + 0.3, y: 5.05, w: 1.6, h: 0.25, fontSize: 11, color: C.muted });
  text(s, "적게", { x: M + 1.9, y: 5.05, w: 0.45, h: 0.25, fontSize: 11, color: C.muted });
  s.addImage({ data: A("scale.png"), x: M + 2.35, y: 5.1, w: 1.8, h: 0.17 });
  text(s, "많이", { x: M + 4.25, y: 5.05, w: 0.6, h: 0.25, fontSize: 11, color: C.muted });
  text(s, "판은 얇게, 갈비뼈(리브)는 조금 높게. 사람이 직관으로 정하기 어려운 조합을 실험으로 찾았습니다.", { x: M + 0.3, y: 5.55, w: lw - 0.6, h: 0.65, fontSize: 13.5, color: C.ink, lineSpacingMultiple: 1.15 });

  const rx = M + lw + 0.3, rw = W - M - rx;
  card(s, rx, 1.75, rw, 4.75);
  text(s, "31%", { x: rx + 0.3, y: 1.9, w: 2.2, h: 0.95, fontSize: 54, bold: true, color: C.ai });
  text(s, "더 가벼워짐", { x: rx + 2.35, y: 2.32, w: 2, h: 0.4, fontSize: 16, color: C.muted });
  s.addChart(pres.charts.BAR, [
    { name: "무게(g)", labels: ["처음 설계", "AI가 찾은 설계"], values: [1178, 814] },
  ], {
    x: rx + 0.15, y: 2.85, w: rw - 0.3, h: 1.65, barDir: "bar", chartColors: ["0C7882"],
    catAxisLabelColor: C.ink, catAxisLabelFontFace: F, catAxisLabelFontSize: 12, catAxisOrientation: "maxMin",
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" }, valAxisMinVal: 0, valAxisMaxVal: 1700,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 13, dataLabelFontBold: true, dataLabelColor: C.ink,
    dataLabelFormatCode: '#,##0" g"', dataLabelFontFace: F, showLegend: false, barGapWidthPct: 45,
  });
  const checks = ["강도 기준 지킴: 응력 250 MPa 이하", "치수를 바꾸면 3D로 바로 예측", "보고서까지 자동 작성"];
  checks.forEach((c, i) => {
    s.addImage({ data: icon("check", "ok"), x: rx + 0.3, y: 4.62 + i * 0.4, w: 0.28, h: 0.28 });
    text(s, c, { x: rx + 0.7, y: 4.6 + i * 0.4, w: rw - 0.9, h: 0.32, fontSize: 13, valign: "middle" });
  });
  text(s, "연습용 계산 모델(데모)로 돌린 결과입니다. 실제 회사 해석 프로그램으로 확인하는 일은 사내에서 진행합니다.", { x: M, y: 6.65, w: CW, h: 0.3, fontSize: 11.5, color: C.muted });
  s.addNotes("결과입니다. 처음 설계는 1,178g이었는데 AI가 찾은 설계는 814g으로 31% 가벼워졌고, 강도 기준 250MPa도 지켰습니다. 판은 얇게, 리브는 조금 높게 하는 조합인데 사람이 직관으로 정하기 어려운 것을 실험으로 찾은 것입니다. 이 숫자는 연습용 계산 모델로 돌린 데모 결과이고, 실제 회사 해석 프로그램으로 확인하는 일은 사내에서 진행합니다.");
}

// ---------- 7. 이런 일도 ----------
{
  const s = pres.addSlide();
  head(s, "5 · 이런 일도 말로 부탁할 수 있어요", "회사에서 쓰는 설계·해석 프로그램 29종을 다룹니다", "아래는 부탁 예시입니다. 프로그램마다 “다루는 법”(스킬)을 AI 조수에게 미리 가르쳐 두었습니다.");
  const ex = [
    ["car", "범퍼 두께를 세 가지로 바꿔 충돌 해석하고, 찌그러짐을 비교해 줘", "충돌 해석 · LS-DYNA"],
    ["thermo", "칩 온도가 85도를 넘지 않는 가장 작은 방열판을 찾아 줘", "열 해석 · Celsius, Flotherm"],
    ["light", "LED 빛이 화면에 고르게 퍼지도록 렌즈 모양을 바꿔 봐 줘", "빛 해석 · LightTools"],
    ["part", "플라스틱 부품이 가장 덜 휘는 주입구 위치를 찾아 줘", "사출 성형 · Moldflow, Moldex3D"],
  ];
  const gw = 0.3, w = (CW - gw) / 2, h = 1.75;
  ex.forEach(([ic, q, tool], i) => {
    const x = M + (i % 2) * (w + gw), y = 2.05 + Math.floor(i / 2) * (h + 0.3);
    card(s, x, y, w, h);
    circleIcon(s, x + 0.3, y + 0.3, 0.8, ic, "ink", C.bg, C.line);
    text(s, `“${q}”`, { x: x + 1.35, y: y + 0.3, w: w - 1.65, h: 0.85, fontSize: 16.5, bold: true, lineSpacingMultiple: 1.1 });
    text(s, tool, { x: x + 1.35, y: y + 1.2, w: w - 1.65, h: 0.3, fontSize: 12.5, color: C.muted });
  });
  const cats = "CAD · 전처리 · 구조 · 충돌 · 유동·열 · 다중물리 · 전자기 · 다물체 · 사출 · 1D 시스템 · 피로 · 광학 · 설계 최적화 · AI 예측";
  text(s, [{ text: "지원 분야  ", options: { bold: true, color: C.ai } }, { text: cats, options: { color: C.muted } }], { x: M, y: 6.25, w: CW, h: 0.4, fontSize: 13 });
  s.addNotes("브래킷 말고도 이런 일을 말로 부탁할 수 있습니다. 충돌, 열, 빛, 사출 같은 분야의 회사 프로그램 29종을 다루도록 프로그램별 사용법을 AI 조수에게 미리 가르쳐 두었습니다.");
}

// ---------- 8. 무엇이 달라지나 ----------
{
  const s = pres.addSlide();
  head(s, "6 · 무엇이 달라지나", "전문가 몇 명의 일에서, 누구나 하는 일로");
  const hdr = (t, fill, color) => ({ text: t, options: { bold: true, fill: { color: fill }, color, fontSize: 14 } });
  const rows = [
    [hdr("", C.white, C.muted), hdr("지금", C.white, C.muted), hdr("DDS AI Engineer", C.aiSoft, C.ai)],
    ["누가", "스크립트를 짤 줄 아는 일부 전문가", "설계 엔지니어 누구나"],
    ["어떻게", "프로그램마다 손으로 클릭, 명령문 작성", "평소 말로 부탁"],
    ["몇 번", "시간이 없어 설계안 몇 개만", "수십 번 자동 실험, 밤에도"],
    ["결과", "개인 PC와 엑셀에 흩어짐", "한 곳에 쌓여 다음 설계에 다시 씀"],
    ["보고", "그래프·보고서를 손으로", "그래프·3D·보고서 자동"],
  ].map((r, i) => i === 0 ? r : [
    { text: r[0], options: { bold: true, color: C.muted, margin: [0, 0.2, 0, 0.85] } },
    { text: r[1], options: { color: C.ink } },
    { text: r[2], options: { bold: true, color: C.ink, fill: { color: "EEF7F8" } } },
  ]);
  const ty = 1.8, rh = 0.72;
  s.addTable(rows, {
    x: M, y: ty, w: CW, colW: [2.0, 4.9, CW - 6.9], rowH: rh, fontFace: F, fontSize: 16, valign: "middle",
    fill: { color: C.white }, border: { type: "solid", pt: 1, color: C.line }, margin: [0, 0.2, 0, 0.2],
  });
  // 줄마다 무엇에 대한 비교인지 그림으로(누가·어떻게·몇 번·결과·보고)
  ["people", "chat", "flask", "db", "doc"].forEach((ic, i) => {
    s.addImage({ data: icon(ic, "ai"), x: M + 0.25, y: ty + rh * (i + 1) + (rh - 0.4) / 2, w: 0.4, h: 0.4 });
  });
  card(s, M, 6.38, CW, 0.62, { fill: C.aiSoft, line: C.aiSoft });
  text(s, [{ text: "핵심  ", options: { bold: true, color: C.ai } }, { text: "설계 실력은 사람에게, 반복 작업은 AI에게. 엔지니어는 더 많은 설계안을 더 빨리 비교합니다.", options: {} }],
    { x: M + 0.3, y: 6.38, w: CW - 0.6, h: 0.62, fontSize: 15, valign: "middle" });
  s.addNotes("정리하면, 지금은 스크립트를 짤 줄 아는 일부 전문가가 손으로 하던 일을, 앞으로는 설계 엔지니어 누구나 말로 부탁해서 할 수 있습니다. 실험은 수십 번, 밤에도 자동으로 돌고, 결과는 한 곳에 쌓여 다음 설계에 다시 쓰입니다.");
}

// ---------- 9. 안심 ----------
{
  const s = pres.addSlide();
  head(s, "7 · 안심하고 쓸 수 있게", "AI가 혼자 결정하지 않고, 설계 자료는 회사 안에 둡니다");
  const bx = M, by = 2.05, bw = 7.0, bh = 3.95;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: by, w: bw, h: bh, rectRadius: 0.25, fill: { type: "none" }, line: { color: C.ai, width: 2, dashType: "dash" } });
  s.addShape(pres.shapes.RECTANGLE, { x: bx + 0.3, y: by - 0.2, w: 1.95, h: 0.4, fill: { color: C.bg }, line: { color: C.bg, width: 0 } });
  text(s, "회사 안 (사내망)", { x: bx + 0.3, y: by - 0.2, w: 1.95, h: 0.4, fontSize: 14, bold: true, color: C.ai, align: "center", valign: "middle" });
  const boxes = [["web", "웹 화면", "대화·결과·3D 보기"], ["building", "회사 서버", "결과 보관·학습·보고서"], ["pc", "내 PC 도우미", "설계 프로그램 실행, 원본 파일 보관"]];
  const w = 1.85, gap = (bw - 0.6 - 3 * w) / 2;
  boxes.forEach(([ic, t, d], i) => {
    const x = bx + 0.3 + i * (w + gap), y = by + 0.45;
    card(s, x, y, w, 2.0);
    s.addImage({ data: icon(ic, "ai"), x: x + (w - 0.55) / 2, y: y + 0.2, w: 0.55, h: 0.55 });
    text(s, t, { x: x + 0.1, y: y + 0.85, w: w - 0.2, h: 0.32, fontSize: 15, bold: true, align: "center" });
    text(s, d, { x: x + 0.12, y: y + 1.2, w: w - 0.24, h: 0.65, fontSize: 11.5, color: C.muted, align: "center", lineSpacingMultiple: 1.1 });
    if (i < 2) s.addShape(pres.shapes.LEFT_RIGHT_ARROW, { x: x + w + (gap - 0.5) / 2, y: y + 0.85, w: 0.5, h: 0.3, fill: { color: C.faint }, line: { color: C.faint, width: 1 } });
  });
  circleIcon(s, bx + 0.3, by + 2.8, 0.6, "bot", "ink", C.white, C.line);
  text(s, "AI와의 대화는 회사가 허용한 AI 계정(Gemini·Claude 등)으로만 합니다.", { x: bx + 1.05, y: by + 2.8, w: bw - 1.35, h: 0.6, fontSize: 13, color: C.muted, valign: "middle" });
  const guards = [
    ["hand", "사람이 먼저 승인", "계획표를 보고 OK해야 시작하고, 내 PC에서 실행하기 전에 한 번 더 묻습니다."],
    ["lock", "설계 자료는 회사 안에", "큰 원본 파일은 내 PC에, 숫자 결과와 그림은 회사 서버에 저장합니다."],
    ["people", "팀 단위로 공유", "프로젝트 멤버만 보고, 가입은 관리자가 승인합니다."],
    ["shield", "결과는 사람이 확인", "처음엔 작은 모델로 시험하고, 중요한 결정은 엔지니어가 내립니다."],
  ];
  const gx = bx + bw + 0.4, gw = W - M - gx;
  guards.forEach(([ic, t, d], i) => {
    const y = 1.95 + i * 1.12;
    squareIcon(s, gx, y, 0.6, ic, "ok", C.okSoft);
    text(s, t, { x: gx + 0.8, y: y - 0.02, w: gw - 0.8, h: 0.34, fontSize: 16, bold: true });
    text(s, d, { x: gx + 0.8, y: y + 0.34, w: gw - 0.8, h: 0.62, fontSize: 12.5, color: C.muted, lineSpacingMultiple: 1.1 });
  });
  s.addNotes("안전장치입니다. AI가 혼자 결정하지 않습니다. 계획표를 사람이 승인해야 시작하고, 내 PC에서 프로그램을 실행하기 전에 한 번 더 묻습니다. 웹 화면, 회사 서버, 내 PC 도우미가 모두 사내망 안에 있고, 큰 원본 파일은 내 PC에, 결과는 회사 서버에 저장됩니다. AI와의 대화는 회사가 허용한 AI 계정으로만 합니다.");
}

// ---------- 10. 지금 어디까지 ----------
{
  const s = pres.addSlide();
  head(s, "8 · 지금 어디까지 왔나", "만들기는 끝났고, 회사 프로그램으로 확인할 차례입니다");
  const ms = [["만들기", "웹 화면·AI 조수·\n설치 프로그램", "done"], ["회사 서버 설치", "준비 스크립트\n1개로 설치", "now"],
    ["실제 프로그램 확인", "라이선스 PC에서\n작은 모델로", ""], ["시범 운영", "1~2개 팀,\n자주 쓰는 프로그램부터", ""], ["전사 확대", "사내 표준\n해석 방식 반영", ""]];
  const x0 = M + 1.0, x1 = W - M - 1.0, step = (x1 - x0) / 4, cy = 2.35, d = 0.62;
  s.addShape(pres.shapes.LINE, { x: x0, y: cy, w: step, h: 0, line: { color: C.ai, width: 4 } });
  s.addShape(pres.shapes.LINE, { x: x0 + step, y: cy, w: step * 3, h: 0, line: { color: C.line, width: 4 } });
  ms.forEach(([t, sub, st], i) => {
    const x = x0 + i * step;
    if (st === "now") s.addShape(pres.shapes.OVAL, { x: x - d / 2 - 0.12, y: cy - d / 2 - 0.12, w: d + 0.24, h: d + 0.24, fill: { color: C.humanSoft }, line: { color: C.humanSoft, width: 1 } });
    s.addShape(pres.shapes.OVAL, { x: x - d / 2, y: cy - d / 2, w: d, h: d, fill: { color: st === "done" ? C.ai : C.white }, line: { color: st === "done" ? C.ai : st === "now" ? C.human : C.line, width: 3 } });
    if (st === "done") s.addImage({ data: icon("check", "white"), x: x - 0.17, y: cy - 0.17, w: 0.34, h: 0.34 });
    else text(s, String(i + 1), { x: x - d / 2, y: cy - d / 2, w: d, h: d, fontSize: 17, bold: true, color: st === "now" ? C.human : C.muted, align: "center", valign: "middle" });
    text(s, t, { x: x - 1.2, y: cy + 0.55, w: 2.4, h: 0.34, fontSize: 15, bold: true, align: "center", color: st === "now" ? C.human : C.ink });
    text(s, sub, { x: x - 1.2, y: cy + 0.9, w: 2.4, h: 0.55, fontSize: 11.5, color: C.muted, align: "center" });
  });
  text(s, "지금 여기", { x: x0 + step - 0.6, y: cy - 0.85, w: 1.2, h: 0.28, fontSize: 11.5, bold: true, color: C.human, align: "center" });
  const stats = [["29종", "연결 준비가 끝난 설계·해석 프로그램 (CAD, 구조, 충돌, 유동·열, 전자기, 광학, 사출 등)"],
    ["6단계", "부탁부터 보고서까지 한 화면에서 이어지는 흐름 (실험·예측·최적화·3D·보고서)"],
    ["설치 1번", "각자 PC에 도우미를 설치하면 프로그램 위치를 스스로 찾고 업데이트도 자동"]];
  const gw = 0.3, w = (CW - 2 * gw) / 3;
  stats.forEach(([n, d], i) => {
    const x = M + i * (w + gw), y = 4.15;
    card(s, x, y, w, 1.85);
    text(s, n, { x: x + 0.3, y: y + 0.22, w: w - 0.6, h: 0.7, fontSize: 36, bold: true, color: C.ai });
    text(s, d, { x: x + 0.3, y: y + 0.98, w: w - 0.6, h: 0.75, fontSize: 12.5, color: C.muted, lineSpacingMultiple: 1.12 });
  });
  text(s, "아직 확인하지 못한 것: 집 PC에는 회사 프로그램 라이선스가 없어, 실제 프로그램과의 연결은 사내에서 처음 확인합니다.", { x: M, y: 6.3, w: CW, h: 0.35, fontSize: 12, color: C.muted });
  s.addNotes("현재 위치입니다. 웹 화면, AI 조수, 설치 프로그램까지 만들기는 끝났습니다. 다음은 회사 서버에 설치하고, 라이선스가 있는 PC에서 실제 프로그램으로 작은 모델부터 확인하는 단계입니다. 집 PC에는 라이선스가 없어서 실제 프로그램과의 연결은 아직 확인하지 못했다는 점을 솔직하게 말씀드립니다.");
}

// ---------- 11. 부탁드리는 것 ----------
{
  const s = pres.addSlide();
  s.background = { color: C.dark };
  text(s, "9 · 부탁드리는 것", { x: M, y: 0.55, w: CW, h: 0.3, fontSize: 12, bold: true, color: "9DB0BA", charSpacing: 1 });
  text(s, "시범 운영을 위해 세 가지가 필요합니다", { x: M, y: 0.88, w: CW, h: 0.7, fontSize: 30, bold: true, color: C.white });
  const asks = [["people", "시범 팀 1~2곳", "반복 해석이 많은 팀을 정해 주시면, 그 팀의 실제 업무로 먼저 확인합니다."],
    ["pc", "라이선스 있는 PC 사용", "실제 설계·해석 프로그램이 설치된 PC에서 확인하고 고칠 시간이 필요합니다."],
    ["building", "사내 서버 1대", "웹 화면과 결과를 보관할 서버입니다. 설치는 준비 스크립트로 바로 됩니다."]];
  const gw = 0.35, w = (CW - 2 * gw) / 3;
  asks.forEach(([ic, t, d], i) => {
    const x = M + i * (w + gw), y = 2.0;
    card(s, x, y, w, 2.85, { fill: C.dark2, line: C.darkLine });
    circleIcon(s, x + 0.35, y + 0.35, 0.8, ic, "humanL", "3A2516");
    text(s, t, { x: x + 0.35, y: y + 1.35, w: w - 0.7, h: 0.4, fontSize: 19, bold: true, color: C.white });
    text(s, d, { x: x + 0.35, y: y + 1.8, w: w - 0.7, h: 0.7, fontSize: 13, color: C.darkText, lineSpacingMultiple: 1.15 });
  });
  text(s, [
    { text: "말로 부탁하면 ", options: { color: C.white } },
    { text: "실험은 AI가", options: { color: C.aiL } },
    { text: ", ", options: { color: C.white } },
    { text: "판단은 사람이", options: { color: C.humanL } },
    { text: ".", options: { color: C.white } },
  ], { x: M, y: 5.5, w: CW, h: 0.9, fontSize: 32, bold: true, align: "center", valign: "middle" });
  s.addNotes("시범 운영을 위해 세 가지를 부탁드립니다. 반복 해석이 많은 시범 팀 한두 곳, 실제 프로그램이 설치된 라이선스 PC를 쓸 시간, 그리고 사내 서버 한 대입니다. 정리하면, 말로 부탁하면 실험은 AI가 하고 판단은 사람이 합니다.");
}

// 저장 후 한글 줄바꿈 고치기(korean_wrap.py): pptxgenjs가 붙이는 중국어 문자 집합 때문에 글자 단위로 잘리는 것을 막는다
const RAW = OUT.replace(/\.pptx$/, ".raw.pptx");
pres.writeFile({ fileName: RAW }).then(() => {
  const venvPy = path.join(__dirname, "..", "..", "..", ".venv", "Scripts", "python.exe");
  const py = process.env.QA_PY || (fs.existsSync(venvPy) ? venvPy : "python");
  require("child_process").execFileSync(py, [path.join(__dirname, "korean_wrap.py"), RAW, OUT], { stdio: "inherit" });
  fs.unlinkSync(RAW);
  console.log("written", OUT);
});
