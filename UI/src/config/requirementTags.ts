import data from './requirement_tags.json' with { type: 'json' }

export type TagSystem =
  | 'cal-getc'
  | 'legacy-ge'
  | 'upper-division'
  | 'american-institutions'
  | 'other'

export interface RequirementTag {
  id: string
  label: string
  system: TagSystem
}

export interface CellTags {
  tags: string[]
  raw: string
  known: boolean
}

export const TAGS: readonly RequirementTag[] = data.tags as RequirementTag[]
export const CELLS: Readonly<Record<string, readonly string[]>> = data.cells

/** Trim, collapse whitespace runs to one space, drop whitespace after a colon. Nothing else. */
export function normalizeCell(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ').replace(/:\s+/g, ':')
}

/** Tags for a raw Satisfies cell. Unknown cells give no tags; raw is always kept. */
export function tagsForCell(raw: string): CellTags {
  const key = normalizeCell(raw)
  const hit = Object.hasOwn(CELLS, key) ? CELLS[key] : undefined
  return { tags: hit ? [...hit] : [], raw, known: hit !== undefined }
}
