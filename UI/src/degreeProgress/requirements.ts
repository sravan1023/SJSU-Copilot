// Graduate degree rules: progress and a next-semester plan. Pure, no I/O.
//
// The rules are hand-written per program (backend/campus/programs/graduate/,
// published to /data/programs/<id>.json). This module only interprets them:
//
//   evaluate()  assigns each course the student marked Done or Taking to one
//               requirement -- most specific requirement first, never counted
//               twice, pool limits ("Area B up to 6 units") respected -- and
//               reports units per requirement and anything that counts nowhere.
//   planNext()  treats Done and Taking as finished by next term and proposes
//               up to `load` courses: required courses by name, otherwise
//               "pick N of ..." with only the options that are still eligible.
//
// It is a guide built from published rules, not an audit: MyProgress and the
// student's advisor are authoritative, and the page says so.

export type GradStatus = "done" | "taking";

export interface CourseInfo {
  title: string;
  units: number;
}

export interface Pool {
  id: string;
  label: string;
  from?: string[];
  from_requirements?: string[];
  min_units?: number;
  max_units?: number;
}

export interface Match {
  subject: string;
  min_number: number;
  exclude?: string[];
}

export interface RuleBody {
  all?: string[];
  from?: string[];
  pools?: Pool[];
  match?: Match;
  from_requirements?: string[];
  suggest?: string[];
}

export interface Requirement extends RuleBody {
  id: string;
  label: string;
  units: number;
  group?: string;
  description?: string;
  choice?: string;
  by_option?: Record<string, RuleBody>;
}

export interface Prerequisite {
  min_units?: number;
  requirements?: string[];
  courses?: string[];
  basis: "documented" | "guide";
  note?: string;
}

export interface Choice {
  id: string;
  label: string;
  options: { id: string; label: string }[];
  /** Not asked: worked out from the courses the student marked (inferChoice). */
  infer?: boolean;
}

export interface GradSource {
  url: string;
  kind: "page" | "catalog-copy";
  last_updated?: string | null;
  copied?: string;
  note?: string;
}

export interface GradProgram {
  id: string;
  level: "graduate";
  name: string;
  department: string;
  total_units: number;
  choices: Choice[];
  courses: Record<string, CourseInfo>;
  requirements: Requirement[];
  prerequisites: Record<string, Prerequisite>;
  notes?: string[];
  /** Requirement groups shown as one section because their course lists overlap. */
  merge_groups?: string[];
  /** Graduation conditions that aren't courses (GWAR, GPA, oral exam), as the sources state them. */
  conditions?: string[];
  sources: GradSource[];
  built_at: string;
}

export type Choices = Readonly<Record<string, string>>;
export type Marks = Readonly<Record<string, string>>; // code -> "done" | "taking" | (ignored)

const DEFAULT_UNITS = 3;

export function unitsOf(program: GradProgram, code: string): number {
  return program.courses[code]?.units ?? DEFAULT_UNITS;
}

export function titleOf(program: GradProgram, code: string): string | null {
  return program.courses[code]?.title ?? null;
}

/** The rule a requirement uses for the student's choices, or null until they choose. */
export function resolveBody(req: Requirement, choices: Choices): RuleBody | null {
  if (!req.choice) return req;
  const option = choices[req.choice];
  return (option && req.by_option?.[option]) || null;
}

/** Choices that name a real option of this program; anything else is dropped. */
export function validChoices(program: GradProgram, choices: Choices): Record<string, string> {
  const out: Record<string, string> = {};
  for (const c of program.choices) {
    const v = choices[c.id];
    if (v && c.options.some((o) => o.id === v)) out[c.id] = v;
  }
  return out;
}

/**
 * The option of an inferred choice (the specialization) that the student's
 * courses point to, or null when they don't point anywhere yet.
 *
 * Each option is tried, and they are ranked by
 *   1. units counted toward the requirements that depend on it,
 *   2. units counted toward the degree overall,
 *   3. how many of the student's courses that option lists.
 * The third settles what MyProgress settles: a student with CMPE 256, 258 and
 * 259 (Intelligent Data Systems) and 258, 260 (Autonomous Systems) gets
 * Intelligent Data Systems, as their report shows ("Data Science").
 *
 * No units is "not decided yet". So is a tie on all three while the
 * specialization is still incomplete -- CMPE 258 alone is in two tracks, and
 * guessing would plan the wrong second course. A tie between options that are
 * both complete takes the first: either one finishes the degree the same way.
 */
export function inferChoice(program: GradProgram, marks: Marks, choiceId: string, base: Choices = {}): string | null {
  const choice = program.choices.find((c) => c.id === choiceId);
  if (!choice) return null;
  const dependent = program.requirements.filter((r) => r.choice === choiceId);
  const needed = dependent.reduce((n, r) => n + r.units, 0);
  const markedCodes = Object.entries(marks).filter(([, s]) => s === "done" || s === "taking").map(([c]) => c);
  const scored = choice.options.map((o) => {
    const ev = evaluate(program, marks, { ...base, [choiceId]: o.id });
    const own = ev.requirements
      .filter((rp) => rp.requirement.choice === choiceId)
      .reduce((n, rp) => n + Math.min(rp.doneUnits + rp.takingUnits, rp.requirement.units), 0);
    const lists = new Set(dependent.flatMap((r) => listed(r.by_option?.[o.id] ?? {})));
    const listedCount = markedCodes.filter((c) => lists.has(c)).length;
    return { id: o.id, own, total: ev.doneUnits + ev.takingUnits, listedCount };
  });
  // Stable sort: a full tie keeps the program's own option order.
  scored.sort((a, b) => b.own - a.own || b.total - a.total || b.listedCount - a.listedCount);
  const [best, next] = scored;
  if (!best || best.own === 0) return null;
  const tied = next && next.own === best.own && next.total === best.total && next.listedCount === best.listedCount;
  if (tied && best.own < needed) return null;
  return best.id;
}

/**
 * The choices to plan with: what the student picked for the choices they're
 * asked, and what their courses say for the inferred ones (never a stored pick).
 */
export function resolveChoices(program: GradProgram, marks: Marks, stored: Choices): Record<string, string> {
  const asked = validChoices(program, stored);
  for (const c of program.choices) if (c.infer) delete asked[c.id];
  const out = { ...asked };
  for (const c of program.choices) {
    if (!c.infer) continue;
    const v = inferChoice(program, marks, c.id, asked);
    if (v) out[c.id] = v;
  }
  return out;
}

export function matchesRule(match: Match, code: string): boolean {
  const [subject, number] = code.split(" ", 2);
  if (subject !== match.subject || !number) return false;
  const n = Number.parseInt(number, 10);
  return Number.isFinite(n) && n >= match.min_number && !(match.exclude ?? []).includes(code);
}

export interface ResolvedPool {
  id: string;
  label: string;
  codes: string[];
  match?: Match;
  min_units?: number;
  max_units?: number;
}

/** Codes a rule body lists, in order (not including from_requirements). */
export function listed(body: RuleBody): string[] {
  const out = [...(body.all ?? []), ...(body.from ?? [])];
  for (const p of body.pools ?? []) out.push(...(p.from ?? []));
  return out;
}

/** Each requirement's pools under these choices (null until its choice is made). */
export function resolvedPools(program: GradProgram, rawChoices: Choices = {}): Record<string, ResolvedPool[] | null> {
  const choices = validChoices(program, rawChoices);
  const out: Record<string, ResolvedPool[] | null> = {};
  for (const req of program.requirements) {
    const body = resolveBody(req, choices);
    out[req.id] = body ? pools(program, body, choices) : null;
  }
  return out;
}

function pools(program: GradProgram, body: RuleBody, choices: Choices): ResolvedPool[] {
  const fromReqs = (ids: string[] | undefined): string[] => {
    const out: string[] = [];
    for (const id of ids ?? []) {
      const other = program.requirements.find((r) => r.id === id);
      const otherBody = other && resolveBody(other, choices);
      if (otherBody) out.push(...listed(otherBody));
    }
    return out;
  };
  if (body.pools?.length) {
    return body.pools.map((p) => ({
      id: p.id,
      label: p.label,
      codes: unique([...(p.from ?? []), ...fromReqs(p.from_requirements)]),
      min_units: p.min_units,
      max_units: p.max_units,
    }));
  }
  return [{
    id: "main",
    label: "",
    codes: unique([...(body.all ?? []), ...(body.from ?? []), ...(body.suggest ?? []), ...fromReqs(body.from_requirements)]),
    match: body.match,
  }];
}

function unique(codes: string[]): string[] {
  return [...new Set(codes)];
}

function inPool(pool: ResolvedPool, code: string): boolean {
  return pool.codes.includes(code) || (!!pool.match && matchesRule(pool.match, code));
}

// Most specific rules take courses first, so a course that two rules accept goes
// to the one that needs it: a required list before a pick list, a pick list
// before pools, explicit lists before "unused courses from above", and those
// before an open "any CMPE 200+" rule.
function tier(body: RuleBody): number {
  if (body.match) return 4;
  if (body.from_requirements?.length || body.pools?.some((p) => p.from_requirements?.length)) return 3;
  if (body.pools?.length) return 2;
  if (body.from?.length) return 1;
  return 0;
}

export interface Assigned {
  code: string;
  status: GradStatus;
  units: number;
  pool: string;
}

export interface RequirementProgress {
  requirement: Requirement;
  /** Null until the student makes the choice this requirement depends on. */
  body: RuleBody | null;
  pools: ResolvedPool[];
  assigned: Assigned[];
  doneUnits: number;
  takingUnits: number;
  complete: boolean;
}

export interface Evaluation {
  requirements: RequirementProgress[];
  /** Marked courses no requirement can use (or that would exceed a limit). */
  uncounted: { code: string; status: GradStatus }[];
  doneUnits: number;
  takingUnits: number;
  totalUnits: number;
}

function markedCourses(marks: Marks): { code: string; status: GradStatus }[] {
  const done: string[] = [];
  const taking: string[] = [];
  for (const [code, s] of Object.entries(marks)) {
    if (s === "done") done.push(code);
    else if (s === "taking") taking.push(code);
  }
  return [
    ...done.sort().map((code) => ({ code, status: "done" as const })),
    ...taking.sort().map((code) => ({ code, status: "taking" as const })),
  ];
}

export function evaluate(program: GradProgram, marks: Marks, rawChoices: Choices = {}): Evaluation {
  const choices = validChoices(program, rawChoices);
  const marked = markedCourses(marks);
  const used = new Set<string>();

  const progress = new Map<string, RequirementProgress>();
  const order = program.requirements
    .map((req, i) => ({ req, i, body: resolveBody(req, choices) }))
    .sort((a, b) => (a.body ? tier(a.body) : 9) - (b.body ? tier(b.body) : 9) || a.i - b.i);

  for (const { req, body } of order) {
    const rp: RequirementProgress = {
      requirement: req, body, pools: body ? pools(program, body, choices) : [],
      assigned: [], doneUnits: 0, takingUnits: 0, complete: false,
    };
    progress.set(req.id, rp);
    if (!body) continue;
    const poolUnits = new Map<string, number>();
    let filled = 0;
    // Candidates in the rule's own order, so "choose one" keeps the first listed.
    const ranked = [...marked].sort((a, b) => rank(rp.pools, a.code) - rank(rp.pools, b.code));
    for (const m of ranked) {
      if (filled >= req.units) break;
      if (used.has(m.code)) continue;
      const units = unitsOf(program, m.code);
      // Room the other pools' unmet minimums still need: "6 units, at least 3
      // from Area A" never lets Area B take the 3 units Area A must fill.
      const reserved = (p: ResolvedPool) =>
        rp.pools.reduce((n, q) => (q === p ? n : n + Math.max(0, (q.min_units ?? 0) - (poolUnits.get(q.id) ?? 0))), 0);
      const pool = rp.pools.find(
        (p) =>
          inPool(p, m.code) &&
          (p.max_units === undefined || (poolUnits.get(p.id) ?? 0) + units <= p.max_units) &&
          filled + reserved(p) < req.units,
      );
      if (!pool) continue;
      used.add(m.code);
      poolUnits.set(pool.id, (poolUnits.get(pool.id) ?? 0) + units);
      filled += units;
      rp.assigned.push({ code: m.code, status: m.status, units, pool: pool.id });
      if (m.status === "done") rp.doneUnits += units;
      else rp.takingUnits += units;
    }
    rp.complete =
      rp.doneUnits + rp.takingUnits >= req.units &&
      rp.pools.every((p) => (poolUnits.get(p.id) ?? 0) >= (p.min_units ?? 0));
  }

  const requirements = program.requirements.map((r) => progress.get(r.id)!);
  const cap = (rp: RequirementProgress, kind: "done" | "taking") => {
    // A requirement never counts for more than its units toward the total.
    const done = Math.min(rp.doneUnits, rp.requirement.units);
    return kind === "done" ? done : Math.min(rp.takingUnits, rp.requirement.units - done);
  };
  return {
    requirements,
    uncounted: marked.filter((m) => !used.has(m.code)),
    doneUnits: requirements.reduce((n, rp) => n + cap(rp, "done"), 0),
    takingUnits: requirements.reduce((n, rp) => n + cap(rp, "taking"), 0),
    totalUnits: program.total_units,
  };
}

function rank(ps: ResolvedPool[], code: string): number {
  let i = 0;
  for (const p of ps) {
    const at = p.codes.indexOf(code);
    if (at >= 0) return i + at;
    i += p.codes.length;
  }
  return i; // matched by a rule, not listed: after every listed course
}

// -- next semester -----------------------------------------------------------------

export interface PlanSlot {
  requirement: Requirement;
  /** "take": these exact courses. "choose": pick `count` of `options`. */
  kind: "take" | "choose";
  count: number;
  options: string[];
  /** Set when the choice is limited to one pool ("Area A (at least 3 units)"). */
  pool?: string;
  /** Options the student has already marked Planned. */
  planned?: string[];
}

export interface PlanNote {
  requirement?: string;
  course?: string;
  text: string;
}

export interface Plan {
  slots: PlanSlot[];
  notes: PlanNote[];
  /** Units counted toward the degree once this term's courses are finished. */
  unitsAfterThisTerm: number;
  plannedUnits: number;
  totalUnits: number;
}

export function prerequisiteMet(
  program: GradProgram,
  code: string,
  finished: ReadonlySet<string>,
  unitsCounted: number,
  complete: (id: string) => boolean,
): boolean {
  const pre = program.prerequisites[code];
  if (!pre) return true;
  if (pre.min_units !== undefined && unitsCounted < pre.min_units) return false;
  if ((pre.courses ?? []).some((c) => !finished.has(c))) return false;
  if ((pre.requirements ?? []).some((r) => !complete(r))) return false;
  return true;
}

/** "Pick 1 of [CMPE 279]" is just "take CMPE 279". */
function slot(requirement: Requirement, count: number, options: string[], pool?: string): PlanSlot {
  if (options.length === count) return { requirement, kind: "take", count, options, pool };
  return { requirement, kind: "choose", count, options, pool };
}

export function planNext(program: GradProgram, marks: Marks, rawChoices: Choices = {}, load = 3): Plan {
  const choices = validChoices(program, rawChoices);
  const ev = evaluate(program, marks, choices);
  const finished = new Set(
    Object.entries(marks).filter(([, s]) => s === "done" || s === "taking").map(([c]) => c),
  );
  const unitsCounted = ev.doneUnits + ev.takingUnits;
  const byId = new Map(ev.requirements.map((rp) => [rp.requirement.id, rp]));
  const complete = (id: string) => byId.get(id)?.complete ?? false;
  const eligible = (code: string) => prerequisiteMet(program, code, finished, unitsCounted, complete);

  const slots: PlanSlot[] = [];
  const notes: PlanNote[] = [];
  const planned = new Set<string>();
  let left = Math.max(0, Math.min(4, Math.floor(load)));

  // Required lists first (core, then the culminating sequence once it's open),
  // then everything that is a choice, each group in the program's own order.
  const order = [...ev.requirements].sort(
    (a, b) => Number(!(a.body?.all)) - Number(!(b.body?.all)),
  );

  for (const rp of order) {
    const req = rp.requirement;
    if (rp.complete) continue;
    if (!rp.body) {
      const choice = program.choices.find((c) => c.id === req.choice);
      const name = choice?.label.toLowerCase() ?? "option";
      if (!choice?.infer) {
        notes.push({ requirement: req.label, text: `Choose your ${name} above to include ${req.label.toLowerCase()} in the plan.` });
        continue;
      }
      // Not decided yet, and decided by courses: start one. Only the first
      // requirement that depends on it is planned; the rest follow once it's set.
      const first = program.requirements.find((r) => r.choice === choice.id);
      if (first?.id !== req.id || left <= 0) continue;
      const options = unique(Object.values(req.by_option ?? {}).flatMap(listed)).filter(
        (c) => !finished.has(c) && !planned.has(c) && eligible(c),
      );
      const count = Math.min(Math.ceil(req.units / DEFAULT_UNITS), left, options.length);
      if (count > 0) {
        slots.push({ requirement: req, kind: "choose", count, options, pool: `all from one ${name}` });
        left -= count;
        notes.push({
          requirement: req.label,
          text: `Your ${name} is set by the courses you take: ${req.units} units from the same ${name}.`,
        });
      }
      continue;
    }
    if (left <= 0) continue;

    if (rp.body.all) {
      const missing = rp.body.all.filter((c) => !finished.has(c));
      const take: string[] = [];
      for (const code of missing) {
        if (take.length >= left) break;
        if (!eligible(code)) {
          const pre = program.prerequisites[code];
          notes.push({ requirement: req.label, course: code, text: pre?.note ?? `${code} isn't open to you yet.` });
          break; // a sequence: what follows waits too
        }
        take.push(code);
      }
      if (take.length) {
        slots.push({ requirement: req, kind: "take", count: take.length, options: take });
        take.forEach((c) => planned.add(c));
        left -= take.length;
      }
      continue;
    }

    let need = req.units - rp.doneUnits - rp.takingUnits;
    const poolUnits = new Map<string, number>();
    for (const a of rp.assigned) poolUnits.set(a.pool, (poolUnits.get(a.pool) ?? 0) + a.units);
    const optionsFor = (pool: ResolvedPool) =>
      pool.codes.filter((c) => !finished.has(c) && !planned.has(c) && eligible(c));
    const room = (pool: ResolvedPool) =>
      pool.max_units === undefined ? Infinity : pool.max_units - (poolUnits.get(pool.id) ?? 0);

    // A pool with a minimum not yet met is planned on its own first.
    for (const pool of rp.pools) {
      const short = (pool.min_units ?? 0) - (poolUnits.get(pool.id) ?? 0);
      if (short <= 0 || left <= 0) continue;
      const options = optionsFor(pool);
      const count = Math.min(Math.ceil(short / DEFAULT_UNITS), left, options.length);
      if (count <= 0) continue;
      slots.push(slot(req, count, options, pool.label));
      poolUnits.set(pool.id, (poolUnits.get(pool.id) ?? 0) + count * DEFAULT_UNITS);
      need -= count * DEFAULT_UNITS;
      left -= count;
    }
    if (need <= 0 || left <= 0) continue;

    const options = unique(
      rp.pools.filter((p) => room(p) >= DEFAULT_UNITS).flatMap((p) => optionsFor(p)),
    );
    const count = Math.min(Math.ceil(need / DEFAULT_UNITS), left, Math.max(options.length, 0));
    if (count > 0) {
      slots.push(slot(req, count, options));
      left -= count;
    } else if (rp.pools.some((p) => p.match)) {
      notes.push({ requirement: req.label, text: `Any eligible course counts toward ${req.label.toLowerCase()}; ask your advisor which to take.` });
    }
  }

  for (const s of slots) {
    const chosen = s.options.filter((c) => marks[c] === "planned");
    if (chosen.length) s.planned = chosen;
  }

  const plannedUnits = slots.reduce(
    (n, s) => n + (s.kind === "take" ? s.options.reduce((u, c) => u + unitsOf(program, c), 0) : s.count * DEFAULT_UNITS),
    0,
  );
  return { slots, notes, unitsAfterThisTerm: unitsCounted, plannedUnits, totalUnits: program.total_units };
}

// -- terms -------------------------------------------------------------------------

/** The next regular term after `today`: Aug-Dec plans Spring, Jan-Jul plans Fall. */
export function nextTerm(today: Date = new Date()): { label: string; key: string } {
  const y = today.getFullYear();
  const spring = today.getMonth() >= 7;
  const season = spring ? "Spring" : "Fall";
  const year = spring ? y + 1 : y;
  return { label: `${season} ${year}`, key: `${season.toLowerCase()}-${year}` };
}

/** SJSU's class schedule page for a term key ("spring-2027"); see campus/terms.py url_for. */
export function scheduleUrl(termKey: string): string {
  return `https://www.sjsu.edu/classes/schedules/${termKey}.php`;
}

// -- status, summaries and text ---------------------------------------------------

/**
 * Where the marked courses leave the degree: "open" while any requirement is
 * unfilled, "covered-if-passed" when they fill every requirement only by
 * counting this term's Taking courses, "covered" when Done courses alone do.
 * Requirements only -- the program's `conditions` (GPA, GWAR, oral exam) are
 * never checked here, so the page must never call "covered" done.
 */
export function completion(ev: Evaluation): "open" | "covered" | "covered-if-passed" {
  if (!ev.requirements.every((rp) => rp.complete)) return "open";
  return ev.requirements.some((rp) => rp.takingUnits > 0) ? "covered-if-passed" : "covered";
}

export type RequirementState = "complete" | "in-progress" | "open";

/**
 * MyProgress's three states per requirement: complete with Done courses alone,
 * in progress (something counts, or it completes only with Taking courses),
 * or not started.
 */
export function requirementState(rp: RequirementProgress): RequirementState {
  const doneIn = (id: string) =>
    rp.assigned.filter((a) => a.pool === id && a.status === "done").reduce((n, a) => n + a.units, 0);
  const doneOnly = rp.doneUnits >= rp.requirement.units && rp.pools.every((p) => doneIn(p.id) >= (p.min_units ?? 0));
  if (rp.complete && doneOnly) return "complete";
  if (rp.assigned.length > 0) return "in-progress";
  return "open";
}

export interface Summary {
  complete: number;
  inProgress: number;
  open: number;
  items: { id: string; label: string; state: RequirementState }[];
}

export function summarize(ev: Evaluation): Summary {
  const items = ev.requirements.map((rp) => ({
    id: rp.requirement.id,
    label: rp.requirement.label,
    state: requirementState(rp),
  }));
  return {
    complete: items.filter((i) => i.state === "complete").length,
    inProgress: items.filter((i) => i.state === "in-progress").length,
    open: items.filter((i) => i.state === "open").length,
    items,
  };
}

function moreCourses(n: number): string {
  return `${n} more course${n === 1 ? "" : "s"}`;
}

/**
 * What a requirement still needs, in MyProgress's words rather than units:
 * "1 more course from Area A", "2 more courses", or null when complete.
 */
export function shortfall(rp: RequirementProgress): string | null {
  if (rp.complete) return null;
  const got = (id: string) => rp.assigned.filter((a) => a.pool === id).reduce((n, a) => n + a.units, 0);
  const parts: string[] = [];
  let poolShort = 0;
  for (const p of rp.pools) {
    const short = (p.min_units ?? 0) - got(p.id);
    if (short > 0) {
      const n = Math.ceil(short / DEFAULT_UNITS);
      poolShort += n * DEFAULT_UNITS;
      parts.push(`${moreCourses(n)} from ${p.label.replace(/\s*\(.*\)\s*$/, "")}`);
    }
  }
  const rest = rp.requirement.units - rp.doneUnits - rp.takingUnits - poolShort;
  if (rest > 0) {
    const n = Math.ceil(rest / DEFAULT_UNITS);
    parts.push(parts.length ? `${moreCourses(n)} from any listed area` : moreCourses(n));
  }
  return parts.length ? parts.join(", plus ") : null;
}

export interface SearchHit {
  code: string;
  title: string | null;
  /** The requirements whose lists name this course, for the dropdown. */
  requirements: string[];
  /** A well-formed code the program doesn't list. */
  other: boolean;
}

function codeOf(raw: string): string | null {
  const text = raw.trim().replace(/\s+/g, " ").toUpperCase();
  const m = /^([A-Z][A-Z0-9]{0,5}) (\d{1,3}[A-Z]{0,3})$/.exec(text) ?? /^([A-Z]{1,6})(\d{1,3}[A-Z]{0,3})$/.exec(text);
  return m ? `${m[1]} ${m[2].replace(/^0+(?=\d)/, "")}` : null;
}

/**
 * Courses matching what the student typed, for the search box: code prefixes
 * first ("cmpe 25" finds CMPE 252-259), then title words ("deep" finds Deep
 * Learning), then -- when the text is a well-formed code the program doesn't
 * list -- that code as an "other" course.
 */
export function searchCourses(program: GradProgram, query: string, limit = 8): SearchHit[] {
  const q = query.trim().toLowerCase().replace(/\s+/g, " ");
  if (!q) return [];
  const listedBy = new Map<string, string[]>();
  for (const req of program.requirements) {
    const bodies = req.by_option ? Object.values(req.by_option) : [req];
    for (const b of bodies) {
      for (const c of [...listed(b), ...(b.suggest ?? [])]) {
        const names = listedBy.get(c) ?? [];
        if (!names.includes(req.label)) names.push(req.label);
        listedBy.set(c, names);
      }
    }
  }
  const codes = Object.keys(program.courses).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const compact = q.replace(/ /g, "");
  const byCode = codes.filter((c) => c.toLowerCase().replace(/ /g, "").startsWith(compact));
  const words = q.split(" ");
  const byTitle = codes.filter(
    (c) => !byCode.includes(c) && words.every((w) => (titleOf(program, c) ?? "").toLowerCase().includes(w)),
  );
  const out: SearchHit[] = [...byCode, ...byTitle].slice(0, limit).map((code) => ({
    code, title: titleOf(program, code), requirements: listedBy.get(code) ?? [], other: false,
  }));
  const code = codeOf(query);
  if (code && !program.courses[code] && out.length < limit) {
    out.push({ code, title: null, requirements: [], other: true });
  }
  return out;
}

/** Marks with Planned courses counted as if taken: for "with your plan" projections only. */
export function withPlanned(marks: Marks): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [c, s] of Object.entries(marks)) out[c] = s === "planned" ? "taking" : s;
  return out;
}

/** The plan as plain text, for an advisor meeting or a note. */
export function planText(program: GradProgram, plan: Plan, term: { label: string }, ev: Evaluation): string {
  const name = (c: string) => {
    const t = titleOf(program, c);
    return t ? `${c} ${t}` : c;
  };
  const lines = [
    `${program.name}: ${ev.doneUnits + ev.takingUnits} of ${program.total_units} units (${ev.doneUnits} done, ${ev.takingUnits} taking)`,
    "",
    `Plan for ${term.label}:`,
  ];
  if (!plan.slots.length) lines.push("- Nothing to plan.");
  for (const sl of plan.slots) {
    if (sl.kind === "take") {
      for (const c of sl.options) lines.push(`- ${name(c)} (${sl.requirement.label})`);
    } else {
      lines.push(`- Pick ${sl.count} for ${sl.requirement.label}${sl.pool ? `, ${sl.pool}` : ""}: ${sl.options.join(", ")}`);
    }
  }
  for (const n of plan.notes) lines.push(`Note: ${n.course ? `${n.course}: ` : ""}${n.text}`);
  lines.push("", "A planning guide from published requirements. MyProgress and your advisor are authoritative.");
  return lines.join("\n");
}
