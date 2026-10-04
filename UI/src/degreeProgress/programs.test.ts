import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { groupCourses, matchProgram, normalizeMajor, type ProgramData, type ProgramIndexEntry } from "./programs.ts";
import { normalizeCourseCode } from "./selfReport.ts";

// The real built files, so a change to the builder's output shape breaks here.
function built(id: string): ProgramData {
  return JSON.parse(readFileSync(new URL(`../../public/data/programs/${id}.json`, import.meta.url), "utf8"));
}

test("a page without sections is grouped by subject, own subject first", () => {
  const groups = groupCourses(built("bsae"));
  assert.equal(groups[0].title, "AE courses");
  assert.deepEqual(
    groups.map((g) => g.title),
    ["AE courses", "MATH courses", "CHEM courses", "PHYS courses", "ENGR courses", "EE courses"],
  );
  assert.equal(groups.reduce((n, g) => n + g.courses.length, 0), 42);
});

test("a page with sections keeps them, in page order", () => {
  const groups = groupCourses(built("bsee"));
  assert.deepEqual(groups.map((g) => [g.title, g.courses.length]), [
    ["Required EE Courses", 17],
    ["Technical Electives", 20],
  ]);
});

test("every built code is already in the form the page stores", () => {
  const index = JSON.parse(readFileSync(new URL("../../public/data/programs/index.json", import.meta.url), "utf8"));
  for (const entry of index.programs) {
    const data = JSON.parse(readFileSync(new URL(`../../public/data/programs/${entry.id}.json`, import.meta.url), "utf8"));
    // Undergraduate files list courses; graduate files key them in a rule file.
    const codes: string[] = entry.level === "graduate" ? Object.keys(data.courses) : data.courses.map((c: { code: string }) => c.code);
    assert.equal(codes.length, entry.course_count, entry.id);
    for (const code of codes) assert.equal(normalizeCourseCode(code), code, `${entry.id} ${code}`);
  }
});

test("ungrouped courses on a sectioned page still appear", () => {
  const groups = groupCourses({
    own_subjects: [],
    courses: [
      { code: "EE 98", title: "Circuits", group: "Required" },
      { code: "EE 1", title: "Intro", group: null },
    ],
  });
  assert.deepEqual(groups.map((g) => g.title), ["Required", "Other listed courses"]);
});

// -- the profile's major --------------------------------------------------------

function index(): ProgramIndexEntry[] {
  return JSON.parse(readFileSync(new URL("../../public/data/programs/index.json", import.meta.url), "utf8")).programs;
}

test("normalizeMajor also drops a master's prefix or suffix", () => {
  for (const typed of ["MS Artificial Intelligence", "M.S. Artificial Intelligence", "Master of Science in Artificial Intelligence", "Masters in Artificial Intelligence", "Artificial Intelligence (MS)"]) {
    assert.equal(normalizeMajor(typed), "artificial intelligence", typed);
  }
});

test("normalizeMajor ignores case, punctuation, '&' and a degree prefix or suffix", () => {
  for (const typed of [
    "Aerospace Engineering", "aerospace engineering", "B.S. Aerospace Engineering", "BS Aerospace Engineering",
    "Bachelor of Science in Aerospace Engineering", "Aerospace Engineering (BS)", "  Aerospace   Engineering major ",
  ]) {
    assert.equal(normalizeMajor(typed), "aerospace engineering", typed);
  }
  assert.equal(normalizeMajor("Industrial & Systems Engineering"), "industrial and systems engineering");
  assert.equal(normalizeMajor("BS"), "");
});

test("the profile's major picks its program by name or alias, within a level", () => {
  const programs = index();
  const id = (major: string | null) => matchProgram(major, programs, "undergraduate")?.id ?? null;
  assert.equal(id("Aerospace Engineering"), "bsae");
  assert.equal(id("aerospace"), "bsae");
  assert.equal(id("BSAE"), "bsae");
  assert.equal(id("Computer Engineering"), "bscmpe");
  assert.equal(id("Software Engineering"), "bsse");
  assert.equal(id("Electrical Engineering"), "bsee");
  assert.equal(id("Industrial & Systems Engineering"), "bsise");
  assert.equal(id("Biomedical Engineering"), "bsbme");
  const grad = (major: string) => matchProgram(major, programs, "graduate")?.id ?? null;
  assert.equal(grad("Artificial Intelligence"), "msai");
  assert.equal(grad("MS AI"), "msai");
  assert.equal(grad("Software Engineering"), "msse"); // not BS Software Engineering
  assert.equal(grad("Computer Science"), "mscs");
  assert.equal(grad("Computer Engineering"), null); // MS CMPE isn't covered
});

test("a major with no list, or a vague one, matches nothing rather than the wrong list", () => {
  const programs = index();
  for (const major of ["Computer Science", "Engineering", "Mechanical Engineering", "", "   ", null, undefined]) {
    assert.equal(matchProgram(major, programs, "undergraduate"), null, String(major));
  }
  for (const major of ["Engineering", "Aerospace Engineering", "Data Science"]) {
    assert.equal(matchProgram(major, programs, "graduate"), null, major);
  }
});
