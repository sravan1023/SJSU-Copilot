// Self-reported degree progress: what a student marks on the Degree Progress
// page. Pure functions, no I/O.
//
// Stored in profile_audience_details.details.degree_progress (owner-only RLS;
// the whole blob is capped at 8 KB, so only course codes are kept, never
// titles). The program is not stored: it follows the major in the student's
// profile (programs.ts matchProgram). It is a service page like Settings and is
// not sent to chat.
//
// Progress against a graduate program's rules is computed from this record by
// requirements.ts; this module only keeps the record well-formed. MyProgress
// and the student's advisor are authoritative.

// "planned" is next term's intent: shown in a "with your plan" projection,
// never counted as Done or Taking (requirements.ts evaluate ignores it).
export type CourseStatus = "done" | "taking" | "need" | "planned";
export const STATUSES: readonly CourseStatus[] = ["done", "taking", "need", "planned"];

/** Marked courses kept per student: a whole degree plus room for extras. */
export const MAX_COURSES = 120;
/** Courses the next-semester plan may hold, and its default (a full-time grad load). */
export const MIN_LOAD = 1;
export const MAX_LOAD = 4;
export const DEFAULT_LOAD = 3;
const CHOICE_TOKEN = /^[a-z0-9-]{1,40}$/;
const MAX_CHOICES = 10;

export interface SelfReport {
  version: 1;
  /** Course code in keys.course_key form ("CS 146") -> what the student marked. */
  courses: Record<string, CourseStatus>;
  /** The program's choices, e.g. { track: "intelligent-data-systems", plan: "project" }. */
  choices: Record<string, string>;
  /** How many courses to plan for next semester. */
  load: number;
  /** YYYY-MM-DD of the last save. */
  updated_at: string | null;
}

export function emptyReport(): SelfReport {
  return {
    version: 1,
    courses: {},
    choices: {},
    load: DEFAULT_LOAD,
    updated_at: null,
  };
}

// -- course codes ---------------------------------------------------------------

// The shapes on SJSU's schedule: a subject of 1-6 characters starting with a
// letter (CS, CMPE, BUS1) and a number of 1-3 digits with up to 3 letters
// (146, 1A, 100W, 18AW). Measured on the Fall 2026 schedule fixture.
const SPACED = /^([A-Z][A-Z0-9]{0,5}) (\d{1,3}[A-Z]{0,3})$/;
// Without a space the split is ambiguous for subjects that end in a digit
// (BUS1170), so the subject is taken to be letters only: "cs146" -> "CS 146".
const UNSPACED = /^([A-Z]{1,6})(\d{1,3}[A-Z]{0,3})$/;

/**
 * "cs146", "cs 146" or " CS  146 " -> "CS 146", the form
 * backend/campus/registration/keys.course_key produces. Leading zeros go, as
 * the department pages and the schedule disagree on them ("AE 015" is "AE 15").
 * Null when the text is not shaped like a course code.
 */
export function normalizeCourseCode(raw: string): string | null {
  const text = raw.trim().replace(/\s+/g, " ").toUpperCase();
  const m = SPACED.exec(text) ?? UNSPACED.exec(text);
  if (!m) return null;
  const number = m[2].replace(/^0+(?=\d)/, "");
  return `${m[1]} ${number}`;
}

/** Mark a course, or clear it with null. Returns a new map; never mutates. */
export function setStatus(
  courses: Readonly<Record<string, CourseStatus>>,
  code: string,
  status: CourseStatus | null,
): Record<string, CourseStatus> {
  const next = { ...courses };
  if (status === null) delete next[code];
  else next[code] = status;
  return next;
}

export type AddResult = { courses: Record<string, CourseStatus>; code: string | null; error: string | null };

/**
 * Add a typed course code as "need", or say why not. `listed` is the codes the
 * chosen program already shows, which are marked in place rather than added.
 */
export function addCourse(
  courses: Readonly<Record<string, CourseStatus>>,
  raw: string,
  listed: ReadonlySet<string> = new Set(),
): AddResult {
  const code = normalizeCourseCode(raw);
  const same = { ...courses };
  if (!code) return { courses: same, code: null, error: "Use a course code like CS 146." };
  if (listed.has(code)) return { courses: same, code, error: `${code} is already in your program's list above.` };
  if (code in courses) return { courses: same, code, error: `${code} is already in your list.` };
  if (Object.keys(courses).length >= MAX_COURSES) {
    return { courses: same, code, error: `You can mark up to ${MAX_COURSES} courses.` };
  }
  return { courses: setStatus(courses, code, "need"), code, error: null };
}

export function countByStatus(courses: Readonly<Record<string, CourseStatus>>): Record<CourseStatus, number> {
  const counts: Record<CourseStatus, number> = { done: 0, taking: 0, need: 0, planned: 0 };
  for (const status of Object.values(courses)) counts[status] += 1;
  return counts;
}

// -- loading and saving ---------------------------------------------------------

function courseMap(value: unknown): Record<string, CourseStatus> {
  const out: Record<string, CourseStatus> = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return out;
  let n = 0;
  for (const [raw, status] of Object.entries(value as Record<string, unknown>)) {
    const code = normalizeCourseCode(raw);
    if (!code || code in out || !STATUSES.includes(status as CourseStatus)) continue;
    out[code] = status as CourseStatus;
    n += 1;
    if (n === MAX_COURSES) break;
  }
  return out;
}

function choiceMap(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return out;
  for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
    if (Object.keys(out).length === MAX_CHOICES) break;
    if (CHOICE_TOKEN.test(key) && typeof v === "string" && CHOICE_TOKEN.test(v)) out[key] = v;
  }
  return out;
}

/**
 * A stored or edited report, made safe: malformed codes and statuses are
 * dropped and the map is capped. Fields the page no longer asks for (program,
 * catalog_year, units_completed) are not carried over. Anything that isn't a
 * report at all becomes an empty one.
 */
export function sanitizeReport(raw: unknown): SelfReport {
  const base = emptyReport();
  if (!raw || typeof raw !== "object") return base;
  const r = raw as Record<string, unknown>;

  return {
    version: 1,
    courses: courseMap(r.courses),
    choices: choiceMap(r.choices),
    load:
      typeof r.load === "number" && Number.isInteger(r.load) && r.load >= MIN_LOAD && r.load <= MAX_LOAD
        ? r.load
        : DEFAULT_LOAD,
    updated_at:
      typeof r.updated_at === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.updated_at) ? r.updated_at : null,
  };
}

/** True when the report says nothing a reader could use. The load is a preference, not data. */
export function isEmptyReport(r: SelfReport): boolean {
  return Object.keys(r.courses).length === 0 && Object.keys(r.choices).length === 0;
}

/** Today as YYYY-MM-DD in the viewer's own calendar. */
export function localDay(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

// -- comparing and sizing ---------------------------------------------------------

function canonical(r: SelfReport): string {
  const s = sanitizeReport(r);
  const sorted = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
  return JSON.stringify({ courses: sorted(s.courses), choices: sorted(s.choices), load: s.load });
}

/** Same content, ignoring when it was saved and the order keys were added in. */
export function sameReport(a: SelfReport, b: SelfReport): boolean {
  return canonical(a) === canonical(b);
}

/**
 * Roughly how many characters the record takes in `details::text`. Postgres
 * prints jsonb with a space after each ':' and ',', which JSON.stringify
 * doesn't, so 2 per key is added.
 */
export function reportChars(r: SelfReport): number {
  let keys = 0;
  const count = (v: unknown) => {
    if (v && typeof v === "object") {
      for (const x of Object.values(v as Record<string, unknown>)) {
        keys += 1;
        count(x);
      }
    }
  };
  count(r);
  return JSON.stringify(r).length + 2 * keys;
}

/**
 * The record's share of the 8,192-character cap on the whole
 * profile_audience_details.details blob (20260917000100), which it shares with
 * the Settings fields.
 */
export const REPORT_CHAR_BUDGET = 6144;

// -- terms ------------------------------------------------------------------------

/** The term a day falls in: Jan-May spring, Jun-Jul summer, Aug-Dec fall. */
export function termOfDay(day: Date | string): string {
  let y: number;
  let m: number;
  if (typeof day === "string") {
    const match = /^(\d{4})-(\d{2})-\d{2}$/.exec(day);
    if (!match) return "";
    y = Number(match[1]);
    m = Number(match[2]) - 1;
  } else {
    y = day.getFullYear();
    m = day.getMonth();
  }
  const season = m <= 4 ? "spring" : m <= 6 ? "summer" : "fall";
  return `${season}-${y}`;
}

/**
 * True when the record was last saved in an earlier term and still holds
 * Taking or Planned courses: time to ask whether they were finished.
 */
export function rolloverDue(r: SelfReport, today: Date = new Date()): boolean {
  if (!r.updated_at) return false;
  const saved = termOfDay(r.updated_at);
  if (!saved || saved === termOfDay(today)) return false;
  return Object.values(r.courses).some((s) => s === "taking" || s === "planned");
}
