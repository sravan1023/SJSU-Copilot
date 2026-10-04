import { test } from "node:test";
import assert from "node:assert/strict";
import {
  REPORT_CHAR_BUDGET,
  addCourse,
  countByStatus,
  emptyReport,
  isEmptyReport,
  MAX_COURSES,
  normalizeCourseCode,
  reportChars,
  rolloverDue,
  sameReport,
  sanitizeReport,
  setStatus,
  termOfDay,
} from "./selfReport.ts";

// -- course codes ---------------------------------------------------------------

test("course codes normalise to the keys.course_key form", () => {
  assert.equal(normalizeCourseCode("cs146"), "CS 146");
  assert.equal(normalizeCourseCode("cs 146"), "CS 146");
  assert.equal(normalizeCourseCode("  CS   146 "), "CS 146");
  assert.equal(normalizeCourseCode("engl 1a"), "ENGL 1A");
  assert.equal(normalizeCourseCode("ENGL100W"), "ENGL 100W");
  assert.equal(normalizeCourseCode("bus1 170"), "BUS1 170");
  assert.equal(normalizeCourseCode("aas 18aw"), "AAS 18AW");
});

test("leading zeros go, as on the class schedule", () => {
  assert.equal(normalizeCourseCode("AE 015"), "AE 15");
  assert.equal(normalizeCourseCode("ae015"), "AE 15");
  assert.equal(normalizeCourseCode("ENGR 0"), "ENGR 0");
});

test("text not shaped like a course code is rejected", () => {
  for (const bad of ["", "CS", "146", "computer science", "CS 1460", "CS-146", "CS 146; drop table", "1CS 146", "TOOLONGX 1"]) {
    assert.equal(normalizeCourseCode(bad), null, bad);
  }
});

// -- marking --------------------------------------------------------------------

test("setStatus marks, changes and clears, and never mutates", () => {
  const start = { "CS 146": "need" } as const;
  const marked = setStatus(start, "CS 151", "taking");
  assert.deepEqual(marked, { "CS 146": "need", "CS 151": "taking" });
  assert.deepEqual(setStatus(marked, "CS 146", "done")["CS 146"], "done");
  assert.deepEqual(setStatus(marked, "CS 146", null), { "CS 151": "taking" });
  assert.deepEqual(start, { "CS 146": "need" });
});

test("addCourse adds a typed code as 'need' or says why not", () => {
  const listed = new Set(["AE 100"]);
  const ok = addCourse({}, "math 123", listed);
  assert.deepEqual(ok, { courses: { "MATH 123": "need" }, code: "MATH 123", error: null });
  assert.match(addCourse({}, "ae100", listed).error ?? "", /already in your program's list/);
  assert.match(addCourse({ "MATH 123": "done" }, "MATH 123").error ?? "", /already in your list/);
  assert.match(addCourse({}, "nope").error ?? "", /course code like/);
  const full = Object.fromEntries(Array.from({ length: MAX_COURSES }, (_, i) => [`CS ${i + 1}`, "need" as const]));
  assert.match(addCourse(full, "MATH 1").error ?? "", new RegExp(`up to ${MAX_COURSES}`));
});

test("countByStatus counts what was marked", () => {
  assert.deepEqual(countByStatus({ "A 1": "done", "A 2": "done", "A 3": "need" }), { done: 2, taking: 0, need: 1, planned: 0 });
  assert.deepEqual(countByStatus({}), { done: 0, taking: 0, need: 0, planned: 0 });
});

// -- sanitizing -----------------------------------------------------------------

test("sanitizeReport drops what it can't trust and caps the map", () => {
  const courses: Record<string, unknown> = {
    cs146: "done",
    "CS 146": "need", // a duplicate after normalising: the first one wins
    garbage: "done",
    "MATH 30": "finished", // not a status
  };
  for (let i = 1; i <= 200; i += 1) courses[`PHYS ${i}`] = "need";
  const r = sanitizeReport({
    version: 1,
    courses,
    updated_at: "yesterday",
    name: "Ada Lovelace",
  });
  assert.equal(r.courses["CS 146"], "done");
  assert.ok(!("MATH 30" in r.courses));
  assert.equal(Object.keys(r.courses).length, MAX_COURSES);
  assert.equal(r.updated_at, null);
  assert.ok(!("name" in r));
  for (const gone of ["program", "catalog_year", "units_completed"]) {
    assert.ok(!(gone in sanitizeReport({ [gone]: gone === "units_completed" ? 64 : "x" })), gone); // no longer asked
  }
});

test("sanitizeReport keeps a good report as it is", () => {
  const good = {
    version: 1 as const,
    courses: { "AE 15": "done" as const, "MATH 31": "taking" as const },
    choices: { track: "intelligent-data-systems", plan: "project" },
    load: 2,
    updated_at: "2026-10-01",
  };
  assert.deepEqual(sanitizeReport(good), good);
});

test("sanitizeReport turns junk into an empty report", () => {
  for (const junk of [null, undefined, "x", 3, [], { courses: ["CS 146"] }]) {
    assert.deepEqual(sanitizeReport(junk), emptyReport());
  }
});

test("isEmptyReport is true only when nothing has been entered", () => {
  assert.ok(isEmptyReport(emptyReport()));
  assert.ok(!isEmptyReport({ ...emptyReport(), courses: { "CS 146": "need" } }));
});

test("choices and load are kept only when well-formed", () => {
  const r = sanitizeReport({
    courses: {},
    choices: { track: "intelligent-data-systems", plan: "Project!", "bad key": "x", n: 3 },
    load: 9,
  });
  assert.deepEqual(r.choices, { track: "intelligent-data-systems" });
  assert.equal(r.load, 3);
  assert.equal(sanitizeReport({ load: 1 }).load, 1);
  assert.equal(sanitizeReport({ load: 2.5 }).load, 3);
  assert.ok(!isEmptyReport({ ...emptyReport(), choices: { plan: "thesis" } }));
});

test("sameReport ignores the save date and key order, not content", () => {
  const a = { ...emptyReport(), courses: { "CS 1": "done" as const, "CS 2": "taking" as const }, updated_at: "2026-10-01" };
  const b = { ...emptyReport(), courses: { "CS 2": "taking" as const, "CS 1": "done" as const }, updated_at: "2026-10-02" };
  assert.ok(sameReport(a, b));
  assert.ok(!sameReport(a, { ...b, load: 2 }));
  assert.ok(!sameReport(a, { ...b, courses: { "CS 1": "done" } }));
});

test("the largest record stays inside its share of the 8 KB details cap", () => {
  const courses = Object.fromEntries(
    Array.from({ length: MAX_COURSES }, (_, i) => [`CMPE ${100 + i}W`, "planned" as const]),
  );
  const biggest = { ...emptyReport(), courses, choices: { plan: "project" }, load: 4, updated_at: "2026-10-02" };
  assert.ok(reportChars(biggest) <= REPORT_CHAR_BUDGET, String(reportChars(biggest)));
});

test("terms: Jan-May spring, Jun-Jul summer, Aug-Dec fall", () => {
  assert.equal(termOfDay("2026-10-02"), "fall-2026");
  assert.equal(termOfDay("2027-02-01"), "spring-2027");
  assert.equal(termOfDay("2027-06-15"), "summer-2027");
  assert.equal(termOfDay(new Date(2026, 7, 1)), "fall-2026");
  assert.equal(termOfDay("yesterday"), "");
});

test("a new term asks about Taking and Planned courses saved last term", () => {
  const r = { ...emptyReport(), courses: { "CMPE 295B": "taking" as const }, updated_at: "2026-10-02" };
  assert.ok(rolloverDue(r, new Date(2027, 1, 1)));
  assert.ok(!rolloverDue(r, new Date(2026, 11, 1))); // same term
  assert.ok(!rolloverDue({ ...r, courses: { "CMPE 295B": "done" } }, new Date(2027, 1, 1)));
  assert.ok(rolloverDue({ ...r, courses: { "CMPE 295B": "planned" } }, new Date(2027, 1, 1)));
  assert.ok(!rolloverDue({ ...r, updated_at: null }, new Date(2027, 1, 1)));
});
