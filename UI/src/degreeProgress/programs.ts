// Degree program course lists, built offline from department pages by
// backend/campus/programs/build.py and served as static files from
// UI/public/data/programs/. Not bundled: a list loads when a student whose
// major has one opens Degree Progress.

/** The shape of a program id in /data/programs (and of its file name). */
export const PROGRAM_ID = /^[a-z0-9-]{1,40}$/;

export interface ProgramCourse {
  code: string;
  title: string | null;
  /** The department page's own section heading, when the page has them. */
  group: string | null;
}

export interface ProgramSource {
  url: string;
  last_updated: string | null;
}

export interface ProgramData {
  id: string;
  name: string;
  department: string;
  college: string;
  own_subjects: string[];
  built_at: string;
  sources: ProgramSource[];
  courses: ProgramCourse[];
}

export type ProgramLevel = "undergraduate" | "graduate";

export interface ProgramIndexEntry {
  id: string;
  level: ProgramLevel;
  name: string;
  /** What a student might type as their major ("Aerospace Engineering", "BSAE"). */
  aliases: string[];
  department: string;
  college: string;
  course_count: number;
  /** Graduate programs only. */
  total_units?: number;
  last_updated: string | null;
}

export interface CourseGroup {
  title: string;
  courses: ProgramCourse[];
}

function dataUrl(file: string): string {
  // BASE_URL is "/" unless the app is served from a sub-path.
  const base = import.meta.env?.BASE_URL ?? "/";
  return `${base.replace(/\/?$/, "/")}data/programs/${file}`;
}

async function getJson<T>(file: string): Promise<T> {
  const res = await fetch(dataUrl(file), { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`Could not load ${file} (HTTP ${res.status}).`);
  return (await res.json()) as T;
}

export async function fetchProgramIndex(): Promise<ProgramIndexEntry[]> {
  const index = await getJson<{ programs?: ProgramIndexEntry[] }>("index.json");
  return Array.isArray(index.programs) ? index.programs : [];
}

export async function fetchProgram(id: string): Promise<ProgramData> {
  if (!PROGRAM_ID.test(id)) throw new Error("Unknown program.");
  return getJson<ProgramData>(`${id}.json`);
}

/**
 * A major as typed, reduced for comparison: case, punctuation and "&" vs "and"
 * ignored, and a degree prefix or suffix dropped. "B.S. Aerospace Engineering",
 * "aerospace engineering (BS)" and "Aerospace Engineering" all become
 * "aerospace engineering".
 */
export function normalizeMajor(text: string): string {
  const words = text.toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ").trim();
  let s = ` ${words} `;
  s = s.replace(/^ (?:(?:bachelor|master)s? of science(?: in)?|masters? in|b s|bs|m s|ms) /, " ");
  s = s.replace(/ (?:bs|b s|ms|m s|major|degree|program) $/, " ");
  return s.trim();
}

/**
 * The program of `level` whose name or alias is the student's major, or null.
 * Exact after normalising, so a vague major ("Engineering") matches nothing
 * rather than the wrong program. The level matters: "Software Engineering" is
 * both a BS and an MS.
 */
export function matchProgram(
  major: string | null | undefined,
  programs: readonly ProgramIndexEntry[],
  level: ProgramLevel,
): ProgramIndexEntry | null {
  const want = normalizeMajor(major ?? "");
  if (!want) return null;
  const hits = programs.filter((p) =>
    (p.level ?? "undergraduate") === level &&
    [p.name, ...(p.aliases ?? [])].some((name) => normalizeMajor(name) === want),
  );
  return hits.length === 1 ? hits[0] : null;
}

function subjectOf(code: string): string {
  return code.split(" ", 1)[0];
}

/**
 * The course list as the page shows it. A department page that has its own
 * sections ("Required EE Courses", "Technical Electives") keeps them, in page
 * order; otherwise courses are grouped by subject, with the program's own
 * subject first ("AE courses", then "MATH courses", ...).
 */
export function groupCourses(data: Pick<ProgramData, "courses" | "own_subjects">): CourseGroup[] {
  const groups = new Map<string, ProgramCourse[]>();
  const add = (title: string, course: ProgramCourse) => {
    const list = groups.get(title);
    if (list) list.push(course);
    else groups.set(title, [course]);
  };

  if (data.courses.some((c) => c.group)) {
    for (const c of data.courses) add(c.group ?? "Other listed courses", c);
    return [...groups].map(([title, courses]) => ({ title, courses }));
  }

  const own = new Set(data.own_subjects);
  const ordered = [
    ...data.courses.filter((c) => own.has(subjectOf(c.code))),
    ...data.courses.filter((c) => !own.has(subjectOf(c.code))),
  ];
  for (const c of ordered) add(`${subjectOf(c.code)} courses`, c);
  return [...groups].map(([title, courses]) => ({ title, courses }));
}
