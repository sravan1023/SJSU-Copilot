import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseTranscript, PARSER_VERSION } from "./transcript.ts";
import { groupItems, readPages, type LoadingTask } from "./extractText.ts";
import type { TextLine } from "../types.ts";

const lines: TextLine[] = JSON.parse(
  readFileSync(new URL("./__fixtures__/synthetic-transcript.json", import.meta.url), "utf8"),
);
const { record, parserVersion } = parseTranscript(lines);

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

test("unmatched lines are counted, never stored", () => {
  assert.equal(record.unparsedCount, 1);
  assert.deepEqual(record.unparsedByPage, { 1: 1 });
  assert.ok(!JSON.stringify(record).includes("footer thing"));
});

test("identity lines are dropped, not stored", () => {
  const blob = JSON.stringify(record);
  for (const s of ["Fabricated", "000000000", "01/02/2000", "Birth", "Student ID"]) {
    assert.ok(!blob.includes(s), `record leaks ${s}`);
  }
  assert.equal(record.droppedHeaderLines, 4);
  assert.deepEqual(Object.keys(record).sort(), [
    "cumulative", "droppedHeaderLines", "parserVersion", "terms", "unparsedByPage", "unparsedCount",
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

const L = (text: string, page = 1): TextLine => ({ page, y: 0, items: [], text });

test("identity text never reaches the record (page-2 headers, email, ID tail)", () => {
  const r = parseTranscript([
    L("FALL SEMESTER 2022 / MAJOR: MS Computer Science  Student ID: 012345678"),
    L("CS 157C NoSQL Database Systems 3.0 3.0 3.0 A 12.0"),
    L("Pat Q Fabricated", 2),
    L("Unofficial Transcript for Pat Q Fabricated", 2),
    L("Student: Pat Q Fabricated", 2),
    L("pat.fabricated@example.invalid", 2),
    L("Phone (408) 555-0100", 2),
    L("SPRING SEMESTER 2023 / MAJOR: BS Art 987654321", 2),
    L("Name: Pat Q Fabricated   EMPL ID 123456789", 2),
  ]).record;
  const blob = JSON.stringify(r);
  for (const s of ["Pat", "Fabricated", "012345678", "987654321", "123456789", "example", "555", "Student"]) {
    assert.ok(!blob.includes(s), `record leaks ${s}`);
  }
  assert.equal(r.terms[0].major, "MS Computer Science");
  assert.equal(r.terms[1].major, "BS Art");
  assert.equal(r.terms[0].courses.length, 1);
  assert.equal(r.unparsedCount, 1); // the bare unlabelled name
  assert.deepEqual(r.unparsedByPage, { 2: 1 });
});

test("major is dropped when it is not plain words", () => {
  const r = parseTranscript([L("FALL 2022 / MAJOR: x7y")]).record;
  assert.equal(r.terms[0].major, null);
});

test("readPages destroys the task when loading rejects", async () => {
  let destroyed = 0;
  const task: LoadingTask = {
    promise: Promise.reject(new Error("PasswordException")),
    destroy: async () => { destroyed++; },
  };
  await assert.rejects(readPages(task), /PasswordException/);
  assert.equal(destroyed, 1);
});

test("readPages destroys the task on success and on page failure", async () => {
  let destroyed = 0;
  const ok: LoadingTask = {
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        getTextContent: async () => ({ items: [{ str: "Hi", transform: [1, 0, 0, 1, 10, 700], width: 5 }] }),
      }),
    }),
    destroy: async () => { destroyed++; },
  };
  assert.equal((await readPages(ok))[0].text, "Hi");
  const bad: LoadingTask = {
    promise: Promise.resolve({ numPages: 1, getPage: async () => { throw new Error("x"); } }),
    destroy: async () => { destroyed++; },
  };
  await assert.rejects(readPages(bad));
  assert.equal(destroyed, 2);
});
