// Types for the browser-side unofficial-transcript parser.
//
// Privacy: there is deliberately no name, student ID or date-of-birth field.
// The parser drops header identity lines instead of storing them.
//
// Column meanings (UA, UG, UE, GR, GP, GPA) come from SJSU's sample and are
// DOCUMENTED, NOT CONFIRMED: presumably units attempted, units graded, units
// earned, grade, grade points, GPA. The fields keep the raw column names so
// nothing here claims more than the printout shows.

/** One reconstructed text line, the output shape of extractText. */
export interface TextItem {
  str: string;
  x: number;
}
export interface TextLine {
  page: number;
  y: number;
  items: TextItem[];
  text: string;
}

export interface TranscriptCourse {
  subject: string; // "CS"
  number: string; // "157C"
  title: string;
  ua: number | null;
  ug: number | null;
  ue: number | null;
  /** Letter grade or CR/NC/W/I/RP...; null when blank (in progress). */
  grade: string | null;
  gp: number | null;
}

/** Totals as printed. Never computed by the parser. */
export interface TranscriptTotals {
  ua: number | null;
  ug: number | null;
  ue: number | null;
  gp: number | null;
  gpa: number | null;
}

export type Season = "Fall" | "Spring" | "Summer" | "Winter";

export interface TranscriptTerm {
  season: Season;
  year: number;
  major: string | null;
  courses: TranscriptCourse[];
  /** The "SEMESTER TOTAL" line, if present. */
  totals: TranscriptTotals | null;
}

export interface TranscriptRecord {
  parserVersion: string;
  terms: TranscriptTerm[];
  /** Cumulative total lines as printed (there may be several, e.g. per career). */
  cumulative: TranscriptTotals[];
  /** Lines that matched nothing. Counted only: their text is never kept. */
  unparsedCount: number;
  unparsedByPage: Record<number, number>;
  /** Identity or pre-term header lines discarded, counted only. */
  droppedHeaderLines: number;
}

export interface ParseResult {
  record: TranscriptRecord;
  parserVersion: string;
}
