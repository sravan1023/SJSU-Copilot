import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  completion,
  evaluate,
  inferChoice,
  matchesRule,
  nextTerm,
  planNext,
  planText,
  resolveChoices,
  scheduleUrl,
  searchCourses,
  shortfall,
  summarize,
  withPlanned,
  validChoices,
  type GradProgram,
  type Plan,
} from "./requirements.ts";

// The real published rule files, so a data change that breaks the planner breaks here.
function program(id: string): GradProgram {
  return JSON.parse(readFileSync(new URL(`../../public/data/programs/${id}.json`, import.meta.url), "utf8"));
}
const msai = program("msai");
const msse = program("msse");
const mscs = program("mscs");

function done(...codes: string[]): Record<string, string> {
  return Object.fromEntries(codes.map((c) => [c, "done"]));
}

function req(ev: ReturnType<typeof evaluate>, id: string) {
  const rp = ev.requirements.find((r) => r.requirement.id === id);
  assert.ok(rp, id);
  return rp;
}

function slotsOf(plan: Plan) {
  return plan.slots.map((s) => `${s.requirement.id}:${s.kind}:${s.count}:${s.options.join(",")}`);
}

// -- evaluate -------------------------------------------------------------------

test("a new MS AI student: nothing counted, 33 units to go", () => {
  const ev = evaluate(msai, {}, {});
  assert.equal(ev.doneUnits, 0);
  assert.equal(ev.takingUnits, 0);
  assert.equal(ev.totalUnits, 33);
  assert.equal(req(ev, "specialization").body, null); // waits for a track
});

test("a course two rules accept goes to the more specific one", () => {
  // CMPE 258 is in the Intelligent Data Systems track and in Area A.
  const ev = evaluate(msai, done("CMPE 258"), { track: "intelligent-data-systems" });
  assert.deepEqual(req(ev, "specialization").assigned.map((a) => a.code), ["CMPE 258"]);
  assert.deepEqual(req(ev, "electives").assigned, []);
});

test("Area B is capped at 6 units, so a third Area B course counts nowhere", () => {
  const ev = evaluate(msai, done("CMPE 214", "CMPE 217", "CMPE 243"), {});
  assert.equal(req(ev, "electives").doneUnits, 6);
  assert.deepEqual(ev.uncounted.map((u) => u.code), ["CMPE 243"]);
});

test("done and taking are reported apart and capped per requirement", () => {
  const ev = evaluate(msai, { "CMPE 252": "done", "CMPE 257": "taking", "ISE 201": "done" }, {});
  assert.equal(ev.doneUnits, 6);
  assert.equal(ev.takingUnits, 3);
  assert.equal(req(ev, "core").complete, true);
});

test("an unknown choice value is ignored rather than trusted", () => {
  assert.deepEqual(validChoices(msai, { track: "made-up", plan: "project", extra: "x" }), { plan: "project" });
});

test("MS SE electives accept any CMPE 200+ course except the excluded ones", () => {
  const rule = msse.requirements.find((r) => r.id === "electives")!.match!;
  assert.ok(matchesRule(rule, "CMPE 220"));
  assert.ok(matchesRule(rule, "CMPE 296A"));
  for (const no of ["CMPE 294", "CMPE 295A", "CMPE 298I", "CMPE 180", "CS 255"]) assert.ok(!matchesRule(rule, no), no);
  const ev = evaluate(msse, done("CMPE 220"), {});
  assert.deepEqual(req(ev, "electives").assigned.map((a) => a.code), ["CMPE 220"]);
});

test("MS SE Cybersecurity counts one of 209/219 in the core and the other as an elective", () => {
  const ev = evaluate(msse, done("CMPE 209", "CMPE 219", "CMPE 279"), { track: "cybersecurity" });
  assert.equal(req(ev, "specialization-core").doneUnits, 6);
  assert.deepEqual(req(ev, "specialization-core").assigned.map((a) => a.code).sort(), ["CMPE 209", "CMPE 279"]);
  assert.deepEqual(req(ev, "electives").assigned.map((a) => a.code), ["CMPE 219"]);
});

test("MS CS: a second Foundations course counts as a specialty course", () => {
  const ev = evaluate(mscs, done("CS 255", "CS 252"), {});
  assert.deepEqual(req(ev, "foundations").assigned.map((a) => a.code), ["CS 252"]);
  assert.deepEqual(req(ev, "specialty").assigned.map((a) => a.code), ["CS 255"]);
});

test("MS CS: at most 3 units from the second elective list", () => {
  const ev = evaluate(mscs, done("MATH 161A", "MATH 163"), {});
  assert.equal(req(ev, "electives").doneUnits, 3);
  assert.deepEqual(ev.uncounted.map((u) => u.code), ["MATH 163"]);
});

// -- planNext -------------------------------------------------------------------

test("a new MS AI student is planned the core first, and a fourth course is GWAR", () => {
  assert.deepEqual(slotsOf(planNext(msai, {}, {}, 3)), ["core:take:3:CMPE 252,CMPE 257,ISE 201"]);
  const four = planNext(msai, {}, {}, 4);
  assert.deepEqual(slotsOf(four), ["core:take:3:CMPE 252,CMPE 257,ISE 201", "gwar:take:1:CMPE 294"]);
  // No project or thesis chosen yet: said so, not guessed.
  assert.ok(four.notes.some((n) => n.text.includes("culminating experience")));
});

// -- the specialization, from the courses ---------------------------------------

test("the specialization is the track the courses fill", () => {
  assert.equal(inferChoice(msai, done("CMPE 256", "CMPE 259"), "track"), "intelligent-data-systems");
  assert.equal(inferChoice(msai, done("CMPE 249", "CMPE 260"), "track"), "autonomous-systems");
  assert.equal(inferChoice(msai, done("CMPE 209"), "track"), "cybersecurity-in-ai");
  assert.equal(inferChoice(msse, done("CMPE 257", "CMPE 258"), "track"), "data-science");
  assert.equal(inferChoice(msse, done("CMPE 209", "CMPE 279"), "track"), "cybersecurity");
});

test("no courses, or courses that fit two tracks equally, leave it undecided", () => {
  assert.equal(inferChoice(msai, {}, "track"), null);
  assert.equal(inferChoice(msai, done("CMPE 252", "CMPE 257"), "track"), null); // core only
  assert.equal(inferChoice(msai, done("CMPE 258"), "track"), null); // in two tracks
  // One more course breaks the tie.
  assert.equal(inferChoice(msai, done("CMPE 258", "CMPE 260"), "track"), "autonomous-systems");
});

test("a stored pick never overrides the courses; asked choices are kept", () => {
  const stored = { track: "cybersecurity-in-ai", plan: "thesis" };
  assert.deepEqual(resolveChoices(msai, done("CMPE 256", "CMPE 259"), stored), {
    plan: "thesis",
    track: "intelligent-data-systems",
  });
  assert.deepEqual(resolveChoices(msai, {}, stored), { plan: "thesis" });
});

test("with no specialization yet, the plan starts one, all from one track", () => {
  const marks = done("CMPE 252", "CMPE 257", "ISE 201", "CMPE 294");
  const plan = planNext(msai, marks, resolveChoices(msai, marks, {}), 2);
  const [slot] = plan.slots;
  assert.equal(slot.requirement.id, "specialization");
  assert.equal(slot.count, 2);
  assert.match(slot.pool ?? "", /one specialization/);
  for (const c of ["CMPE 256", "CMPE 209", "CMPE 249"]) assert.ok(slot.options.includes(c), c);
  assert.ok(plan.notes.some((n) => n.text.includes("set by the courses you take")));
});

test("once the courses set it, the plan finishes that specialization", () => {
  const marks = done("CMPE 252", "CMPE 257", "ISE 201", "CMPE 294", "CMPE 249");
  const plan = planNext(msai, marks, resolveChoices(msai, marks, {}), 1);
  assert.deepEqual(plan.slots[0].options, ["CMPE 258", "CMPE 260"]); // the rest of Autonomous Systems
});

test("the load bounds the plan", () => {
  for (const load of [1, 2, 3, 4]) {
    const plan = planNext(msai, {}, { track: "autonomous-systems", plan: "project" }, load);
    assert.equal(plan.slots.reduce((n, s) => n + s.count, 0), load);
  }
});

test("MS AI electives plan Area A first while its minimum is unmet", () => {
  const marks = done("CMPE 252", "CMPE 257", "ISE 201", "CMPE 294", "CMPE 256", "CMPE 259", "CMPE 214", "CMPE 217");
  // No project/thesis chosen, so the culminating experience isn't planned ahead of electives.
  const plan = planNext(msai, marks, { track: "intelligent-data-systems" }, 1);
  assert.equal(plan.slots.length, 1);
  const [slot] = plan.slots;
  assert.equal(slot.requirement.id, "electives");
  assert.match(slot.pool ?? "", /Area A/);
  assert.ok(slot.options.every((c) => msai.requirements[3].pools![0].from!.includes(c)));
  assert.ok(!slot.options.includes("CMPE 256")); // already done
});

test("the master's project opens after 15 units and the core, and 295B follows 295A", () => {
  const choices = { track: "intelligent-data-systems", plan: "project" };
  const early = planNext(msai, done("CMPE 252", "CMPE 257"), choices, 4);
  assert.ok(!early.slots.some((s) => s.options.includes("CMPE 295A")));
  assert.ok(early.notes.some((n) => n.course === "CMPE 295A"));

  const ready = done("CMPE 252", "CMPE 257", "ISE 201", "CMPE 294", "CMPE 256");
  const plan = planNext(msai, ready, choices, 3);
  assert.deepEqual(plan.slots[0].options, ["CMPE 295A"]);
  assert.ok(!plan.slots.some((s) => s.options.includes("CMPE 295B")));

  const next = planNext(msai, { ...ready, "CMPE 295A": "taking" }, choices, 3);
  assert.deepEqual(next.slots[0].options, ["CMPE 295B"]);
});

test("MS SE: CMPE 295A waits for the specialization core, as the department requires", () => {
  const choices = { track: "data-science", plan: "project" };
  // 15 units, but only one specialization core class.
  const marks = done("CMPE 202", "CMPE 272", "CMPE 294", "CMPE 257", "CMPE 220");
  const plan = planNext(msse, marks, choices, 4);
  assert.ok(!plan.slots.some((s) => s.options.includes("CMPE 295A")));
  assert.deepEqual(plan.slots.find((s) => s.requirement.id === "specialization-core")?.options, ["CMPE 258"]);
});

test("MS SE Cybersecurity plans one of 209/219 and CMPE 279, never 209 and 219 together", () => {
  const plan = planNext(msse, done("CMPE 202", "CMPE 272"), { track: "cybersecurity" }, 4);
  const core = plan.slots.filter((s) => s.requirement.id === "specialization-core");
  assert.deepEqual(core.map((s) => [s.count, s.options]), [
    [1, ["CMPE 209", "CMPE 219"]],
    [1, ["CMPE 279"]],
  ]);
});

test("MS CS plans CS 200W first, then one course from each core area", () => {
  const plan = planNext(mscs, {}, { plan: "project" }, 4);
  assert.deepEqual(plan.slots.map((s) => s.requirement.id), ["gwar", "foundations", "architecture", "systems"]);
  assert.deepEqual(plan.slots[0].options, ["CS 200W"]);
  assert.equal(plan.slots[1].kind, "choose");
});

test("MS CS: CS 298 only after CS 297", () => {
  const firstYear = done("CS 200W", "CS 255", "CS 247", "CS 249", "CS 256", "CS 265");
  const plan = planNext(mscs, firstYear, { plan: "project" }, 3);
  assert.ok(plan.slots.some((s) => s.options.includes("CS 297")));
  assert.ok(!plan.slots.some((s) => s.options.includes("CS 298")));
  const later = planNext(mscs, { ...firstYear, "CS 297": "done" }, { plan: "project" }, 3);
  assert.ok(later.slots.some((s) => s.options.includes("CS 298")));
});

test("a finished degree plans nothing", () => {
  const all = done(
    "CMPE 252", "CMPE 257", "ISE 201", "CMPE 294", "CMPE 256", "CMPE 259",
    "CMPE 258", "CMPE 214", "CMPE 217", "CMPE 295A", "CMPE 295B",
  );
  const plan = planNext(msai, all, { track: "intelligent-data-systems", plan: "project" }, 4);
  assert.equal(plan.unitsAfterThisTerm, 33);
  assert.deepEqual(plan.slots, []);
});

// -- terms ----------------------------------------------------------------------

test("next term: autumn plans spring, the first half of the year plans fall", () => {
  assert.deepEqual(nextTerm(new Date(2026, 9, 2)), { label: "Spring 2027", key: "spring-2027" });
  assert.deepEqual(nextTerm(new Date(2027, 2, 1)), { label: "Fall 2027", key: "fall-2027" });
  assert.equal(scheduleUrl("spring-2027"), "https://www.sjsu.edu/classes/schedules/spring-2027.php");
});

// -- against an official report --------------------------------------------------

// A real MS AI MyProgress report, reduced to course codes and Done/Taking (no
// grades, terms or identity). MyProgress shows: core 252/257/ISE 201, GWAR 294,
// specialization "Data Science" 258 + 256, electives 260 (Area A) + 255 + 259
// (Area A or B), project 295A done and 295B in progress.
test("an official MyProgress report is reproduced", () => {
  const marks = {
    "CMPE 252": "done", "CMPE 257": "done", "ISE 201": "done", "CMPE 294": "done",
    "CMPE 258": "done", "CMPE 256": "done", "CMPE 260": "done",
    "CMPE 295A": "done", "CMPE 255": "done", "CMPE 259": "done",
    "CMPE 295B": "taking",
  };
  const choices = resolveChoices(msai, marks, { plan: "project" });
  assert.equal(choices.track, "intelligent-data-systems");
  const ev = evaluate(msai, marks, choices);
  for (const rp of ev.requirements) assert.ok(rp.complete, rp.requirement.id);
  assert.deepEqual(req(ev, "specialization").assigned.map((a) => a.code).sort(), ["CMPE 256", "CMPE 258"]);
  assert.deepEqual(req(ev, "electives").assigned.map((a) => a.code).sort(), ["CMPE 255", "CMPE 259", "CMPE 260"]);
  assert.deepEqual(ev.uncounted, []);
  assert.equal(ev.doneUnits, 30);
  assert.equal(ev.takingUnits, 3);
  assert.deepEqual(planNext(msai, marks, choices, 3).slots, []);
});

test("a tie between two complete specializations still decides; an incomplete tie doesn't", () => {
  // 258 + 260 complete Autonomous Systems; 256 + 259 would complete IDS. More IDS courses listed.
  assert.equal(inferChoice(msai, done("CMPE 256", "CMPE 258", "CMPE 259", "CMPE 260"), "track"), "intelligent-data-systems");
  assert.equal(inferChoice(msai, done("CMPE 258"), "track"), null);
});

// -- v2: pool minimums, status, search, plan text, planned ------------------------

// A minimal program whose pool minimum isn't implied by another pool's maximum.
const synthetic: GradProgram = {
  id: "syn", level: "graduate", name: "Synthetic", department: "X", total_units: 6,
  choices: [], prerequisites: {}, sources: [], built_at: "2026-10-02",
  courses: Object.fromEntries(["A 1", "A 2", "B 1", "B 2", "B 3"].map((c) => [c, { title: c, units: 3 }])),
  requirements: [{
    id: "electives", label: "Electives", units: 6,
    pools: [
      { id: "a", label: "Area A (at least 3 units)", from: ["A 1", "A 2"], min_units: 3 },
      { id: "b", label: "Area B", from: ["B 1", "B 2", "B 3"] },
    ],
  }],
};

test("a pool minimum must be met before the requirement is complete", () => {
  const ev = evaluate(synthetic, done("B 1", "B 2"), {});
  const rp = req(ev, "electives");
  assert.equal(rp.complete, false);
  assert.equal(rp.doneUnits, 3); // room is kept for Area A
  assert.deepEqual(ev.uncounted.map((u) => u.code), ["B 2"]);
  assert.equal(shortfall(rp), "1 more course from Area A");
  const plan = planNext(synthetic, done("B 1", "B 2"), {}, 2);
  assert.match(plan.slots[0].pool ?? "", /Area A/);
  assert.deepEqual(plan.slots[0].options, ["A 1", "A 2"]);
});

test("with the minimum met, the requirement completes", () => {
  const ev = evaluate(synthetic, done("A 1", "B 1"), {});
  assert.equal(req(ev, "electives").complete, true);
  assert.equal(shortfall(req(ev, "electives")), null);
});

const REPORT = {
  "CMPE 252": "done", "CMPE 257": "done", "ISE 201": "done", "CMPE 294": "done",
  "CMPE 258": "done", "CMPE 256": "done", "CMPE 260": "done",
  "CMPE 295A": "done", "CMPE 255": "done", "CMPE 259": "done",
  "CMPE 295B": "taking",
};

test("completion never says done: covered only with Taking is 'covered-if-passed'", () => {
  const choices = resolveChoices(msai, REPORT, { plan: "project" });
  assert.equal(completion(evaluate(msai, REPORT, choices)), "covered-if-passed");
  assert.equal(completion(evaluate(msai, { ...REPORT, "CMPE 295B": "done" }, choices)), "covered");
  assert.equal(completion(evaluate(msai, {}, {})), "open");
});

test("summarize mirrors MyProgress: the project is in progress, the rest complete", () => {
  const sum = summarize(evaluate(msai, REPORT, resolveChoices(msai, REPORT, { plan: "project" })));
  assert.equal(sum.complete, 4);
  assert.equal(sum.inProgress, 1);
  assert.equal(sum.open, 0);
  assert.equal(sum.items.find((i) => i.id === "culminating")?.state, "in-progress");
  const empty = summarize(evaluate(msai, {}, {}));
  assert.deepEqual([empty.complete, empty.inProgress, empty.open], [0, 0, 5]);
});

test("shortfall speaks in courses", () => {
  const ev = evaluate(msai, done("CMPE 252"), {});
  assert.equal(shortfall(req(ev, "core")), "2 more courses");
  assert.equal(shortfall(req(ev, "gwar")), "1 more course");
});

test("search finds codes by prefix, then titles, then offers an unlisted code", () => {
  const hits = searchCourses(msai, "cmpe 25");
  assert.ok(hits.length > 0 && hits.every((h) => h.code.startsWith("CMPE 25")));
  assert.deepEqual(searchCourses(msai, "deep").map((h) => h.code), ["CMPE 258"]);
  assert.ok(searchCourses(msai, "deep")[0].requirements.includes("Specialization"));
  const other = searchCourses(msai, "cmpe220");
  assert.deepEqual(other.at(-1), { code: "CMPE 220", title: null, requirements: [], other: true });
  assert.deepEqual(searchCourses(msai, "  "), []);
});

test("planned courses never count, but show in a 'with your plan' projection and on the plan", () => {
  const marks = { ...done("CMPE 252", "CMPE 257"), "ISE 201": "planned" };
  const ev = evaluate(msai, marks, {});
  assert.equal(ev.doneUnits, 6);
  assert.equal(ev.takingUnits, 0);
  assert.deepEqual(ev.uncounted, []); // planned isn't "marked but uncounted" either
  const projected = evaluate(msai, withPlanned(marks), {});
  assert.equal(projected.doneUnits + projected.takingUnits, 9);
  const plan = planNext(msai, marks, {}, 3);
  assert.deepEqual(plan.slots[0].planned, ["ISE 201"]);
});

test("the plan as text names the courses and the authority", () => {
  const plan = planNext(msai, {}, {}, 3);
  const text = planText(msai, plan, { label: "Spring 2027" }, evaluate(msai, {}, {}));
  assert.match(text, /^MS Artificial Intelligence: 0 of 33 units/);
  assert.match(text, /Plan for Spring 2027:/);
  assert.match(text, /- CMPE 252 Artificial Intelligence and Data Engineering \(Core courses\)/);
  assert.match(text, /MyProgress and your advisor are authoritative/);
});
