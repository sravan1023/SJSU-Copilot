// In-memory holder for the current degree record. Module state only: nothing
// goes to localStorage, sessionStorage, IndexedDB or Supabase.
import type { TranscriptRecord } from "./types.ts";

let current: TranscriptRecord | null = null;

export function getDegreeRecord(): TranscriptRecord | null {
  return current;
}

export function setDegreeRecord(record: TranscriptRecord): void {
  current = record;
}

export function clearDegreeRecord(): void {
  current = null;
}
