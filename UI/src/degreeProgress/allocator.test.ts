// Is evaluate()'s greedy, most-specific-first assignment of courses to
// requirements optimal? For random course sets in every program, compare the
// units it counts with the best any valid assignment can count (brute force).
//
// A valid assignment puts each course in at most one requirement pool that
// accepts it, keeps every pool within its max, keeps every requirement within
// its units, and leaves room for every pool minimum that isn't met yet
// (sum over pools of max(units, min) <= requirement units) -- the same rules
// evaluate() follows, which MyProgress shows as "Area A: 1 course" plus
// "Area A or B: 2 courses".
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { evaluate, matchesRule, resolvedPools, unitsOf, type GradProgram, type ResolvedPool } from "./requirements.ts";

function program(id: string): GradProgram {
  return JSON.parse(readFileSync(new URL(`../../public/data/programs/${id}.json`, import.meta.url), "utf8"));
}

/** mulberry32: a small seeded PRNG, so failures reproduce. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Slot {
  req: string;
  units: number;
  pool: ResolvedPool;
}

function best(p: GradProgram, codes: string[], choices: Record<string, string>): number {
  const pools = resolvedPools(p, choices);
  const slots: Slot[] = [];
  for (const r of p.requirements) for (const pool of pools[r.id] ?? []) slots.push({ req: r.id, units: r.units, pool });
  const accepts = (s: Slot, c: string) => s.pool.codes.includes(c) || (!!s.pool.match && matchesRule(s.pool.match, c));
  const options = codes.map((c) => slots.filter((s) => accepts(s, c)));

  const poolUnits = new Map<Slot, number>();
  const valid = (req: string, units: number) => {
    let sum = 0;
    for (const s of slots) if (s.req === req) sum += Math.max(poolUnits.get(s) ?? 0, s.pool.min_units ?? 0);
    return sum <= units;
  };
  let top = 0;
  const go = (i: number, total: number, remaining: number) => {
    if (total + remaining <= top) return; // can't beat the best found
    if (i === codes.length) {
      top = Math.max(top, total);
      return;
    }
    const u = unitsOf(p, codes[i]);
    for (const s of options[i]) {
      const now = poolUnits.get(s) ?? 0;
      if (s.pool.max_units !== undefined && now + u > s.pool.max_units) continue;
      poolUnits.set(s, now + u);
      // The requirement's units, with room kept for unmet minimums.
      let filled = 0;
      for (const t of slots) if (t.req === s.req) filled += poolUnits.get(t) ?? 0;
      if (filled <= s.units && valid(s.req, s.units)) go(i + 1, total + u, remaining - u);
      poolUnits.set(s, now);
    }
    go(i + 1, total, remaining - u); // count it nowhere
  };
  go(0, 0, codes.reduce((n, c) => n + unitsOf(p, c), 0));
  return top;
}

function sample(p: GradProgram, rand: () => number, extra: string[]) {
  const pool = [...Object.keys(p.courses), ...extra];
  const n = 1 + Math.floor(rand() * 8);
  const codes = new Set<string>();
  while (codes.size < n) codes.add(pool[Math.floor(rand() * pool.length)]);
  const marks: Record<string, string> = {};
  for (const c of codes) marks[c] = rand() < 0.7 ? "done" : "taking";
  const choices: Record<string, string> = {};
  for (const ch of p.choices) if (rand() < 0.85) choices[ch.id] = ch.options[Math.floor(rand() * ch.options.length)].id;
  return { codes: [...codes], marks, choices };
}

for (const [id, extra] of [["msai", []], ["msse", ["CMPE 220", "CMPE 296A"]], ["mscs", []]] as const) {
  test(`${id}: the greedy assignment counts as many units as any valid assignment`, () => {
    const p = program(id);
    const rand = rng(20261002 + id.length);
    for (let i = 0; i < 150; i += 1) {
      const { codes, marks, choices } = sample(p, rand, [...extra]);
      const ev = evaluate(p, marks, choices);
      const greedy = ev.doneUnits + ev.takingUnits;
      const optimum = best(p, codes, choices);
      assert.equal(greedy, optimum, `${id} ${JSON.stringify({ marks, choices })}`);
    }
  });
}

test("the brute force agrees on the official MyProgress record", () => {
  const p = program("msai");
  const codes = ["CMPE 252", "CMPE 257", "ISE 201", "CMPE 294", "CMPE 258", "CMPE 256", "CMPE 260",
    "CMPE 295A", "CMPE 255", "CMPE 259", "CMPE 295B"];
  assert.equal(best(p, codes, { track: "intelligent-data-systems", plan: "project" }), 33);
});
