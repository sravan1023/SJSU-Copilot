// Pure unofficial-transcript parser: (lines) => result. No DOM, no pdf.js.
//
// Format (from a 3-line documented SJSU sample; UNVERIFIED against a real
// transcript, no sample PDF has been seen):
//   FALL SEMESTER 2022 / MAJOR: MS Computer Science
//                             UA   UG   UE  GR  GP   GPA
//   CS 157C NoSQL             3.0  3.0  3.0 A  12.0
//   SEMESTER TOTAL:           9.0  9.0  9.0    36.0 4.000
// UA/UG/UE/GR/GP/GPA are presumably units attempted / graded / earned, grade,
// grade points, GPA. DOCUMENTED, NOT CONFIRMED. The parser records what is
// printed and NEVER computes a GPA.
//
// Parsing is token-based on the reconstructed line text: columns are located
// by order from the title onward, not by runs of spaces, which pdf.js loses.
import type {
  ParseResult,
  Season,
  TextLine,
  TranscriptCourse,
  TranscriptRecord,
  TranscriptTerm,
  TranscriptTotals,
} from "../types.ts";

export const PARSER_VERSION = "transcript-0.1.0";

const NUM = /^\d+\.\d+$/;
const GRADE = /^(?:[A-D][+-]?|F|CR|NC|W|WU|I|IC|RP|AU|NR|RD|SP)$/;
const TERM_HEADER =
  /^(FALL|SPRING|SUMMER|WINTER)(?:\s+(?:SEMESTER|TERM|SESSION))?\s+(\d{4})(?:\s*\/\s*(?:MAJOR:?\s*)?(.*))?$/i;
const COURSE_HEAD = /^([A-Z]{2,6}) (\d{1,3}[A-Z]{0,2}) (.+)$/;
const TERM_TOTAL = /^(?:SEMESTER|TERM)\s+TOTALS?:?\s*(.*)$/i;
const CUM_TOTAL = /^(?:CUM(?:ULATIVE)?|OVERALL)\b[^:\d]*:?\s*(\d.*)$/i;
const COLUMN_HEADER = /^UA\s+UG\s+UE\s+GR\s+GP\s+GPA$/i;
// Identity-bearing text. Matched unanchored, anywhere in a line, because
// groupItems merges the whole page width into one line. Dropped, never stored.
const IDENTITY_PATTERNS: RegExp[] = [
  /\b(?:STUDENT|SJSU|EMPL)\s*(?:NAME|ID)?\s*:/i,
  /\b(?:STUDENT|EMPL|SJSU)\s*ID\b/i,
  /\bNAME\s*:/i,
  /\bID\s*:/i,
  /\bDOB\b/i,
  /BIRTH/i,
  /\bTRANSCRIPT\s+FOR\b/i,
  /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/,
  /(?:\+?1[ .-]?)?\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b/,
  /\b\d{8,9}\b/,
  /\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/,
  /\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.? \d{1,2},? \d{4}\b/i,
];
const TERM_PREFIX = /^(?:FALL|SPRING|SUMMER|WINTER)\b/i;
// A major is plain words only; anything else is not stored.
const MAJOR_OK = /^[A-Za-z][A-Za-z &,.'()/-]{0,79}$/;

/** Index of the earliest identity-looking text, or -1. */
export function identityIndex(text: string): number {
  let best = -1;
  for (const re of IDENTITY_PATTERNS) {
    const m = re.exec(text);
    if (m && (best < 0 || m.index < best)) best = m.index;
  }
  return best;
}

function titleCase(s: string): Season {
  return (s[0].toUpperCase() + s.slice(1).toLowerCase()) as Season;
}

function toTotals(tokens: string[]): TranscriptTotals | null {
  if (!tokens.length || !tokens.every((t) => NUM.test(t))) return null;
  const n = tokens.map(Number);
  if (n.length === 5) return { ua: n[0], ug: n[1], ue: n[2], gp: n[3], gpa: n[4] };
  if (n.length === 4) return { ua: n[0], ug: n[1], ue: n[2], gp: n[3], gpa: null };
  return null;
}

function parseCourse(text: string): TranscriptCourse | null {
  const m = COURSE_HEAD.exec(text);
  if (!m) return null;
  const tokens = m[3].split(" ");
  // The title runs up to the first decimal token (the units columns).
  const firstNum = tokens.findIndex((t) => NUM.test(t));
  if (firstNum < 1) return null; // no title, or no units
  const title = tokens.slice(0, firstNum).join(" ");
  const rest = tokens.slice(firstNum);
  const units: number[] = [];
  let i = 0;
  while (i < rest.length && NUM.test(rest[i]) && units.length < 3) units.push(Number(rest[i++]));
  let grade: string | null = null;
  let gp: number | null = null;
  if (i < rest.length && GRADE.test(rest[i])) grade = rest[i++];
  if (i < rest.length && NUM.test(rest[i])) gp = Number(rest[i++]);
  if (i !== rest.length) return null;
  return {
    subject: m[1],
    number: m[2],
    title,
    ua: units[0] ?? null,
    ug: units[1] ?? null,
    ue: units[2] ?? null,
    grade,
    gp,
  };
}

export function parseTranscript(lines: TextLine[]): ParseResult {
  const terms: TranscriptTerm[] = [];
  const cumulative: TranscriptTotals[] = [];
  let unparsedCount = 0;
  const unparsedByPage: Record<number, number> = {};
  let dropped = 0;
  let current: TranscriptTerm | null = null;

  for (const line of lines) {
    let text = line.text.replace(/\s+/g, " ").trim();
    if (!text) continue;

    // Identity first. A term header with an identity tail keeps its head
    // (the term is real data); every other identity line is dropped whole.
    const idx = identityIndex(text);
    if (idx >= 0) {
      dropped++;
      if (idx === 0 || !TERM_PREFIX.test(text)) continue;
      text = text.slice(0, idx).replace(/[\s/:-]+$/, "").trim();
    }

    const th = TERM_HEADER.exec(text);
    if (th) {
      const major = th[3]?.trim() ?? "";
      current = {
        season: titleCase(th[1]),
        year: Number(th[2]),
        major: MAJOR_OK.test(major) ? major : null,
        courses: [],
        totals: null,
      };
      terms.push(current);
      continue;
    }
    if (COLUMN_HEADER.test(text)) continue;

    const tt = TERM_TOTAL.exec(text);
    if (tt && current) {
      const totals = toTotals(tt[1].split(" ").filter(Boolean));
      if (totals) {
        current.totals = totals;
        continue;
      }
    }
    const ct = CUM_TOTAL.exec(text);
    if (ct) {
      const totals = toTotals(ct[1].split(" ").filter(Boolean));
      if (totals) {
        cumulative.push(totals);
        continue;
      }
    }
    const course = parseCourse(text);
    if (course && current) {
      current.courses.push(course);
    } else if (current || course) {
      unparsedCount++;
      unparsedByPage[line.page] = (unparsedByPage[line.page] ?? 0) + 1;
    } else {
      // Before the first term: the header block, which may hold an
      // unlabelled name. Counted, not stored.
      dropped++;
    }
  }

  const record: TranscriptRecord = {
    parserVersion: PARSER_VERSION,
    terms,
    cumulative,
    unparsedCount,
    unparsedByPage,
    droppedHeaderLines: dropped,
  };
  return { record, parserVersion: PARSER_VERSION };
}
