// 판단을 어디에 놓을지(canvas/judgement.ts) 시험한다. 서버·브라우저 없이 돈다: node e2e/judgement_place.mjs
// AI가 흐름을 그리므로 단계 모양은 정해져 있지 않다. 어떤 모양이어도 판단이 틀린 단계에 붙거나 사라지면 안 된다.
import { transformSync } from "esbuild";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const src = fs.readFileSync(path.join(import.meta.dirname, "../src/canvas/judgement.ts"), "utf8");
const js = transformSync(src, { loader: "ts", format: "esm" }).code;
const { judged, pickNext, placeSections, studiesForFlow } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

const sec = (key, state = "good", extra = {}) => ({ key, state, state_label: state, sentence: key, notes: [], charts: [], ...extra });
const node = (id, type, label = id, status = "complete") => ({ id, type, label, status });
const ids = (list) => (list ?? []).map((s) => s.key);
const flow = (id, status = "complete", nodes = []) => ({ id, status, nodes });
const FULL = [node("doe", "doe"), node("solve", "solve"), node("train", "train"), node("opt", "optimize")];
const ALL = ["coverage", "quality", "influence", "trust", "convergence", "optimum", "pareto", "active"];

const cases = {
  "공간 충진은 DOE 단계, 수렴은 최적화 단계에 붙는다": () => {
    const { byNode, outside } = placeSections(ALL.map((k) => sec(k)), FULL);
    assert.deepEqual(ids(byNode.doe), ["coverage"]);
    assert.deepEqual(ids(byNode.solve), ["quality"]);
    assert.deepEqual(ids(byNode.train), ["influence", "trust", "active"]);
    assert.deepEqual(ids(byNode.opt), ["convergence", "optimum", "pareto"]);
    assert.deepEqual(outside, []);
  },
  "아직 판단할 수 없는 항목은 어디에도 나오지 않는다": () => {
    const { byNode, outside } = placeSections([sec("coverage", "unknown"), sec("trust", "good", { pending: true }), sec("pareto", "unknown", { collapsed: true }), sec("quality", "fair", { collapsed: true })], FULL);
    assert.deepEqual(byNode, {});
    assert.deepEqual(outside, []);
  },
  "출력 하나라도 판단했으면 그 출력을 머리에 올리고 판단 못 한 출력은 뺀다": () => {
    const v = (name, state) => ({ name, label: name, state, state_label: state, sentence: `${name} 문장`, notes: [], charts: [] });
    const s = judged(sec("trust", "unknown", { variants: [v("a", "unknown"), v("b", "fair")], lead: "a" }));
    assert.equal(s.state, "fair");
    assert.equal(s.sentence, "b 문장");
    assert.deepEqual(s.variants.map((x) => x.name), ["b"]);
    assert.equal(s.lead, "b");
    const known = judged(sec("trust", "caution", { variants: [v("a", "caution"), v("b", "unknown"), v("c", "good")], lead: "a" }));
    assert.deepEqual(known.variants.map((x) => x.name), ["a", "c"]);
    assert.equal(known.lead, "a");
    assert.equal(judged(sec("trust", "unknown", { variants: [v("a", "unknown")] })), null);
  },
  "DOE와 해석을 한 단계로 묶은 흐름에서도 빠지지 않는다": () => {
    assert.deepEqual(ids(placeSections([sec("coverage"), sec("quality")], [node("all", "doe")]).byNode.all), ["coverage", "quality"]);
    assert.deepEqual(ids(placeSections([sec("coverage"), sec("quality")], [node("all", "solve")]).byNode.all), ["coverage", "quality"]);
  },
  "맞는 단계가 없으면 사라지지 않고 단계 밖으로 간다": () => {
    const { byNode, outside } = placeSections(ALL.map((k) => sec(k)), [node("a", "task"), node("b", "task")]);
    assert.deepEqual(byNode, {});
    assert.deepEqual(ids(outside), ALL);
  },
  "건너뛴 단계에는 붙이지 않는다": () => {
    assert.deepEqual(ids(placeSections([sec("convergence")], [node("opt", "optimize", "opt", "skipped")]).outside), ["convergence"]);
  },
  "학습 단계가 여럿이면 3D·능동학습 단계에 스칼라 판단을 붙이지 않는다": () => {
    const nodes = [node("field", "train", "형상·응력 필드 학습"), node("al", "train", "능동학습 6건"), node("first", "train", "첫 예측 모델")];
    const { byNode } = placeSections([sec("trust"), sec("influence"), sec("active")], nodes);
    assert.deepEqual(ids(byNode.first), ["trust", "influence"]);
    assert.deepEqual(ids(byNode.al), ["active"]);
    assert.equal(byNode.field, undefined);
  },
  "학습 단계가 3D 하나뿐이어도 판단은 남는다": () => {
    assert.deepEqual(ids(placeSections([sec("trust")], [node("field", "train", "필드 학습")]).byNode.field), ["trust"]);
  },
  "어느 흐름에도 없는 스터디는 다른 일의 단계에 붙지 않고 최근 흐름의 단계 밖에만 간다": () => {
    const flows = [flow(9), flow(5)];
    const s = [{ id: 1, flow_id: 5 }, { id: 2, flow_id: null }, { id: 3, flow_id: 77 }, { id: 4 }, { id: 5, flow_id: 9 }];
    assert.deepEqual(studiesForFlow(s, flows, flows[0]), { onSteps: [s[4]], loose: [s[1], s[2], s[3]] });
    assert.deepEqual(studiesForFlow(s, flows, flows[1]), { onSteps: [s[0]], loose: [] });
  },
  "흐름이 없으면 모든 스터디가 단계 밖이다": () => {
    const s = [{ id: 1, flow_id: null }];
    assert.deepEqual(studiesForFlow(s, [], undefined), { onSteps: [], loose: s });
  },
  "다음 할 일은 하나만, 앞의 후보부터 고른다": () => {
    const a = { next: { key: "report", label: "보고서 요청", request: "r" } }, b = { next: { key: "train", label: "학습 요청", request: "t" } };
    assert.equal(pickNext([a, b], [flow(1)]), a);
    assert.equal(pickNext([{ next: null }, b], [flow(1)]), b);
    assert.equal(pickNext([], [flow(1)]), null);
  },
  "흐름이 승인 대기이거나 도는 동안에는 AI가 할 일을 버튼으로 내지 않는다": () => {
    const a = { next: { key: "train", label: "학습 요청", request: "t" } };
    for (const st of ["review", "approved", "in_progress"]) assert.equal(pickNext([a], [flow(2, st), flow(1)]), null, st);
    for (const st of ["complete", "failed", "rejected"]) assert.equal(pickNext([a], [flow(1, st)]), a, st);
    assert.equal(pickNext([a], []), a); // 흐름 없는 표 프로젝트
  },
  "기다리라는 말은 할 일이 아니고, 연결이 끊긴 것은 언제나 알린다": () => {
    const wait = { next: { key: "wait", text: "기다리세요", label: "", request: "" } };
    const computing = { next: { key: "computing", text: "계산 중", label: "", request: "" } };
    const stuck = { next: { key: "stuck", text: "끊김", label: "", request: "" } };
    const train = { next: { key: "train", label: "학습 요청", request: "t" } };
    assert.equal(pickNext([wait, computing], [flow(1)]), null);
    assert.equal(pickNext([train, stuck], [flow(1, "in_progress")]), stuck);
    assert.equal(pickNext([train, stuck], [flow(1)]), stuck);
  },
};

let failed = 0;
for (const [name, run] of Object.entries(cases)) {
  try { run(); console.log(`  통과  ${name}`); }
  catch (e) { failed += 1; console.log(`  실패  ${name}\n        ${e.message}`); }
}
console.log(failed ? `실패 ${failed}건` : "PASS");
process.exit(failed ? 1 : 0);
