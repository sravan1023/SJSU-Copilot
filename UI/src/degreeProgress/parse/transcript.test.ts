import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseTranscript, PARSER_VERSION } from "./transcript.ts";
import { groupItems } from "./extractText.ts";
import type { TextLine } from "../types.ts";

const lines: TextLine[] = JSON.parse(
  readFileSync(new URL("./__fixtures__/synthetic-transcript.json", import.meta.url), "utf8"),
);
const { record, unparsed, parserVersion } = parseTranscript(lines);

test("version is reported", () => {
  assert.equal(parserVersion, PARSER_VERSION);
  assert.equal(record.parserVersion, PARSER_VERSION);
});

test("terms: all four seasons, with major", () => {
  assert.deepEqual(
    record.terms.map((t) => `${t.season} ${t.year}`),
    ["Fall 2022", "Spring 2023", "Summer 2023", "Winter 2024"],
  );
  assert.equal(record.terms[0].major, "MS Computer Science");
  assert.equal(record.terms[2].major, null);
});

test("multi-word title and letter-suffix codes", () => {
  const [a, b] = record.terms[0].courses;
  assert.deepEqual(a, {
    subject: "CS", number: "157C", title: "NoSQL Database Systems",
    ua: 3, ug: 3, ue: 3, grade: "A", gp: 12,
  });
  assert.equal(b.subject + b.number, "MATH30P");
  assert.equal(b.grade, "B+");
  assert.equal(record.terms[2].courses[0].number, "100W");
  assert.equal(record.terms[2].courses[0].grade, "A-");
});

test("non-letter and blank grades", () => {
  assert.equal(record.terms[0].courses[2].grade, "CR");
  assert.equal(record.terms[0].courses[2].gp, null);
  assert.deepEqual(record.terms[1].courses.map((c) => c.grade), ["W", "NC", "I", "RP"]);
  const inProgress = record.terms[3].courses[0];
  assert.equal(inProgress.grade, null);
  assert.equal(inProgress.gp, null);
  assert.equal(inProgress.ua, 3);
});

test("totals are recorded as printed, never computed", () => {
  // Fixture total GPA is deliberately not what the courses would give.
  assert.deepEqual(record.terms[0].totals, { ua: 9, ug: 6, ue: 9, gp: 21.9, gpa: 3.65 });
  assert.equal(record.terms[1].totals, null);
  assert.deepEqual(record.cumulative, [{ ua: 12, ug: 9, ue: 12, gp: 33, gpa: 3.667 }]);
});

test("unmatched lines go to unparsed, with a count", () => {
  assert.deepEqual(unparsed, [{ page: 1, text: "Page 1 of 2 footer thing" }]);
  assert.equal(record.unparsedCount, 1);
  assert.deepEqual(record.unparsed, unparsed);
});

test("identity lines are dropped, not stored", () => {
  const blob = JSON.stringify(record);
  for (const s of ["Fabricated", "000000000", "01/02/2000", "Birth", "Student ID"]) {
    assert.ok(!blob.includes(s), `record leaks ${s}`);
  }
  assert.equal(record.droppedHeaderLines, 4);
  assert.deepEqual(Object.keys(record).sort(), [
    "cumulative", "droppedHeaderLines", "parserVersion", "terms", "unparsed", "unparsedCount",
  ]);
});

test("identity lines after the first term are dropped too", () => {
  const extra: TextLine[] = [
    { page: 1, y: 1, items: [], text: "FALL SEMESTER 2020" },
    { page: 1, y: 2, items: [], text: "Student ID: 123456789" },
    { page: 1, y: 3, items: [], text: "Printed 10/01/2026" },
  ];
  const r = parseTranscript(extra);
  assert.equal(r.record.unparsedCount, 0);
  assert.equal(r.record.droppedHeaderLines, 2);
});

test("empty input yields an empty record", () => {
  const r = parseTranscript([]);
  assert.deepEqual(r.record.terms, []);
  assert.equal(r.record.unparsedCount, 0);
});

test("groupItems: y tolerance, x order, word gaps from x", () => {
  const out = groupItems(1, [
    { str: "3.0", x: 200, y: 700.4, width: 15 },
    { str: "CS", x: 10, y: 700, width: 12 },
    { str: "157C", x: 24, y: 700.2, width: 24 },
    { str: "Next", x: 10, y: 680, width: 20 },
    { str: " ", x: 60, y: 700, width: 3 },
  ]);
  assert.equal(out.length, 2);
  assert.equal(out[0].text, "CS 157C 3.0");
  assert.deepEqual(out[0].items.map((i) => i.x), [10, 24, 200]);
  assert.equal(out[1].text, "Next");
});
